"""碳排管理路由：科目库、因子库、年度盘查填报与审定（FR-5.1~5.4）。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit, auth, calc, db

router = APIRouter(prefix="/api/carbon", tags=["碳排管理"])

WRITE_ROLES = ("admin", "manager", "reporter")
AUDIT_ROLES = ("admin", "manager")


# ── 科目库（管理员维护，FR-5.1）──

@router.get("/sources")
def list_sources(all: bool = False, user=Depends(auth.get_current_user)):
    sql = "SELECT * FROM emission_source"
    if not all:
        sql += " WHERE enabled=1"
    return [dict(r) for r in db.query(sql + " ORDER BY sort_no")]


class SourceIn(BaseModel):
    code: str
    name_zh: str
    name_en: str | None = None
    scope: str = Field(pattern="^(范围一|范围二|范围三)$")
    source_type: str | None = None
    unit: str
    dept_code: str | None = None
    guide: str | None = None
    factor_ref: str | None = None
    map_energy_type: str | None = None
    map_convert: float = 1.0
    enabled: bool = True
    sort_no: int = 0


@router.post("/sources")
def upsert_source(body: SourceIn, request: Request,
                  user=Depends(auth.require("admin"))):
    now = db.now_iso()
    exists = db.query_one("SELECT code FROM emission_source WHERE code=?", (body.code,))
    if exists:
        # 科目编码一经使用不可修改（FR-5.1），按编码更新其余字段
        db.execute(
            "UPDATE emission_source SET name_zh=?,name_en=?,scope=?,source_type=?,unit=?,"
            "dept_code=?,guide=?,factor_ref=?,map_energy_type=?,map_convert=?,enabled=?,"
            "sort_no=?,updated_at=?,updated_by=? WHERE code=?",
            (body.name_zh, body.name_en, body.scope, body.source_type, body.unit,
             body.dept_code, body.guide, body.factor_ref, body.map_energy_type,
             body.map_convert, int(body.enabled), body.sort_no, now, user["name"], body.code))
        action = "update"
    else:
        db.execute(
            "INSERT INTO emission_source(code,name_zh,name_en,scope,source_type,unit,dept_code,"
            "guide,factor_ref,map_energy_type,map_convert,enabled,sort_no,created_at,created_by)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (body.code, body.name_zh, body.name_en, body.scope, body.source_type, body.unit,
             body.dept_code, body.guide, body.factor_ref, body.map_energy_type,
             body.map_convert, int(body.enabled), body.sort_no, now, user["name"]))
        action = "create"
    audit.log_op(user, f"carbon-source:{action}", "emission_source", body.code,
                 body.model_dump_json(), request)
    return {"ok": True}


# ── 因子库（管理员维护，FR-5.2）──

@router.get("/factors")
def list_factors(source_code: str | None = None, user=Depends(auth.get_current_user)):
    sql = ("SELECT f.*, s.name_zh AS source_name FROM emission_factor f "
           "JOIN emission_source s ON s.code=f.source_code")
    params: tuple = ()
    if source_code:
        sql += " WHERE f.source_code=?"
        params = (source_code,)
    return [dict(r) for r in db.query(sql + " ORDER BY f.source_code, f.year_from", params)]


class FactorIn(BaseModel):
    source_code: str
    factor: float = Field(gt=0)
    year_from: int = Field(ge=2000, le=2100)
    year_to: int | None = Field(default=None, ge=2000, le=2100)
    ref_source: str
    change_reason: str = Field(min_length=1)  # 审计必填（FR-5.2）


def _factor_ranges_overlap(source_code: str, year_from: int, year_to: int | None,
                           exclude_id: int | None = None) -> bool:
    """生效区间重叠校验（模板规范 9.4）。"""
    new_end = year_to if year_to is not None else 9999
    for r in db.query("SELECT id, year_from, year_to FROM emission_factor WHERE source_code=?",
                      (source_code,)):
        if exclude_id and r["id"] == exclude_id:
            continue
        old_end = r["year_to"] if r["year_to"] is not None else 9999
        if year_from <= old_end and r["year_from"] <= new_end:
            return True
    return False


@router.post("/factors")
def add_factor(body: FactorIn, request: Request,
               user=Depends(auth.require("admin"))):
    if not db.query_one("SELECT 1 FROM emission_source WHERE code=?", (body.source_code,)):
        raise HTTPException(404, f"科目 {body.source_code} 不存在")
    if body.year_to is not None and body.year_to < body.year_from:
        raise HTTPException(400, "生效年度止不能小于起")
    if _factor_ranges_overlap(body.source_code, body.year_from, body.year_to):
        raise HTTPException(409, "与已有因子版本的生效年度区间重叠")
    fid = db.execute(
        "INSERT INTO emission_factor(source_code,factor,year_from,year_to,ref_source,"
        "change_reason,created_at,created_by) VALUES(?,?,?,?,?,?,?,?)",
        (body.source_code, body.factor, body.year_from, body.year_to, body.ref_source,
         body.change_reason, db.now_iso(), user["name"]))
    audit.log_op(user, "carbon-factor:add", "emission_factor", fid,
                 body.model_dump_json(), request)
    return {"ok": True, "id": fid}


@router.put("/factors/{fid}")
def update_factor(fid: int, body: FactorIn, request: Request,
                  user=Depends(auth.require("admin"))):
    old = db.query_one("SELECT * FROM emission_factor WHERE id=?", (fid,))
    if not old:
        raise HTTPException(404, "因子版本不存在")
    if body.year_to is not None and body.year_to < body.year_from:
        raise HTTPException(400, "生效年度止不能小于起")
    if _factor_ranges_overlap(body.source_code, body.year_from, body.year_to, exclude_id=fid):
        raise HTTPException(409, "与已有因子版本的生效年度区间重叠")
    # 前后值写日志（审计要求）
    detail = (f"旧: factor={old['factor']} [{old['year_from']}-{old['year_to'] or '长期'}] → "
              f"新: factor={body.factor} [{body.year_from}-{body.year_to or '长期'}]；"
              f"原因: {body.change_reason}")
    db.execute(
        "UPDATE emission_factor SET factor=?,year_from=?,year_to=?,ref_source=?,"
        "change_reason=?,updated_at=?,updated_by=? WHERE id=?",
        (body.factor, body.year_from, body.year_to, body.ref_source,
         body.change_reason, db.now_iso(), user["name"], fid))
    audit.log_op(user, "carbon-factor:update", "emission_factor", fid, detail, request)
    return {"ok": True}


# ── 年度盘查（FR-5.3）──

def _ensure_inventory(year: int, user_name: str) -> None:
    if not db.query_one("SELECT 1 FROM carbon_inventory WHERE year=?", (year,)):
        db.execute("INSERT INTO carbon_inventory(year,status,created_at,created_by)"
                   " VALUES(?,'draft',?,?)", (year, db.now_iso(), user_name))


def _check_inventory_writable(year: int) -> None:
    inv = db.query_one("SELECT status FROM carbon_inventory WHERE year=?", (year,))
    if inv and inv["status"] == "approved":
        raise HTTPException(403, f"{year} 年度盘查已审定锁定，不可修改")
    if inv and inv["status"] == "submitted":
        raise HTTPException(403, f"{year} 年度盘查已提交待审定，不可修改")


@router.get("/activities")
def get_activities(year: int, user=Depends(auth.get_current_user)):
    """年度活动数据 + 实时核算结果 + 台账映射参考值。"""
    result = calc.compute_year(year)
    result["mapped"] = calc.ledger_mapped_values(year)
    result["net_electricity"] = calc.net_electricity(year)
    return result


class ActivityItem(BaseModel):
    source_code: str
    activity_value: float = Field(ge=0)
    remark: str | None = Field(default=None, max_length=200)


class ActivitiesIn(BaseModel):
    year: int = Field(ge=2000, le=2100)
    items: list[ActivityItem]
    total_revenue: float | None = Field(default=None, ge=0)  # 年度总产值（万美金）
    data_origin: str = Field(default="manual", pattern="^(manual|import|mapped)$")


@router.post("/activities")
def save_activities(body: ActivitiesIn, request: Request,
                    user=Depends(auth.require(*WRITE_ROLES))):
    """保存年度填报（填报员限本科目所属部门，评审 A2）。"""
    _check_inventory_writable(body.year)
    _ensure_inventory(body.year, user["name"])
    now = db.now_iso()
    saved = 0
    for item in body.items:
        src = db.query_one("SELECT * FROM emission_source WHERE code=? AND enabled=1",
                           (item.source_code,))
        if not src:
            raise HTTPException(400, f"科目 {item.source_code} 不存在或已停用")
        if user["role_code"] == "reporter":
            dept = db.query_one("SELECT code FROM sys_dept WHERE id=?", (user["dept_id"],))
            if src["dept_code"] and (not dept or dept["code"] != src["dept_code"]):
                raise HTTPException(403, f"科目「{src['name_zh']}」归 {src['dept_code']} 部门填报，越权")
        existing = db.query_one(
            "SELECT id FROM carbon_activity WHERE year=? AND source_code=?",
            (body.year, item.source_code))
        if existing:
            db.execute(
                "UPDATE carbon_activity SET activity_value=?, data_origin=?, remark=?,"
                "reported_by=?, reported_at=?, updated_at=?, updated_by=? WHERE id=?",
                (item.activity_value, body.data_origin, item.remark, user["id"], now,
                 now, user["name"], existing["id"]))
        else:
            db.execute(
                "INSERT INTO carbon_activity(year,source_code,activity_value,data_origin,"
                "remark,reported_by,reported_at,created_at,created_by)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (body.year, item.source_code, item.activity_value, body.data_origin,
                 item.remark, user["id"], now, now, user["name"]))
        saved += 1
    if body.total_revenue is not None:
        if db.query_one("SELECT 1 FROM finance_metric WHERE year=?", (body.year,)):
            db.execute("UPDATE finance_metric SET total_revenue=?, updated_at=?, updated_by=?"
                       " WHERE year=?", (body.total_revenue, now, user["name"], body.year))
        else:
            db.execute("INSERT INTO finance_metric(year,total_revenue,created_at,created_by)"
                       " VALUES(?,?,?,?)", (body.year, body.total_revenue, now, user["name"]))
    audit.log_op(user, "carbon-activity:save", "carbon_activity", body.year,
                 f"保存 {saved} 条活动数据（{body.data_origin}）", request)
    return {"ok": True, "saved": saved, "result": calc.compute_year(body.year)}


@router.post("/inventory/{year}/submit")
def submit_inventory(year: int, request: Request,
                     user=Depends(auth.require(*WRITE_ROLES))):
    inv = db.query_one("SELECT * FROM carbon_inventory WHERE year=?", (year,))
    if not inv:
        raise HTTPException(404, f"{year} 年度盘查不存在")
    if inv["status"] != "draft":
        raise HTTPException(400, f"当前状态 {inv['status']} 不可提交")
    db.execute("UPDATE carbon_inventory SET status='submitted', submitted_by=?,"
               "submitted_at=?, updated_at=?, updated_by=? WHERE year=?",
               (user["id"], db.now_iso(), db.now_iso(), user["name"], year))
    audit.log_op(user, "carbon-inventory:submit", "carbon_inventory", year, "", request)
    return {"ok": True}


@router.post("/inventory/{year}/approve")
def approve_inventory(year: int, request: Request,
                      user=Depends(auth.require(*AUDIT_ROLES))):
    """审定年度盘查：封存因子快照与排放量（评审 M4），全年度数据锁定。"""
    inv = db.query_one("SELECT * FROM carbon_inventory WHERE year=?", (year,))
    if not inv:
        raise HTTPException(404, f"{year} 年度盘查不存在")
    if inv["status"] != "submitted":
        raise HTTPException(400, "仅已提交（submitted）的盘查可审定")
    now = db.now_iso()
    result = calc.compute_year(year)
    for d in result["details"]:
        db.execute(
            "INSERT INTO carbon_activity(year,source_code,activity_value,data_origin,"
            "factor_snapshot,emission,reported_at,created_at,created_by)"
            " VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(year,source_code) DO UPDATE SET factor_snapshot=excluded.factor_snapshot,"
            "emission=excluded.emission, updated_at=excluded.updated_at",
            (year, d["source_code"], d["activity_value"], d["data_origin"],
             d["factor"], d["emission"], now, now, user["name"]))
    db.execute("UPDATE carbon_inventory SET status='approved', approved_by=?, approved_at=?,"
               "updated_at=?, updated_by=? WHERE year=?",
               (user["id"], now, now, user["name"], year))
    audit.log_op(user, "carbon-inventory:approve", "carbon_inventory", year,
                 f"审定总排放 {result['total']:.4f} tCO2e", request)
    return {"ok": True, "total": result["total"]}


@router.get("/inventory")
def list_inventory(user=Depends(auth.get_current_user)):
    return [dict(r) for r in db.query("SELECT * FROM carbon_inventory ORDER BY year")]


@router.get("/trace")
def trace(year: int, source_code: str, user=Depends(auth.get_current_user)):
    """单项溯源（FR-5.4）：活动值、来源、因子版本、填报人、审核人、时间。"""
    src = db.query_one("SELECT * FROM emission_source WHERE code=?", (source_code,))
    if not src:
        raise HTTPException(404, "科目不存在")
    act = db.query_one("SELECT * FROM carbon_activity WHERE year=? AND source_code=?",
                       (year, source_code))
    inv = db.query_one("SELECT * FROM carbon_inventory WHERE year=?", (year,))
    factor_row = db.query_one(
        "SELECT * FROM emission_factor WHERE source_code=? AND year_from<=? "
        "AND (year_to IS NULL OR year_to>=?) ORDER BY year_from DESC LIMIT 1",
        (source_code, year, year))
    reporter = reviewer = None
    if act and act["reported_by"]:
        u = db.query_one("SELECT name FROM sys_user WHERE id=?", (act["reported_by"],))
        reporter = u["name"] if u else None
    if inv and inv["approved_by"]:
        u = db.query_one("SELECT name FROM sys_user WHERE id=?", (inv["approved_by"],))
        reviewer = u["name"] if u else None
    return {
        "year": year, "source": dict(src),
        "activity": dict(act) if act else None,
        "factor_version": dict(factor_row) if factor_row else None,
        "inventory": dict(inv) if inv else None,
        "reporter_name": reporter, "approver_name": reviewer,
    }
