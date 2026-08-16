"""操作日志与登录日志（PRD FR-10.3，保留 3 年）。"""
from fastapi import Request

from . import db


def _client_ip(request: Request | None) -> str:
    if request is None or request.client is None:
        return ""
    return request.client.host


def log_op(user, action: str, object_type: str, object_id: str = "",
           detail: str = "", request: Request | None = None) -> None:
    """记录关键操作：人、动作、对象、前后值摘要、IP、时间。"""
    db.execute(
        "INSERT INTO op_log(user_id,user_name,action,object_type,object_id,detail,ip,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (user["id"] if user else None, user["name"] if user else "system",
         action, object_type, str(object_id), detail[:500],
         _client_ip(request), db.now_iso()))


def log_login(unionid: str, user_name: str, action: str, result: str,
              request: Request | None = None) -> None:
    ua = request.headers.get("user-agent", "")[:200] if request else ""
    db.execute(
        "INSERT INTO login_log(unionid,user_name,ip,ua,action,result,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (unionid, user_name, _client_ip(request), ua, action, result, db.now_iso()))
