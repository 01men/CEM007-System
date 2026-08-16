"""第一轮：单元测试 —— 核算引擎核心逻辑（calc.py）。

金标准：2025 年聚杰减碳项目总表（seeds 预置，口径见总表「计算方式」sheet）。
"""
import os

import pytest

os.environ.setdefault("DINGTALK_MOCK", "1")

# 2025 预置盘查金标准（由总表活动数据 × 总表因子得出）
TOTAL_2025 = 2464.536952
GROUP_S1 = 284.711807
GROUP_S2 = 1947.330537
GROUP_S3 = 232.494608
INTENSITY_2025 = 0.363994  # total / 6770.81 万美金


@pytest.fixture()
def seeded(tmp_path):
    """独立数据库 + 种子数据，返回 db 模块句柄。"""
    os.environ["JUJIE_DB_PATH"] = str(tmp_path / "unit.db")
    from server import auth, db, seeds
    db.configure(tmp_path / "unit.db")
    db.init_db()
    seeds.seed_all(auth.hash_password("admin123"))
    return db


def _approve_year(year: int) -> None:
    """把指定年度盘查置为 approved 并封存因子快照（模拟审定）。"""
    from server import calc, db
    now = db.now_iso()
    for act in db.query("SELECT * FROM carbon_activity WHERE year=?", (year,)):
        factor, _ = calc.get_factor(act["source_code"], year)
        db.execute("UPDATE carbon_activity SET factor_snapshot=?, emission=? WHERE id=?",
                   (factor, round(act["activity_value"] * factor, 6), act["id"]))
    db.execute("UPDATE carbon_inventory SET status='approved', approved_by=1, approved_at=?"
               " WHERE year=?", (now, year))


# ── 基准复现（验收项 2）──

def test_baseline_total_matches_excel(seeded):
    from server import calc
    r = calc.compute_year(2025)
    assert r["total"] == pytest.approx(TOTAL_2025, abs=0.01)
    assert r["groups"]["范围一"] == pytest.approx(GROUP_S1, abs=0.01)
    assert r["groups"]["范围二"] == pytest.approx(GROUP_S2, abs=0.01)
    assert r["groups"]["范围三"] == pytest.approx(GROUP_S3, abs=0.01)
    assert r["intensity"] == pytest.approx(INTENSITY_2025, abs=0.0001)
    assert r["inventory_status"] == "draft"  # 预置为草稿，待提交审定


def test_emission_formula_per_source(seeded):
    """排放量 = 活动数据 × 因子（逐科目验证）。"""
    from server import calc
    r = calc.compute_year(2025)
    for d in r["details"]:
        assert d["emission"] == pytest.approx(d["activity_value"] * d["factor"], abs=1e-4)


def test_allocation_bosch_bd(seeded):
    from server import calc
    _approve_year(2025)
    a = calc.compute_allocation(2025)
    items = {i["customer_code"]: i for i in a["items"]}
    assert items["BOSCH"]["share"] == pytest.approx(2051.48 / 6770.81, abs=1e-6)
    assert items["BOSCH"]["allocated"] == pytest.approx(TOTAL_2025 * 2051.48 / 6770.81, abs=0.01)
    assert items["BD"]["allocated"] == pytest.approx(TOTAL_2025 * 265.66 / 6770.81, abs=0.01)
    assert a["warning"] is None


# ── 因子版本与快照（评审 M4）──

def test_factor_version_by_year(seeded):
    from server import calc, db
    now = db.now_iso()
    # 种子因子 year_from=2025、year_to=NULL（长期有效），2026 直接被覆盖，无回退提示
    factor, note = calc.get_factor("S2-ELEC-001", 2026)
    assert factor == pytest.approx(0.000583)
    assert note is None
    # 将 2025 版本限定为仅 2025 有效 → 2026 缺失时回退最近版本并提示
    db.execute("UPDATE emission_factor SET year_to=2025 WHERE source_code='S2-ELEC-001'")
    factor, note = calc.get_factor("S2-ELEC-001", 2026)
    assert factor == pytest.approx(0.000583)
    assert note and "2025" in note
    # 新增 2026 专属版本后优先取新版本
    db.execute("INSERT INTO emission_factor(source_code,factor,year_from,ref_source,"
               "change_reason,created_at,created_by) VALUES('S2-ELEC-001',0.0006,2026,"
               "'测试','测试',?,?)", (now, "test"))
    factor, note = calc.get_factor("S2-ELEC-001", 2026)
    assert factor == pytest.approx(0.0006)
    assert note is None


def test_snapshot_frozen_after_approve(seeded):
    """审定后修改因子，已审定年度结果不变（历史可复现）。"""
    from server import calc, db
    _approve_year(2025)
    before = calc.compute_year(2025)["total"]
    db.execute("UPDATE emission_factor SET factor=0.999 WHERE source_code='S2-ELEC-001'")
    after = calc.compute_year(2025)["total"]
    assert after == pytest.approx(before, abs=1e-9)


# ── 台账映射与绿电（评审 M1/M2）──

def test_ledger_mapping_with_convert(seeded):
    """天然气台账 m³ → 科目 Nm³（1:1，总表口径）。"""
    from server import calc, db
    now = db.now_iso()
    db.execute("INSERT INTO energy_record(year,month,energy_type_code,unit_id,quantity,"
               "status,created_at,created_by) VALUES(2026,1,'NG',1,61.744,'approved',?,'1')",
               (now,))
    mapped = calc.ledger_mapped_values(2026)
    assert mapped["S1-NG-001"] == pytest.approx(61.744, abs=0.01)


def test_net_electricity_and_pv(seeded):
    """净电量 = 总表−外租−光伏；光伏减碳量 = 光伏电量 × 电力因子。"""
    from server import calc, db
    now = db.now_iso()
    db.execute("INSERT INTO energy_record(year,month,energy_type_code,unit_id,quantity,"
               "gross_qty,lease_deduct,pv_qty,status,created_at,created_by)"
               " VALUES(2026,1,'ELEC',1,850,1000,100,50,'approved',?,'1')", (now,))
    db.execute("INSERT INTO energy_record(year,month,energy_type_code,unit_id,quantity,"
               "status,created_at,created_by) VALUES(2026,1,'PV',1,50,'approved',?,'1')", (now,))
    assert calc.net_electricity(2026) == pytest.approx(850.0)
    green = calc.pv_green_deduction(2026)
    assert green["pv_quantity"] == pytest.approx(50.0)
    assert green["pv_reduction"] == pytest.approx(50 * 0.000583, abs=1e-6)


def test_draft_records_excluded_from_mapping(seeded):
    """仅已审核（approved）台账参与映射。"""
    from server import calc, db
    now = db.now_iso()
    db.execute("INSERT INTO energy_record(year,month,energy_type_code,unit_id,quantity,"
               "status,created_at,created_by) VALUES(2026,1,'NG',1,100,'draft',?,'1')", (now,))
    assert calc.ledger_mapped_values(2026) == {}


# ── 分摊边界 ──

def test_allocation_requires_approved_year(seeded):
    from server import calc
    with pytest.raises(ValueError, match="未审定"):
        calc.compute_allocation(2025)  # 预置为 draft


def test_allocation_warning_over_100_percent(seeded):
    from server import calc, db
    _approve_year(2025)
    db.execute("UPDATE customer_revenue SET revenue=7000 WHERE year=2025 AND customer_code='BOSCH'")
    a = calc.compute_allocation(2025)
    assert a["warning"] and "超过 100%" in a["warning"]
