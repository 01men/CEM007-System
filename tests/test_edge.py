"""第三轮：边界测试 —— 异常输入、越权、状态机边界、幂等。"""
import io

from openpyxl import Workbook

from conftest import login


# ── 认证边界 ──

def test_unauthenticated_401(client):
    assert client.get("/api/carbon/activities", params={"year": 2024}).status_code == 401
    assert client.get("/api/analysis/dashboard", params={"year": 2024}).status_code == 401


def test_local_login_wrong_password(client):
    r = client.post("/api/auth/local-login", json={"name": "系统管理员", "password": "wrong"})
    assert r.status_code == 401


def test_local_login_ok_then_force_revoke(client):
    r = client.post("/api/auth/local-login", json={"name": "系统管理员", "password": "admin123"})
    assert r.status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    # 管理员强制下线自己 → 会话立即失效
    client.post("/api/sys/users/1/revoke")
    assert client.get("/api/auth/me").status_code == 401


def test_pending_user_cannot_use_api(client):
    """未绑定钉钉的新用户进入 pending，接口 401。"""
    client.post("/api/auth/mock-login", json={"unionid": "unknown-new-user"})
    assert client.get("/api/auth/me").status_code == 401


# ── 越权（4.4 权限矩阵）──

def test_reporter_forbidden_admin_endpoints(client):
    login(client, "mock-reporter-admin")
    assert client.get("/api/sys/users").status_code == 403
    assert client.post("/api/energy/types",
                       json={"code": "X", "name": "X", "unit": "X"}).status_code == 403
    assert client.get("/api/sys/logs").status_code == 403


def test_reporter_cross_dept_carbon_forbidden(client):
    """财务填报员写行政部科目（天然气）→ 403（评审 A2）。"""
    login(client, "mock-reporter-fin")
    r = client.post("/api/carbon/activities", json={
        "year": 2026,
        "items": [{"source_code": "S1-NG-001", "activity_value": 100}]})
    assert r.status_code == 403
    # 本科目（财务部 S3-MILEAGE）正常
    r = client.post("/api/carbon/activities", json={
        "year": 2026,
        "items": [{"source_code": "S3-MILEAGE", "activity_value": 100}]})
    assert r.status_code == 200


def test_auditor_readonly_logs(client):
    login(client, "mock-auditor")
    assert client.get("/api/sys/logs", params={"type": "op"}).status_code == 200
    assert client.post("/api/energy/records", json={
        "year": 2025, "month": 1, "energy_type_code": "ELEC", "unit_id": 1,
        "quantity": 1}).status_code == 403


# ── 非法输入 ──

def test_negative_and_illegal_values(client):
    login(client, "mock-reporter-admin")
    # 负数用量 → 422（pydantic）
    r = client.post("/api/energy/records", json={
        "year": 2025, "month": 1, "energy_type_code": "ELEC", "unit_id": 1, "quantity": -1})
    assert r.status_code == 422
    # 13 月 → 422
    r = client.post("/api/energy/records", json={
        "year": 2025, "month": 13, "energy_type_code": "ELEC", "unit_id": 1, "quantity": 1})
    assert r.status_code == 422
    # 字典外能源类型 → 400
    r = client.post("/api/energy/records", json={
        "year": 2025, "month": 1, "energy_type_code": "NOPE", "unit_id": 1, "quantity": 1})
    assert r.status_code == 400
    # 净电量为负（总表 < 外租+光伏）→ 400
    r = client.post("/api/energy/records", json={
        "year": 2025, "month": 2, "energy_type_code": "ELEC", "unit_id": 1, "quantity": 1,
        "gross_qty": 100, "lease_deduct": 80, "pv_qty": 50})
    assert r.status_code == 400
    # 碳活动负数 → 422
    r = client.post("/api/carbon/activities", json={
        "year": 2026, "items": [{"source_code": "S1-NG-001", "activity_value": -5}]})
    assert r.status_code == 422


def test_factor_boundaries(client):
    login(client, "mock-admin")
    # 变更原因为空 → 422
    r = client.post("/api/carbon/factors", json={
        "source_code": "S2-ELEC-001", "factor": 0.5, "year_from": 2030,
        "ref_source": "x", "change_reason": ""})
    assert r.status_code == 422
    # 年度止 < 起 → 400
    r = client.post("/api/carbon/factors", json={
        "source_code": "S2-ELEC-001", "factor": 0.5, "year_from": 2030, "year_to": 2029,
        "ref_source": "x", "change_reason": "x"})
    assert r.status_code == 400
    # 与已有版本区间重叠（2025-长期）→ 409
    r = client.post("/api/carbon/factors", json={
        "source_code": "S2-ELEC-001", "factor": 0.5, "year_from": 2030,
        "ref_source": "x", "change_reason": "x"})
    assert r.status_code == 409
    # 科目不存在 → 404
    r = client.post("/api/carbon/factors", json={
        "source_code": "NOPE", "factor": 0.5, "year_from": 2030,
        "ref_source": "x", "change_reason": "x"})
    assert r.status_code == 404


# ── 状态机边界 ──

def test_audit_non_submitted_record(client):
    login(client, "mock-manager")
    assert client.post("/api/energy/records/999/audit",
                       json={"approve": True}).status_code == 404


def test_allocation_unapproved_year_400(client):
    login(client, "mock-viewer")
    r = client.get("/api/allocation", params={"year": 2030})
    assert r.status_code == 400


def test_pdf_export_unapproved_year_400(client):
    login(client, "mock-viewer")
    assert client.get("/api/export/pdf", params={"year": 2030}).status_code == 400


# ── 导入边界 ──

def test_import_rejects_non_xlsx_and_wrong_dict(client):
    login(client, "mock-manager")
    files = {"file": ("a.txt", b"hello", "text/plain")}
    assert client.post("/api/import/upload",
                       params={"biz_type": "carbon"}, files=files).status_code == 400
    # 因子导入仅管理员
    assert client.post("/api/import/upload",
                       params={"biz_type": "factor"},
                       files={"file": ("a.xlsx", b"x", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
                       ).status_code == 403


def test_import_error_detection_accuracy(client):
    """构造含 3 类错误的台账导入，错误行全部检出且原因准确（FR-4.2 验收）。"""
    login(client, "mock-manager")
    wb = Workbook()
    ws = wb.active
    ws.append(["年度", "月份", "能源类型编码", "用能单元编码", "用量", "费用（元）", "备注"])
    ws.append([2026, 1, "ELEC", "PLANT", 1, 1, "示例行"])
    ws.append([2026, 1, "ELEC", "PLANT", 100, 100, "合法"])
    ws.append([2026, 0, "ELEC", "PLANT", 100, None, ""])       # 月份非法
    ws.append([2026, 1, "WAT", "PLANT", 100, None, ""])         # 字典外编码
    ws.append([2026, 1, "ELEC", "PLANT", "abc", None, ""])      # 非数值
    buf = io.BytesIO()
    wb.save(buf)
    files = {"file": ("台账.xlsx", buf.getvalue(),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r = client.post("/api/import/upload", params={"biz_type": "energy"}, files=files)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["new"] == 1 and p["errors"] == 3
    reasons = [e["reason"] for e in p["error_detail"]]
    assert any("月份" in x for x in reasons)
    assert any("能源类型编码" in x for x in reasons)
    assert any("用量" in x for x in reasons)


# ── 钉钉 OAuth2 链路（真实模式，不打外网）──

def _real_ding_mode(monkeypatch, with_config=True):
    """切到非 Mock 模式；with_config 时写入测试用钉钉配置。"""
    import server.config
    monkeypatch.setattr(server.config, "DINGTALK_MOCK", False)
    from server import db
    db.execute("DELETE FROM ding_config")  # 种子已预置默认配置，先清空
    if with_config:
        from server import auth as srv_auth
        db.execute(
            "INSERT INTO ding_config(id,qr_app_id,qr_app_secret_enc) VALUES(1,?,?) "
            "ON CONFLICT(id) DO UPDATE SET qr_app_id=excluded.qr_app_id,"
            "qr_app_secret_enc=excluded.qr_app_secret_enc",
            ("dingtestkey", srv_auth.encrypt_secret("secret")))


def test_dingtalk_qr_url_requires_config(client, monkeypatch):
    _real_ding_mode(monkeypatch, with_config=False)
    assert client.get("/api/auth/dingtalk/qr-url").status_code == 400


def test_dingtalk_qr_url_absolute_and_state_once(client, monkeypatch):
    import urllib.parse
    _real_ding_mode(monkeypatch)
    r = client.get("/api/auth/dingtalk/qr-url")
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("https://login.dingtalk.com/oauth2/auth?")
    assert "client_id=dingtestkey" in url
    # 回调地址必须是绝对 URL（相对地址是钉钉扫码登录失败的常见原因）
    assert "redirect_uri=http" in url
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    assert state != "jujie" and len(state) == 32
    # state 一次性：首次有效（进入缺授权码分支），再次使用即失效
    r1 = client.get("/api/auth/dingtalk/callback", params={"state": state})
    assert r1.status_code == 302
    assert "授权码" in urllib.parse.unquote(r1.headers["location"])
    r2 = client.get("/api/auth/dingtalk/callback", params={"state": state})
    assert "过期" in urllib.parse.unquote(r2.headers["location"])


def test_dingtalk_callback_bad_state_redirects(client, monkeypatch):
    import urllib.parse
    _real_ding_mode(monkeypatch)
    r = client.get("/api/auth/dingtalk/callback",
                   params={"state": "bogus", "authCode": "x"})
    assert r.status_code == 302
    assert "过期" in urllib.parse.unquote(r.headers["location"])


# ── 用户管理：钉钉扫码绑定/解绑 ──

def _create_member(client, name="绑定测试员"):
    """管理员创建一个待绑定成员，返回其 id。"""
    r = client.post("/api/sys/users", json={
        "name": name, "role_code": "viewer", "status": "active"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_ding_bind_full_flow_mock(client):
    """Mock 模式：生成绑定链接 → 模拟成员扫码 → unionid 落库。"""
    import urllib.parse
    login(client, "mock-admin")
    uid = _create_member(client)
    # 生成绑定地址（Mock 返回本地模拟扫码页，携带绑定态 state）
    r = client.post(f"/api/sys/users/{uid}/ding-bind")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["mock"] is True and "mode=bind" in d["url"]
    state = urllib.parse.parse_qs(urllib.parse.urlparse(d["url"]).query)["state"][0]
    # 成员用钉钉扫码确认（Mock）：302 跳绑定结果页，不签发会话
    client.cookies.clear()  # 模拟成员手机（无管理员会话）
    r = client.post("/api/auth/mock-login",
                    json={"unionid": "mock-bind-target", "name": "绑定测试员", "state": state})
    assert r.status_code == 302
    loc = urllib.parse.unquote(r.headers["location"])
    assert loc.startswith("/bind-result.html?") and "ok=1" in loc
    assert "jujie_session" not in r.headers.get("set-cookie", "")
    # state 一次性：再次使用即失败
    r = client.post("/api/auth/mock-login",
                    json={"unionid": "mock-bind-target", "state": state})
    assert "过期" in urllib.parse.unquote(r.headers["location"])


def test_ding_bind_conflict_and_unbind(client):
    """同一钉钉账号不可绑定两个成员；解绑后可重新绑定。"""
    import urllib.parse
    from server import db
    login(client, "mock-admin")
    uid1 = _create_member(client, "绑定员甲")
    uid2 = _create_member(client, "绑定员乙")
    db.execute("UPDATE sys_user SET ding_unionid='mock-taken' WHERE id=?", (uid1,))
    # 乙尝试绑定已被甲占用的 unionid → 冲突
    r = client.post(f"/api/sys/users/{uid2}/ding-bind")
    state = urllib.parse.parse_qs(urllib.parse.urlparse(r.json()["url"]).query)["state"][0]
    r = client.post("/api/auth/mock-login", json={"unionid": "mock-taken", "state": state})
    assert r.status_code == 302
    assert "conflict" in r.headers["location"] or "已绑定" in \
        urllib.parse.unquote(r.headers["location"])
    # 解绑甲 → 乙可绑定
    assert client.post(f"/api/sys/users/{uid1}/ding-unbind").status_code == 200
    assert db.query_one("SELECT ding_unionid FROM sys_user WHERE id=?", (uid1,))["ding_unionid"] is None
    r = client.post(f"/api/sys/users/{uid2}/ding-bind")
    state = urllib.parse.parse_qs(urllib.parse.urlparse(r.json()["url"]).query)["state"][0]
    r = client.post("/api/auth/mock-login", json={"unionid": "mock-taken", "state": state})
    assert "ok=1" in urllib.parse.unquote(r.headers["location"])
    # 重复解绑 → 400
    assert client.post(f"/api/sys/users/{uid1}/ding-unbind").status_code == 400


def test_ding_bind_permission(client):
    """非管理员不能生成绑定链接/解绑。"""
    login(client, "mock-manager")
    assert client.post("/api/sys/users/1/ding-bind").status_code == 403
    assert client.post("/api/sys/users/1/ding-unbind").status_code == 403
    # mock-accounts 仅 Mock 模式可用（当前 DINGTALK_MOCK=1）
    assert client.get("/api/auth/mock-accounts").status_code == 200
