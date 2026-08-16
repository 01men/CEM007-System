"""FastAPI 应用入口：装配路由、静态托管、启动初始化、每日备份调度。

启动：python -m uvicorn server.main:app --host 0.0.0.0 --port 8080
"""
import logging
import threading
import time
from logging.handlers import TimedRotatingFileHandler

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth, backup, config, db, seeds
from .routers import admin, allocation, analysis, auth as auth_router
from .routers import carbon, energy, export, import_excel, targets


def _setup_logging() -> None:
    handler = TimedRotatingFileHandler(
        config.LOG_DIR / "app.log", when="midnight", backupCount=90, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _daily_backup_loop() -> None:
    """每日凌晨自动备份（FR-10.4）。轻量实现：后台线程每小时检查一次。"""
    last_day = None
    while True:
        now = time.localtime()
        if now.tm_hour == 2 and now.tm_mday != last_day:
            try:
                backup.do_backup()
                logging.info("每日自动备份完成")
            except Exception:
                logging.exception("每日自动备份失败")
            last_day = now.tm_mday
        time.sleep(1800)


def create_app() -> FastAPI:
    _setup_logging()
    db.configure()
    db.init_db()
    seeds.seed_all(auth.hash_password("admin123"))  # 幂等；admin 初始口令 admin123

    app = FastAPI(title="聚杰电器·能耗与碳排管理系统", version="1.0.0",
                  docs_url="/api/docs", openapi_url="/api/openapi.json")

    for r in (auth_router.router, energy.router, carbon.router, analysis.router,
              targets.router, allocation.router, import_excel.router, export.router,
              admin.router):
        app.include_router(r)

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/index.html", 302)

    # SPA 静态托管（登录页/待开通页/Mock 扫码页均在 web/ 下）
    app.mount("/", StaticFiles(directory=str(config.WEB_DIR), html=True), name="web")

    threading.Thread(target=_daily_backup_loop, daemon=True).start()
    return app


app = create_app()
