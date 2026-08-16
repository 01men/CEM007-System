"""UAT 走查驱动：以产品经理视角模拟 3 个典型用户故事，记录真实接口响应。

运行：.venv/Scripts/python scripts/uat_drive.py
输出：docs/uat_log.md（走查实录，供 04-UAT走查.md 引用）
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["DINGTALK_MOCK"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LOG: list[str] = []


def step(title: str, detail: str = "") -> None:
    LOG.append(f"**{title}**")
    if detail:
        LOG.append(f"```\n{detail}\n```")
    LOG.append("")


def brief(r, keys=None, maxlen=500) -> str:
    try:
        d = r.json()
        if keys:
            d = {k: d.get(k) for k in keys}
        s = json.dumps(d, ensure_ascii=False, indent=1)
    except Exception:
        s = r.text
    return f"HTTP {r.status_code}\n{s[:maxlen]}"


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="uat_"))
    os.environ["JUJIE_DB_PATH"] = str(tmp / "uat.db")
    from fastapi.testclient import TestClient
    from server import auth, db, seeds
    db.configure(tmp / "uat.db")
    db.init_db()
    seeds.seed_all(auth.hash_password("admin123"))
    from server.main import create_app
    c = TestClient(create_app(), follow_redirects=False)

    # ══ 故事一：行政部填报员 录入台账→被驳回→修改→过审 ══
    LOG.append("# 故事一：行政部填报员 —— 月度台账录入与审核闭环\n")
    r = c.post("/api/auth/mock-login", json={"unionid": "mock-reporter-admin"})
    step("1. 钉钉扫码（Mock）登录", f"HTTP {r.status_code} → 302 跳工作台，Cookie 已签发")
    r = c.get("/api/auth/me")
    step("2. 获取当前用户", brief(r, ["name", "role_code", "dept_name"]))
    body = {"year": 2025, "month": 1, "energy_type_code": "ELEC", "unit_id": 1,
            "quantity": 295000, "amount": 210000, "gross_qty": 300000,
            "lease_deduct": 3000, "pv_qty": 2000, "remark": "1月电费单"}
    r = c.post("/api/energy/records", json=body)
    rid = r.json()["id"]
    step("3. 录入 1 月电力台账（含总表/外租/光伏拆分）", brief(r))
    r = c.post("/api/energy/records", json=body)
    step("4. 同月重复录入 → 唯一性拦截", brief(r))
    r = c.post(f"/api/energy/records/{rid}/submit")
    step("5. 提交审核", brief(r))
    c.post("/api/auth/mock-login", json={"unionid": "mock-manager"})
    r = c.post(f"/api/energy/records/{rid}/audit", json={"approve": False})
    step("6. 能碳管理员驳回但不填原因 → 拦截", brief(r))
    r = c.post(f"/api/energy/records/{rid}/audit",
               json={"approve": False, "reject_reason": "电量与账单不符，请复核总表数"})
    step("7. 填写原因后驳回成功", brief(r))
    c.post("/api/auth/mock-login", json={"unionid": "mock-reporter-admin"})
    body["gross_qty"] = 298000
    body["quantity"] = 293000
    body["overwrite"] = True
    r = c.put(f"/api/energy/records/{rid}", json=body)
    step("8. 填报员按驳回意见修改", brief(r))
    c.post(f"/api/energy/records/{rid}/submit")
    c.post("/api/auth/mock-login", json={"unionid": "mock-manager"})
    r = c.post(f"/api/energy/records/{rid}/audit", json={"approve": True})
    step("9. 再提交 → 审核通过并锁定", brief(r))
    c.post("/api/auth/mock-login", json={"unionid": "mock-reporter-admin"})
    r = c.put(f"/api/energy/records/{rid}", json=body)
    step("10. 锁定后填报员再编辑 → 403", brief(r))

    # ══ 故事二：能碳管理员 导入 2025 盘查→错误修复→审定→导出 PDF ══
    LOG.append("# 故事二：能碳管理员 —— 2025 年度盘查与披露报告\n")
    c.post("/api/auth/mock-login", json={"unionid": "mock-manager"})
    r = c.get("/api/import/template", params={"type": "carbon"})
    step("1. 下载碳活动导入模板", f"HTTP {r.status_code}，{len(r.content)} 字节 xlsx")
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["盘查年度", "科目编码", "科目名称", "活动数据", "备注"])
    ws.append([2025, "S2-ELEC-001", "示例", 1, "示例行"])
    rows = [
        (2025, "S2-ELEC-001", "", 3400000, "电网发票汇总"),
        (2025, "S1-NG-001", "", 60000, "食堂燃气"),
        (2025, "S1-LPG-001", "", 9500, ""),
        (2025, "S1-DIESEL-001", "", 8200, ""),
        (2025, "S1-GAS-001", "", 23000, ""),
        (2025, "S1-CO2-001", "", 4, ""),
        (2025, "S1-REF-OFF", "", 500, ""),
        (2025, "S1-REF-PRD", "", 1.2, ""),
        (2025, "S1-WORKHRS", "", 1650000, ""),
        (2025, "S3-FREIGHT", "", 2400000, ""),
        (2025, "BAD-CODE", "", 100, "故意的错误行1"),
        (2025, "S3-MILEAGE", "", -1, "故意的错误行2"),
    ]
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    files = {"file": ("2025碳活动.xlsx", buf.getvalue(),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r = c.post("/api/import/upload", params={"biz_type": "carbon", "strategy": "skip"},
               files=files)
    p = r.json()
    step("2. 上传导入（含 2 行故意错误）→ 预览", brief(r, ["new", "conflict", "errors", "error_detail"]))
    r = c.post("/api/import/confirm", json={"task_id": p["task_id"]})
    step("3. 确认入库（错误行自动跳过）", brief(r))
    r = c.post("/api/carbon/activities", json={
        "year": 2025, "total_revenue": 6800,
        "items": [{"source_code": "S3-MILEAGE", "activity_value": 198000,
                   "remark": "补齐导入缺失科目"}]})
    step("4. 手工补齐私车公用里程 + 录入总产值", brief(r, ["ok", "saved"]))
    r = c.get("/api/carbon/activities", params={"year": 2025})
    d = r.json()
    step("5. 实时核算结果", brief(r, ["total", "groups", "intensity", "inventory_status"]))
    c.post("/api/carbon/inventory/2025/submit")
    r = c.post("/api/carbon/inventory/2025/approve")
    step("6. 提交盘查 → 审定（因子快照封存）", brief(r))
    for code, rev in (("BOSCH", 2100), ("BD", 320)):
        c.post("/api/customers/revenue",
               json={"year": 2025, "customer_code": code, "revenue": rev})
    r = c.get("/api/allocation", params={"year": 2025})
    step("7. 生成 2025 分摊账单", brief(r, ["total_emission", "items", "warning"]))
    r = c.get("/api/export/pdf", params={"year": 2025})
    step("8. 导出 PDF 披露报告", f"HTTP {r.status_code}，{len(r.content)} 字节，文件头 {r.content[:5]}")

    # ══ 故事三：管理层 工作台→分析→分摊→导出 Excel ══
    LOG.append("# 故事三：管理层 —— 经营视角一屏总览\n")
    c.post("/api/auth/mock-login", json={"unionid": "mock-viewer"})
    r = c.get("/api/analysis/dashboard", params={"year": 2025})
    step("1. 工作台 KPI 与待办", brief(r, ["kpi", "todos"]))
    r = c.get("/api/analysis/carbon", params={"year": 2025})
    d = r.json()
    step("2. 碳排分析（同比 2024 基准）",
         brief(r, ["year"]) + "\noverview: " +
         json.dumps(d["overview"], ensure_ascii=False)[:400])
    r = c.get("/api/allocation", params={"year": 2025})
    a = r.json()
    bosch = next(i for i in a["items"] if i["customer_code"] == "BOSCH")
    step("3. 查看博世年度碳账单",
         json.dumps(bosch, ensure_ascii=False, indent=1))
    r = c.get("/api/export/excel", params={"year": 2025})
    step("4. 导出 Excel 活动总账", f"HTTP {r.status_code}，{len(r.content)} 字节，文件头 {r.content[:2]}")
    r = c.post("/api/energy/records", json={
        "year": 2025, "month": 3, "energy_type_code": "ELEC", "unit_id": 1, "quantity": 1})
    step("5. 管理层尝试录入数据 → 403 只读拦截", brief(r))

    out = Path(__file__).resolve().parent.parent / "docs" / "uat_log.md"
    out.write_text("\n".join(LOG), encoding="utf-8")
    print(f"UAT 走查完成 → {out}")


if __name__ == "__main__":
    main()
