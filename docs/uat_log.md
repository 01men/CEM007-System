# 故事一：行政部填报员 —— 月度台账录入与审核闭环

**1. 钉钉扫码（Mock）登录**
```
HTTP 302 → 302 跳工作台，Cookie 已签发
```

**2. 获取当前用户**
```
HTTP 200
{
 "name": "行政填报员",
 "role_code": "reporter",
 "dept_name": "行政部"
}
```

**3. 录入 1 月电力台账（含总表/外租/光伏拆分）**
```
HTTP 200
{
 "ok": true,
 "id": 1
}
```

**4. 同月重复录入 → 唯一性拦截**
```
HTTP 409
{
 "detail": "该年月+能源类型+单元已存在记录，确认覆盖请重试（overwrite=true）"
}
```

**5. 提交审核**
```
HTTP 200
{
 "ok": true
}
```

**6. 能碳管理员驳回但不填原因 → 拦截**
```
HTTP 400
{
 "detail": "驳回必须填写驳回原因"
}
```

**7. 填写原因后驳回成功**
```
HTTP 200
{
 "ok": true
}
```

**8. 填报员按驳回意见修改**
```
HTTP 200
{
 "ok": true
}
```

**9. 再提交 → 审核通过并锁定**
```
HTTP 200
{
 "ok": true
}
```

**10. 锁定后填报员再编辑 → 403**
```
HTTP 403
{
 "detail": "记录已审核锁定，如需修改请联系能碳管理员解锁"
}
```

# 故事二：能碳管理员 —— 2025 年度盘查与披露报告

**1. 下载碳活动导入模板**
```
HTTP 200，8100 字节 xlsx
```

**2. 上传导入（含 2 行故意错误）→ 预览**
```
HTTP 200
{
 "new": 10,
 "conflict": 0,
 "errors": 2,
 "error_detail": [
  {
   "row_no": 13,
   "reason": "科目编码 'BAD-CODE' 不存在或已停用",
   "raw": [
    2025,
    "BAD-CODE",
    null,
    100,
    "故意的错误行1"
   ]
  },
  {
   "row_no": 14,
   "reason": "活动数据非法（须为 ≥0 的数值）",
   "raw": [
    2025,
    "S3-MILEAGE",
    null,
    -1,
    "故意的错误行2"
   ]
  }
 ]
}
```

**3. 确认入库（错误行自动跳过）**
```
HTTP 200
{
 "ok": true,
 "added": 10,
 "overwritten": 0,
 "skipped": 0
}
```

**4. 手工补齐私车公用里程 + 录入总产值**
```
HTTP 200
{
 "ok": true,
 "saved": 1
}
```

**5. 实时核算结果**
```
HTTP 200
{
 "total": 3030.255159,
 "groups": {
  "范围一": 343.195479,
  "范围二": 2391.9,
  "范围三": 295.15968
 },
 "intensity": 0.445626,
 "inventory_status": "draft"
}
```

**6. 提交盘查 → 审定（因子快照封存）**
```
HTTP 200
{
 "ok": true,
 "total": 3030.255159
}
```

**7. 生成 2025 分摊账单**
```
HTTP 200
{
 "total_emission": 3030.255159,
 "items": [
  {
   "customer_code": "BOSCH",
   "name_zh": "博世",
   "name_en": "Bosch",
   "revenue": 2100.0,
   "share": 0.308824,
   "allocated": 935.814093,
   "intensity": 0.445626
  },
  {
   "customer_code": "BD",
   "name_zh": "百得",
   "name_en": "Black & Decker",
   "revenue": 320.0,
   "share": 0.047059,
   "allocated": 142.600243,
   "intensity": 0.445626
  }
 ],
 "warning": null
}
```

**8. 导出 PDF 披露报告**
```
HTTP 200，58071 字节，文件头 b'%PDF-'
```

# 故事三：管理层 —— 经营视角一屏总览

**1. 工作台 KPI 与待办**
```
HTTP 200
{
 "kpi": {
  "energy_tce": 36.0097,
  "energy_cost": 210000.0,
  "carbon_total": 3030.255159,
  "intensity": 0.445626,
  "target_rate": null,
  "inventory_status": "approved"
 },
 "todos": [
  {
   "type": "import",
   "text": "最近导入任务 2025碳活动.xlsx 有 2 条错误"
  }
 ]
}
```

**2. 碳排分析（同比 2024 基准）**
```
HTTP 200
{
 "year": 2025
}
overview: {"total": 3030.255159, "groups": {"范围一": 343.195479, "范围二": 2391.9, "范围三": 295.15968}, "green_reduction": 0.0, "pv_quantity": 0, "intensity": 0.445626, "total_revenue": 6800.0, "yoy": {"total": 3151.318549, "rate": -3.84}}
```

**3. 查看博世年度碳账单**
```
{
 "customer_code": "BOSCH",
 "name_zh": "博世",
 "name_en": "Bosch",
 "revenue": 2100.0,
 "share": 0.308824,
 "allocated": 935.814093,
 "intensity": 0.445626
}
```

**4. 导出 Excel 活动总账**
```
HTTP 200，10000 字节，文件头 b'PK'
```

**5. 管理层尝试录入数据 → 403 只读拦截**
```
HTTP 403
{
 "detail": "需要角色权限：admin/manager/reporter"
}
```
