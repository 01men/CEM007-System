"""供应链碳分摊路由：客户档案（FR-8.1）与分摊账单（FR-8.2）。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit, auth, calc, db

router = APIRouter(prefix="/api", tags=["供应链分摊"])

WRITE_ROLES = ("admin", "manager")


@router.get("/customers")
def list_customers(user=Depends(auth.get_current_user)):
    customers = [dict(r) for r in db.query("SELECT * FROM customer ORDER BY code")]
    for c in customers:
        c["revenues"] = [dict(r) for r in db.query(
            "SELECT year, revenue FROM customer_revenue WHERE customer_code=? ORDER BY year",
            (c["code"],))]
    return customers


class CustomerIn(BaseModel):
    code: str
    name_zh: str
    name_en: str | None = None
    contact: str | None = None
    enabled: bool = True


@router.post("/customers")
def upsert_customer(body: CustomerIn, request: Request,
                    user=Depends(auth.require(*WRITE_ROLES))):
    now = db.now_iso()
    if db.query_one("SELECT 1 FROM customer WHERE code=?", (body.code,)):
        db.execute("UPDATE customer SET name_zh=?,name_en=?,contact=?,enabled=?,"
                   "updated_at=?,updated_by=? WHERE code=?",
                   (body.name_zh, body.name_en, body.contact, int(body.enabled),
                    now, user["name"], body.code))
        action = "update"
    else:
        db.execute("INSERT INTO customer(code,name_zh,name_en,contact,enabled,created_at,created_by)"
                   " VALUES(?,?,?,?,?,?,?)",
                   (body.code, body.name_zh, body.name_en, body.contact,
                    int(body.enabled), now, user["name"]))
        action = "create"
    audit.log_op(user, f"customer:{action}", "customer", body.code,
                 body.model_dump_json(), request)
    return {"ok": True}


class RevenueIn(BaseModel):
    year: int = Field(ge=2000, le=2100)
    customer_code: str
    revenue: float = Field(ge=0)  # 万美金


@router.post("/customers/revenue")
def set_revenue(body: RevenueIn, request: Request,
                user=Depends(auth.require(*WRITE_ROLES))):
    if not db.query_one("SELECT 1 FROM customer WHERE code=?", (body.customer_code,)):
        raise HTTPException(404, f"客户 {body.customer_code} 不存在")
    now = db.now_iso()
    db.execute(
        "INSERT INTO customer_revenue(year,customer_code,revenue,created_at,created_by)"
        " VALUES(?,?,?,?,?) "
        "ON CONFLICT(year,customer_code) DO UPDATE SET revenue=excluded.revenue,"
        "updated_at=excluded.created_at, updated_by=excluded.created_by",
        (body.year, body.customer_code, body.revenue, now, user["name"]))
    audit.log_op(user, "customer:revenue-set", "customer_revenue",
                 f"{body.year}/{body.customer_code}", body.model_dump_json(), request)
    return {"ok": True}


@router.get("/allocation")
def allocation(year: int, user=Depends(auth.get_current_user)):
    """分摊账单（仅已审定年度；占比合计>100% 返回 warning，评审 A3）。"""
    try:
        return calc.compute_allocation(year)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
