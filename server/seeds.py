"""种子数据：首次启动初始化。

内容来源：data/seed_excel/ 下《2025年聚杰减碳项目_总表》与《GHG Protocol 排放因子明细》，
由 scripts/build_seed_data.py 解析生成 server/seed_data.py。科目口径与因子以总表
「计算方式」「项目目录」sheet 为准；2025 盘查年度预置为 draft，待用户走提交/审定流。
"""
from . import db, seed_data

BASE_YEAR = 2025

# ── 部门 ──
DEPTS = [
    ("ADMIN", "行政部"), ("FIN", "财务部"), ("PROD", "生产设备部"),
    ("LOGI", "供应链物流部"), ("GM", "总经办"),
]

# ── 初始用户：仅保留管理员（admin123），其余成员由管理员在用户管理中开通 ──
USERS = [
    # name, mobile, dept, role, unionid, status
    ("系统管理员", "13800000001", "GM", "admin", None, "active"),
]

# ── 能源类型（LPG 按总表口径改为升，密度 0.54 kg/L 折算 tce）──
# code, name, unit, tce_factor(kgce/单位), in_carbon, is_green, map_source_code
ENERGY_TYPES = [
    ("ELEC", "电力", "kWh", 0.1229, 1, 0, "S2-ELEC-001"),
    ("WATER", "自来水", "吨", 0, 0, 0, None),
    ("NG", "天然气", "m³", 1.3300, 1, 0, "S1-NG-001"),
    ("LPG", "液化石油气", "升", 0.9257, 1, 0, "S1-LPG-001"),
    ("DIESEL", "柴油", "升", 1.4571, 1, 0, None),
    ("GASOLINE", "汽油", "升", 1.4714, 1, 0, None),
    ("PV", "光伏绿电（自发自用）", "kWh", 0.1229, 0, 1, None),
    ("STEAM", "外购蒸汽（预留）", "吨", 0, 0, 0, None),
]

# ── 核算科目（因子口径见总表「计算方式」；ref 标注 GHG 因子表明细出处）──
# code, name_zh, name_en, scope, source_type, unit, dept_code, map_energy_type, map_convert, factor, guide, factor_ref
SOURCES = [
    ("S1-NG-001", "天然气炉灶", "Natural Gas Stoves", "范围一", "固定源", "Nm³", "ADMIN", "NG", 1.0, 0.00216,
     "请联系行政部或食堂管理员获取全年天然气费发票或气表对账单，按标准立方米（Nm³）填入。",
     "排放量 = 年天然气消耗量(Nm³) × 2.16 kgCO₂/Nm³ ÷ 1000（总表计算方式；GHG 因子表 Scope1-固定燃烧：天然气 2.184×10⁻³ tCO₂/m³）。",
     ),
    ("S1-LPG-001", "液化石油气炉灶", "LPG Stoves", "范围一", "固定源", "升", "ADMIN", "LPG", 1.0, 0.001674,
     "收集食堂全年液化气钢瓶采购发票、充气记录或供应商结算单，按升（L）填入；若原始记录为千克，升 = 公斤 ÷ 0.54。",
     "排放量 = 消耗量(L) × 密度 0.54 kg/L × 3.10 kgCO₂/kg ÷ 1000（总表计算方式；GHG 因子表 Scope1-固定燃烧：LPG 3.166×10⁻³ tCO₂/kg）。",
     ),
    ("S1-DIESEL-001", "货车（柴油）", "Logistics Truck Diesel", "范围一", "移动源", "升", "FIN", "DIESEL", 1.0, 0.00263,
     "请财务部或车队管理员导出公司名下货运车辆专属油卡年度对账单，汇总柴油发票实际加油升数。",
     "排放量 = 柴油消耗量(L) × 2.63 kgCO₂e/L ÷ 1000（总表计算方式；GHG 因子表 Scope1-固定燃烧：柴油 2.718×10⁻³ tCO₂/L）。",
     ),
    ("S1-GAS-001", "小汽车（汽油）", "Fleet Car Gasoline", "范围一", "移动源", "升", "FIN", "GASOLINE", 1.0, 0.00226,
     "请财务部提供公务小汽车油卡年度对账单，汇总当年汽油发票实际加油总量（升）。",
     "排放量 = 汽油消耗量(L) × 2.26 kgCO₂e/L ÷ 1000（总表计算方式；GHG 因子表 Scope1-固定燃烧：汽油 2.179×10⁻³ tCO₂/L）。",
     ),
    ("S1-CO2-001", "灭火器（二氧化碳）", "CO2 Fire Extinguishers", "范围一", "逸散源", "千克", "ADMIN", None, 1.0, 0.001,
     "请安全员或消防负责人提供消防设备维护台账，填报当年补充安装的 CO₂ 千克数（非库存数）。",
     "排放量 = 当年补充安装 CO₂(kg) × GWP(CO₂)=1 ÷ 1000（总表计算方式；GHG 因子表 GWP 参数表）。",
     ),
    ("S1-REF-OFF", "制冷剂（办公空调）", "HVAC Refrigerants (Office)", "范围一", "逸散源", "千克", "ADMIN", None, 1.0, 0.675,
     "请行政部查看办公区空调维保记录，填报当年实际补充加注的制冷剂千克数（2025 年为 R-32 共 90kg）。",
     "排放量 = 当年补充加注量(kg) × 该冷媒 GWP ÷ 1000；2025 预置 GWP(AR4) R-32=675（总表空调制冷剂sheet；GHG 因子表 Scope1-工艺逸散）。",
     ),
    ("S1-REF-PRD", "制冷剂（生产设备）", "Chiller Refrigerants (Prod)", "范围一", "逸散源", "千克", "PROD", None, 1.0, 2.088,
     "请车间设备维护组提供冷水机组、空压机配套干燥机等设备的维保台账，填报当年实际补充加注的制冷剂千克数。",
     "排放量 = 当年补充加注量(kg) × 该冷媒 GWP ÷ 1000；预置 GWP(AR4) R410A=2088（总表制冷剂设备明细sheet；GHG 因子表 Scope1-工艺逸散）。",
     ),
    ("S1-WORKHRS", "厂区化粪池（当量人口）", "Septic Tank (Equivalent Population)", "范围一", "逸散源", "人", "ADMIN", None, 1.0, 0.175,
     "请行政部统计 1-12 月月在岗人数取平均值，得出平均在岗人数（当量人口）填入。2025 年为 890 人。",
     "排放量 = 当量人口 × 175 kgCO₂e/（人·a）÷ 1000（总表计算方式·厂区化粪池）。",
     ),
    ("S2-ELEC-001", "全厂用电量（净）", "Purchased Electricity", "范围二", "外购电力", "千瓦时", "ADMIN", "ELEC", 1.0, 0.000583,
     "请财务部或能源管理部收集全年电费发票，全厂总用电量减去租赁户分表、减去太阳能光伏绿电后填入。2025 年净电量为 3,340,189.6 kWh。",
     "排放量 = 年度净用电量(kWh) × 0.000583 tCO₂e/kWh 浙江省级电网排放因子（总表计算方式；GHG 因子表 Scope2-电力热力）。",
     ),
    ("S3-FREIGHT", "成品运输", "Outbound Freight Logistics", "范围三", "交通运输", "吨·公里", "LOGI", None, 1.0, 0.000085,
     "请供应链或物流部提供成品外发运输台账：每批次货物总重量（吨）× 送货单程距离（公里），求和后填入（工厂+外购合计）。",
     "排放量 = 总运输周转量(t·km) × 0.000085 tCO₂e/(t·km) 公路货运因子（总表计算方式；GHG 因子表 Scope3-差旅物流）。",
     ),
    ("S3-MILEAGE", "私车公用燃油", "Compensated Private Car Fuel", "范围三", "差旅", "升", "FIN", None, 1.0, 0.00226,
     "请财务部提供年度私车公用补贴里程，按 11 升/百公里折算为汽油升数填入（总表私车公用sheet已折算，2025 年为 15,504.13 升）。",
     "排放量 = 私车公务燃油消耗量(L) × 2.26 kgCO₂e/L ÷ 1000（总表计算方式；GHG 因子表 Scope1-固定燃烧汽油因子）。",
     ),
]


def seed_all(admin_password_hash: str) -> None:
    """幂等初始化：任何一步已存在数据则跳过对应步骤。"""
    now = db.now_iso()
    data = seed_data.DATA
    inventory_year = data["meta"]["inventory_year"]

    if not db.query_one("SELECT 1 FROM sys_dept LIMIT 1"):
        db.execute_many(
            "INSERT INTO sys_dept(code,name,created_at,created_by) VALUES(?,?,?,?)",
            [(c, n, now, "seed") for c, n in DEPTS])

    if not db.query_one("SELECT 1 FROM sys_user LIMIT 1"):
        for name, mobile, dept, role, uid, status in USERS:
            dept_id = db.query_one("SELECT id FROM sys_dept WHERE code=?", (dept,))["id"]
            pwd = admin_password_hash if role == "admin" else None
            db.execute(
                "INSERT INTO sys_user(name,mobile,dept_id,role_code,ding_unionid,status,password_hash,"
                "created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?)",
                (name, mobile, dept_id, role, uid, status, pwd, now, "seed"))

    if not db.query_one("SELECT 1 FROM energy_unit LIMIT 1"):
        db.execute("INSERT INTO energy_unit(code,name,created_at,created_by) VALUES('PLANT','全厂',?,?)",
                   (now, "seed"))

    if not db.query_one("SELECT 1 FROM energy_type LIMIT 1"):
        db.execute_many(
            "INSERT INTO energy_type(code,name,unit,tce_factor,in_carbon,is_green,map_source_code,"
            "created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?)",
            [(c, n, u, t, ic, g, m, now, "seed") for c, n, u, t, ic, g, m in ENERGY_TYPES])

    if not db.query_one("SELECT 1 FROM emission_source LIMIT 1"):
        for i, (code, nzh, nen, scope, stype, unit, dept, met, mconv, factor,
                guide, ref) in enumerate(SOURCES, start=1):
            db.execute(
                "INSERT INTO emission_source(code,name_zh,name_en,scope,source_type,unit,dept_code,"
                "guide,factor_ref,map_energy_type,map_convert,enabled,sort_no,created_at,created_by)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
                (code, nzh, nen, scope, stype, unit, dept, guide, ref, met, mconv, i, now, "seed"))
            db.execute(
                "INSERT INTO emission_factor(source_code,factor,year_from,year_to,ref_source,"
                "change_reason,created_at,created_by) VALUES(?,?,?,?,?,?,?,?)",
                (code, factor, inventory_year, None, ref, "初始版本（2025年聚杰减碳项目总表）", now, "seed"))

    # ── 能耗台账：总表各 sheet 实际数据，直接置为 approved ──
    if not db.query_one("SELECT 1 FROM energy_record LIMIT 1"):
        unit_id = db.query_one("SELECT id FROM energy_unit WHERE code='PLANT'")["id"]
        for year, month, tcode, qty, amount, gross, lease, pv, remark in data["energy_records"]:
            db.execute(
                "INSERT INTO energy_record(year,month,energy_type_code,unit_id,quantity,amount,"
                "gross_qty,lease_deduct,pv_qty,status,review_by,review_at,remark,created_at,created_by)"
                " VALUES(?,?,?,?,?,?,?,?,?,'approved',1,?,?,?,?)",
                (year, month, tcode, unit_id, qty, amount, gross, lease, pv, now, remark, now, "seed"))

    if not db.query_one("SELECT 1 FROM customer LIMIT 1"):
        for cust in data["customers"]:
            db.execute(
                "INSERT INTO customer(code,name_zh,name_en,enabled,created_at,created_by)"
                " VALUES(?,?,?,1,?,?)", (cust["code"], cust["name_zh"], cust["name_en"], now, "seed"))

    # ── 2025 盘查年度：活动数据按总表预置为 draft，待提交/审定 ──
    if not db.query_one("SELECT 1 FROM carbon_inventory WHERE year=?", (inventory_year,)):
        db.execute(
            "INSERT INTO carbon_inventory(year,status,created_at,created_by)"
            " VALUES(?, 'draft', ?, 'seed')", (inventory_year, now))
        for s in SOURCES:
            code = s[0]
            value = data["carbon_activity_2025"].get(code)
            if value is None:
                continue
            db.execute(
                "INSERT INTO carbon_activity(year,source_code,activity_value,data_origin,"
                "reported_by,reported_at,created_at,created_by)"
                " VALUES(?,?,?,'import',1,?,?,?)",
                (inventory_year, code, value, now, now, now))
        db.execute(
            "INSERT INTO finance_metric(year,total_revenue,created_at,created_by) VALUES(?,?,?,?)",
            (inventory_year, data["meta"]["total_revenue"], now, "seed"))
        for cust in data["customers"]:
            db.execute(
                "INSERT INTO customer_revenue(year,customer_code,revenue,created_at,created_by)"
                " VALUES(?,?,?,?,?)", (inventory_year, cust["code"], cust["revenue"], now, "seed"))
