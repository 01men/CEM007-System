"""全局配置：路径、端口、安全与钉钉 Mock 开关。

约定：所有路径相对项目根目录（V2/），运行时目录自动创建。
"""
import os
from pathlib import Path

# ── 目录规划（对齐 PRD 5.4）──
BASE_DIR = Path(__file__).resolve().parent.parent          # V2/
DATA_DIR = BASE_DIR / "data"
FILES_DIR = DATA_DIR / "files"                             # 导入/导出文件
BACKUP_DIR = DATA_DIR / "backup"                           # 每日备份
LOG_DIR = BASE_DIR / "logs"
DB_PATH = DATA_DIR / "jujie_energy.db"
WEB_DIR = BASE_DIR / "web"
FONT_PATH = WEB_DIR / "vendor" / "simhei.ttf"              # PDF 中文字体
SECRET_KEY_PATH = DATA_DIR / "secret.key"                  # 会话签名/加密密钥

for _d in (DATA_DIR, FILES_DIR, BACKUP_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── 服务 ──
HOST = os.getenv("JUJIE_HOST", "0.0.0.0")
PORT = int(os.getenv("JUJIE_PORT", "8080"))

# ── 会话 ──
SESSION_COOKIE = "jujie_session"
SESSION_TTL_SECONDS = 8 * 3600          # 8 小时滑动续期（PRD FR-1.3）

# ── 钉钉 ──
# DINGTALK_MOCK=1 时不访问钉钉接口，扫码页改为本地模拟页，便于无凭据环境演示/UAT
DINGTALK_MOCK = os.getenv("DINGTALK_MOCK", "1") == "1"
# 钉钉 OAuth2 回调绝对地址的源（如 http://192.168.0.7:8080）；
# 反代/端口转发场景必须设置，否则按请求 Host 头推导可能丢端口
PUBLIC_ORIGIN = os.getenv("JUJIE_PUBLIC_ORIGIN", "")

# ── 导入限制 ──
IMPORT_MAX_BYTES = 10 * 1024 * 1024     # 单文件 ≤10MB（PRD FR-4.2）

# ── 备份 ──
BACKUP_KEEP = 30                        # 保留最近 30 份（PRD FR-10.4）
EXPORT_KEEP_DAYS = 30                   # 导出文件 30 天内可再下载（PRD FR-9.3）

BASE_YEAR = 2025                        # 基准年（2025年聚杰减碳项目总表）
