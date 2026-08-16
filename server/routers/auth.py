"""认证路由：钉钉扫码登录（含 Mock）、本地兜底登录、会话管理。

钉钉 OAuth2 链路（PRD 5.5，对齐钉钉新版统一身份认证）：
qr-url 生成一次性 state 并给出绝对跳转地址 → 钉钉 302 回 callback 带 authCode+state
→ 校验并消费 state → 后端换 unionId/昵称/手机号 → 匹配本地用户 → 建会话。
Mock 模式（DINGTALK_MOCK=1）用 /mock-login 直接模拟身份换取，便于无凭据演示。
"""
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .. import audit, auth, config, db

router = APIRouter(prefix="/api/auth", tags=["认证"])

STATE_TTL_SECONDS = 600  # OAuth2 state 有效期 10 分钟，一次性消费


def _ding_config() -> dict:
    row = db.query_one("SELECT * FROM ding_config WHERE id=1")
    if not row:
        return {}
    cfg = dict(row)
    for k in ("qr_app_secret_enc", "app_secret_enc"):
        if cfg.get(k):
            try:
                cfg[k.replace("_enc", "")] = auth.decrypt_secret(cfg[k])
            except Exception:
                cfg[k.replace("_enc", "")] = ""
    return cfg


def _public_origin(request: Request) -> str:
    """推导对外访问源：环境变量 > 反向代理头 > 请求 Host。"""
    if config.PUBLIC_ORIGIN:
        return config.PUBLIC_ORIGIN.rstrip("/")
    fwd_host = request.headers.get("x-forwarded-host")
    if fwd_host:
        proto = request.headers.get("x-forwarded-proto", "http")
        return f"{proto}://{fwd_host.split(',')[0].strip()}"
    return str(request.base_url).rstrip("/")


def _new_state(bind_user_id: int | None = None) -> str:
    state = secrets.token_hex(16)
    db.execute("DELETE FROM auth_state WHERE expires_at<?", (int(time.time()),))
    db.execute("INSERT INTO auth_state(state, expires_at, bind_user_id) VALUES(?,?,?)",
               (state, int(time.time()) + STATE_TTL_SECONDS, bind_user_id))
    return state


def _consume_state(state: str) -> dict | None:
    """一次性消费：取出即删；缺失或过期均视为无效。返回整条记录（含 bind_user_id）。"""
    if not state:
        return None
    row = db.query_one("SELECT * FROM auth_state WHERE state=?", (state,))
    db.execute("DELETE FROM auth_state WHERE state=?", (state,))
    if row and row["expires_at"] >= int(time.time()):
        return dict(row)
    return None


def build_bind_url(uid: int, request: Request) -> dict:
    """为指定成员生成钉钉扫码绑定地址（绑定态 state）；Mock 模式返回本地模拟页。"""
    state = _new_state(bind_user_id=uid)
    if config.DINGTALK_MOCK:
        return {"mock": True, "url": f"/mock-scan.html?mode=bind&state={state}"}
    cfg = _ding_config()
    if not cfg.get("qr_app_id") or not cfg.get("qr_app_secret"):
        raise HTTPException(400, "钉钉扫码未配置，请先在系统管理中维护")
    redirect = urllib.parse.quote(
        f"{_public_origin(request)}/api/auth/dingtalk/callback", safe="")
    url = ("https://login.dingtalk.com/oauth2/auth?"
           f"redirect_uri={redirect}&response_type=code&client_id={cfg['qr_app_id']}"
           f"&scope=openid&state={state}&prompt=consent")
    return {"mock": False, "url": url}


@router.get("/dingtalk/qr-url")
def qr_url(request: Request):
    """返回钉钉扫码跳转地址；Mock 模式返回本地模拟扫码页地址。"""
    if config.DINGTALK_MOCK:
        return {"mock": True, "url": "/mock-scan.html"}
    cfg = _ding_config()
    if not cfg.get("qr_app_id") or not cfg.get("qr_app_secret"):
        raise HTTPException(400, "钉钉扫码登录未配置，请在系统管理中维护")
    redirect = urllib.parse.quote(
        f"{_public_origin(request)}/api/auth/dingtalk/callback", safe="")
    url = ("https://login.dingtalk.com/oauth2/auth?"
           f"redirect_uri={redirect}&response_type=code&client_id={cfg['qr_app_id']}"
           f"&scope=openid&state={_new_state()}&prompt=consent")
    return {"mock": False, "url": url}


def _dingtalk_error(exc: Exception) -> str:
    """提取钉钉返回的错误描述，便于排查配置/权限问题。"""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = json.loads(exc.read())
            return body.get("message") or body.get("code") or f"HTTP {exc.code}"
        except Exception:
            return f"HTTP {exc.code}"
    return str(exc)


def _exchange_auth_code(auth_code: str) -> dict:
    """authCode → userAccessToken → 钉钉用户身份（unionId/昵称/手机号）。"""
    cfg = _ding_config()
    token_req = urllib.request.Request(
        "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
        data=json.dumps({
            "clientId": cfg.get("qr_app_id"), "clientSecret": cfg.get("qr_app_secret"),
            "code": auth_code, "grantType": "authorization_code",
        }).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(token_req, timeout=10) as resp:
            token = json.loads(resp.read()).get("accessToken")
    except Exception as exc:
        raise RuntimeError(f"授权码校验失败：{_dingtalk_error(exc)}") from exc
    if not token:
        raise RuntimeError("授权码校验失败：钉钉未返回 accessToken")
    info_req = urllib.request.Request(
        "https://api.dingtalk.com/v1.0/contact/users/me",
        headers={"x-acs-dingtalk-access-token": token})
    try:
        with urllib.request.urlopen(info_req, timeout=10) as resp:
            me = json.loads(resp.read())
    except Exception as exc:
        raise RuntimeError(f"获取用户信息失败：{_dingtalk_error(exc)}") from exc
    if not me.get("unionId"):
        raise RuntimeError("获取用户信息失败：钉钉未返回 unionId")
    return {"unionid": me["unionId"], "name": me.get("nick", ""),
            "mobile": me.get("mobile", "")}


def _login_as(identity: dict, request: Request, response: Response) -> RedirectResponse:
    """身份匹配与登录落地：active→工作台；disabled→拦截；未绑定→手机号匹配或待开通。

    注意：直接返回 Response 时 Cookie 必须签在返回对象上（FastAPI 不会合并
    注入 response 上的 Cookie），因此重定向响应就地签发会话。
    """
    unionid, name, mobile = identity["unionid"], identity.get("name", ""), identity.get("mobile", "")
    user = auth.find_user_by_identity(unionid, mobile)
    if user and not user["ding_unionid"] and unionid:
        db.execute("UPDATE sys_user SET ding_unionid=?, updated_at=? WHERE id=?",
                   (unionid, db.now_iso(), user["id"]))
        user = db.query_one("SELECT * FROM sys_user WHERE id=?", (user["id"],))
    if user is None:
        # 未匹配：创建 pending 用户，等待管理员开通（PRD FR-1.1）
        db.execute(
            "INSERT INTO sys_user(name,mobile,role_code,ding_unionid,status,created_at,created_by)"
            " VALUES(?,?,'viewer',?, 'pending',?, 'dingtalk')",
            (name or unionid, mobile, unionid, db.now_iso()))
        audit.log_login(unionid, name, "login", "pending", request)
        return RedirectResponse("/pending.html", 302)
    if user["status"] == "disabled":
        audit.log_login(unionid, user["name"], "login", "disabled", request)
        return RedirectResponse("/login.html?err=disabled", 302)
    if user["status"] == "pending":
        audit.log_login(unionid, user["name"], "login", "pending", request)
        return RedirectResponse("/pending.html", 302)
    redirect = RedirectResponse("/", 302)
    auth.issue_session(redirect, user["id"])
    audit.log_login(unionid, user["name"], "login", "ok", request)
    return redirect


def _bind_as(identity: dict, bind_user_id: int, request: Request) -> RedirectResponse:
    """扫码绑定落地：把钉钉 unionId 绑定到指定成员，不签发会话。"""
    def _result(**params) -> RedirectResponse:
        return RedirectResponse("/bind-result.html?" + urllib.parse.urlencode(params), 302)

    unionid = identity["unionid"]
    target = db.query_one("SELECT * FROM sys_user WHERE id=?", (bind_user_id,))
    if not target:
        return _result(err="目标用户不存在或已删除")
    conflict = db.query_one(
        "SELECT id, name FROM sys_user WHERE ding_unionid=? AND id<>?", (unionid, bind_user_id))
    if conflict:
        audit.log_login(unionid, target["name"], "bind", f"conflict:{conflict['name']}", request)
        return _result(err=f"该钉钉账号已绑定成员「{conflict['name']}」", name=target["name"])
    db.execute("UPDATE sys_user SET ding_unionid=?, updated_at=?, updated_by='dingtalk-bind'"
               " WHERE id=?", (unionid, db.now_iso(), bind_user_id))
    audit.log_login(unionid, target["name"], "bind", "ok", request)
    return _result(ok="1", name=target["name"])


def _login_fail(msg: str) -> RedirectResponse:
    """登录失败统一回跳登录页并展示中文原因（对齐参考实践，不暴露裸 JSON 错误）。"""
    return RedirectResponse("/login.html?err=" + urllib.parse.quote(msg), 302)


@router.get("/dingtalk/callback")
def dingtalk_callback(request: Request, authCode: str = "", code: str = "",
                      state: str = ""):
    if config.DINGTALK_MOCK:
        raise HTTPException(400, "Mock 模式请使用 /api/auth/mock-login")
    st = _consume_state(state)
    if not st:
        return _login_fail("登录状态已过期，请重新扫码")
    auth_code = authCode or code  # 钉钉回跳参数名两种都兼容
    if not auth_code:
        return _login_fail("钉钉未返回授权码（可能已取消授权）")
    try:
        identity = _exchange_auth_code(auth_code)
    except Exception as exc:
        audit.log_login("", "", "login", f"dingtalk-error:{exc}", request)
        return _login_fail(str(exc))
    if st.get("bind_user_id"):
        return _bind_as(identity, st["bind_user_id"], request)
    return _login_as(identity, request, None)


class MockLogin(BaseModel):
    unionid: str
    name: str = ""
    mobile: str = ""
    state: str = ""  # 绑定流程必带


@router.post("/mock-login")
def mock_login(body: MockLogin, request: Request, response: Response):
    """Mock 扫码登录/绑定：模拟钉钉返回身份（仅 DINGTALK_MOCK=1 可用）。"""
    if not config.DINGTALK_MOCK:
        raise HTTPException(403, "Mock 模式未开启")
    if body.state:
        st = _consume_state(body.state)
        if not st or not st.get("bind_user_id"):
            return RedirectResponse("/bind-result.html?err=" +
                                    urllib.parse.quote("绑定状态已过期，请重新生成二维码"), 302)
        return _bind_as({"unionid": body.unionid, "name": body.name, "mobile": body.mobile},
                        st["bind_user_id"], request)
    user = db.query_one("SELECT * FROM sys_user WHERE ding_unionid=?", (body.unionid,))
    name = body.name or (user["name"] if user else body.unionid)
    mobile = body.mobile or (user["mobile"] if user else "")
    return _login_as({"unionid": body.unionid, "name": name, "mobile": mobile},
                     request, response)


@router.get("/mock-accounts")
def mock_accounts():
    """Mock 扫码页账号清单（仅 DINGTALK_MOCK=1）：现有用户 + 一个未注册路人身份。"""
    if not config.DINGTALK_MOCK:
        raise HTTPException(403, "Mock 模式未开启")
    rows = []
    for u in db.query("SELECT id, name, role_code, mobile, ding_unionid, status "
                      "FROM sys_user ORDER BY id"):
        rows.append({
            "id": u["id"], "name": u["name"], "role_code": u["role_code"],
            "status": u["status"],
            "unionid": u["ding_unionid"] or f"mock-user-{u['id']}",
            "mobile": u["mobile"] or "",
        })
    rows.append({"id": 0, "name": "未注册路人", "role_code": "", "status": "",
                 "unionid": "mock-stranger", "mobile": ""})
    return rows


class LocalLogin(BaseModel):
    name: str
    password: str


@router.post("/local-login")
def local_login(body: LocalLogin, request: Request, response: Response):
    """管理员本地口令兜底登录（评审 R1）。"""
    user = db.query_one(
        "SELECT * FROM sys_user WHERE name=? AND role_code='admin'", (body.name,))
    if not user or not user["password_hash"] or \
            not auth.verify_password(body.password, user["password_hash"]):
        audit.log_login("", body.name, "local-login", "bad-credential", request)
        raise HTTPException(401, "用户名或密码错误")
    if user["status"] != "active":
        raise HTTPException(401, "账号已停用")
    auth.issue_session(response, user["id"])
    audit.log_login(user["ding_unionid"] or "", user["name"], "local-login", "ok", request)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response, user=Depends(auth.get_current_user)):
    audit.log_login(user["ding_unionid"] or "", user["name"], "logout", "ok", request)
    auth.clear_session(response)
    return {"ok": True}


@router.get("/me")
def me(user=Depends(auth.get_current_user)):
    dept = db.query_one("SELECT name FROM sys_dept WHERE id=?", (user["dept_id"],))
    return {
        "id": user["id"], "name": user["name"], "role_code": user["role_code"],
        "dept_id": user["dept_id"], "dept_name": dept["name"] if dept else "",
        "mock_mode": config.DINGTALK_MOCK,
    }
