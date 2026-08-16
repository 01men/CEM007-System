"""数据导入路由（FR-4）：模板下载、上传解析预览、确认入库、任务与错误报告。"""
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from .. import audit, auth, config, db, excel_io

router = APIRouter(prefix="/api/import", tags=["数据导入"])

ROLE_BY_TYPE = {"energy": ("admin", "manager", "reporter"),
                "carbon": ("admin", "manager", "reporter"),
                "factor": ("admin",)}


@router.get("/template")
def download_template(type: str, user=Depends(auth.get_current_user)):
    if type not in excel_io.BIZ_TYPES:
        raise HTTPException(400, f"模板类型须为 {'/'.join(excel_io.BIZ_TYPES)}")
    if user["role_code"] not in ROLE_BY_TYPE[type]:
        raise HTTPException(403, "无该模板下载权限（因子模板仅管理员）")
    content = excel_io.build_template(type)
    names = {"energy": "能耗台账导入模板", "carbon": "碳活动数据导入模板", "factor": "排放因子导入模板"}
    fname = f"{names[type]}_{datetime.now():%Y%m%d}.xlsx"
    return Response(
        content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


@router.post("/upload")
async def upload(file: UploadFile, biz_type: str, strategy: str = "skip",
                 request: Request = None, user=Depends(auth.get_current_user)):
    """上传 → 解析 → 校验 → 预览（第五步确认入库见 /confirm）。"""
    if biz_type not in excel_io.BIZ_TYPES:
        raise HTTPException(400, "业务类型非法")
    if user["role_code"] not in ROLE_BY_TYPE[biz_type]:
        raise HTTPException(403, "无该业务导入权限")
    if strategy not in ("skip", "overwrite"):
        raise HTTPException(400, "冲突策略须为 skip 或 overwrite")
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "仅接受 .xlsx 文件")
    content = await file.read()
    if len(content) > config.IMPORT_MAX_BYTES:
        raise HTTPException(400, "文件超过 10MB 限制")
    try:
        result = excel_io.parse_and_validate(biz_type, content)
    except Exception as exc:
        raise HTTPException(400, f"文件解析失败：{exc}")

    # 冲突预判：合法行中已存在记录的数量
    rows, errors = result["rows"], result["errors"]
    conflict = _count_conflicts(biz_type, rows)
    error_file = None
    if errors:
        error_file = f"errors_{datetime.now():%Y%m%d%H%M%S}.xlsx"
        (config.FILES_DIR / error_file).write_bytes(excel_io.build_error_file(biz_type, errors))

    task_id = db.execute(
        "INSERT INTO import_task(biz_type,file_name,strategy,total,success,failed,status,"
        "error_file,created_at,created_by) VALUES(?,?,?,?,?,?, 'parsed',?,?,?)",
        (biz_type, file.filename, strategy, len(rows) + len(errors), 0,
         len(errors), error_file, db.now_iso(), user["name"]))
    excel_io.save_staging(task_id, {"biz_type": biz_type, "rows": rows})
    audit.log_op(user, "import:upload", "import_task", task_id,
                 f"{file.filename} 合法{len(rows)} 错误{len(errors)}", request)
    return {"task_id": task_id, "new": len(rows) - conflict, "conflict": conflict,
            "errors": len(errors), "error_detail": errors[:20],
            "error_file": error_file}


def _count_conflicts(biz_type: str, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        if biz_type == "energy":
            dup = db.query_one(
                "SELECT 1 FROM energy_record WHERE year=? AND month=? AND energy_type_code=?"
                " AND unit_id=?", (r["year"], r["month"], r["energy_type_code"], r["unit_id"]))
        elif biz_type == "carbon":
            dup = db.query_one(
                "SELECT 1 FROM carbon_activity WHERE year=? AND source_code=?",
                (r["year"], r["source_code"]))
        else:
            from .carbon import _factor_ranges_overlap
            dup = _factor_ranges_overlap(r["source_code"], r["year_from"], r["year_to"])
        if dup:
            n += 1
    return n


class ConfirmIn(BaseModel):
    task_id: int


@router.post("/confirm")
def confirm(body: ConfirmIn, request: Request, user=Depends(auth.get_current_user)):
    """确认入库：跳过错误行，合法行按策略写入（幂等，FR-4.2）。"""
    task = db.query_one("SELECT * FROM import_task WHERE id=?", (body.task_id,))
    if not task:
        raise HTTPException(404, "导入任务不存在")
    if task["status"] != "parsed":
        raise HTTPException(400, f"任务状态 {task['status']} 不可确认（防止重复入库）")
    staging = excel_io.load_staging(task["id"])
    result = excel_io.apply_import(task["biz_type"], staging["rows"], task["strategy"], user)
    success = result["added"] + result["overwritten"]
    db.execute("UPDATE import_task SET success=?, status='confirmed', updated_at=? WHERE id=?",
               (success, db.now_iso(), task["id"]))
    audit.log_op(user, "import:confirm", "import_task", task["id"],
                 f"新增{result['added']} 覆盖{result['overwritten']} 跳过{result['skipped']}",
                 request)
    return {"ok": True, **result}


@router.get("/tasks")
def list_tasks(user=Depends(auth.get_current_user)):
    return [dict(r) for r in db.query("SELECT * FROM import_task ORDER BY id DESC LIMIT 100")]


@router.get("/tasks/{task_id}/errors")
def download_errors(task_id: int, user=Depends(auth.get_current_user)):
    task = db.query_one("SELECT * FROM import_task WHERE id=?", (task_id,))
    if not task or not task["error_file"]:
        raise HTTPException(404, "该任务无错误标注文件")
    path = config.FILES_DIR / task["error_file"]
    if not path.exists():
        raise HTTPException(404, "错误文件已被清理")
    return Response(
        path.read_bytes(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={task['error_file']}"})
