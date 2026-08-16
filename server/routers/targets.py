"""目标管理路由：碳中和目标（FR-7.1）与年度计划（FR-7.2）。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit, auth, calc, db

router = APIRouter(prefix="/api/targets", tags=["目标管理"])

WRITE_ROLES = ("admin", "manager")


@router.get("/carbon")
def get_carbon_target(user=Depends(auth.get_current_user)):
    row = db.query_one("SELECT * FROM carbon_target WHERE id=1")
    if not row:
        return {"target": None, "trend": [], "progress": None}
    target = dict(row)
    base_emission = target["base_emission"] or 0
    # 目标趋势线：基准年 → 目标年 线性减排计划值 vs 实际值（已审定年度）
    trend = []
    span = max((target["target_year"] or 0) - (target["base_year"] or 0), 1)
    actual = {r["year"]: r for r in calc.year_summary()}
    cur_emission = None
    for y in range(target["base_year"], target["target_year"] + 1):
        planned = round(base_emission - target["total_reduction"] * (y - target["base_year"]) / span, 4)
        act = actual.get(y)
        actual_v = act["total"] if act and act["status"] == "approved" else None
        if actual_v is not None:
            cur_emission = actual_v
        if y <= (target["base_year"] or 0) + 40:  # 防止异常目标年产生过长序列
            trend.append({"year": y, "planned": planned, "actual": actual_v})
    progress = None
    if cur_emission is not None and target["total_reduction"]:
        progress = round((base_emission - cur_emission) / target["total_reduction"] * 100, 2)
    return {"target": target, "trend": trend, "progress": progress,
            "current_emission": cur_emission}


class CarbonTargetIn(BaseModel):
    target_year: int = Field(ge=2024, le=2200)
    base_year: int = Field(ge=2000, le=2100)
    total_reduction: float = Field(gt=0)


@router.post("/carbon")
def set_carbon_target(body: CarbonTargetIn, request: Request,
                      user=Depends(auth.require(*WRITE_ROLES))):
    if body.target_year <= body.base_year:
        raise HTTPException(400, "目标年份必须晚于基准年")
    base = calc.compute_year(body.base_year)
    now = db.now_iso()
    db.execute(
        "INSERT INTO carbon_target(id,target_year,base_year,base_emission,total_reduction,"
        "created_at,created_by) VALUES(1,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET target_year=excluded.target_year,"
        "base_year=excluded.base_year, base_emission=excluded.base_emission,"
        "total_reduction=excluded.total_reduction, updated_at=excluded.created_at,"
        "updated_by=excluded.created_by",
        (body.target_year, body.base_year, base["total"], body.total_reduction, now, user["name"]))
    audit.log_op(user, "target:carbon-set", "carbon_target", "1",
                 body.model_dump_json(), request)
    return {"ok": True, "base_emission": base["total"]}


@router.get("/annual")
def get_annual_plan(year: int, user=Depends(auth.get_current_user)):
    plan = db.query_one("SELECT * FROM annual_plan WHERE year=?", (year,))
    actual_carbon = calc.compute_year(year)["total"]
    actual_energy = calc.energy_year_summary(year)
    result = {"year": year, "plan": dict(plan) if plan else None,
              "actual": {"carbon": actual_carbon, "energy_tce": actual_energy["total_tce"],
                         "cost": actual_energy["total_cost"]}}
    # 实际值超目标 90% 预警（FR-7.2）
    if plan:
        warns = []
        for key, goal, actual in (("carbon_goal", plan["carbon_goal"], actual_carbon),
                                  ("energy_goal_tce", plan["energy_goal_tce"], actual_energy["total_tce"]),
                                  ("cost_budget", plan["cost_budget"], actual_energy["total_cost"])):
            if goal and goal > 0 and actual / goal >= 0.9:
                warns.append({"item": key, "rate": round(actual / goal * 100, 1)})
        result["warnings"] = warns
    return result


class AnnualPlanIn(BaseModel):
    year: int = Field(ge=2000, le=2100)
    carbon_goal: float | None = Field(default=None, ge=0)
    energy_goal_tce: float | None = Field(default=None, ge=0)
    cost_budget: float | None = Field(default=None, ge=0)


@router.post("/annual")
def set_annual_plan(body: AnnualPlanIn, request: Request,
                    user=Depends(auth.require(*WRITE_ROLES))):
    now = db.now_iso()
    db.execute(
        "INSERT INTO annual_plan(year,carbon_goal,energy_goal_tce,cost_budget,created_at,created_by)"
        " VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(year) DO UPDATE SET carbon_goal=excluded.carbon_goal,"
        "energy_goal_tce=excluded.energy_goal_tce, cost_budget=excluded.cost_budget,"
        "updated_at=excluded.created_at, updated_by=excluded.created_by",
        (body.year, body.carbon_goal, body.energy_goal_tce, body.cost_budget, now, user["name"]))
    audit.log_op(user, "target:annual-set", "annual_plan", body.year,
                 body.model_dump_json(), request)
    return {"ok": True}
