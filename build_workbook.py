"""Build the options time-space fault-tolerant risk-rating Excel workbook.

Structures the risk-rating system from xueqiu blogger 战胜自己 into a reusable
Excel tool. Data is generated & updated by Python scripts (akshare + openpyxl).
"""
from openpyxl import Workbook
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side, NamedStyle
)
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.comments import Comment
from openpyxl.drawing.image import Image as XlImage
import json
import os

# ---------- styling ----------
TITLE_FONT = Font(name="微软雅黑", size=16, bold=True, color="FFFFFF")
HEADER_FONT = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
BODY_FONT = Font(name="微软雅黑", size=10)
WARN_FONT = Font(name="微软雅黑", size=10, bold=True, color="C00000")
OK_FONT = Font(name="微软雅黑", size=10, bold=True, color="006100")

TITLE_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FILL = PatternFill("solid", fgColor="2E75B6")
SUBHEAD_FILL = PatternFill("solid", fgColor="DDEBF7")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
CALC_FILL = PatternFill("solid", fgColor="E2EFDA")
WARN_FILL = PatternFill("solid", fgColor="FCE4D6")
OK_FILL = PatternFill("solid", fgColor="C6EFCE")

thin = Side(style="thin", color="BFBFBF")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)

# Risk levels: value maps to numeric score for weighted calc
RISK_LEVELS = [
    "巨大机会", "较大机会", "一般机会",
    "中性",
    "一般风险", "较大风险", "巨大风险",
]
RISK_SCORES = {
    "巨大机会": -3, "较大机会": -2, "一般机会": -1,
    "中性": 0,
    "一般风险": 1, "较大风险": 2, "巨大风险": 3,
}

OUT_XLSX = "/workspace/期权时空容错交易系统.xlsx"


def style_header_row(ws, row, cols, fill=HEADER_FILL, font=HEADER_FONT):
    for c in range(1, cols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = font
        cell.fill = fill
        cell.alignment = CENTER
        cell.border = BORDER


def set_col_widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def add_title(ws, text, span):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=span)
    c = ws.cell(row=1, column=1, value=text)
    c.font = TITLE_FONT
    c.fill = TITLE_FILL
    c.alignment = CENTER
    ws.row_dimensions[1].height = 32


def build_risk_rating(ws):
    """Sheet 1: 风险评级总览 — 6 underlyings, ratings pulled from K-line sheets."""
    add_title(ws, "期权时空容错交易模式 · 风险评级总览", 8)
    set_col_widths(ws, [14, 14, 12, 12, 12, 14, 16, 30])

    # ---- 数据更新提示 (右侧) ----
    note_cell = ws.cell(row=1, column=10, value="📋 数据更新方式")
    note_cell.font = Font(name="微软雅黑", size=12, bold=True, color="1F4E78")
    note_cell.alignment = Alignment(horizontal="center", vertical="center")
    note_cell.fill = PatternFill("solid", fgColor="E3F2FD")
    note_cell.border = Border(
        left=Side(style="medium", color="1565C0"),
        right=Side(style="medium", color="1565C0"),
        top=Side(style="medium", color="1565C0"),
        bottom=Side(style="medium", color="1565C0"),
    )
    ws.merge_cells(start_row=1, start_column=10, end_row=2, end_column=12)
    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 22

    # 使用说明
    note2 = ws.cell(row=3, column=10, value="在命令行中运行:\npython update_excel_data.py\n\n即可更新全部数据与图表")
    note2.font = Font(name="微软雅黑", size=10, color="333333")
    note2.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
    note2.fill = PatternFill("solid", fgColor="F5F5F5")
    ws.merge_cells(start_row=3, start_column=10, end_row=7, end_column=12)
    for r in range(3, 8):
        ws.row_dimensions[r].height = 18

    # 按钮区列宽
    for col_letter in ["J", "K", "L"]:
        ws.column_dimensions[col_letter].width = 14

    # ---- input block ----
    ws.cell(row=3, column=1, value="【自动评级区】日线/周线/月线评级取自K线数据表最新交易日").font = Font(name="微软雅黑", size=12, bold=True, color="1F4E78")
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=8)

    headers = ["标的名称", "日线评级", "周线评级", "月线评级", "趋势方向", "综合评分", "综合评级", "操作建议"]
    for i, h in enumerate(headers, 1):
        ws.cell(row=4, column=i, value=h)
    style_header_row(ws, 4, 8)

    # 7 underlyings — B/C/D are formulas pulling latest ratings from K-line summary cells
    underlyings = [
        ("沪深300ETF(510300)",  "K线-沪深300ETF"),
        ("上证50ETF(510050)",   "K线-上证50ETF"),
        ("科创50ETF(588000)",   "K线-科创50ETF"),
        ("创业板ETF(159915)",   "K线-创业板ETF"),
        ("中证500ETF(510500)",  "K线-中证500ETF"),
        ("中证1000(000852)",    "K线-中证1000"),
        ("铁矿石主连(I0)",      "K线-铁矿石主连"),
    ]
    for r, (name, sheet_name) in enumerate(underlyings, start=5):
        ws.cell(row=r, column=1, value=name).fill = INPUT_FILL
        # B/C/D pull from K-line sheet summary (B1943=daily, B1944=weekly, B1945=monthly)
        ws.cell(row=r, column=2, value=f"=IFERROR('{sheet_name}'!B1943,\"\")").fill = CALC_FILL
        ws.cell(row=r, column=3, value=f"=IFERROR('{sheet_name}'!B1944,\"\")").fill = CALC_FILL
        ws.cell(row=r, column=4, value=f"=IFERROR('{sheet_name}'!B1945,\"\")").fill = CALC_FILL
        # trend direction based on monthly rating
        mscore = f'(MATCH(D{r},参数设置!$A$5:$A$11,0)-4)'
        ws.cell(row=r, column=5, value=f'=IFERROR(IF({mscore}>0,"上行(风险)",IF({mscore}<0,"下行(机会)","震荡")),"")').fill = CALC_FILL
        # weighted score
        score = (
            f'=IFERROR((MATCH(B{r},参数设置!$A$5:$A$11,0)-4)*参数设置!$B$13'
            f'+(MATCH(C{r},参数设置!$A$5:$A$11,0)-4)*参数设置!$B$14'
            f'+(MATCH(D{r},参数设置!$A$5:$A$11,0)-4)*参数设置!$B$15,"")'
        )
        ws.cell(row=r, column=6, value=score).fill = CALC_FILL
        ws.cell(row=r, column=7, value=f'=IFERROR(INDEX(参数设置!$A$5:$A$11,MATCH(ROUND(F{r},0),参数设置!$B$5:$B$11,0)),"")').fill = CALC_FILL
        # recommendation
        rec = (f'=IF(F{r}>=2,"上涨密集卖出网格·下跌稀疏买进网格",'
               f'IF(F{r}<=-2,"上涨稀疏卖出网格·下跌密集买进网格",'
               f'"均衡网格·观望为主"))')
        ws.cell(row=r, column=8, value=rec).fill = CALC_FILL

    # data validation for risk levels (allows manual override)
    dv = DataValidation(type="list", formula1='"巨大机会,较大机会,一般机会,中性,一般风险,较大风险,巨大风险"', allow_blank=True)
    dv.error = "请选择有效的风险评级"
    dv.errorTitle = "输入错误"
    ws.add_data_validation(dv)
    dv.add("B5:D10")

    # ---- explanation block ----
    ws.cell(row=12, column=1, value="【评级说明】量价时空 · 风险是涨出来的，机会是跌出来的").font = Font(name="微软雅黑", size=12, bold=True, color="1F4E78")
    ws.merge_cells(start_row=12, start_column=1, end_row=12, end_column=8)

    # 量化标准说明：每个评级的量化触发条件 + 实战指导
    # 评分由 ROC(动量) + MA偏离 + RSI 三因子加权后四舍五入得出
    explain = [
        ("评级等级", "分值", "量化触发条件（ROC动量+MA偏离+RSI）", "实战指导"),
        ("巨大机会", "-3",
         "ROC<-4%(跌势加速) 且 MA偏离<-4%(远低于均线) 且 RSI<30(超卖)；三因子全部偏空",
         "极大概率3-5周/月上涨；上涨稀疏卖出、下跌密集买进"),
        ("较大机会", "-2",
         "ROC<-2.5% 或 MA偏离<-2%，且至少一个因子达极端值；偏空动能较强",
         "大概率上涨；卖出偏稀、买进偏密"),
        ("一般机会", "-1",
         "ROC<-1% 或 MA偏离<-2% 或 RSI<30；轻度偏空",
         "偏多机会；正常网格"),
        ("中性", "0",
         "各因子均在中性区间（ROC±1%，MA偏离±2%，RSI 30-70）",
         "方向不明；均衡网格"),
        ("一般风险", "1",
         "ROC>1% 或 MA偏离>2% 或 RSI>70；轻度偏多",
         "偏空风险；正常网格"),
        ("较大风险", "2",
         "ROC>2.5% 或 MA偏离>2%，且至少一个因子达极端值；偏多动能较强",
         "大概率下跌；卖出偏密、买进偏稀"),
        ("巨大风险", "3",
         "ROC>4%(涨势加速) 且 MA偏离>4%(远高于均线) 且 RSI>70(超买)；三因子全部偏多",
         "极大概率3-5周/月调整；上涨密集卖出、下跌稀疏买进"),
    ]
    # 表头行
    hdr_row = 13
    for j, val in enumerate(explain[0], 1):
        c = ws.cell(row=hdr_row, column=j, value=val)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.border = BORDER
        c.alignment = CENTER
    # 合并表头第3列到G列（量化条件列较宽），H列保持单独
    ws.merge_cells(start_row=hdr_row, start_column=3, end_row=hdr_row, end_column=7)

    # 数据行 14-20
    for i, row in enumerate(explain[1:], start=14):
        # 列1: 评级等级
        c1 = ws.cell(row=i, column=1, value=row[0])
        c1.fill = SUBHEAD_FILL
        c1.border = BORDER
        c1.alignment = CENTER
        c1.font = Font(name="微软雅黑", size=10, bold=True)
        # 列2: 分值
        c2 = ws.cell(row=i, column=2, value=row[1])
        c2.border = BORDER
        c2.alignment = CENTER
        c2.font = Font(name="微软雅黑", size=10, bold=True)
        # 列3-7合并: 量化触发条件
        ws.merge_cells(start_row=i, start_column=3, end_row=i, end_column=7)
        c3 = ws.cell(row=i, column=3, value=row[2])
        c3.border = BORDER
        c3.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        c3.font = Font(name="微软雅黑", size=9)
        # 给合并区域所有单元格加边框
        for col in range(4, 8):
            ws.cell(row=i, column=col).border = BORDER
        # 列8: 实战指导
        c8 = ws.cell(row=i, column=8, value=row[3])
        c8.border = BORDER
        c8.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        c8.font = Font(name="微软雅黑", size=9)

    # 设置行高让内容显示完整
    for r in range(14, 21):
        ws.row_dimensions[r].height = 32

    # 量化参数说明
    ws.cell(row=22, column=1, value="【评分公式】综合评分 = ROC得分 + MA偏离得分 + RSI得分；ROC±1/2.5/4%→0.5/1/1.5分，MA偏离±2/4%→0.5/1分，RSI>70或<30→±0.5分；总和四舍五入到[-3,+3]").font = Font(name="微软雅黑", size=9, italic=True, color="595959")
    ws.merge_cells(start_row=22, start_column=1, end_row=22, end_column=8)
    ws.cell(row=23, column=1, value="【周期参数】日线:ROC5日+MA20日+RSI14日 | 周线:ROC3周+MA8周+RSI10周 | 月线:ROC2月+MA4月+RSI6月").font = Font(name="微软雅黑", size=9, italic=True, color="595959")
    ws.merge_cells(start_row=23, start_column=1, end_row=23, end_column=8)
    ws.cell(row=24, column=1, value="【综合评级】= 日线评分×日线权重 + 周线评分×周线权重 + 月线评分×月线权重；月线权重最高").font = Font(name="微软雅黑", size=9, italic=True, color="595959")
    ws.merge_cells(start_row=24, start_column=1, end_row=24, end_column=8)

    for r in range(5, 11):
        for c in range(1, 9):
            ws.cell(row=r, column=c).border = BORDER
            ws.cell(row=r, column=c).alignment = CENTER


def build_options(ws):
    """Sheet 2: 期权持仓"""
    add_title(ws, "期权持仓 · 时空容错监控", 12)
    set_col_widths(ws, [6, 18, 8, 10, 10, 12, 8, 10, 12, 10, 10, 12])

    headers = [
        "序号", "标的", "类型", "仓位", "行权价", "到期日",
        "数量(张)", "权利金", "标的现价", "内在价值", "时间价值",
        "容错空间%",
    ]
    for i, h in enumerate(headers, 1):
        ws.cell(row=3, column=i, value=h)
    style_header_row(ws, 3, 12)

    # sample positions — covers all 6 underlyings
    samples = [
        (1, "沪深300ETF", "购", "义务", 4.8, "2026-05-22", 10, 0.042, 4.05),
        (2, "沪深300ETF", "沽", "义务", 3.8, "2026-05-22", 10, 0.035, 4.05),
        (3, "上证50ETF", "购", "义务", 3.2, "2026-06-25", 5, 0.038, 2.85),
        (4, "上证50ETF", "沽", "权利", 2.6, "2026-09-24", 20, 0.025, 2.85),
        (5, "沪深300ETF", "沽", "义务", 3.7, "2026-04-23", 8, 0.020, 4.05),
        (6, "创业板ETF", "购", "义务", 3.8, "2026-06-25", 5, 0.045, 3.20),
        (7, "中证500ETF", "沽", "义务", 5.8, "2026-05-22", 5, 0.035, 6.30),
        (8, "中证1000", "购", "义务", 6800, "2026-06-25", 3, 45.0, 6500.0),
    ]
    for r, row in enumerate(samples, start=4):
        for c, val in enumerate(row, 1):
            cell = ws.cell(row=r, column=c, value=val)
            cell.fill = INPUT_FILL if c <= 9 else CALC_FILL
            cell.border = BORDER
            cell.alignment = CENTER
        # 内在价值: 购=max(S-K,0), 沽=max(K-S,0)
        ws.cell(row=r, column=10, value=f'=IF(C{r}="购",MAX(I{r}-E{r},0),MAX(E{r}-I{r},0))')
        # 时间价值 = 权利金 - 内在价值
        ws.cell(row=r, column=11, value=f'=MAX(H{r}-J{r},0)')
        # 容错空间% = |行权价-现价|/现价  (distance to strike as % of price)
        ws.cell(row=r, column=12, value=f'=ABS(E{r}-I{r})/I{r}')
        ws.cell(row=r, column=12).number_format = "0.00%"

    # summary
    sr = 12
    ws.cell(row=sr, column=1, value="汇总").font = HEADER_FONT
    ws.cell(row=sr, column=1).fill = SUBHEAD_FILL
    ws.cell(row=sr, column=8, value="平均容错").font = HEADER_FONT
    ws.cell(row=sr, column=8).fill = SUBHEAD_FILL
    ws.cell(row=sr, column=9, value=f'=AVERAGE(L4:L11)').number_format = "0.00%"
    ws.cell(row=sr, column=10, value="最小容错").font = HEADER_FONT
    ws.cell(row=sr, column=10).fill = SUBHEAD_FILL
    ws.cell(row=sr, column=11, value=f'=MIN(L4:L11)').number_format = "0.00%"

    # risk status column
    ws.cell(row=3, column=13, value="风险状态")
    ws.cell(row=3, column=13).font = HEADER_FONT
    ws.cell(row=3, column=13).fill = HEADER_FILL
    ws.cell(row=3, column=13).alignment = CENTER
    ws.cell(row=3, column=13).border = BORDER
    ws.column_dimensions["M"].width = 14
    for r in range(4, 12):
        # 容错空间阈值判断：结合到期天数
        days = f'DATEDIF(TODAY(),F{r},"D")'
        status = (f'=IF(L{r}>=8%,"安全",'
                  f'IF(AND({days}<=7,L{r}<5%),"⚠末日期权容错不足",'
                  f'IF(AND({days}<=30,L{r}<5%),"⚠临近到期容错不足",'
                  f'IF(L{r}<5%,"⚠容错空间偏低","正常"))))')
        c = ws.cell(row=r, column=13, value=status)
        c.border = BORDER
        c.alignment = CENTER

    # data validation
    dv_type = DataValidation(type="list", formula1='"购,沽"', allow_blank=True)
    dv_pos = DataValidation(type="list", formula1='"义务,权利"', allow_blank=True)
    ws.add_data_validation(dv_type)
    ws.add_data_validation(dv_pos)
    dv_type.add("C4:C50")
    dv_pos.add("D4:D50")

    # conditional formatting for 容错空间
    red_fill = PatternFill("solid", fgColor="FFC7CE")
    yellow_fill = PatternFill("solid", fgColor="FFEB9C")
    green_fill = PatternFill("solid", fgColor="C6EFCE")
    ws.conditional_formatting.add("L4:L50",
        CellIsRule(operator="lessThan", formula=["0.05"], fill=red_fill))
    ws.conditional_formatting.add("L4:L50",
        CellIsRule(operator="between", formula=["0.05", "0.08"], fill=yellow_fill))
    ws.conditional_formatting.add("L4:L50",
        CellIsRule(operator="greaterThanOrEqual", formula=["0.08"], fill=green_fill))


def build_account_risk(ws):
    """Sheet 3: 账户风险"""
    add_title(ws, "账户风险度 · 容错空间总控", 6)
    set_col_widths(ws, [20, 14, 14, 14, 14, 30])

    ws.cell(row=3, column=1, value="账户资金与保证金").font = Font(name="微软雅黑", size=12, bold=True, color="1F4E78")
    ws.merge_cells("A3:F3")

    items = [
        ("总资金(元)", 187000),
        ("已用保证金(元)", 93500),
        ("可用保证金(元)", "=B4-B5"),
        ("风险度", "=B5/B4"),
    ]
    for i, (label, val) in enumerate(items, start=4):
        ws.cell(row=i, column=1, value=label).fill = SUBHEAD_FILL
        ws.cell(row=i, column=1).font = Font(name="微软雅黑", size=10, bold=True)
        c = ws.cell(row=i, column=2, value=val)
        c.fill = INPUT_FILL if isinstance(val, (int, float)) else CALC_FILL
        c.border = BORDER
        ws.cell(row=i, column=1).border = BORDER
        if i == 7:
            c.number_format = "0.00%"

    # risk degree status
    ws.cell(row=4, column=3, value="风险状态").font = HEADER_FONT
    ws.cell(row=4, column=3).fill = HEADER_FILL
    ws.cell(row=4, column=3).alignment = CENTER
    ws.cell(row=4, column=3).border = BORDER
    ws.cell(row=5, column=3, value="").border = BORDER
    ws.cell(row=6, column=3, value="").border = BORDER
    status = (f'=IF(B7<=参数设置!$B$19,"安全(≤目标)",'
              f'IF(B7<=参数设置!$B$20,"偏高(≤极端)","危险(超极端)"))')
    c = ws.cell(row=7, column=3, value=status)
    c.border = BORDER
    c.alignment = CENTER

    # thresholds reference
    ws.cell(row=4, column=4, value="目标阈值").font = HEADER_FONT
    ws.cell(row=4, column=4).fill = HEADER_FILL
    ws.cell(row=4, column=4).alignment = CENTER
    ws.cell(row=4, column=4).border = BORDER
    ws.cell(row=7, column=4, value="=参数设置!$B$19").number_format = "0%"
    ws.cell(row=7, column=4).border = BORDER
    ws.cell(row=7, column=4).fill = CALC_FILL

    ws.cell(row=4, column=5, value="极端阈值").font = HEADER_FONT
    ws.cell(row=4, column=5).fill = HEADER_FILL
    ws.cell(row=4, column=5).alignment = CENTER
    ws.cell(row=4, column=5).border = BORDER
    ws.cell(row=7, column=5, value="=参数设置!$B$20").number_format = "0%"
    ws.cell(row=7, column=5).border = BORDER
    ws.cell(row=7, column=5).fill = CALC_FILL

    # 容错空间汇总
    ws.cell(row=9, column=1, value="持仓容错空间汇总").font = Font(name="微软雅黑", size=12, bold=True, color="1F4E78")
    ws.merge_cells("A9:F9")

    ft_items = [
        ("平均容错空间", "=AVERAGE(期权持仓!L4:L50)", "0.00%"),
        ("最小容错空间", "=MIN(期权持仓!L4:L50)", "0.00%"),
        ("最大容错空间", "=MAX(期权持仓!L4:L50)", "0.00%"),
        ("末日期权(<7天)最小容错", "=MINIFS(期权持仓!L4:L50,期权持仓!F4:F50,\"<=\"&TODAY()+7)", "0.00%"),
    ]
    for i, (label, formula, fmt) in enumerate(ft_items, start=10):
        ws.cell(row=i, column=1, value=label).fill = SUBHEAD_FILL
        ws.cell(row=i, column=1).border = BORDER
        c = ws.cell(row=i, column=2, value=formula)
        c.number_format = fmt
        c.fill = CALC_FILL
        c.border = BORDER
        ws.cell(row=i, column=3, value="").border = BORDER

    # control rules
    ws.cell(row=15, column=1, value="时空容错铁律（来自战胜自己体系）").font = Font(name="微软雅黑", size=12, bold=True, color="1F4E78")
    ws.merge_cells("A15:F15")
    rules = [
        "1. 义务仓风险度尽量 ≤ 50%，极端情况收盘不超过 70%",
        "2. 义务仓容错空间：两个月 ≥ 8%，一个月 ≥ 5%",
        "3. 末日期权（临近到期）容错空间 ≥ 5%，否则移仓",
        "4. 核心是吃时间价值流逝，耐心持有等待虚值归零",
        "5. 不在盘前预设网格，盘中标的涨跌超 1% 再开始",
        "6. 短期容错 + 长期容错都要控制，概率站在我方",
    ]
    for i, rule in enumerate(rules, start=16):
        c = ws.cell(row=i, column=1, value=rule)
        ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=6)
        c.alignment = LEFT
        c.fill = SUBHEAD_FILL
        c.border = BORDER


def build_grid_plan(ws):
    """Sheet 4: 网格计划"""
    add_title(ws, "网格交易计划 · 按风险评级生成", 6)
    set_col_widths(ws, [14, 14, 14, 14, 14, 30])

    ws.cell(row=3, column=1, value="参数输入").font = Font(name="微软雅黑", size=12, bold=True, color="1F4E78")
    ws.merge_cells("A3:F3")

    inputs = [
        ("标的现价", 4.05),
        ("风险评级(综合评分)", 2),
        ("每格价差(元)", 0.02),
        ("每格数量(张)", 1),
        ("网格方向", '=IF(B5>=2,"上涨密集卖出/下跌稀疏买进",IF(B5<=-2,"上涨稀疏卖出/下跌密集买进","均衡"))'),
    ]
    for i, (label, val) in enumerate(inputs, start=4):
        ws.cell(row=i, column=1, value=label).fill = SUBHEAD_FILL
        ws.cell(row=i, column=1).border = BORDER
        c = ws.cell(row=i, column=2, value=val)
        c.fill = INPUT_FILL if isinstance(val, (int, float)) else CALC_FILL
        c.border = BORDER

    # grid table
    ws.cell(row=10, column=1, value="价格").font = HEADER_FONT
    ws.cell(row=10, column=2, value="方向").font = HEADER_FONT
    ws.cell(row=10, column=3, value="数量").font = HEADER_FONT
    ws.cell(row=10, column=4, value="累计").font = HEADER_FONT
    ws.cell(row=10, column=5, value="价差%").font = HEADER_FONT
    ws.cell(row=10, column=6, value="说明").font = HEADER_FONT
    style_header_row(ws, 10, 6)

    # generate 10 sell grids above and 10 buy grids below
    base_price = 4.05
    step = 0.02
    for i in range(1, 11):
        r = 10 + i
        price = round(base_price + step * i, 4)
        ws.cell(row=r, column=1, value=price).number_format = "0.0000"
        ws.cell(row=r, column=2, value="卖出")
        ws.cell(row=r, column=3, value=1)
        ws.cell(row=r, column=4, value=f"=SUM(C11:C{r})")
        ws.cell(row=r, column=5, value=f"=(A{r}-$B$4)/$B$4").number_format = "0.00%"
        ws.cell(row=r, column=6, value=f"第{i}档卖出网格")
        for c in range(1, 7):
            ws.cell(row=r, column=c).border = BORDER
            ws.cell(row=r, column=c).alignment = CENTER
    for i in range(1, 11):
        r = 20 + i
        price = round(base_price - step * i, 4)
        ws.cell(row=r, column=1, value=price).number_format = "0.0000"
        ws.cell(row=r, column=2, value="买进")
        ws.cell(row=r, column=3, value=1)
        ws.cell(row=r, column=4, value=f"=SUM(C11:C{r})")
        ws.cell(row=r, column=5, value=f"=(A{r}-$B$4)/$B$4").number_format = "0.00%"
        ws.cell(row=r, column=6, value=f"第{i}档买进网格")
        for c in range(1, 7):
            ws.cell(row=r, column=c).border = BORDER
            ws.cell(row=r, column=c).alignment = CENTER


def build_parameters(ws):
    """Sheet 5: 参数设置"""
    add_title(ws, "参数设置 · 阈值与权重", 4)
    set_col_widths(ws, [22, 16, 16, 30])

    ws.cell(row=3, column=1, value="风险评级等级与分值").font = Font(name="微软雅黑", size=12, bold=True, color="1F4E78")
    ws.merge_cells("A3:D3")
    ws.cell(row=4, column=1, value="评级").font = HEADER_FONT
    ws.cell(row=4, column=2, value="分值").font = HEADER_FONT
    style_header_row(ws, 4, 4)
    for i, lvl in enumerate(RISK_LEVELS):
        ws.cell(row=5 + i, column=1, value=lvl).fill = SUBHEAD_FILL
        ws.cell(row=5 + i, column=2, value=RISK_SCORES[lvl])
        ws.cell(row=5 + i, column=1).border = BORDER
        ws.cell(row=5 + i, column=2).border = BORDER
        ws.cell(row=5 + i, column=1).alignment = CENTER
        ws.cell(row=5 + i, column=2).alignment = CENTER

    # weights
    ws.cell(row=12, column=1, value="周期权重（合计=1）").font = Font(name="微软雅黑", size=12, bold=True, color="1F4E78")
    ws.merge_cells("A12:D12")
    weights = [("日线权重", 0.2), ("周线权重", 0.3), ("月线权重", 0.5)]
    for i, (label, val) in enumerate(weights, start=13):
        ws.cell(row=i, column=1, value=label).fill = SUBHEAD_FILL
        ws.cell(row=i, column=1).border = BORDER
        c = ws.cell(row=i, column=2, value=val)
        c.fill = INPUT_FILL
        c.number_format = "0.00"
        c.border = BORDER
    ws.cell(row=16, column=1, value="合计").font = HEADER_FONT
    ws.cell(row=16, column=1).fill = HEADER_FILL
    ws.cell(row=16, column=1).border = BORDER
    ws.cell(row=16, column=2, value="=SUM(B13:B15)").number_format = "0.00"
    ws.cell(row=16, column=2).border = BORDER

    # thresholds
    ws.cell(row=18, column=1, value="风险度阈值").font = Font(name="微软雅黑", size=12, bold=True, color="1F4E78")
    ws.merge_cells("A18:D18")
    thresh = [
        ("目标风险度上限", 0.5, "义务仓尽量控制在此以下"),
        ("极端风险度上限", 0.7, "收盘不超过此值"),
    ]
    for i, (label, val, note) in enumerate(thresh, start=19):
        ws.cell(row=i, column=1, value=label).fill = SUBHEAD_FILL
        ws.cell(row=i, column=1).border = BORDER
        c = ws.cell(row=i, column=2, value=val)
        c.fill = INPUT_FILL
        c.number_format = "0%"
        c.border = BORDER
        ws.cell(row=i, column=4, value=note).alignment = LEFT
        ws.cell(row=i, column=4).border = BORDER

    ws.cell(row=22, column=1, value="容错空间阈值").font = Font(name="微软雅黑", size=12, bold=True, color="1F4E78")
    ws.merge_cells("A22:D22")
    ft = [
        ("两个月容错下限", 0.08, "≥8%"),
        ("一个月容错下限", 0.05, "≥5%"),
        ("末日期权容错下限", 0.05, "≥5%，否则移仓"),
    ]
    for i, (label, val, note) in enumerate(ft, start=23):
        ws.cell(row=i, column=1, value=label).fill = SUBHEAD_FILL
        ws.cell(row=i, column=1).border = BORDER
        c = ws.cell(row=i, column=2, value=val)
        c.fill = INPUT_FILL
        c.number_format = "0%"
        c.border = BORDER
        ws.cell(row=i, column=4, value=note).alignment = LEFT
        ws.cell(row=i, column=4).border = BORDER

    # update settings
    ws.cell(row=27, column=1, value="更新设置").font = Font(name="微软雅黑", size=12, bold=True, color="1F4E78")
    ws.merge_cells("A27:D27")
    upd = [
        ("数据来源", "akshare", "Python脚本通过akshare获取真实行情"),
        ("样本起始日", "2020-11-16", "科创50ETF上市日，便于历史复盘"),
        ("更新方式", "运行Python脚本", "执行 generate_kline_data.py + build_workbook.py"),
    ]
    for i, (label, val, note) in enumerate(upd, start=28):
        ws.cell(row=i, column=1, value=label).fill = SUBHEAD_FILL
        ws.cell(row=i, column=1).border = BORDER
        c = ws.cell(row=i, column=2, value=val)
        c.fill = INPUT_FILL
        c.border = BORDER
        ws.cell(row=i, column=4, value=note).alignment = LEFT
        ws.cell(row=i, column=4).border = BORDER


def build_update_log(ws):
    """Sheet 6: 更新日志"""
    add_title(ws, "更新日志 · 手动/自动实时更新记录", 5)
    set_col_widths(ws, [20, 12, 20, 16, 30])

    headers = ["时间", "类型", "操作", "结果", "备注"]
    for i, h in enumerate(headers, 1):
        ws.cell(row=3, column=i, value=h)
    style_header_row(ws, 3, 5)

    # initial log
    ws.cell(row=4, column=1, value="=NOW()").number_format = "yyyy-mm-dd hh:mm:ss"
    ws.cell(row=4, column=2, value="系统")
    ws.cell(row=4, column=3, value="初始化")
    ws.cell(row=4, column=4, value="成功")
    ws.cell(row=4, column=5, value="工作簿已创建，等待首次更新")
    for c in range(1, 6):
        ws.cell(row=4, column=c).border = BORDER
        ws.cell(row=4, column=c).alignment = CENTER

    # instructions
    ws.cell(row=6, column=1, value="使用说明").font = Font(name="微软雅黑", size=12, bold=True, color="1F4E78")
    ws.merge_cells("A6:E6")
    notes = [
        "【数据更新方式】本工作簿由 Python 脚本生成与维护，不再使用 VBA 宏：",
        "1. 生成K线数据与图表：python3 generate_kline_data.py",
        "   —— 通过 akshare 获取6个标的(510050/510300/588000/159915/510500/000852)的真实行情",
        "   —— 样本起始日统一为 2020-11-16（科创50ETF上市日），便于历史复盘",
        "   —— 自动计算日/周/月K线的风险评级，输出 JSON 数据与 K线图 PNG",
        "2. 构建工作簿：python3 build_workbook.py",
        "   —— 读取 JSON 数据与图表，生成包含12个工作表的 .xlsx 文件",
        "3. 增量更新数据（不重建结构）：python3 update_excel_data.py",
        "   —— 仅刷新K线数据与评级，保留工作簿其他结构不变",
        "─────────────────────────────────────────────",
        "【功能说明】",
        "• 风险评级总览：自动汇总6个标的的日/周/月线最新评级，给出综合评分与操作建议",
        "• K线工作表(6个标的)：每个工作表含日K/周K/月K三段数据 + 右侧置顶K线图",
        "  图表从上到下依次为日K、周K、月K，便于多周期对比查看",
        "  巨大风险日红色高亮，巨大机会日绿色高亮，便于复盘历史极值",
        "  底部为最新交易日评级汇总，风险评级总览自动引用",
        "• 期权持仓 / 账户风险 / 网格计划：持仓容错监控与风险度控制",
        "• 参数设置：评级阈值、周期权重、风险度与容错空间阈值均可调整",
        "• 首次使用请先在【参数设置】中确认阈值与权重",
    ]
    for i, note in enumerate(notes, start=7):
        c = ws.cell(row=i, column=1, value=note)
        ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=5)
        c.alignment = LEFT
        c.fill = SUBHEAD_FILL
        c.border = BORDER


KLINE_DIR = "/workspace/kline_data"
CHART_DIR = "/workspace/kline_charts"

# Risk-based row fills
RISK3_FILL = PatternFill("solid", fgColor="FFC7CE")   # 巨大风险 - red
RISK2_FILL = PatternFill("solid", fgColor="FCE4D6")   # 较大风险 - orange
OPP3_FILL = PatternFill("solid", fgColor="C6EFCE")    # 巨大机会 - green
OPP2_FILL = PatternFill("solid", fgColor="E2EFDA")    # 较大机会 - light green


# ---- Fixed row layout constants (sample starts 2020-11-16) ----
KLINE_DAILY_START = 5
KLINE_DAILY_END = 1504       # 1500 bars  (2020-11-16 ~ 今)
KLINE_WEEKLY_START = 1508
KLINE_WEEKLY_END = 1857      # 350 bars
KLINE_MONTHLY_START = 1861
KLINE_MONTHLY_END = 1940     # 80 bars
KLINE_SUMMARY_ROW = 1942

KLINE_LAYOUT = [
    # period, label, section_header_row, col_header_row, data_start, data_end
    ("daily",   "日K线", 3,    4,    KLINE_DAILY_START,   KLINE_DAILY_END),
    ("weekly",  "周K线", 1506, 1507, KLINE_WEEKLY_START,  KLINE_WEEKLY_END),
    ("monthly", "月K线", 1859, 1860, KLINE_MONTHLY_START, KLINE_MONTHLY_END),
]

# 图表置顶：日/周/月三张图在右侧纵向排列，紧凑间距便于对比
# 数据列为 A-K (11列)，图表从 L 列开始
CHART_COL = "L"
CHART_ANCHORS = [
    ("daily",   1),    # 日K图置顶
    ("weekly",  20),   # 日K统计区下方紧接周K图
    ("monthly", 39),   # 周K统计区下方紧接月K图
]
CHART_DISPLAY_W = 760
CHART_DISPLAY_H = 280


def build_kline_sheet(ws, etf_code: str, etf_name: str):
    """Build a K-line data + chart sheet for one ETF with fixed row layout."""
    add_title(ws, f"{etf_name} ({etf_code}) · K线数据与风险标注", 11)
    set_col_widths(ws, [12, 10, 10, 10, 10, 14, 10, 12, 10, 10, 10])

    for period, period_label, hdr_row, col_hdr_row, data_start, data_end in KLINE_LAYOUT:
        json_path = os.path.join(KLINE_DIR, f"{etf_code}_{period}.json")
        if not os.path.exists(json_path):
            ws.cell(row=hdr_row, column=1, value=f"[{period_label}] 数据文件缺失: {json_path}")
            continue

        with open(json_path, "r", encoding="utf-8") as f:
            records = json.load(f)

        # Section header
        risk_count = sum(1 for r in records if r["risk_score"] == 3)
        opp_count = sum(1 for r in records if r["risk_score"] == -3)
        header_text = f"【{period_label}数据】共{len(records)}条 · 巨大风险={risk_count}天 · 巨大机会={opp_count}天"
        c = ws.cell(row=hdr_row, column=1, value=header_text)
        c.font = Font(name="微软雅黑", size=12, bold=True, color="1F4E78")
        ws.merge_cells(start_row=hdr_row, start_column=1, end_row=hdr_row, end_column=11)

        # Column headers (A-K: 日期,开盘,最高,最低,收盘,成交量,风险评分,风险评级,期中涨幅,期中跌幅,收盘涨跌)
        headers = ["日期", "开盘", "最高", "最低", "收盘", "成交量", "风险评分", "风险评级",
                   "期中涨幅", "期中跌幅", "收盘涨跌"]
        for i, h in enumerate(headers, 1):
            ws.cell(row=col_hdr_row, column=i, value=h)
        style_header_row(ws, col_hdr_row, 11)

        # Data rows — use fixed positions
        max_bars = data_end - data_start + 1
        for idx, rec in enumerate(records[:max_bars]):
            row = data_start + idx
            ws.cell(row=row, column=1, value=rec["date"]).alignment = CENTER
            ws.cell(row=row, column=2, value=rec["open"]).number_format = "0.0000"
            ws.cell(row=row, column=3, value=rec["high"]).number_format = "0.0000"
            ws.cell(row=row, column=4, value=rec["low"]).number_format = "0.0000"
            ws.cell(row=row, column=5, value=rec["close"]).number_format = "0.0000"
            vol_cell = ws.cell(row=row, column=6, value=rec["volume"])
            vol_cell.number_format = "#,##0"
            ws.cell(row=row, column=7, value=rec["risk_score"]).alignment = CENTER
            label_cell = ws.cell(row=row, column=8, value=rec["risk_label"])
            label_cell.alignment = CENTER

            # I/J/K: 期中涨幅, 期中跌幅, 收盘涨跌 (相对上一交易日收盘价的百分比)
            for col_idx, key in [(9, "intraday_up"), (10, "intraday_down"), (11, "close_change")]:
                val = rec.get(key)
                if val is not None:
                    cell = ws.cell(row=row, column=col_idx, value=val)
                    cell.number_format = "0.00%"
                    cell.alignment = CENTER

            # Highlight by risk level (A-K 共11列)
            score = rec["risk_score"]
            if score == 3:
                for col in range(1, 12):
                    ws.cell(row=row, column=col).fill = RISK3_FILL
                ws.cell(row=row, column=8).font = WARN_FONT
            elif score == 2:
                for col in range(1, 12):
                    ws.cell(row=row, column=col).fill = RISK2_FILL
            elif score == -3:
                for col in range(1, 12):
                    ws.cell(row=row, column=col).fill = OPP3_FILL
                ws.cell(row=row, column=8).font = OK_FONT
            elif score == -2:
                for col in range(1, 12):
                    ws.cell(row=row, column=col).fill = OPP2_FILL

            for col in range(1, 12):
                ws.cell(row=row, column=col).border = BORDER

    # ---- 图表置顶 + 统计数据：日/周/月纵向紧凑排列，每张图下方附统计 ----
    stats_font = Font(name="微软雅黑", size=9, color="333333")
    stats_bold_font = Font(name="微软雅黑", size=9, bold=True, color="1F4E78")
    stats_fill = PatternFill("solid", fgColor="F8F9FA")

    for period, anchor_row in CHART_ANCHORS:
        # 嵌入图表
        chart_path = os.path.join(CHART_DIR, f"{etf_code}_{period}.png")
        if os.path.exists(chart_path):
            img = XlImage(chart_path)
            img.width = CHART_DISPLAY_W
            img.height = CHART_DISPLAY_H
            ws.add_image(img, f"{CHART_COL}{anchor_row}")

        # 图表下方统计数据 (从图表锚点+16行开始，共7行)
        json_path = os.path.join(KLINE_DIR, f"{etf_code}_{period}.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                recs = json.load(f)
            stats_row = anchor_row + 14  # 图表高约14行，紧接其下

            closes = [r["close"] for r in recs]
            highs = [r["high"] for r in recs]
            lows = [r["low"] for r in recs]
            risk_n = sum(1 for r in recs if r["risk_score"] == 3)
            opp_n = sum(1 for r in recs if r["risk_score"] == -3)
            total = len(recs)
            ret_pct = (closes[-1] / closes[0] - 1) * 100 if closes[0] else 0

            period_cn = {"daily": "日K", "weekly": "周K", "monthly": "月K"}[period]
            stats_lines = [
                (f"【{period_cn}统计】", stats_bold_font),
                (f"数据条数: {total}条 ({recs[0]['date']} ~ {recs[-1]['date']})", stats_font),
                (f"最高价: {max(highs):.4f}  最低价: {min(lows):.4f}", stats_font),
                (f"期初价: {closes[0]:.4f}  期末价: {closes[-1]:.4f}  涨幅: {ret_pct:+.2f}%", stats_font),
                (f"巨大风险: {risk_n}天 ({risk_n/total*100:.1f}%)  巨大机会: {opp_n}天 ({opp_n/total*100:.1f}%)", stats_font),
            ]
            for i, (text, font) in enumerate(stats_lines):
                r = stats_row + i
                cell = ws.cell(row=r, column=12, value=text)  # L列=12
                cell.font = font
                cell.fill = stats_fill
                cell.alignment = Alignment(horizontal="left", vertical="center")
                ws.merge_cells(start_row=r, start_column=12, end_row=r, end_column=20)

    # Set column widths for chart area
    for col_letter in ["J", "K", "L", "M", "N", "O", "P", "Q", "R", "S"]:
        ws.column_dimensions[col_letter].width = 12

    # ---- Summary section: latest trading day ratings ----
    sr = KLINE_SUMMARY_ROW
    c = ws.cell(row=sr, column=1, value="【最新交易日评级】LOOKUP自动取H列最后非空值")
    c.font = Font(name="微软雅黑", size=12, bold=True, color="1F4E78")
    ws.merge_cells(start_row=sr, start_column=1, end_row=sr, end_column=4)

    # Row 1943: daily rating
    sr = KLINE_SUMMARY_ROW + 1
    ws.cell(row=sr, column=1, value="日线评级").fill = SUBHEAD_FILL
    ws.cell(row=sr, column=1).border = BORDER
    ws.cell(row=sr, column=1).alignment = CENTER
    ws.cell(row=sr, column=2, value=f'=IFERROR(LOOKUP(2,1/(H{KLINE_DAILY_START}:H{KLINE_DAILY_END}<>""),H{KLINE_DAILY_START}:H{KLINE_DAILY_END}),"")')
    ws.cell(row=sr, column=2).fill = CALC_FILL
    ws.cell(row=sr, column=2).border = BORDER
    ws.cell(row=sr, column=2).alignment = CENTER

    # Row 1944: weekly rating
    sr = KLINE_SUMMARY_ROW + 2
    ws.cell(row=sr, column=1, value="周线评级").fill = SUBHEAD_FILL
    ws.cell(row=sr, column=1).border = BORDER
    ws.cell(row=sr, column=1).alignment = CENTER
    ws.cell(row=sr, column=2, value=f'=IFERROR(LOOKUP(2,1/(H{KLINE_WEEKLY_START}:H{KLINE_WEEKLY_END}<>""),H{KLINE_WEEKLY_START}:H{KLINE_WEEKLY_END}),"")')
    ws.cell(row=sr, column=2).fill = CALC_FILL
    ws.cell(row=sr, column=2).border = BORDER
    ws.cell(row=sr, column=2).alignment = CENTER

    # Row 1945: monthly rating
    sr = KLINE_SUMMARY_ROW + 3
    ws.cell(row=sr, column=1, value="月线评级").fill = SUBHEAD_FILL
    ws.cell(row=sr, column=1).border = BORDER
    ws.cell(row=sr, column=1).alignment = CENTER
    ws.cell(row=sr, column=2, value=f'=IFERROR(LOOKUP(2,1/(H{KLINE_MONTHLY_START}:H{KLINE_MONTHLY_END}<>""),H{KLINE_MONTHLY_START}:H{KLINE_MONTHLY_END}),"")')
    ws.cell(row=sr, column=2).fill = CALC_FILL
    ws.cell(row=sr, column=2).border = BORDER
    ws.cell(row=sr, column=2).alignment = CENTER


def main():
    wb = Workbook()
    # remove default
    wb.remove(wb.active)

    sheets = [
        ("风险评级总览", build_risk_rating),
        ("期权持仓", build_options),
        ("账户风险", build_account_risk),
        ("网格计划", build_grid_plan),
        ("参数设置", build_parameters),
        ("更新日志", build_update_log),
    ]
    for name, builder in sheets:
        ws = wb.create_sheet(name)
        builder(ws)

    # K-line sheets — 7 underlyings
    kline_etfs = [
        ("510050", "上证50ETF"),
        ("510300", "沪深300ETF"),
        ("588000", "科创50ETF"),
        ("159915", "创业板ETF"),
        ("510500", "中证500ETF"),
        ("000852", "中证1000"),
        ("I0", "铁矿石主连"),
    ]
    for code, name in kline_etfs:
        ws = wb.create_sheet(f"K线-{name}")
        build_kline_sheet(ws, code, name)

    wb.save(OUT_XLSX)

    # ---- 修复 openpyxl 的 XML 命名空间 bug ----
    # openpyxl 3.1.5 在使用超链接时，不会在 <worksheet> 根元素声明 xmlns:r，
    # 导致 <drawing r:id="..."/> 元素的 r: 前缀未声明，Excel 打开报错
    import zipfile, shutil, os, re
    tmp_path = OUT_XLSX + ".tmp"
    with zipfile.ZipFile(OUT_XLSX, "r") as zin, zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item.startswith("xl/worksheets/sheet") and item.endswith(".xml"):
                xml = data.decode("utf-8")
                # 在 <worksheet 根元素上添加 xmlns:r 声明
                if 'xmlns:r=' not in xml and 'r:id=' in xml:
                    xml = xml.replace(
                        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"',
                        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"',
                        1
                    )
                    data = xml.encode("utf-8")
            zout.writestr(item, data)
    shutil.move(tmp_path, OUT_XLSX)

    print(f"Workbook saved: {OUT_XLSX}")


if __name__ == "__main__":
    main()
