"""能耗管理路由：能源类型/用能单元配置、台账录入与审核流（FR-3.1~3.3）。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit, auth, db

router = APIRouter(prefix="/api/energy", tags=["能耗管理"])

WRITE_ROLES = ("admin", "manager", "reporter")
AUDIT_ROLES = ("admin", "manager")


# ── 能源类型与用能单元（管理员维护）──

@router.get("/types")
def list_types(user=Depends(auth.get_current_user)):
    return [dict(r) for r in db.query("SELECT * FROM energy_type ORDER BY code")]


class EnergyTypeIn(BaseModel):
    code: str
    name: str
    unit: str
    tce_factor: float = 0
    in_carbon: bool = True
    is_green: bool = False
    map_source_code: str | None = None
    enabled: bool = True


@router.post("/types")
def upsert_type(body: EnergyTypeIn, request: Request,
                user=Depends(auth.require("admin"))):
    now = db.now_iso()
    exists = db.query_one("SELECT code FROM energy_type WHERE code=?", (body.code,))
    if exists:
        db.execute(
            "UPDATE energy_type SET name=?,unit=?,tce_factor=?,in_carbon=?,is_green=?,"
            "map_source_code=?,enabled=?,updated_at=?,updated_by=? WHERE code=?",
            (body.name, body.unit, body.tce_factor, int(body.in_carbon), int(body.is_green),
             body.map_source_code, int(body.enabled), now, user["name"], body.code))
        action = "update"
    else:
        db.execute(
            "INSERT INTO energy_type(code,name,unit,tce_factor,in_carbon,is_green,"
            "map_source_code,enabled,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (body.code, body.name, body.unit, body.tce_factor, int(body.in_carbon),
             int(body.is_green), body.map_source_code, int(body.enabled), now, user["name"]))
        action = "create"
    audit.log_op(user, f"energy-type:{action}", "energy_type", body.code,
                 body.model_dump_json(), request)
    return {"ok": True}


@router.get("/units")
def list_units(user=Depends(auth.get_current_user)):
    return [dict(r) for r in db.query("SELECT * FROM energy_unit ORDER BY id")]


class EnergyUnitIn(BaseModel):
    code: str
    name: str
    parent_id: int | None = None
    enabled: bool = True


@router.post("/units")
def upsert_unit(body: EnergyUnitIn, request: Request,
                user=Depends(auth.require("admin"))):
    now = db.now_iso()
    exists = db.query_one("SELECT id FROM energy_unit WHERE code=?", (body.code,))
    if exists:
        db.execute(
            "UPDATE energy_unit SET name=?,parent_id=?,enabled=?,updated_at=?,updated_by=?"
            " WHERE code=?",
            (body.name, body.parent_id, int(body.enabled), now, user["name"], body.code))
    else:
        db.execute(
            "INSERT INTO energy_unit(code,name,parent_id,enabled,created_at,created_by)"
            " VALUES(?,?,?,?,?,?)",
            (body.code, body.name, body.parent_id, int(body.enabled), now, user["name"]))
    audit.log_op(user, "energy-unit:upsert", "energy_unit", body.code,
                 body.model_dump_json(), request)
    return {"ok": True}


# ── 台账 ──

def _record_with_names(r) -> dict:
    d = dict(r)
    t = db.query_one("SELECT name,unit FROM energy_type WHERE code=?", (r["energy_type_code"],))
    u = db.query_one("SELECT name FROM energy_unit WHERE id=?", (r["unit_id"],))
    d["energy_type_name"] = t["name"] if t else r["energy_type_code"]
    d["unit_name"] = u["name"] if u else ""
    return d


@router.get("/records")
def list_records(year: int, month: int | None = None, status: str | None = None,
                 user=Depends(auth.get_current_user)):
    """台账查询：填报员仅见本部门人员录入的数据（评审 A2）。"""
    sql = ("SELECT r.* FROM energy_record r "
           "LEFT JOIN sys_user cu ON cu.id = CAST(r.created_by AS INTEGER) "
           "WHERE r.year=?")
    params: list = [year]
    if month:
        sql += " AND r.month=?"
        params.append(month)
    if status:
        sql += " AND r.status=?"
        params.append(status)
    if user["role_code"] == "reporter":
        sql += " AND (cu.dept_id=? OR r.created_by=?)"
        params += [user["dept_id"], str(user["id"])]
    sql += " ORDER BY r.month, r.energy_type_code"
    return [_record_with_names(r) for r in db.query(sql, tuple(params))]


class RecordIn(BaseModel):
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    energy_type_code: str
    unit_id: int
    quantity: float = Field(ge=0)
    amount: float | None = Field(default=None, ge=0)
    gross_qty: float | None = Field(default=None, ge=0)
    lease_deduct: float | None = Field(default=None, ge=0)
    pv_qty: float | None = Field(default=None, ge=0)
    remark: str | None = Field(default=None, max_length=200)
    overwrite: bool = False  # 冲突时选择覆盖（评审 A1）


def _validate_record(body: RecordIn) -> None:
    if not db.query_one("SELECT 1 FROM energy_type WHERE code=? AND enabled=1",
                        (body.energy_type_code,)):
        raise HTTPException(400, f"能源类型 {body.energy_type_code} 不存在或已停用")
    if not db.query_one("SELECT 1 FROM energy_unit WHERE id=? AND enabled=1",
                        (body.unit_id,)):
        raise HTTPException(400, f"用能单元 {body.unit_id} 不存在或已停用")
    if body.gross_qty is not None:
        net = body.gross_qty - (body.lease_deduct or 0) - (body.pv_qty or 0)
        if net < 0:
            raise HTTPException(400, "净结算电量为负：总表电量不能小于外租+光伏之和")


@router.post("/records")
def create_record(body: RecordIn, request: Request,
                  user=Depends(auth.require(*WRITE_ROLES))):
    """台账录入。唯一约束冲突时：draft/rejected 可覆盖；submitted/approved 拒绝。"""
    _validate_record(body)
    now = db.now_iso()
    existing = db.query_one(
        "SELECT * FROM energy_record WHERE year=? AND month=? AND energy_type_code=? AND unit_id=?",
        (body.year, body.month, body.energy_type_code, body.unit_id))
    if existing:
        if existing["status"] in ("submitted", "approved"):
            raise HTTPException(409, f"该记录已{ '提交审核' if existing['status']=='submitted' else '审核锁定' }，不可覆盖")
        if not body.overwrite:
            raise HTTPException(409, "该年月+能源类型+单元已存在记录，确认覆盖请重试（overwrite=true）")
        db.execute(
            "UPDATE energy_record SET quantity=?,amount=?,gross_qty=?,lease_deduct=?,pv_qty=?,"
            "remark=?,status='draft',reject_reason=NULL,updated_at=?,updated_by=? WHERE id=?",
            (body.quantity, body.amount, body.gross_qty, body.lease_deduct, body.pv_qty,
             body.remark, now, user["name"], existing["id"]))
        rid = existing["id"]
        action = "overwrite"
    else:
        rid = db.execute(
            "INSERT INTO energy_record(year,month,energy_type_code,unit_id,quantity,amount,"
            "gross_qty,lease_deduct,pv_qty,remark,status,created_at,created_by,updated_at,updated_by)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,'draft',?,?,?,?)",
            (body.year, body.month, body.energy_type_code, body.unit_id, body.quantity,
             body.amount, body.gross_qty, body.lease_deduct, body.pv_qty, body.remark,
             now, str(user["id"]), now, user["name"]))
        action = "create"
    audit.log_op(user, f"energy-record:{action}", "energy_record", rid,
                 body.model_dump_json(), request)
    return {"ok": True, "id": rid}


@router.put("/records/{rid}")
def update_record(rid: int, body: RecordIn, request: Request,
                  user=Depends(auth.require(*WRITE_ROLES))):
    r = db.query_one("SELECT * FROM energy_record WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404, "记录不存在")
    if r["status"] == "approved":
        raise HTTPException(403, "记录已审核锁定，如需修改请联系能碳管理员解锁")
    if r["status"] == "submitted":
        raise HTTPException(403, "记录已提交待审核，不可修改")
    _validate_record(body)
    db.execute(
        "UPDATE energy_record SET year=?,month=?,energy_type_code=?,unit_id=?,quantity=?,"
        "amount=?,gross_qty=?,lease_deduct=?,pv_qty=?,remark=?,status='draft',"
        "reject_reason=NULL,updated_at=?,updated_by=? WHERE id=?",
        (body.year, body.month, body.energy_type_code, body.unit_id, body.quantity,
         body.amount, body.gross_qty, body.lease_deduct, body.pv_qty, body.remark,
         db.now_iso(), user["name"], rid))
    audit.log_op(user, "energy-record:update", "energy_record", rid,
                 body.model_dump_json(), request)
    return {"ok": True}


@router.post("/records/{rid}/submit")
def submit_record(rid: int, request: Request,
                  user=Depends(auth.require(*WRITE_ROLES))):
    r = db.query_one("SELECT * FROM energy_record WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404, "记录不存在")
    if r["status"] != "draft" and r["status"] != "rejected":
        raise HTTPException(400, f"当前状态 {r['status']} 不可提交")
    db.execute("UPDATE energy_record SET status='submitted', reject_reason=NULL,"
               "updated_at=?,updated_by=? WHERE id=?",
               (db.now_iso(), user["name"], rid))
    audit.log_op(user, "energy-record:submit", "energy_record", rid, "", request)
    return {"ok": True}


class AuditIn(BaseModel):
    approve: bool
    reject_reason: str | None = None


@router.post("/records/{rid}/audit")
def audit_record(rid: int, body: AuditIn, request: Request,
                 user=Depends(auth.require(*AUDIT_ROLES))):
    r = db.query_one("SELECT * FROM energy_record WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404, "记录不存在")
    if r["status"] != "submitted":
        raise HTTPException(400, "仅待审核（submitted）记录可审核")
    now = db.now_iso()
    if body.approve:
        db.execute("UPDATE energy_record SET status='approved', review_by=?, review_at=?,"
                   "reject_reason=NULL, updated_at=?, updated_by=? WHERE id=?",
                   (user["id"], now, now, user["name"], rid))
        action = "approve"
    else:
        if not body.reject_reason or not body.reject_reason.strip():
            raise HTTPException(400, "驳回必须填写驳回原因")
        db.execute("UPDATE energy_record SET status='rejected', review_by=?, review_at=?,"
                   "reject_reason=?, updated_at=?, updated_by=? WHERE id=?",
                   (user["id"], now, body.reject_reason.strip(), now, user["name"], rid))
        action = "reject"
    audit.log_op(user, f"energy-record:{action}", "energy_record", rid,
                 body.model_dump_json(), request)
    return {"ok": True}


@router.post("/records/{rid}/unlock")
def unlock_record(rid: int, request: Request,
                  user=Depends(auth.require(*AUDIT_ROLES))):
    """解锁已审核记录（留痕，PRD FR-3.3）。"""
    r = db.query_one("SELECT * FROM energy_record WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404, "记录不存在")
    if r["status"] != "approved":
        raise HTTPException(400, "仅已审核（approved）记录可解锁")
    db.execute("UPDATE energy_record SET status='draft', updated_at=?, updated_by=? WHERE id=?",
               (db.now_iso(), user["name"], rid))
    audit.log_op(user, "energy-record:unlock", "energy_record", rid,
                 "审核锁定数据被解锁", request)
    return {"ok": True}
