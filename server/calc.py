"""碳核算引擎：排放计算、范围汇总、台账映射、绿电扣减、供应链分摊。

口径决策（评审 L1/L2/L4）：
- 排放量 = 活动数据 × 当期排放因子；审定后使用封存快照，保证历史可复现。
- 台账映射：科目活动值 = Σ(已审核台账用量) × map_convert（如台账与科目单位不同时换算）。
- 绿电只扣一次：电力净量 = 总表 − 外租 − 光伏，发生在台账层；光伏减碳量单独展示。
"""
from . import db

SCOPES = ("范围一", "范围二", "范围三")


def get_factor(source_code: str, year: int) -> tuple[float, str | None]:
    """取覆盖盘查年度的因子；缺失时回退最近版本并返回提示（评审 M4）。"""
    row = db.query_one(
        "SELECT factor FROM emission_factor WHERE source_code=? "
        "AND year_from<=? AND (year_to IS NULL OR year_to>=?) "
        "ORDER BY year_from DESC LIMIT 1", (source_code, year, year))
    if row:
        return row["factor"], None
    row = db.query_one(
        "SELECT factor, year_from FROM emission_factor WHERE source_code=? "
        "AND year_from<=? ORDER BY year_from DESC LIMIT 1", (source_code, year))
    if row:
        return row["factor"], f"因子取自 {row['year_from']} 版本（{year} 年无专属版本）"
    row = db.query_one(
        "SELECT factor, year_from FROM emission_factor WHERE source_code=? "
        "ORDER BY year_from ASC LIMIT 1", (source_code,))
    if row:
        return row["factor"], f"因子取自最早 {row['year_from']} 版本（{year} 年无专属版本）"
    raise ValueError(f"科目 {source_code} 未配置任何排放因子")


def ledger_mapped_values(year: int) -> dict[str, float]:
    """由能耗台账（已审核）映射出的科目活动值：{source_code: value}。"""
    rows = db.query(
        "SELECT s.code AS source_code, s.map_convert, SUM(r.quantity) AS qty "
        "FROM emission_source s "
        "JOIN energy_record r ON r.energy_type_code = s.map_energy_type AND r.year=? "
        "WHERE s.map_energy_type IS NOT NULL AND s.enabled=1 AND r.status='approved' "
        "GROUP BY s.code", (year,))
    return {r["source_code"]: round((r["qty"] or 0) * r["map_convert"], 6) for r in rows}


def pv_green_deduction(year: int) -> dict:
    """光伏绿电：年度自用电量与对应减碳量（= 光伏电量 × 电力因子）。"""
    row = db.query_one(
        "SELECT SUM(quantity) AS qty FROM energy_record "
        "WHERE year=? AND energy_type_code='PV' AND status='approved'", (year,))
    pv_qty = (row["qty"] or 0) if row else 0
    elec = db.query_one("SELECT code FROM emission_source WHERE scope='范围二' AND enabled=1 LIMIT 1")
    reduction = 0.0
    if elec and pv_qty:
        factor, _ = get_factor(elec["code"], year)
        reduction = round(pv_qty * factor, 6)
    return {"pv_quantity": pv_qty, "pv_reduction": reduction}


def net_electricity(year: int) -> float:
    """净结算电量（已审核台账）：优先使用拆分列（总表−外租−光伏），否则用 quantity。"""
    rows = db.query(
        "SELECT quantity, gross_qty, lease_deduct, pv_qty FROM energy_record "
        "WHERE year=? AND energy_type_code='ELEC' AND status='approved'", (year,))
    total = 0.0
    for r in rows:
        if r["gross_qty"] is not None:
            total += (r["gross_qty"] or 0) - (r["lease_deduct"] or 0) - (r["pv_qty"] or 0)
        else:
            total += r["quantity"] or 0
    return round(total, 6)


def compute_year(year: int) -> dict:
    """年度盘查核算结果。

    已审定年度使用封存快照；未审定年度按当前因子实时核算。
    返回：明细列表（含因子/排放/来源）、范围小计、总量、强度、绿电、警告列表。
    """
    inv = db.query_one("SELECT * FROM carbon_inventory WHERE year=?", (year,))
    approved = bool(inv and inv["status"] == "approved")

    sources = db.query(
        "SELECT * FROM emission_source WHERE enabled=1 ORDER BY sort_no")
    acts = {r["source_code"]: r for r in db.query(
        "SELECT * FROM carbon_activity WHERE year=?", (year,))}
    fin = db.query_one("SELECT total_revenue FROM finance_metric WHERE year=?", (year,))
    total_revenue = fin["total_revenue"] if fin else 0.0

    details, warnings = [], []
    groups = {s: 0.0 for s in SCOPES}
    total = 0.0
    for s in sources:
        act = acts.get(s["code"])
        value = act["activity_value"] if act else 0.0
        origin = act["data_origin"] if act else "manual"
        if approved and act and act["factor_snapshot"] is not None:
            factor, emission = act["factor_snapshot"], act["emission"] or 0.0
            factor_note = None
        else:
            factor, factor_note = get_factor(s["code"], year)
            emission = round(value * factor, 6)
            if factor_note:
                warnings.append(f"{s['name_zh']}：{factor_note}")
        groups[s["scope"]] += emission
        total += emission
        details.append({
            "source_code": s["code"], "name_zh": s["name_zh"], "name_en": s["name_en"],
            "scope": s["scope"], "source_type": s["source_type"], "unit": s["unit"],
            "dept_code": s["dept_code"], "guide": s["guide"], "factor_ref": s["factor_ref"],
            "activity_value": value, "factor": factor, "emission": round(emission, 6),
            "data_origin": origin,
            "reported_by": act["reported_by"] if act else None,
            "reported_at": act["reported_at"] if act else None,
            "pending": act is None,
        })

    total = round(total, 6)
    intensity = round(total / total_revenue, 6) if total_revenue > 0 else 0.0
    return {
        "year": year,
        "inventory_status": inv["status"] if inv else "none",
        "details": details,
        "groups": {k: round(v, 6) for k, v in groups.items()},
        "total": total,
        "total_revenue": total_revenue,
        "intensity": intensity,
        "green": pv_green_deduction(year),
        "warnings": warnings,
    }


def compute_allocation(year: int) -> dict:
    """供应链碳分摊：客户分摊 = 企业总排放 × 客户营收 / 总产值（取已审定年度）。"""
    inv = db.query_one("SELECT status FROM carbon_inventory WHERE year=?", (year,))
    if not inv or inv["status"] != "approved":
        raise ValueError(f"{year} 年度盘查未审定，不能生成分摊账单")
    result = compute_year(year)
    total, revenue = result["total"], result["total_revenue"]
    rows = db.query(
        "SELECT c.code, c.name_zh, c.name_en, r.revenue FROM customer_revenue r "
        "JOIN customer c ON c.code=r.customer_code AND c.enabled=1 "
        "WHERE r.year=? ORDER BY r.revenue DESC", (year,))
    items, share_sum = [], 0.0
    for r in rows:
        share = (r["revenue"] / revenue) if revenue > 0 else 0.0
        share_sum += share
        items.append({
            "customer_code": r["code"], "name_zh": r["name_zh"], "name_en": r["name_en"],
            "revenue": r["revenue"], "share": round(share, 6),
            "allocated": round(total * share, 6),
            "intensity": round((total * share) / r["revenue"], 6) if r["revenue"] > 0 else 0.0,
        })
    warning = None
    if share_sum > 1.0:
        warning = f"客户营收合计占总产值 {share_sum * 100:.2f}%，超过 100%，请核查营收数据"
    return {
        "year": year, "total_emission": total, "total_revenue": revenue,
        "groups": result["groups"], "items": items,
        "share_sum": round(share_sum, 6), "warning": warning,
    }


def year_summary(years: list[int] | None = None) -> list[dict]:
    """多年度总量/范围/强度汇总（趋势图用）。"""
    if years is None:
        years = [r["year"] for r in db.query(
            "SELECT DISTINCT year FROM carbon_activity ORDER BY year")]
    out = []
    for y in sorted(years):
        r = compute_year(y)
        out.append({"year": y, "total": r["total"], "groups": r["groups"],
                    "intensity": r["intensity"], "status": r["inventory_status"]})
    return out


def energy_year_summary(year: int) -> dict:
    """年度能耗汇总：折标煤(tce)、总电费、分类型明细（仅已审核台账）。"""
    rows = db.query(
        "SELECT r.energy_type_code, t.name, t.unit, t.tce_factor, t.is_green, "
        "SUM(r.quantity) AS qty, SUM(r.amount) AS amount "
        "FROM energy_record r JOIN energy_type t ON t.code=r.energy_type_code "
        "WHERE r.year=? AND r.status='approved' GROUP BY r.energy_type_code", (year,))
    total_tce, total_cost, items = 0.0, 0.0, []
    for r in rows:
        tce = round((r["qty"] or 0) * (r["tce_factor"] or 0) / 1000, 6)  # kgce→tce
        total_tce += tce
        total_cost += r["amount"] or 0
        items.append({"code": r["energy_type_code"], "name": r["name"], "unit": r["unit"],
                      "quantity": r["qty"] or 0, "amount": r["amount"] or 0,
                      "tce": tce, "is_green": bool(r["is_green"])})
    return {"year": year, "total_tce": round(total_tce, 6),
            "total_cost": round(total_cost, 2), "items": items}


def monthly_carbon_estimate(year: int) -> list[dict]:
    """月度碳排估算：已审核台账 × 当期因子（仅可映射科目：燃料 + 电力）。

    用于碳排分析的月度趋势，不要求年度盘查已填报。
    电力口径（评审 L2 延伸）：某月存在全厂总表（PLANT）记录时只取总表，
    分表仅用于单元结构分析，避免总分表重复计；无总表的月份回退为全单元求和。
    返回 12 个月 [{month, scope1, scope2, total}]，无数据的月为 0。
    """
    months = {m: {"month": m, "scope1": 0.0, "scope2": 0.0} for m in range(1, 13)}

    # 范围二：电力净量（总表优先）
    elec = db.query_one(
        "SELECT code FROM emission_source WHERE scope='范围二' AND enabled=1 LIMIT 1")
    plant = db.query_one("SELECT id FROM energy_unit WHERE code='PLANT'")
    if elec:
        factor, _ = get_factor(elec["code"], year)
        rows = db.query(
            "SELECT month, unit_id, quantity, gross_qty, lease_deduct, pv_qty "
            "FROM energy_record WHERE year=? AND energy_type_code='ELEC' AND status='approved'",
            (year,))
        plant_months = {r["month"] for r in rows if plant and r["unit_id"] == plant["id"]}
        per_month: dict[int, float] = {}
        for r in rows:
            if r["month"] in plant_months and r["unit_id"] != plant["id"]:
                continue  # 该月有总表：跳过分表
            net = ((r["gross_qty"] or 0) - (r["lease_deduct"] or 0) - (r["pv_qty"] or 0)
                   if r["gross_qty"] is not None else (r["quantity"] or 0))
            per_month[r["month"]] = per_month.get(r["month"], 0.0) + net
        for m, qty in per_month.items():
            months[m]["scope2"] += qty * factor

    # 范围一：可映射燃料（天然气/液化气/柴油/汽油）
    rows = db.query(
        "SELECT s.code AS source_code, s.map_convert, r.month, SUM(r.quantity) AS qty "
        "FROM emission_source s "
        "JOIN energy_record r ON r.energy_type_code=s.map_energy_type AND r.year=? "
        "WHERE s.map_energy_type IS NOT NULL AND s.enabled=1 AND s.scope='范围一' "
        "AND r.status='approved' GROUP BY s.code, r.month", (year,))
    for r in rows:
        factor, _ = get_factor(r["source_code"], year)
        months[r["month"]]["scope1"] += (r["qty"] or 0) * r["map_convert"] * factor

    out = []
    for m in months.values():
        s1, s2 = round(m["scope1"], 6), round(m["scope2"], 6)
        out.append({"month": m["month"], "scope1": s1, "scope2": s2,
                    "total": round(s1 + s2, 6)})
    return out


def unit_carbon_breakdown(year: int) -> list[dict]:
    """分单元电力碳排结构（分表口径）。

    share 以全厂总表（PLANT）净量排放为分母；无总表时以各单元合计为分母。
    分单元为分表计量，合计 ≤ 全厂口径属正常（未分表区域计入差值）。
    """
    elec = db.query_one(
        "SELECT code FROM emission_source WHERE scope='范围二' AND enabled=1 LIMIT 1")
    if not elec:
        return []
    factor, _ = get_factor(elec["code"], year)
    rows = db.query(
        "SELECT u.code AS unit_code, u.name AS unit_name, r.quantity, "
        "r.gross_qty, r.lease_deduct, r.pv_qty "
        "FROM energy_record r JOIN energy_unit u ON u.id=r.unit_id "
        "WHERE r.year=? AND r.energy_type_code='ELEC' AND r.status='approved'", (year,))
    per_unit: dict[str, dict] = {}
    for r in rows:
        net = ((r["gross_qty"] or 0) - (r["lease_deduct"] or 0) - (r["pv_qty"] or 0)
               if r["gross_qty"] is not None else (r["quantity"] or 0))
        e = per_unit.setdefault(r["unit_code"], {"unit_code": r["unit_code"],
                                                 "unit_name": r["unit_name"], "quantity": 0.0})
        e["quantity"] += net
    items = []
    for e in per_unit.values():
        qty = round(e["quantity"], 2)
        items.append({"unit_code": e["unit_code"], "unit_name": e["unit_name"],
                      "quantity": qty, "emission": round(qty * factor, 6)})
    plant_qty = next((i["emission"] for i in items if i["unit_code"] == "PLANT"), None)
    denom = plant_qty if plant_qty else sum(i["emission"] for i in items)
    for i in items:
        i["share"] = round(i["emission"] / denom * 100, 2) if denom else 0.0
    return sorted(items, key=lambda i: -i["emission"])
