"""分析路由：工作台总览（FR-2.1）、能耗分析（FR-3.4）、碳排分析（FR-6.1）。"""
from fastapi import APIRouter, Depends

from .. import auth, calc, config, db
from .carbon import trace as _carbon_trace

router = APIRouter(prefix="/api/analysis", tags=["分析"])


# 溯源接口对外挂在 /api/analysis/trace（与架构文档一致），实现复用碳排模块
router.add_api_route("/trace", _carbon_trace, methods=["GET"])


@router.get("/dashboard")
def dashboard(year: int, user=Depends(auth.get_current_user)):
    """工作台：KPI + 近 12 月能耗趋势 + 近 5 年碳排 + 待办。"""
    energy = calc.energy_year_summary(year)
    carbon = calc.compute_year(year)
    target = db.query_one("SELECT * FROM carbon_target WHERE id=1")

    # 近 12 个月能耗/电费迷你趋势
    monthly = db.query(
        "SELECT month, SUM(quantity) AS qty, SUM(amount) AS amt FROM energy_record "
        "WHERE year=? AND status='approved' GROUP BY month ORDER BY month", (year,))

    # 近 5 年碳排趋势
    trend = calc.year_summary([y for y in range(year - 4, year + 1)])

    # 待办：待审核条数（manager/admin）、本月未提交草稿（reporter）、最近导入失败
    todos = []
    if user["role_code"] in ("admin", "manager"):
        n = db.query_one("SELECT COUNT(*) AS c FROM energy_record WHERE status='submitted'")["c"]
        if n:
            todos.append({"type": "audit", "text": f"{n} 条能耗台账待审核"})
    drafts = db.query_one(
        "SELECT COUNT(*) AS c FROM energy_record WHERE status IN ('draft','rejected')"
        " AND created_by=?", (str(user["id"]),))["c"]
    if drafts:
        todos.append({"type": "draft", "text": f"你有 {drafts} 条台账草稿/被驳回数据待处理"})
    failed = db.query_one(
        "SELECT file_name, failed FROM import_task WHERE failed>0 ORDER BY id DESC LIMIT 1")
    if failed:
        todos.append({"type": "import", "text": f"最近导入任务 {failed['file_name']} 有 {failed['failed']} 条错误"})

    # 碳中和目标完成率
    target_rate = None
    if target and target["base_emission"] and target["total_reduction"]:
        reduced = target["base_emission"] - carbon["total"]
        target_rate = round(reduced / target["total_reduction"] * 100, 2)

    return {
        "year": year,
        "kpi": {
            "energy_tce": energy["total_tce"],
            "energy_cost": energy["total_cost"],
            "carbon_total": carbon["total"],
            "intensity": carbon["intensity"],
            "target_rate": target_rate,
            "inventory_status": carbon["inventory_status"],
        },
        "energy_monthly": [dict(r) for r in monthly],
        "carbon_trend": trend,
        "todos": todos,
    }


@router.get("/energy")
def energy_analysis(year: int, user=Depends(auth.get_current_user)):
    """能耗分析：月度趋势、结构占比(tce)、强度、同比（异常波动 ±20% 标记）。"""
    cur = calc.energy_year_summary(year)
    prev = calc.energy_year_summary(year - 1)
    monthly = db.query(
        "SELECT month, energy_type_code, SUM(quantity) AS qty, SUM(amount) AS amt "
        "FROM energy_record WHERE year=? AND status='approved' "
        "GROUP BY month, energy_type_code ORDER BY month", (year,))
    fin = db.query_one("SELECT total_revenue FROM finance_metric WHERE year=?", (year,))
    revenue = fin["total_revenue"] if fin else 0.0

    def yoy(cur_v: float, prev_v: float):
        if prev_v <= 0:
            return None
        rate = round((cur_v - prev_v) / prev_v * 100, 2)
        return {"rate": rate, "abnormal": abs(rate) >= 20}

    return {
        "year": year,
        "summary": cur,
        "monthly": [dict(r) for r in monthly],
        "intensity_tce": round(cur["total_tce"] / revenue, 6) if revenue else None,
        "cost_intensity": round(cur["total_cost"] / revenue, 2) if revenue else None,
        "yoy_tce": yoy(cur["total_tce"], prev["total_tce"]),
        "yoy_cost": yoy(cur["total_cost"], prev["total_cost"]),
    }


def _build_pareto(details: list[dict], total: float) -> list[dict]:
    """帕累托热点：按排放量降序，输出占比与累计占比（识别"关键的少数"）。"""
    out, cum = [], 0.0
    for d in sorted((x for x in details if x["emission"] > 0),
                    key=lambda x: -x["emission"]):
        share = d["emission"] / total if total > 0 else 0.0
        cum += share
        out.append({"source_code": d["source_code"], "name_zh": d["name_zh"],
                    "scope": d["scope"], "emission": d["emission"],
                    "share": round(share * 100, 2), "cumulative": round(cum * 100, 2)})
    return out


def _build_insights(year: int, cur: dict, prev: dict | None, has_data: bool,
                    baseline: dict | None, monthly: list[dict]) -> list[dict]:
    """规则引擎式分析洞察（对齐成熟双碳 SaaS 的"诊断建议"）。"""
    tips: list[dict] = []

    if not has_data:
        if any(m["total"] > 0 for m in monthly):
            tips.append({"level": "info",
                         "text": f"{year} 年度盘查尚未填报活动数据，"
                                 "以下月度趋势为基于已审核台账的实时估算值。"})
        else:
            tips.append({"level": "warn",
                         "text": f"{year} 年暂无盘查数据，也无可映射的已审核台账，"
                                 "请先录入能耗台账或填报年度盘查。"})
        return tips

    # 1. 最大排放源
    if cur["total"] > 0:
        top = max(cur["details"], key=lambda d: d["emission"])
        if top["emission"] > 0:
            tips.append({"level": "info",
                         "text": f"最大排放源为「{top['name_zh']}」，排放 "
                                 f"{top['emission']:.2f} tCO₂e，占总量 "
                                 f"{top['emission'] / cur['total'] * 100:.1f}%。"})

    # 2. 同比主因
    if prev and prev["total"] > 0:
        rate = (cur["total"] - prev["total"]) / prev["total"] * 100
        prev_map = {d["source_code"]: d["emission"] for d in prev["details"]}
        driver = max(cur["details"], key=lambda d: d["emission"] - prev_map.get(d["source_code"], 0.0))
        level = "warn" if rate > 0 else "info"
        tips.append({"level": level,
                     "text": f"同比 {'上升' if rate >= 0 else '下降'} {abs(rate):.1f}%，"
                             f"主要驱动科目为「{driver['name_zh']}」"
                             f"（变动 {driver['emission'] - prev_map.get(driver['source_code'], 0.0):+.2f} tCO₂e）。"})

    # 3. 距基准年降幅
    if baseline:
        rate = baseline["rate"]
        tips.append({"level": "info" if rate >= 0 else "warn",
                     "text": f"相对 {baseline['year']} 基准年（{baseline['total']:.2f} tCO₂e）"
                             f"{'下降' if rate >= 0 else '上升'} {abs(rate):.1f}%。"})

    # 4. 绿电占比
    green = cur["green"]["pv_reduction"]
    denom = cur["total"] + green
    if denom > 0:
        ratio = green / denom * 100
        if ratio < 5:
            tips.append({"level": "warn",
                         "text": f"光伏绿电减碳贡献率仅 {ratio:.1f}%，"
                                 "建议评估屋顶光伏扩容或绿电采购，进一步降低范围二排放。"})
        else:
            tips.append({"level": "info",
                         "text": f"光伏绿电当年减碳 {green:.2f} tCO₂e，贡献率 {ratio:.1f}%。"})

    # 5. 未填报科目提醒
    pending = [d["name_zh"] for d in cur["details"] if d["pending"]]
    if pending:
        tips.append({"level": "warn",
                     "text": f"以下 {len(pending)} 个科目尚未填报活动数据："
                             f"{'、'.join(pending[:3])}{' 等' if len(pending) > 3 else ''}。"})
    return tips


@router.get("/carbon")
def carbon_analysis(year: int, user=Depends(auth.get_current_user)):
    """碳排分析：概览卡、范围明细、占比、同比、多年度趋势（对齐能耗宝）。

    增强（对齐成熟双碳 SaaS）：月度台账估算趋势（含去年同比）、帕累托热点、
    分单元电力结构、基准年对比、目标进度、规则引擎洞察。
    """
    cur = calc.compute_year(year)
    prev_inv = db.query_one("SELECT status FROM carbon_inventory WHERE year=?", (year - 1,))
    prev = calc.compute_year(year - 1) if prev_inv else None
    yoy = None
    if prev and prev["total"] > 0:
        yoy = {"total": prev["total"],
               "rate": round((cur["total"] - prev["total"]) / prev["total"] * 100, 2)}
    trend = calc.year_summary()

    has_data = any(not d["pending"] for d in cur["details"])

    # 月度估算（基于已审核台账，不依赖盘查填报）与去年同期
    monthly = calc.monthly_carbon_estimate(year)
    monthly_prev = calc.monthly_carbon_estimate(year - 1)
    if not any(m["total"] > 0 for m in monthly_prev):
        monthly_prev = None

    # 基准年对比（仅当年有数据且非基准年本身时输出）
    baseline = None
    if has_data and year != config.BASE_YEAR:
        base = calc.compute_year(config.BASE_YEAR)
        if base["total"] > 0:
            baseline = {"year": config.BASE_YEAR, "total": base["total"],
                        "reduction": round(base["total"] - cur["total"], 6),
                        "rate": round((base["total"] - cur["total"]) / base["total"] * 100, 2)}

    # 目标进度：碳中和总目标 + 当年年度计划
    target = None
    tgt = db.query_one("SELECT * FROM carbon_target WHERE id=1")
    plan = db.query_one("SELECT * FROM annual_plan WHERE year=?", (year,))
    if tgt and tgt["base_emission"] and tgt["total_reduction"]:
        progress = (round((tgt["base_emission"] - cur["total"]) / tgt["total_reduction"] * 100, 2)
                    if has_data else None)
        target = {"target_year": tgt["target_year"], "base_year": tgt["base_year"],
                  "base_emission": tgt["base_emission"],
                  "total_reduction": tgt["total_reduction"], "progress_rate": progress}
    if plan and plan["carbon_goal"]:
        target = dict(target or {})
        target["carbon_goal"] = plan["carbon_goal"]
        target["goal_gap"] = round(cur["total"] - plan["carbon_goal"], 6) if has_data else None

    return {
        "year": year,
        "overview": {
            "total": cur["total"], "groups": cur["groups"],
            "green_reduction": cur["green"]["pv_reduction"],
            "pv_quantity": cur["green"]["pv_quantity"],
            "intensity": cur["intensity"], "total_revenue": cur["total_revenue"],
            "yoy": yoy,
        },
        "details": cur["details"],
        "warnings": cur["warnings"],
        "trend": trend,
        "has_data": has_data,
        "monthly_estimate": monthly,
        "monthly_estimate_prev": monthly_prev,
        "pareto": _build_pareto(cur["details"], cur["total"]),
        "unit_breakdown": calc.unit_carbon_breakdown(year),
        "baseline": baseline,
        "target": target,
        "insights": _build_insights(year, cur, prev, has_data, baseline, monthly),
    }
