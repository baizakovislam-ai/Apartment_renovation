"""Final report as a PDF document (ТЗ FR21). Built with ReportLab; DejaVu fonts give Cyrillic letters."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.workspace.templatetags.workspace_ui import display_name, money, normalize, signed_money

FONTS = Path(__file__).resolve().parent / "fonts"
INK, MUTED, LINE, SOFT, BRAND = (colors.HexColor(code) for code in ("#0d1729", "#64748b", "#d7deea", "#f3f6fb", "#1d4ed8"))


def register_fonts():
    if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVu", str(FONTS / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONTS / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold", italic="DejaVu", boldItalic="DejaVu-Bold")


STYLES = {
    "title": ParagraphStyle("title", fontName="DejaVu-Bold", fontSize=18, leading=22, textColor=INK, spaceAfter=2 * mm),
    "h2": ParagraphStyle("h2", fontName="DejaVu-Bold", fontSize=12.5, leading=16, textColor=BRAND, spaceBefore=6 * mm, spaceAfter=2.5 * mm),
    "body": ParagraphStyle("body", fontName="DejaVu", fontSize=9.5, leading=13, textColor=INK),
    "muted": ParagraphStyle("muted", fontName="DejaVu", fontSize=8.5, leading=11.5, textColor=MUTED),
    "cell": ParagraphStyle("cell", fontName="DejaVu", fontSize=8.5, leading=11, textColor=INK),
    "num": ParagraphStyle("num", fontName="DejaVu", fontSize=8.5, leading=11, textColor=INK, alignment=TA_RIGHT),
    "head": ParagraphStyle("head", fontName="DejaVu-Bold", fontSize=7.5, leading=10, textColor=MUTED),
    "head-num": ParagraphStyle("head-num", fontName="DejaVu-Bold", fontSize=7.5, leading=10, textColor=MUTED, alignment=TA_RIGHT),
    "total": ParagraphStyle("total", fontName="DejaVu-Bold", fontSize=15, leading=19, textColor=INK),
}


def p(text, style="cell"):
    return Paragraph(escape(str(text if text not in (None, "") else "—")).replace("\n", "<br/>"), STYLES[style])


def date(value):
    return value.strftime("%d.%m.%Y") if value else "—"


def table(header, rows, widths, numeric=(), total=None):
    """A striped table; `numeric` are column indexes aligned right, `total` is an optional bold last row."""
    data = [[Paragraph(escape(title), STYLES["head-num" if index in numeric else "head"]) for index, title in enumerate(header)]]
    for row in rows:
        data.append([cell if isinstance(cell, Paragraph) or cell == ""
                     else p(cell, "num" if index in numeric else "cell") for index, cell in enumerate(row)])
    if total:
        data.append([Paragraph(f"<b>{escape(str(cell))}</b>", STYLES["num" if index in numeric else "cell"]) for index, cell in enumerate(total)])
    result = Table(data, colWidths=[width * mm for width in widths], repeatRows=1)
    style = [("LINEBELOW", (0, 0), (-1, 0), 0.8, INK), ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
             ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    style += [("BACKGROUND", (0, index), (-1, index), SOFT) for index in range(2, len(data), 2)]
    if total:
        style += [("LINEABOVE", (0, -1), (-1, -1), 0.8, INK), ("BACKGROUND", (0, -1), (-1, -1), colors.white)]
    result.setStyle(TableStyle(style))
    return result


def facts(pairs):
    """Two-column «label: value» block."""
    rows = [[p(label, "muted"), p(value, "body")] for label, value in pairs]
    result = Table(rows, colWidths=[45 * mm, 125 * mm])
    result.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return result


def section_project(report):
    project = report["project"]
    yield Paragraph("1. Информация о проекте", STYLES["h2"])
    yield facts([
        ("Адрес", project.address), ("Площадь", f"{normalize(project.area)} м²" if project.area else "—"),
        ("Статус", project.get_status_display()), ("Заказчик", display_name(report["customer"]) if report["customer"] else "—"),
        ("Прораб", display_name(report["foreman"]) if report["foreman"] else "—"),
        ("Исполнители", ", ".join(display_name(user) for user in report["executors"]) or "—"),
        ("Лимит бюджета", money(project.budget)), ("Готовность", f"{report['progress']['percent']} %"),
        ("Описание", project.description),
    ])


def section_stages(report):
    schedule = report["schedule"]
    yield Paragraph("2. Этапы и сроки", STYLES["h2"])
    yield facts([
        ("Плановые сроки", f"{date(schedule['planned_start'])} — {date(schedule['planned_end'])}"),
        ("Фактическое окончание", date(schedule["actual_end"]) if schedule["actual_end"] else "работы ещё не приняты полностью"),
        ("Отклонение от срока", (f"{schedule['days_late']:+d} дн." if schedule["days_late"] is not None else "—")),
        ("Этапов с нарушением срока", schedule["late_stages"]),
    ])
    yield Spacer(1, 3 * mm)
    rows = [[row["stage"].name, row["stage"].get_status_display(), display_name(row["stage"].responsible.user),
             f"{date(row['stage'].start_date)} —\n{date(row['stage'].end_date)}", date(row["accepted_at"]),
             f"{row['days_late']} дн." if row["days_late"] else "в срок", f"{row['progress']} %"] for row in report["stages"]]
    yield table(["Этап", "Статус", "Ответственный", "План", "Принят", "Просрочка", "Готово"], rows or [["Этапов нет", "", "", "", "", "", ""]],
                [36, 26, 27, 25, 22, 19, 15], numeric=(6,))


def section_estimate(report):
    estimate = report["estimate"]
    yield Paragraph("3. Смета", STYLES["h2"])
    approved = estimate["approved"]
    if approved is None:
        yield p("Утверждённой сметы нет.", "body")
        return
    yield p(f"Утверждённая смета, версия {approved.number} от {date(approved.decided_at)}. "
            f"Первоначальная стоимость: {money(estimate['initial_total'])}; одобренные изменения: {signed_money(estimate['changes_total'])}.", "body")
    yield Spacer(1, 3 * mm)
    rows = []
    for group in estimate["summary"]["groups"]:
        rows.append([Paragraph(f"<b>{escape(group['stage'].name)}</b>", STYLES["cell"]), "", "", "", "", money(group["total"])])
        rows += [[f"   {line.name}", line.get_cost_type_display(), line.unit, normalize(line.quantity), money(line.price), money(line.amount)]
                 for line in group["lines"]]
    yield table(["Позиция", "Вид затрат", "Ед.", "Кол-во", "Цена", "Сумма"], rows, [50, 25, 15, 16, 30, 34], numeric=(3, 4, 5),
                total=["Итого по смете", "", "", "", "", money(approved.total)])
    if estimate["changes"]:
        yield Spacer(1, 4 * mm)
        yield p("Одобренные изменения сметы", "head")
        yield Spacer(1, 1.5 * mm)
        rows = [[f"№{change.number}", change.reason, date(change.decided_at), display_name(change.decided_by),
                 signed_money(change.cost_delta)] for change in estimate["changes"]]
        yield table(["№", "Причина", "Одобрена", "Кем", "Изменение"], rows, [12, 78, 22, 28, 30], numeric=(4,))


def section_expenses(report):
    yield Paragraph("4. Расходы", STYLES["h2"])
    expenses = report["expenses"]
    if expenses is None:
        yield p("Учёт фактических расходов в системе пока не ведётся, поэтому итоговая стоимость указана по утверждённой смете "
                "с учётом одобренных изменений.", "body")
    elif not expenses:
        yield p("Подтверждённых расходов нет.", "body")
    else:
        rows = [[date(expense.date), expense.name, money(expense.amount)] for expense in expenses]
        yield table(["Дата", "Расход", "Сумма"], rows, [25, 115, 32], numeric=(2,))


def section_works(report):
    yield Paragraph("5. Выполненные работы", STYLES["h2"])
    progress = report["progress"]
    yield p(f"Принято этапов: {progress['stages_accepted']} из {progress['stages_total']}. Выполнено задач: {progress['tasks_done']} "
            f"из {progress['tasks_total']}. Фотоотчётов: {report['photo_reports']}.", "body")
    if report["done_tasks"]:
        yield Spacer(1, 3 * mm)
        rows = [[task.name, task.stage.name, display_name(task.assignee.user), date(task.completed_at), task.result]
                for task in report["done_tasks"]]
        yield table(["Работа", "Этап", "Исполнитель", "Дата", "Результат"], rows, [40, 31, 28, 21, 50])


def section_remarks(report):
    yield Paragraph("6. Замечания", STYLES["h2"])
    remarks = report["remarks"]
    if not remarks:
        yield p("Замечаний не было.", "body")
        return
    yield p(f"Всего замечаний: {len(remarks)}, не закрыто: {report['remarks_open']}.", "body")
    yield Spacer(1, 3 * mm)
    rows = [[remark.description, remark.stage.name, display_name(remark.responsible.user), remark.get_status_display(),
             date(remark.closed_at)] for remark in remarks]
    yield table(["Замечание", "Этап", "Ответственный", "Статус", "Закрыто"], rows, [56, 31, 28, 33, 22])


def section_total(report):
    heading = Paragraph("7. Итоговая стоимость ремонта", STYLES["h2"])
    if report["final_cost"] is None:
        yield KeepTogether([heading, p("Итоговую стоимость определить нельзя: нет утверждённой сметы и подтверждённых расходов.", "body")])
        return
    block = [heading, Paragraph(escape(money(report["final_cost"])), STYLES["total"]), p(f"По {report['cost_source']}.", "muted")]
    if report["budget"] is not None:
        left = report["budget_left"]
        block.append(p(f"Лимит бюджета: {money(report['budget'])}. "
                       + (f"Остаток: {money(left)}." if left >= 0 else f"Превышение лимита: {money(-left)}."), "body"))
    yield KeepTogether(block)


def render_pdf(report):
    register_fonts()
    buffer = BytesIO()
    project = report["project"]
    footer = f"Сформирован {report['generated_at']:%d.%m.%Y %H:%M} · {display_name(report['generated_by'])}"

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFont("DejaVu", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, 10 * mm, f"Контроль ремонта квартиры · {project.name}")
        canvas.drawRightString(190 * mm, 10 * mm, f"{footer} · стр. {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title=f"Итоговый отчёт — {project.name}", author=display_name(report["generated_by"]))
    story = [Paragraph("Итоговый отчёт о ремонте", STYLES["title"]),
             p(f"Проект № {project.pk} «{project.name}» · сформирован {report['generated_at']:%d.%m.%Y}", "muted"), Spacer(1, 2 * mm)]
    for section in (section_project, section_stages, section_estimate, section_expenses, section_works, section_remarks, section_total):
        story += list(section(report))
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buffer.getvalue()
