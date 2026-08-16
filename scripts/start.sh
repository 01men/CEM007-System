#!/usr/bin/env bash
# 聚杰电器·能耗与碳排管理系统 一键启动（Linux/macOS）
# 生产部署建议使用 systemd（见 docs/05-结项报告.md 部署指南）
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  echo "[1/3] 创建虚拟环境..."
  python3 -m venv .venv
  echo "[2/3] 安装依赖..."
  .venv/bin/pip install -r requirements.txt
else
  echo "[1/2] 虚拟环境已存在，跳过安装"
fi

# 钉钉 Mock 模式：无钉钉凭据时保持 1；上线真实钉钉时改为 0
export DINGTALK_MOCK=${DINGTALK_MOCK:-1}

echo "[启动] http://localhost:8080"
exec .venv/bin/python -m uvicorn server.main:app --host 0.0.0.0 --port 8080
