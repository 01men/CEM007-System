"""第二轮：集成测试 —— httpx 模拟真实接口调用全链路。

流程：登录 → 台账录入→提交→审核 → 碳盘查填报→提交→审定 →
分摊账单 → Excel 导入五步 → 导出 Excel/PDF → 备份与恢复。
模块内用例按业务流程顺序共享一个数据库（flow_client）。
"""
import io

import pytest
from openpyxl import Workbook

from conftest import login


# ── 台账审核流（FR-3.2/3.3）──

def test_01_energy_record_full_audit_flow(flow_client):
    c = flow_client
    login(c, "mock-reporter-admin")
    # 2026 年：种子台账只覆盖 2021-2025，避免与预置记录冲突
    body = {"year": 2026, "month": 1, "energy_type_code": "ELEC", "unit_id": 1,
            "quantity": 295000, "amount": 210000,
            "gross_qty": 300000, "lease_deduct": 3000, "pv_qty": 2000,
            "remark": "1月电费单"}
    r = c.post("/api/energy/records", json=body)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]

    # 重复录入 → 409；确认覆盖 → 200
    assert c.post("/api/energy/records", json=body).status_code == 409
    r = c.post("/api/energy/records", json={**body, "quantity": 296000, "overwrite": True})
    assert r.status_code == 200

    # 提交审核
    assert c.post(f"/api/energy/records/{rid}/submit").status_code == 200
    # 提交后不可改
    assert c.put(f"/api/energy/records/{rid}", json=body).status_code == 403

    # 填报员无权审核
    assert c.post(f"/api/energy/records/{rid}/audit", json={"approve": True}).status_code == 403

    # 能碳管理员驳回（原因必填）→ 再提交 → 通过 → 锁定
    login(c, "mock-manager")
    assert c.post(f"/api/energy/records/{rid}/audit", json={"approve": False}).status_code == 400
    assert c.post(f"/api/energy/records/{rid}/audit",
                  json={"approve": False, "reject_reason": "电量与账单不符"}).status_code == 200
    login(c, "mock-reporter-admin")
    assert c.post(f"/api/energy/records/{rid}/submit").status_code == 200
    login(c, "mock-manager")
    assert c.post(f"/api/energy/records/{rid}/audit", json={"approve": True}).status_code == 200

    # 锁定后编辑 403，解锁后可编辑
    login(c, "mock-reporter-admin")
    assert c.put(f"/api/energy/records/{rid}", json=body).status_code == 403
    login(c, "mock-manager")
    assert c.post(f"/api/energy/records/{rid}/unlock").status_code == 200
    r = c.get("/api/energy/records", params={"year": 2026, "month": 1})
    assert r.status_code == 200 and len(r.json()) == 1


# ── 碳盘查（FR-5.3）──

def test_02_carbon_inventory_flow(flow_client):
    c = flow_client
    login(c, "mock-reporter-admin")
    items = [{"source_code": "S2-ELEC-001", "activity_value": 3400000},
             {"source_code": "S1-NG-001", "activity_value": 60000},
             {"source_code": "S1-LPG-001", "activity_value": 9500},
             {"source_code": "S1-REF-OFF", "activity_value": 500},
             {"source_code": "S1-WORKHRS", "activity_value": 900}]
    r = c.post("/api/carbon/activities",
               json={"year": 2025, "items": items, "total_revenue": 6800})
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    # 填报覆盖 5 个科目；其余科目仍为种子预置值（总表口径因子）
    expected = 3400000 * 0.000583 + 60000 * 0.00216 + 9500 * 0.001674 \
        + 500 * 0.675 + 900 * 0.175 \
        + 4106.76 * 0.00263 + 19158.29 * 0.00226 \
        + 2323003.22 * 0.000085 + 15504.13 * 0.00226
    assert result["total"] == pytest.approx(expected, abs=1e-4), \
        f"{result['total']} != {expected}"

    # 提交 → 活动数据只读 → 审定 → 快照封存
    assert c.post("/api/carbon/inventory/2025/submit").status_code == 200
    assert c.post("/api/carbon/activities", json={"year": 2025, "items": items}).status_code == 403
    login(c, "mock-manager")
    r = c.post("/api/carbon/inventory/2025/approve")
    assert r.status_code == 200, r.text
    r = c.get("/api/carbon/activities", params={"year": 2025})
    d = r.json()
    assert d["inventory_status"] == "approved"
    elec = next(x for x in d["details"] if x["source_code"] == "S2-ELEC-001")
    assert elec["emission"] == pytest.approx(elec["activity_value"] * elec["factor"], abs=1e-4)


# ── 分摊账单（FR-8.2）──

def test_03_allocation_2025(flow_client):
    c = flow_client
    login(c, "mock-manager")
    assert c.post("/api/customers/revenue",
                  json={"year": 2025, "customer_code": "BOSCH", "revenue": 2100}).status_code == 200
    assert c.post("/api/customers/revenue",
                  json={"year": 2025, "customer_code": "BD", "revenue": 320}).status_code == 200
    r = c.get("/api/allocation", params={"year": 2025})
    assert r.status_code == 200, r.text
    a = r.json()
    bosch = next(i for i in a["items"] if i["customer_code"] == "BOSCH")
    assert abs(bosch["allocated"] - a["total_emission"] * 2100 / 6800) < 1e-6


# ── Excel 导入五步流（FR-4）──

def _carbon_import_file() -> bytes:
    """构造碳活动导入文件：2 条合法 + 3 类错误（年度非法/科目非法/数值负）。"""
    wb = Workbook()
    ws = wb.active
    ws.append(["盘查年度", "科目编码", "科目名称", "活动数据", "备注"])
    ws.append([2025, "S2-ELEC-001", "示例", 3500000, "示例行"])
    ws.append([2026, "S3-MILEAGE", "", 200000, "合法行1"])        # 行3 合法
    ws.append([2026, "S1-CO2-001", "", 5, "合法行2"])             # 行4 合法
    ws.append([199, "S3-MILEAGE", "", 100, ""])                   # 行5 年度非法
    ws.append([2026, "BAD-CODE", "", 100, ""])                    # 行6 科目非法
    ws.append([2026, "S3-MILEAGE", "", -5, ""])                   # 行7 数值负
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_04_import_five_steps(flow_client):
    c = flow_client
    login(c, "mock-manager")
    # ① 模板下载
    r = c.get("/api/import/template", params={"type": "carbon"})
    assert r.status_code == 200 and len(r.content) > 1000
    # ② 上传解析校验
    files = {"file": ("碳活动.xlsx", _carbon_import_file(),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r = c.post("/api/import/upload", params={"biz_type": "carbon", "strategy": "skip"},
               files=files)
    assert r.status_code == 200, r.text
    preview = r.json()
    assert preview["new"] == 2 and preview["errors"] == 3
    reasons = "；".join(e["reason"] for e in preview["error_detail"])
    assert "年度" in reasons and "科目编码" in reasons and "活动数据" in reasons
    # ③ 错误标注文件可下载
    r = c.get(f"/api/import/tasks/{preview['task_id']}/errors")
    assert r.status_code == 200 and len(r.content) > 1000
    # ④ 确认入库
    r = c.post("/api/import/confirm", json={"task_id": preview["task_id"]})
    assert r.status_code == 200 and r.json()["added"] == 2
    # ⑤ 幂等：重复任务不可二次确认
    assert c.post("/api/import/confirm", json={"task_id": preview["task_id"]}).status_code == 400
    # 重复上传 + skip 策略 → 全部跳过，不产生重复数据
    r = c.post("/api/import/upload", params={"biz_type": "carbon", "strategy": "skip"},
               files=files)
    task2 = r.json()["task_id"]
    r = c.post("/api/import/confirm", json={"task_id": task2})
    assert r.json()["added"] == 0 and r.json()["skipped"] == 2


# ── 导出（FR-9）──

def test_05_export_excel_pdf(flow_client):
    c = flow_client
    login(c, "mock-viewer")
    r = c.get("/api/export/excel", params={"year": 2025})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    r = c.get("/api/export/pdf", params={"year": 2025})
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"
    r = c.get("/api/export/logs")
    logs = r.json()
    assert len(logs) >= 2 and all(l["downloadable"] for l in logs)
    # 再下载
    r = c.get(f"/api/export/file/{logs[0]['id']}")
    assert r.status_code == 200


# ── 工作台与分析 ──

def test_06_dashboard_and_analysis(flow_client):
    c = flow_client
    login(c, "mock-viewer")
    r = c.get("/api/analysis/dashboard", params={"year": 2025})
    assert r.status_code == 200
    assert r.json()["kpi"]["carbon_total"] > 2000
    r = c.get("/api/analysis/carbon", params={"year": 2025})
    assert r.status_code == 200 and r.json()["overview"]["total"] > 0
    r = c.get("/api/analysis/energy", params={"year": 2025})
    assert r.status_code == 200
    # 溯源（test_02 已把电力活动值改为 3,400,000）
    r = c.get("/api/analysis/trace", params={"year": 2025, "source_code": "S2-ELEC-001"})
    assert r.status_code == 200 and r.json()["activity"]["activity_value"] == 3400000


# ── 备份与恢复（FR-10.4）──

def test_07_backup_restore(flow_client):
    c = flow_client
    login(c, "mock-admin")
    r = c.post("/api/sys/backup")
    assert r.status_code == 200, r.text
    fname = r.json()["file"]
    r = c.get(f"/api/sys/backups/{fname}/download")
    assert r.status_code == 200 and r.content[:15] == b"SQLite format 3"
    # 恢复：先校验（不确认）→ 二次确认 → 恢复
    files = {"file": (fname, r.content, "application/octet-stream")}
    r = c.post("/api/sys/restore", files=files)
    assert r.status_code == 200 and r.json()["need_confirm"] is True
    r = c.post("/api/sys/restore", params={"confirm": "true"}, files=files)
    assert r.status_code == 200 and r.json()["ok"] is True
    # 恢复后数据完整
    r = c.get("/api/carbon/activities", params={"year": 2025})
    assert r.json()["total"] > 2000
