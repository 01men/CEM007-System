# 聚杰电器·能耗与碳排管理系统

企业内网私有化部署的"能耗 + 碳排"一体化管理平台（FastAPI + SQLite + 原生 SPA）。

## 快速开始

```
Windows: 双击 scripts\start.bat      →  http://localhost:8080
Linux:   bash scripts/start.sh
```

- 演示登录：登录页点"钉钉扫码登录" → Mock 扫码页任选角色（`DINGTALK_MOCK=0` 时为真实钉钉扫码）
- 管理员兜底：系统管理员 / admin123（上线后立即改密）
- 接口文档：http://localhost:8080/api/docs
- 真实钉钉：`JUJIE_PUBLIC_ORIGIN=http://服务器IP:8080` 必须与钉钉开放平台应用配置的回调域名一致

## 目录结构

```
server/          后端（FastAPI）
  main.py        应用入口（路由装配、静态托管、每日备份线程）
  config.py      路径/端口/Mock 开关等全局配置
  db.py          SQLite 连接（WAL）+ DDL + 写锁串行化
  calc.py        核算引擎（排放/分摊/映射/绿电，所有计算口径在此）
  auth.py        会话（签名 Cookie 8h 滑动续期）、RBAC、口令哈希
  seeds.py       种子数据（附录 A/B、2024 基准，含全部因子与填报指南）
  excel_io.py    三类 Excel 模板生成/解析校验/错误标注/总账导出
  pdf_report.py  PDF 披露报告（reportlab + 内置 simhei）
  backup.py      备份/恢复
  routers/       API 路由（auth/energy/carbon/analysis/targets/allocation/import_excel/export/admin）
web/             前端 SPA（原生 JS + 本地 ECharts）
tests/           三轮测试（pytest，35 用例）
docs/            阶段文档 01-PRD评审 ~ 05-结项报告
scripts/         一键启动、UAT 驱动脚本
data/            运行时生成（数据库/文件/备份/密钥）
```

## Docker 部署（试运行/生产）

```
pip install paramiko                                  # 部署脚本依赖（仅需一次）
# 生成数据快照：.venv/Scripts/python 执行 SQLite backup → deploy_staging/data（见 scripts/deploy_docker.py 报错提示）
.venv/Scripts/python scripts/deploy_docker.py         # 打包→上传→远端 docker build/run→健康检查
```

- 默认目标 192.168.0.7，可用 `JUJIE_DEPLOY_HOST/USER/PASS` 环境变量覆盖
- 默认以真实钉钉模式部署（`DINGTALK_MOCK=0`）并注入 `JUJIE_PUBLIC_ORIGIN`；无凭据演示时 `DINGTALK_MOCK=1 .venv/Scripts/python scripts/deploy_docker.py`
- 目标机手动方式：`docker compose up -d --build`（根目录已附 Dockerfile / docker-compose.yml）
- 数据持久化在 named volume `jujie-data`（首次启动自动注入镜像内测试数据）

## 常用命令

```
.venv/Scripts/python -m pytest tests/ -v     # 跑全部测试（35 passed）
.venv/Scripts/python scripts/uat_drive.py    # 重新生成 UAT 实录 docs/uat_log.md
.venv/Scripts/python scripts/gen_mock_data.py [--force]   # 生成 1000 条演示台账（mock-gen，--force 清掉重建）
```

## 核心口径（改动前必读）

- 排放量 = 活动数据 × 因子；**审定年度用封存快照**（factor_snapshot），历史不可被因子变更污染
- 台账映射：科目活动值 = Σ(已审核台账) × map_convert（天然气 m³→L 为 ×1000，LPG kg→L 为 ×1.8519）
- 绿电只扣一次：净电量 = 总表 − 外租 − 光伏，在台账层完成；光伏减碳量单独展示
- 分摊：客户分摊 = 企业总排放 × 客户营收 / 总产值（仅已审定年度）
- 详细设计见 `docs/02-架构设计.md`，评审修正见 `docs/01-PRD评审.md`
