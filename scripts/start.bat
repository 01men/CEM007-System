@echo off
REM 聚杰电器·能耗与碳排管理系统 一键启动（Windows）
REM 用法：双击运行，或命令行执行 scripts\start.bat
cd /d "%~dp0\.."

if not exist .venv\Scripts\python.exe (
  echo [1/3] 创建虚拟环境...
  python -m venv .venv
  echo [2/3] 安装依赖...
  .venv\Scripts\python -m pip install -r requirements.txt
) else (
  echo [1/2] 虚拟环境已存在，跳过安装
)

REM 钉钉 Mock 模式：无钉钉凭据时保持 1（模拟扫码）；上线真实钉钉时改为 0 并在系统管理页配置
set DINGTALK_MOCK=1

echo [启动] http://localhost:8080  （局域网访问 http://本机IP:8080）
echo 演示账号：Mock 扫码页任选；管理员兜底 系统管理员 / admin123
.venv\Scripts\python -m uvicorn server.main:app --host 0.0.0.0 --port 8080
pause
