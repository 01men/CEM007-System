"""碳排分析增强（FR-6.1）：月度台账估算、帕累托、单元结构、基准/目标、洞察。

覆盖口径：
- 月度估算 = 已审核台账 × 当期因子；电力"总表优先"，分表不重复计入总量
- 帕累托累计占比收敛到 100%
- 空数据年份：has_data=False，洞察降级提示，不报错（边界）
"""
import pytest

from conftest import login

ELEC_FACTOR = 0.000583       # 总表口径：浙江省级电网排放因子
NG_FACTOR = 0.00216          # 总表口径：天然气 2.16 kgCO₂/Nm³（map_convert=1.0）


def _insert_ledger(year: int, month: int, etype: str, unit_id: int, qty: float,
                   gross=None, lease=None, pv=None):
    """直接落库一条已审核台账（测试装置库与接口同库）。"""
    from server import db
    db.execute(
        "INSERT INTO energy_record(year,month,energy_type_code,unit_id,quantity,"
        "gross_qty,lease_deduct,pv_qty,status,created_at,created_by)"
        " VALUES(?,?,?,?,?,?,?,?,'approved','2026-01-01T00:00:00','test')",
        (year, month, etype, unit_id, qty, gross, lease, pv))


def _ensure_ws1() -> int:
    """测试库只有 PLANT 单元，补一个分表单元并返回其 id。"""
    from server import db
    row = db.query_one("SELECT id FROM energy_unit WHERE code='WS1'")
    if row:
        return row["id"]
    db.execute("INSERT INTO energy_unit(code,name,created_at,created_by)"
               " VALUES('WS1','一号车间','2026-01-01T00:00:00','test')")
    return db.query_one("SELECT id FROM energy_unit WHERE code='WS1'")["id"]


def test_carbon_analysis_extended_structure(client):
    """2025 预置盘查年：新增字段齐全，帕累托累计≈100%，月度估算来自种子台账。"""
    login(client, "mock-manager")
    d = client.get("/api/analysis/carbon?year=2025").json()

    for key in ("monthly_estimate", "monthly_estimate_prev", "pareto",
                "unit_breakdown", "baseline", "target", "insights", "has_data"):
        assert key in d, f"缺少字段 {key}"

    assert d["has_data"] is True
    assert len(d["monthly_estimate"]) == 12
    # 种子台账：2025 有 ELEC/LPG/柴汽油月度记录 → 月度估算非全 0
    assert any(m["total"] > 0 for m in d["monthly_estimate"])
    # 2024 有天然气/电力台账 → 去年同期估算存在
    assert d["monthly_estimate_prev"] is not None
    # 基准年（BASE_YEAR=2025）自身不输出基准对比
    assert d["baseline"] is None
    # 帕累托：降序且累计占比收敛到 100%
    par = d["pareto"]
    assert par, "2025 盘查年应有排放热点"
    assert all(par[i]["emission"] >= par[i + 1]["emission"] for i in range(len(par) - 1))
    assert abs(par[-1]["cumulative"] - 100) < 0.1
    # 洞察：数据完整 → 至少含最大排放源洞察
    assert any("最大排放源" in t["text"] for t in d["insights"])


def test_monthly_estimate_meter_priority(client):
    """总表优先：同月有 PLANT 总表时，分表电量不计入月度估算总量。"""
    from server import db
    ws1 = _ensure_ws1()
    # 2026-01：PLANT 总表（拆分列净量 = 110000-10000-5000 = 95000）+ WS1 分表 30000
    _insert_ledger(2026, 1, "ELEC", 1, 95000, gross=110000, lease=10000, pv=5000)
    _insert_ledger(2026, 1, "ELEC", ws1, 30000)
    # 2026-02：无总表，仅 WS1 分表 40000 → 回退全单元求和
    _insert_ledger(2026, 2, "ELEC", ws1, 40000)
    # 范围一：天然气 500 m³（Nm³ 1:1）× 因子
    _insert_ledger(2026, 1, "NG", 1, 500)

    login(client, "mock-manager")
    d = client.get("/api/analysis/carbon?year=2026").json()
    mon = {m["month"]: m for m in d["monthly_estimate"]}

    # 1 月：范围二只算总表净量 95000（不含分表 30000）
    assert mon[1]["scope2"] == pytest.approx(95000 * ELEC_FACTOR, abs=1e-4)
    # 1 月：范围一天然气 500 Nm³ × 因子
    assert mon[1]["scope1"] == pytest.approx(500 * NG_FACTOR, abs=1e-4)
    # 2 月：无总表 → 分表求和 40000
    assert mon[2]["scope2"] == pytest.approx(40000 * ELEC_FACTOR, abs=1e-4)
    # 2026 无盘查活动数据 → has_data=False，洞察提示估算口径，基准对比不输出
    assert d["has_data"] is False
    assert d["baseline"] is None
    assert any("估算" in t["text"] for t in d["insights"])


def test_unit_breakdown_share_denominator(client):
    """分单元结构：以全厂总表排放为分母，PLANT share=100，分表 share<100。"""
    ws1 = _ensure_ws1()
    _insert_ledger(2026, 3, "ELEC", 1, 100000)
    _insert_ledger(2026, 3, "ELEC", ws1, 35000)

    login(client, "mock-viewer")
    d = client.get("/api/analysis/carbon?year=2026").json()
    units = {u["unit_code"]: u for u in d["unit_breakdown"]}
    assert units["PLANT"]["share"] == 100.0
    assert units["WS1"]["share"] == pytest.approx(35.0, abs=0.01)
    assert units["WS1"]["emission"] == pytest.approx(35000 * ELEC_FACTOR, abs=1e-4)


def test_empty_year_degrades_gracefully(client):
    """边界：无盘查也无台账的年份 → 各字段为空但接口 200，洞察给出引导提示。"""
    login(client, "mock-auditor")
    r = client.get("/api/analysis/carbon?year=2023")
    assert r.status_code == 200
    d = r.json()
    assert d["has_data"] is False
    assert d["pareto"] == []
    assert d["unit_breakdown"] == []
    assert d["baseline"] is None and d["target"] is None
    assert all(m["total"] == 0 for m in d["monthly_estimate"])
    assert d["insights"] and d["insights"][0]["level"] == "warn"
