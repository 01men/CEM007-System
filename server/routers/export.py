"""报表中心路由（FR-9）：Excel 总账 / PDF 披露报告导出与导出记录。"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from .. import audit, auth, config, db, excel_io, pdf_report

router = APIRouter(prefix="/api/export", tags=["报表中心"])


def _save_export(file_type: str, year: int, content: bytes, suffix: str,
                 user, request: Request) -> tuple[str, int]:
    fname = f"JUJIE_{file_type}_{year}_{datetime.now():%Y%m%d%H%M%S}.{suffix}"
    path = config.FILES_DIR / fname
    path.write_bytes(content)
    eid = db.execute(
        "INSERT INTO export_log(file_type,year,file_path,file_size,created_at,created_by)"
        " VALUES(?,?,?,?,?,?)",
        (file_type, year, fname, len(content), db.now_iso(), user["name"]))
    audit.log_op(user, f"export:{file_type}", "export_log", eid, fname, request)
    return fname, eid


@router.get("/excel")
def export_excel(year: int, request: Request, user=Depends(auth.get_current_user)):
    """Excel 活动总账导出（FR-9.1）。"""
    content = excel_io.build_ledger_excel(year)
    fname, _ = _save_export("excel", year, content, "xlsx", user, request)
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"})


@router.get("/pdf")
def export_pdf(year: int, request: Request, user=Depends(auth.get_current_user)):
    """PDF 披露报告导出（FR-9.2，仅已审定年度）。"""
    try:
        content = pdf_report.build_pdf_report(year)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    fname, _ = _save_export("pdf", year, content, "pdf", user, request)
    return Response(content, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


@router.get("/logs")
def export_logs(user=Depends(auth.get_current_user)):
    """导出记录（FR-9.3）：标记是否仍在 30 天可下载期内。"""
    rows = []
    for r in db.query("SELECT * FROM export_log ORDER BY id DESC LIMIT 100"):
        d = dict(r)
        created = datetime.strptime(r["created_at"], "%Y-%m-%d %H:%M:%S")
        d["downloadable"] = datetime.now() - created < timedelta(days=config.EXPORT_KEEP_DAYS)
        rows.append(d)
    return rows


@router.get("/file/{eid}")
def download_file(eid: int, user=Depends(auth.get_current_user)):
    r = db.query_one("SELECT * FROM export_log WHERE id=?", (eid,))
    if not r:
        raise HTTPException(404, "导出记录不存在")
    created = datetime.strptime(r["created_at"], "%Y-%m-%d %H:%M:%S")
    if datetime.now() - created >= timedelta(days=config.EXPORT_KEEP_DAYS):
        raise HTTPException(410, "导出文件已超过 30 天保留期，请重新导出")
    path = config.FILES_DIR / r["file_path"]
    if not path.exists():
        raise HTTPException(404, "文件已被清理")
    media = ("application/pdf" if r["file_type"] == "pdf" else
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return Response(path.read_bytes(), media_type=media,
                    headers={"Content-Disposition": f"attachment; filename={r['file_path']}"})
