"""pytest 公共装置：每个测试文件使用独立临时数据库，Mock 登录模式。"""
import os
from pathlib import Path

import pytest

os.environ["DINGTALK_MOCK"] = "1"


def _build_client(db_path: Path):
    """在指定数据库上初始化应用并返回带 Cookie 罐的测试客户端。"""
    os.environ["JUJIE_DB_PATH"] = str(db_path)
    from server import auth, db, seeds
    db.configure(db_path)
    db.init_db()
    seeds.seed_all(auth.hash_password("admin123"))
    from fastapi.testclient import TestClient
    from server.main import create_app
    app = create_app()  # 内部 configure/init/seed 均幂等
    return TestClient(app, follow_redirects=False)


@pytest.fixture()
def client(tmp_path):
    """功能级隔离客户端（每个用例独立数据库）。"""
    yield _build_client(tmp_path / "test.db")


@pytest.fixture(scope="module")
def flow_client(tmp_path_factory):
    """模块级共享客户端（集成测试按业务流程顺序执行）。"""
    tmp = tmp_path_factory.mktemp("flow")
    yield _build_client(tmp / "flow.db")


# Mock 演示账号（种子仅保留 admin，其余由本装置按需开通，unionid 与原种子一致）
MOCK_USERS = {
    "mock-admin": ("系统管理员", "admin", "13800000001", None),
    "mock-manager": ("能碳管理员", "manager", "13800000002", None),
    "mock-reporter-admin": ("行政填报员", "reporter", "13800000003", "ADMIN"),
    "mock-reporter-fin": ("财务填报员", "reporter", "13800000004", "FIN"),
    "mock-viewer": ("总经理", "viewer", "13800000005", None),
    "mock-auditor": ("内审员", "auditor", "13800000006", None),
}


def login(client, unionid: str) -> None:
    """Mock 扫码登录，Cookie 自动存入客户端；用户不存在时按 MOCK_USERS 自动开通。"""
    from server import db
    if not db.query_one("SELECT 1 FROM sys_user WHERE ding_unionid=?", (unionid,)):
        name, role, mobile, dept = MOCK_USERS[unionid]
        existing = db.query_one("SELECT id FROM sys_user WHERE name=?", (name,))
        if existing:  # 种子 admin：直接绑定 unionid
            db.execute("UPDATE sys_user SET ding_unionid=? WHERE id=?", (unionid, existing["id"]))
        else:
            dept_id = db.query_one("SELECT id FROM sys_dept WHERE code=?", (dept,))["id"] \
                if dept else None
            db.execute(
                "INSERT INTO sys_user(name,mobile,dept_id,role_code,ding_unionid,status,"
                "created_at,created_by) VALUES(?,?,?,?,?,'active',?,'test')",
                (name, mobile, dept_id, role, unionid, db.now_iso()))
    r = client.post("/api/auth/mock-login", json={"unionid": unionid})
    assert r.status_code in (200, 302), f"登录失败: {r.status_code} {r.text}"
