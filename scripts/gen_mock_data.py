#!/usr/bin/env python
"""模拟数据生成脚本：为金华聚杰电器生成 1000 条能耗台账（energy_record）。

用途：演示 / UAT / 联调。生成的数据 created_by='mock-gen'，可用 --force 清掉重建。
安全边界：只写 energy_unit（缺失的用能单元）与 energy_record，
不触碰 carbon_activity / carbon_inventory（2024 验收基准必须保持不变）。

用法：
    .venv/Scripts/python scripts/gen_mock_data.py          # 幂等插入（已存在的月份跳过）
    .venv/Scripts/python scripts/gen_mock_data.py --force  # 先删除 mock-gen 数据再重建
"""
import random
import sys
from datetime import date
from pathlib import Path

# 允许从项目根目录直接运行：把根目录加入 import 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import db  # noqa: E402

# ── 生成参数 ──
TOTAL_TARGET = 1000                 # 目标总条数
RNG_SEED = 20240813                 # 固定随机种子，保证可复现
CREATED_BY = "mock-gen"
NOW = db.now_iso()


def start_month() -> tuple[int, int]:
    """起始月份：以当前月为终点向前推 58 个月。

    17 个组合 × 58 个整月 + 最后 1 个月 14 条 = 1000 条；最新数据落在当月，
    前端台账页默认按当前年查询即可直接看到数据。
    """
    y, m = date.today().year, date.today().month - 58
    while m <= 0:
        y, m = y - 1, m + 12
    return y, m

# 用能单元：除种子里已有的 PLANT（全厂）外，补充 4 个单元
EXTRA_UNITS = [
    ("WS1", "一号车间"),
    ("WS2", "二号车间"),
    ("OFFICE", "办公楼"),
    ("DORM", "宿舍楼"),
]

# 生成矩阵：单元 → 能源类型列表（17 个组合）
UNIT_TYPES = [
    # 注意：PV 必须排在 ELEC 之前，ELEC 的 pv_qty 需引用当月光伏发电量
    ("PLANT", ["PV", "WATER", "NG", "LPG", "DIESEL", "GASOLINE", "ELEC"]),
    ("WS1", ["ELEC", "WATER", "NG"]),
    ("WS2", ["ELEC", "WATER", "LPG"]),
    ("OFFICE", ["ELEC", "WATER"]),
    ("DORM", ["ELEC", "WATER"]),
]

# 月基线用量（对齐 2024 基准口径折算到月）
# PLANT 为全厂总表口径；分单元 ELEC/WATER 为总表净量的分表份额（见 SHARE_OF_PLANT），
# 物理上是总表的子集，不与总表加总，避免碳排估算重复计（总表优先口径）。
MONTHLY_BASE = {
    # (unit, type): 月基线
    ("PLANT", "ELEC"): 3529000 / 12,      # 2024 基准 352.9 万 kWh
    ("PLANT", "WATER"): 4200.0,
    ("PLANT", "NG"): 61744 / 1000 / 12,   # 食堂天然气：基准 61744 升/年 → 台账口径 m³/月
    ("PLANT", "LPG"): 10108.09 / 1.8519 / 12,   # 基准为升，台账口径 kg
    ("PLANT", "DIESEL"): 8856.2 / 12,
    ("PLANT", "GASOLINE"): 24868.16 / 12,
    ("PLANT", "PV"): 180000 / 12,         # 光伏年发电约 18 万 kWh
    ("WS1", "NG"): 0.8,                   # 车间独立燃烧源，量级与基准口径对齐
    ("WS2", "LPG"): 320.0,
}

# 分表份额：分单元用量 = 当月全厂净量 × 份额 × 小扰动（合计 72%/80%，其余为未分表区域）
SHARE_OF_PLANT = {
    ("WS1", "ELEC"): 0.35, ("WS2", "ELEC"): 0.25,
    ("OFFICE", "ELEC"): 0.08, ("DORM", "ELEC"): 0.04,
    ("WS1", "WATER"): 0.35, ("WS2", "WATER"): 0.25,
    ("OFFICE", "WATER"): 0.08, ("DORM", "WATER"): 0.12,
}

# 参考单价（元/单位），用于 amount 字段
UNIT_PRICE = {
    "ELEC": 0.75, "WATER": 3.5, "NG": 3.8, "LPG": 7.2,
    "DIESEL": 7.0, "GASOLINE": 7.8, "PV": 0.0,
}

LIVING_UNITS = {"OFFICE", "DORM"}   # 生活类单元：夏季空调负荷更高
REJECT_REASONS = [
    "与发票金额不符，请核对后重报",
    "抄表数与上月波动异常，请附说明",
    "单位填写有误，请按账单原始单位填报",
]


def seasonal_factor(unit: str, month: int) -> float:
    """季节系数：生产类夏/冬略高，生活类夏季空调负荷显著升高。"""
    if month in (6, 7, 8, 9):
        return 1.20 if unit in LIVING_UNITS else 1.15
    if month in (12, 1, 2):
        return 1.05
    return 1.0


def iter_months():
    """从 start_month() 起逐月递增，无限迭代（由调用方按总量截断）。"""
    y, m = start_month()
    while True:
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def status_for(year: int, month: int, rng: random.Random) -> tuple[str, str | None]:
    """状态分布：距当前 ≥2 个月 → 已审定；上月 → 待审；当月 → 草稿为主、少量驳回。"""
    today = date.today()
    diff = (today.year * 12 + today.month) - (year * 12 + month)
    if diff >= 2:
        return "approved", None
    if diff == 1:
        return "submitted", None
    r = rng.random()
    if r < 0.15:
        return "rejected", rng.choice(REJECT_REASONS)
    if r < 0.40:
        return "submitted", None
    return "draft", None


def build_rows() -> list[tuple]:
    """按月外循环生成记录，凑满 TOTAL_TARGET 条后截断。"""
    rng = random.Random(RNG_SEED)
    unit_id = {r["code"]: r["id"] for r in db.query("SELECT id, code FROM energy_unit")}
    reviewer_id = db.query_one(
        "SELECT id FROM sys_user WHERE ding_unionid='mock-manager'")["id"]

    rows: list[tuple] = []
    pv_by_month: dict[tuple[int, int], float] = {}   # 每月 PLANT 光伏量，供 ELEC 净量勾稽
    plant_qty: dict[tuple[int, int, str], float] = {}  # 每月 PLANT 各类型用量，供分表份额折算
    for year, month in iter_months():
        for unit, types in UNIT_TYPES:
            for etype in types:
                if len(rows) >= TOTAL_TARGET:
                    return rows
                if (unit, etype) in SHARE_OF_PLANT:
                    # 分表：当月全厂净量 × 份额 × 小扰动（PLANT 在 UNIT_TYPES 中排最前，已先生成）
                    plant_net = plant_qty[(year, month, etype)]
                    qty = plant_net * SHARE_OF_PLANT[(unit, etype)] * rng.uniform(0.97, 1.03)
                else:
                    base = MONTHLY_BASE[(unit, etype)]
                    qty = base * seasonal_factor(unit, month) * rng.uniform(0.92, 1.08)
                qty = round(qty, 2)
                amount = round(qty * UNIT_PRICE[etype], 2) or None

                # PLANT 电力：拆分 总表/外租/光伏，净量 = 总表 − 外租 − 光伏（绿电只扣一次）
                gross = lease = pv = None
                if unit == "PLANT" and etype == "ELEC":
                    pv = pv_by_month.get((year, month), 0.0)
                    # 净量 = 总表 − 外租 − 光伏，外租 ≈ 总表×8% → 总表 = (净量+光伏)/0.92
                    gross = round((qty + pv) / 0.92, 2)
                    lease = round(gross * 0.08, 2)
                if unit == "PLANT" and etype == "PV":
                    pv_by_month[(year, month)] = qty
                if unit == "PLANT":
                    plant_qty[(year, month, etype)] = qty

                status, reason = status_for(year, month, rng)
                review_by = reviewer_id if status == "approved" else None
                review_at = f"{year}-{month:02d}-28T10:00:00" if status == "approved" else None

                rows.append((
                    year, month, etype, unit_id[unit], qty, amount,
                    gross, lease, pv, status, review_by, review_at, reason,
                    f"{unit} {etype} {year}-{month:02d} 模拟台账",   # remark
                    NOW, CREATED_BY))
    return rows


def main() -> None:
    force = "--force" in sys.argv
    db.init_db()

    # 补充用能单元（幂等）
    for code, name in EXTRA_UNITS:
        if not db.query_one("SELECT 1 FROM energy_unit WHERE code=?", (code,)):
            db.execute(
                "INSERT INTO energy_unit(code,name,created_at,created_by) VALUES(?,?,?,?)",
                (code, name, NOW, CREATED_BY))

    if force:
        db.execute("DELETE FROM energy_record WHERE created_by=?", (CREATED_BY,))

    rows = build_rows()
    db.execute_many(
        "INSERT OR IGNORE INTO energy_record"
        "(year,month,energy_type_code,unit_id,quantity,amount,gross_qty,lease_deduct,pv_qty,"
        "status,review_by,review_at,reject_reason,remark,created_at,created_by)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows)

    # ── 汇总输出 ──
    total = db.query_one(
        "SELECT COUNT(*) AS n FROM energy_record WHERE created_by=?", (CREATED_BY,))["n"]
    print(f"本次生成目标 {len(rows)} 条，库中 mock-gen 台账累计 {total} 条")
    print("按状态分布：")
    for r in db.query(
            "SELECT status, COUNT(*) AS n FROM energy_record WHERE created_by=?"
            " GROUP BY status ORDER BY n DESC", (CREATED_BY,)):
        print(f"  {r['status']:<10} {r['n']}")
    print("按用能单元分布：")
    for r in db.query(
            "SELECT u.name AS uname, COUNT(*) AS n FROM energy_record e"
            " JOIN energy_unit u ON u.id=e.unit_id WHERE e.created_by=?"
            " GROUP BY u.name ORDER BY n DESC", (CREATED_BY,)):
        print(f"  {r['uname']:<10} {r['n']}")


if __name__ == "__main__":
    main()
