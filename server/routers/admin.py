"""系统管理路由（FR-10）：用户/部门、日志、备份恢复、钉钉配置、基础参数。"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .. import audit, auth, backup, db

router = APIRouter(prefix="/api/sys", tags=["系统管理"])

ADMIN = ("admin",)
LOG_READERS = ("admin", "auditor")


# ── 用户管理（FR-10.1）──

@router.get("/users")
def list_users(user=Depends(auth.require(*ADMIN))):
    rows = []
    for r in db.query("SELECT u.*, d.name AS dept_name FROM sys_user u "
                      "LEFT JOIN sys_dept d ON d.id=u.dept_id ORDER BY u.id"):
        d = dict(r)
        d.pop("password_hash", None)  # 哈希永不下发
        rows.append(d)
    return rows


class UserIn(BaseModel):
    name: str
    mobile: str | None = None
    dept_id: int | None = None
    role_code: str
    ding_unionid: str | None = None
    status: str = "active"
    password: str | None = None  # 仅 admin 需要本地口令时设置


@router.post("/users")
def upsert_user(body: UserIn, request: Request, user=Depends(auth.require(*ADMIN))):
    if body.role_code not in ("admin", "manager", "reporter", "viewer", "auditor"):
        raise HTTPException(400, "角色编码非法")
    if body.status not in ("pending", "active", "disabled"):
        raise HTTPException(400, "状态非法")
    now = db.now_iso()
    pwd_hash = auth.hash_password(body.password) if body.password else None
    existing = db.query_one("SELECT * FROM sys_user WHERE name=?", (body.name,))
    if existing:
        db.execute(
            "UPDATE sys_user SET mobile=?,dept_id=?,role_code=?,ding_unionid=?,status=?,"
            + ("password_hash=?," if pwd_hash else "")
            + "updated_at=?,updated_by=? WHERE id=?",
            ((body.mobile, body.dept_id, body.role_code, body.ding_unionid, body.status)
             + ((pwd_hash,) if pwd_hash else ())
             + (now, user["name"], existing["id"])))
        uid, action = existing["id"], "update"
    else:
        uid = db.execute(
            "INSERT INTO sys_user(name,mobile,dept_id,role_code,ding_unionid,status,"
            "password_hash,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?)",
            (body.name, body.mobile, body.dept_id, body.role_code, body.ding_unionid,
             body.status, pwd_hash, now, user["name"]))
        action = "create"
    audit.log_op(user, f"sys-user:{action}", "sys_user", uid,
                 f"{body.name}/{body.role_code}/{body.status}", request)
    return {"ok": True, "id": uid}


@router.post("/users/{uid}/revoke")
def revoke_session(uid: int, request: Request, user=Depends(auth.require(*ADMIN))):
    """强制下线：使该用户此前签发的全部会话失效。"""
    if not db.query_one("SELECT 1 FROM sys_user WHERE id=?", (uid,)):
        raise HTTPException(404, "用户不存在")
    db.execute("UPDATE sys_user SET session_revoked_at=?, updated_at=?, updated_by=? WHERE id=?",
               (db.now_iso(), db.now_iso(), user["name"], uid))
    audit.log_op(user, "sys-user:revoke", "sys_user", uid, "强制下线", request)
    return {"ok": True}


@router.post("/users/{uid}/ding-bind")
def ding_bind_url(uid: int, request: Request, user=Depends(auth.require(*ADMIN))):
    """生成该成员的钉钉扫码绑定地址（有效期 10 分钟，一次性）。"""
    target = db.query_one("SELECT id, name FROM sys_user WHERE id=?", (uid,))
    if not target:
        raise HTTPException(404, "用户不存在")
    from . import auth as auth_router
    result = auth_router.build_bind_url(uid, request)
    audit.log_op(user, "sys-user:ding-bind-url", "sys_user", uid,
                 f"为「{target['name']}」生成绑定二维码", request)
    return result


@router.post("/users/{uid}/ding-unbind")
def ding_unbind(uid: int, request: Request, user=Depends(auth.require(*ADMIN))):
    """解除该成员的钉钉绑定。"""
    target = db.query_one("SELECT id, name, ding_unionid FROM sys_user WHERE id=?", (uid,))
    if not target:
        raise HTTPException(404, "用户不存在")
    if not target["ding_unionid"]:
        raise HTTPException(400, "该用户尚未绑定钉钉")
    db.execute("UPDATE sys_user SET ding_unionid=NULL, updated_at=?, updated_by=? WHERE id=?",
               (db.now_iso(), user["name"], uid))
    audit.log_op(user, "sys-user:ding-unbind", "sys_user", uid,
                 f"解绑「{target['name']}」的钉钉账号", request)
    return {"ok": True}


# ── 部门管理 ──

@router.get("/depts")
def list_depts(user=Depends(auth.get_current_user)):
    return [dict(r) for r in db.query("SELECT * FROM sys_dept ORDER BY id")]


class DeptIn(BaseModel):
    code: str
    name: str
    parent_id: int | None = None
    enabled: bool = True


@router.post("/depts")
def upsert_dept(body: DeptIn, request: Request, user=Depends(auth.require(*ADMIN))):
    now = db.now_iso()
    if db.query_one("SELECT 1 FROM sys_dept WHERE code=?", (body.code,)):
        db.execute("UPDATE sys_dept SET name=?,parent_id=?,enabled=?,updated_at=?,updated_by=?"
                   " WHERE code=?",
                   (body.name, body.parent_id, int(body.enabled), now, user["name"], body.code))
    else:
        db.execute("INSERT INTO sys_dept(code,name,parent_id,enabled,created_at,created_by)"
                   " VALUES(?,?,?,?,?,?)",
                   (body.code, body.name, body.parent_id, int(body.enabled), now, user["name"]))
    audit.log_op(user, "sys-dept:upsert", "sys_dept", body.code, body.model_dump_json(), request)
    return {"ok": True}


# ── 日志（FR-10.3）──

@router.get("/logs")
def query_logs(type: str = "op", keyword: str | None = None, limit: int = 200,
               user=Depends(auth.require(*LOG_READERS))):
    table = "op_log" if type == "op" else "login_log"
    sql = f"SELECT * FROM {table}"
    params: list = []
    if keyword:
        sql += " WHERE user_name LIKE ? OR action LIKE ? OR detail LIKE ?" if type == "op" \
            else " WHERE user_name LIKE ? OR action LIKE ? OR result LIKE ?"
        kw = f"%{keyword}%"
        params = [kw, kw, kw]
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(min(limit, 1000))
    return [dict(r) for r in db.query(sql, tuple(params))]


# ── 备份与恢复（FR-10.4）──

@router.get("/backups")
def list_backup_files(user=Depends(auth.require(*ADMIN))):
    return backup.list_backups()


@router.post("/backup")
def manual_backup(request: Request, user=Depends(auth.require(*ADMIN))):
    path = backup.do_backup(manual=True)
    audit.log_op(user, "sys:backup", "backup", path.name, "", request)
    return {"ok": True, "file": path.name}


@router.post("/restore")
async def restore_backup(file: UploadFile, confirm: bool = False,
                         request: Request = None, user=Depends(auth.require(*ADMIN))):
    """恢复：校验 → 二次确认（confirm=true）→ 恢复前自动备份当前库。"""
    content = await file.read()
    try:
        backup.validate_backup(content)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not confirm:
        return {"validated": True, "need_confirm": True,
                "message": "文件校验通过。恢复将覆盖当前数据库（恢复前会自动备份当前库），"
                           "确认请携带 confirm=true 重新提交。"}
    backup.restore(content)
    audit.log_op(user, "sys:restore", "backup", file.filename or "", "恢复数据库", request)
    return {"ok": True, "message": "恢复完成"}


@router.get("/backups/{name}/download")
def download_backup(name: str, user=Depends(auth.require(*ADMIN))):
    from fastapi.responses import Response
    path = backup.config.BACKUP_DIR / Path(name).name  # 防路径穿越
    if not path.exists():
        raise HTTPException(404, "备份文件不存在")
    return Response(path.read_bytes(), media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename={path.name}"})


# ── 钉钉配置（FR-10.5）──

@router.get("/ding-config")
def get_ding_config(user=Depends(auth.require(*ADMIN))):
    row = db.query_one("SELECT * FROM ding_config WHERE id=1")
    if not row:
        return {}
    d = dict(row)
    for k in ("qr_app_secret_enc", "app_secret_enc"):
        d[k] = "******" if d.get(k) else ""  # 密钥不明文回显（PRD 5.6）
    from .. import config
    d["mock_mode"] = config.DINGTALK_MOCK
    return d


class DingConfigIn(BaseModel):
    corp_id: str | None = None
    qr_app_id: str | None = None
    qr_app_secret: str | None = None  # 留空则不更新
    app_key: str | None = None
    app_secret: str | None = None
    admin_contact: str | None = None


@router.post("/ding-config")
def set_ding_config(body: DingConfigIn, request: Request,
                    user=Depends(auth.require(*ADMIN))):
    now = db.now_iso()
    old = db.query_one("SELECT * FROM ding_config WHERE id=1")
    qr_secret = auth.encrypt_secret(body.qr_app_secret) if body.qr_app_secret \
        else (old["qr_app_secret_enc"] if old else None)
    app_secret = auth.encrypt_secret(body.app_secret) if body.app_secret \
        else (old["app_secret_enc"] if old else None)
    db.execute(
        "INSERT INTO ding_config(id,corp_id,qr_app_id,qr_app_secret_enc,app_key,"
        "app_secret_enc,admin_contact,updated_at) VALUES(1,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET corp_id=excluded.corp_id, qr_app_id=excluded.qr_app_id,"
        "qr_app_secret_enc=excluded.qr_app_secret_enc, app_key=excluded.app_key,"
        "app_secret_enc=excluded.app_secret_enc, admin_contact=excluded.admin_contact,"
        "updated_at=excluded.updated_at",
        (body.corp_id, body.qr_app_id, qr_secret, body.app_key, app_secret,
         body.admin_contact, now))
    audit.log_op(user, "sys:ding-config", "ding_config", "1", "更新钉钉配置", request)
    return {"ok": True}


@router.post("/ding-config/test")
def test_ding_config(user=Depends(auth.require(*ADMIN))):
    """连通性测试：获取 access_token 验证（FR-10.5）。Mock 模式直接通过。"""
    from .. import config
    if config.DINGTALK_MOCK:
        return {"ok": True, "message": "Mock 模式：跳过真实连通性测试"}
    import json
    import urllib.request
    row = db.query_one("SELECT * FROM ding_config WHERE id=1")
    if not row or not row["app_key"] or not row["app_secret_enc"]:
        raise HTTPException(400, "请先配置 AppKey/AppSecret")
    try:
        secret = auth.decrypt_secret(row["app_secret_enc"])
        req = urllib.request.Request(
            "https://api.dingtalk.com/v1.0/oauth2/accessToken",
            data=json.dumps({"appKey": row["app_key"], "appSecret": secret}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return {"ok": True, "message": "连通性测试通过", "expire_in": data.get("expireIn")}
    except Exception as exc:
        raise HTTPException(502, f"连通性测试失败：{exc}")


# ── 基础参数（FR-10.6，一期实现基准年/管理员联系方式展示）──

@router.get("/params")
def get_params(user=Depends(auth.get_current_user)):
    from .. import config
    contact = db.query_one("SELECT admin_contact FROM ding_config WHERE id=1")
    return {"base_year": config.BASE_YEAR,
            "admin_contact": (contact["admin_contact"] if contact else "") or "系统管理员",
            "mock_mode": config.DINGTALK_MOCK}
