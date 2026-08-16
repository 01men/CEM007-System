"""认证与会话：钉钉 OAuth2 身份换取、签名 Cookie 会话、角色鉴权、口令哈希、密钥加密。

会话方案（评审 A4）：itsdangerous 签名 Cookie，8h 滑动续期；
强制下线通过 sys_user.session_revoked_at 时间戳使旧会话失效。
"""
import base64
import hashlib
import hmac
import os
import time

from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeSerializer

from . import config, db

# ── 密钥（会话签名 + 配置加密共用根密钥）──

def _load_root_key() -> bytes:
    if config.SECRET_KEY_PATH.exists():
        return config.SECRET_KEY_PATH.read_bytes().strip()
    key = Fernet.generate_key()
    config.SECRET_KEY_PATH.write_bytes(key)
    try:
        os.chmod(config.SECRET_KEY_PATH, 0o600)
    except OSError:
        pass  # Windows 上 chmod 受限，忽略
    return key


_ROOT_KEY = _load_root_key()
_serializer = URLSafeSerializer(_ROOT_KEY, salt="jujie-session")
_fernet = Fernet(_ROOT_KEY)


def encrypt_secret(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()


# ── 口令哈希（PBKDF2-HMAC-SHA256, 10 万轮，仅 admin 兜底登录用）──

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return f"pbkdf2${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, dk_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


# ── 会话 ──

def issue_session(response: Response, user_id: int) -> None:
    token = _serializer.dumps({"uid": user_id, "iat": int(time.time())})
    response.set_cookie(
        config.SESSION_COOKIE, token,
        max_age=config.SESSION_TTL_SECONDS,  # 浏览器侧 8h，滑动续期见 get_current_user
        httponly=True, samesite="lax")


def clear_session(response: Response) -> None:
    response.delete_cookie(config.SESSION_COOKIE)


def _parse_session(token: str) -> dict | None:
    try:
        data = _serializer.loads(token)
    except BadSignature:
        return None
    if not isinstance(data, dict) or "uid" not in data or "iat" not in data:
        return None
    if int(time.time()) - int(data.get("iat", 0)) > config.SESSION_TTL_SECONDS:
        return None
    return data


def get_current_user(request: Request, response: Response) -> db.sqlite3.Row:
    """FastAPI 依赖：校验会话并滑动续期，返回当前用户行。"""
    token = request.cookies.get(config.SESSION_COOKIE, "")
    data = _parse_session(token) if token else None
    if not data:
        raise HTTPException(401, "未登录或会话已过期")
    user = db.query_one("SELECT * FROM sys_user WHERE id=?", (data["uid"],))
    if not user or user["status"] != "active":
        raise HTTPException(401, "账号不存在或已停用")
    if user["session_revoked_at"]:
        # 会话签发时间早于强制下线时间 → 失效
        iat = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(data["iat"]))
        if iat <= user["session_revoked_at"]:
            raise HTTPException(401, "会话已被管理员强制下线")
    issue_session(response, user["id"])  # 滑动续期
    return user


def require(*roles: str):
    """角色鉴权依赖工厂：require('admin') / require('admin','manager')。"""
    def dep(user=Depends(get_current_user)):
        if user["role_code"] not in roles:
            raise HTTPException(403, f"需要角色权限：{'/'.join(roles)}")
        return user
    return dep


def find_user_by_identity(unionid: str | None, mobile: str | None):
    """钉钉身份匹配：优先 unionId，其次手机号（首次扫码自动绑定）。"""
    if unionid:
        user = db.query_one("SELECT * FROM sys_user WHERE ding_unionid=?", (unionid,))
        if user:
            return user
    if mobile:
        return db.query_one(
            "SELECT * FROM sys_user WHERE mobile=? AND (ding_unionid IS NULL OR ding_unionid='')",
            (mobile,))
    return None
