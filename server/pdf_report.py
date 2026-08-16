"""PDF 披露报告生成（FR-9.2）：reportlab + 内置中文字体，零系统依赖。

版式对齐原型：封面 → 核心绩效摘要 → 全口径排放源明细 → 历年趋势 →
大客户供应链分摊明细 → 编制/审核签字区；页脚含核算边界说明与报告编号。
仅允许导出已审定年度。
"""
from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from . import calc, config, db

_FONT_REGISTERED = False


def _register_font() -> str:
    global _FONT_REGISTERED
    if not _FONT_REGISTERED:
        pdfmetrics.registerFont(TTFont("SimHei", str(config.FONT_PATH)))
        _FONT_REGISTERED = True
    return "SimHei"


def _styles(font: str) -> dict:
    return {
        "title": ParagraphStyle("title", fontName=font, fontSize=20, leading=28,
                                alignment=1, textColor=colors.HexColor("#0a0f1a")),
        "sub": ParagraphStyle("sub", fontName=font, fontSize=10, leading=16,
                              alignment=1, textColor=colors.HexColor("#64748b")),
        "h": ParagraphStyle("h", fontName=font, fontSize=13, leading=20,
                            textColor=colors.HexColor("#e85d3a"),
                            spaceBefore=10, spaceAfter=6),
        "cell": ParagraphStyle("cell", fontName=font, fontSize=8, leading=11),
        "note": ParagraphStyle("note", fontName=font, fontSize=8, leading=12,
                               textColor=colors.HexColor("#64748b")),
    }


def _table(data: list[list], font: str, col_widths=None, header=True) -> Table:
    wrapped = [[Paragraph(str(c), ParagraphStyle("c", fontName=font, fontSize=8,
                                                 leading=11)) for c in row] for row in data]
    t = Table(wrapped, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")))
    t.setStyle(TableStyle(style))
    return t


def build_pdf_report(year: int) -> bytes:
    """生成年度碳披露报告 PDF。数据仅取自已审定年度（FR-9.2）。"""
    inv = db.query_one("SELECT * FROM carbon_inventory WHERE year=?", (year,))
    if not inv or inv["status"] != "approved":
        raise ValueError(f"{year} 年度盘查未审定，不能导出披露报告")
    font = _register_font()
    st = _styles(font)
    result = calc.compute_year(year)
    base = calc.compute_year(config.BASE_YEAR)
    alloc = calc.compute_allocation(year)

    delta = ((result["total"] - base["total"]) / base["total"] * 100) if base["total"] else 0
    improved = delta <= 0

    buf = BytesIO()
    report_no = f"JUJIE-GHG-{year}-{datetime.now():%Y%m%d}"
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=20 * mm, bottomMargin=18 * mm,
                            title=f"温室气体排放披露报告 {year}",
                            author="金华聚杰电器有限公司")
    story = []

    # ── 封面 ──
    story.append(Spacer(1, 60 * mm))
    story.append(Paragraph("金华聚杰电器有限公司", st["title"]))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(f"{year} 年度温室气体排放披露报告", st["title"]))
    story.append(Spacer(1, 12 * mm))
    story.append(Paragraph(
        f"报告编号：{report_no}<br/>签发日期：{datetime.now():%Y-%m-%d}<br/>"
        "核算标准：GHG Protocol Corporate Standard（运营控制权法）", st["sub"]))
    story.append(Spacer(1, 40 * mm))

    # ── 核心绩效摘要 ──
    story.append(Paragraph("一、核心绩效摘要", st["h"]))
    sign = "▼" if improved else "▲"
    g = result["groups"]
    story.append(_table([
        ["盘查核心维度指标", f"{config.BASE_YEAR} 基准年", f"{year} 盘查年", "同比变化"],
        ["碳排总量 (tCO₂e)", f"{base['total']:.1f}", f"{result['total']:.1f}",
         f"{sign} {abs(delta):.1f}%"],
        ["范围一 直接排放 (tCO₂e)", f"{base['groups']['范围一']:.1f}", f"{g['范围一']:.1f}", "—"],
        ["范围二 外购电力 (tCO₂e)", f"{base['groups']['范围二']:.1f}", f"{g['范围二']:.1f}", "—"],
        ["范围三 价值链 (tCO₂e)", f"{base['groups']['范围三']:.1f}", f"{g['范围三']:.1f}", "—"],
        ["年度总产值 (万美金)", f"{base['total_revenue']:.0f}", f"{result['total_revenue']:.0f}", "—"],
        ["产值碳强度 (tCO₂e/万美金)", f"{base['intensity']:.4f}", f"{result['intensity']:.4f}", "—"],
        ["绿电减碳量 (tCO₂e)", "—", f"{result['green']['pv_reduction']:.2f}",
         f"光伏自用 {result['green']['pv_quantity']:.0f} kWh"],
    ], font, col_widths=[62 * mm, 36 * mm, 36 * mm, 40 * mm]))

    # ── 排放源明细 ──
    story.append(Paragraph("二、全口径排放源明细", st["h"]))
    rows = [["范围", "排放源", "责任部门", "活动数据", "单位", "排放因子",
             "排放量(tCO₂e)", "系数来源"]]
    for d in sorted(result["details"], key=lambda x: -x["emission"]):
        rows.append([d["scope"], d["name_zh"], d["dept_code"] or "",
                     f"{d['activity_value']:,.2f}", d["unit"], f"{d['factor']:.8f}".rstrip("0"),
                     f"{d['emission']:.3f}", (d["factor_ref"] or "")[:40]])
    rows.append(["合计", "", "", "", "", "", f"{result['total']:.3f}", ""])
    story.append(_table(rows, font, col_widths=[14 * mm, 30 * mm, 16 * mm, 24 * mm,
                                                14 * mm, 20 * mm, 22 * mm, 34 * mm]))
    if result["warnings"]:
        story.append(Spacer(1, 2 * mm))
        for w in result["warnings"]:
            story.append(Paragraph(f"※ {w}", st["note"]))

    # ── 历年趋势 ──
    story.append(Paragraph("三、历年排放趋势", st["h"]))
    rows = [["盘查年度", "范围一", "范围二", "范围三", "总量 (tCO₂e)", "碳强度", "状态"]]
    for s in calc.year_summary():
        rows.append([s["year"], f"{s['groups']['范围一']:.1f}", f"{s['groups']['范围二']:.1f}",
                     f"{s['groups']['范围三']:.1f}", f"{s['total']:.1f}",
                     f"{s['intensity']:.4f}", s["status"]])
    story.append(_table(rows, font))

    # ── 供应链分摊 ──
    story.append(Paragraph("四、大客户供应链碳分摊明细", st["h"]))
    rows = [["客户", "年度营收(万美金)", "营收占比", "分摊排放量(tCO₂e)", "分摊碳强度"]]
    for item in alloc["items"]:
        rows.append([f"{item['name_zh']} ({item['name_en'] or item['customer_code']})",
                     f"{item['revenue']:,.0f}", f"{item['share']*100:.2f}%",
                     f"{item['allocated']:.2f}", f"{item['intensity']:.4f}"])
    story.append(_table(rows, font))
    if alloc["warning"]:
        story.append(Paragraph(f"⚠ {alloc['warning']}", st["note"]))
    story.append(Paragraph(
        "分摊口径：客户分摊排放量 = 企业年度总排放 ×（该客户年度营收 ÷ 企业年度总产值），"
        "依据 GHG Protocol 范围三类别 1/11 的支出比例法简化实现。", st["note"]))

    # ── 签字区 ──
    story.append(Spacer(1, 16 * mm))
    story.append(_table([
        ["编制人（签字）：_________________", "审核人（签字）：_________________"],
        ["日期：______年____月____日", "日期：______年____月____日"],
    ], font, header=False))
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(
        f"核算边界说明：本报告采用运营控制权法，覆盖范围一（直接排放）、范围二（外购电力）"
        f"与范围三（成品运输、私车公用差旅）共 11 项排放源。报告编号 {report_no}。"
        "「厂区总工时」科目为企业自定义经验折算项，非 GHG Protocol 标准科目。", st["note"]))

    doc.build(story)
    return buf.getvalue()
