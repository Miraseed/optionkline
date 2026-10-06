"""
期权时空容错交易系统 - Python数据更新脚本
用akshare获取7个标的的真实K线数据（从2020-11-16起），更新Excel工作簿

使用方法:
    python3 update_excel_data.py

前提: 已安装akshare (pip install akshare)
说明: 本系统完全基于Python，不再使用VBA宏
"""
import json
import os
import sys
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.drawing.image import Image as XlImage
from datetime import datetime

# matplotlib 用于生成K线图
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D

# 中文字体
plt.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# ============ 配置 ============
XLSX_FILE = "期权时空容错交易系统.xlsx"

# 样本起始日 = 科创50ETF上市日，所有标的统一从此日开始便于历史复盘
START_DATE = "2020-11-16"

# 7个标的配置 (6个ETF/指数 + 铁矿石主连)
UNDERLYINGS = [
    {"code": "510050", "name": "上证50ETF", "sheet": "K线-上证50ETF", "symbol": "sh510050", "type": "etf"},
    {"code": "510300", "name": "沪深300ETF", "sheet": "K线-沪深300ETF", "symbol": "sh510300", "type": "etf"},
    {"code": "588000", "name": "科创50ETF", "sheet": "K线-科创50ETF", "symbol": "sh588000", "type": "etf"},
    {"code": "159915", "name": "创业板ETF", "sheet": "K线-创业板ETF", "symbol": "sz159915", "type": "etf"},
    {"code": "510500", "name": "中证500ETF", "sheet": "K线-中证500ETF", "symbol": "sh510500", "type": "etf"},
    {"code": "000852", "name": "中证1000", "sheet": "K线-中证1000", "symbol": "sh000852", "type": "index"},
    {"code": "I0", "name": "铁矿石主连", "sheet": "K线-铁矿石主连", "symbol": "nf_I0", "type": "futures"},
]

# ETF/指数 -> 乐咕乐股指数代码映射 (用于获取PE数据)
# 铁矿石没有PE，不映射
PE_INDEX_MAP = {
    "510050": "000016.SH",  # 上证50
    "510300": "000300.SH",  # 沪深300
    "588000": "000688.SH",  # 科创50
    "159915": "399006.SZ",  # 创业板指 (创业板ETF跟踪创业板指)
    "510500": "000905.SH",  # 中证500
    "000852": "000852.SH",  # 中证1000
    "I0": None,             # 铁矿石无PE
}

# K线周期配置 (样本从 2020-11-16 起，行范围需与 build_workbook.py 一致)
PERIODS = [
    {"name": "daily",   "label": "日K线", "max_bars": 1500, "start_row": 5,    "end_row": 1504, "hdr_row": 3},
    {"name": "weekly",  "label": "周K线", "max_bars": 350,  "start_row": 1508, "end_row": 1857, "hdr_row": 1506},
    {"name": "monthly", "label": "月K线", "max_bars": 80,   "start_row": 1861, "end_row": 1940, "hdr_row": 1859},
]

# 风险评级参数
PERIOD_PARAMS = {
    "daily":   {"roc_len": 5, "ma_len": 20, "rsi_len": 14},
    "weekly":  {"roc_len": 3, "ma_len": 8, "rsi_len": 10},
    "monthly": {"roc_len": 2, "ma_len": 4, "rsi_len": 6},
}

RISK_LABELS = {
    -3: "巨大机会", -2: "较大机会", -1: "一般机会",
    0: "中性",
    1: "一般风险", 2: "较大风险", 3: "巨大风险",
}

# 样式
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
RISK3_FILL = PatternFill("solid", fgColor="F4CCCC")
RISK2_FILL = PatternFill("solid", fgColor="FCE4D6")
OPP3_FILL = PatternFill("solid", fgColor="D9EAD3")
OPP2_FILL = PatternFill("solid", fgColor="E2EFDA")
WARN_FONT = Font(name="微软雅黑", size=10, bold=True, color="C00000")
OK_FONT = Font(name="微软雅黑", size=10, bold=True, color="006100")


def calc_risk_score(closes, period):
    """计算风险评分: ROC + MA偏离 + RSI"""
    params = PERIOD_PARAMS[period]
    roc_len = params["roc_len"]
    ma_len = params["ma_len"]
    rsi_len = params["rsi_len"]
    scores = []

    for i in range(len(closes)):
        score = 0.0

        # ROC (动量)
        if i >= roc_len:
            roc = (closes[i] / closes[i - roc_len] - 1) * 100
            if roc > 4: score += 1.5
            elif roc > 2.5: score += 1
            elif roc > 1: score += 0.5
            elif roc < -4: score -= 1.5
            elif roc < -2.5: score -= 1
            elif roc < -1: score -= 0.5

        # MA偏离
        if i >= ma_len:
            ma = sum(closes[i - ma_len:i]) / ma_len
            ma_dist = (closes[i] / ma - 1) * 100
            if ma_dist > 4: score += 1
            elif ma_dist > 2: score += 0.5
            elif ma_dist < -4: score -= 1
            elif ma_dist < -2: score -= 0.5

        # RSI
        if i >= rsi_len:
            gains = losses = 0
            for j in range(i - rsi_len + 1, i + 1):
                chg = closes[j] - closes[j - 1]
                if chg > 0: gains += chg
                else: losses -= chg
            gains /= rsi_len
            losses /= rsi_len
            rsi = 100 if losses == 0 else 100 - 100 / (1 + gains / losses)
            if rsi > 70: score += 0.5
            elif rsi < 30: score -= 0.5

        score = max(-3, min(3, score))
        scores.append(int(round(score)))

    return scores


def fetch_realtime_quote(symbol):
    """从新浪实时行情接口获取当天数据，补充历史K线的T+1滞后。
    返回 dict {date, open, high, low, close, volume} 或 None。
    """
    import requests
    url = f"https://hq.sinajs.cn/list={symbol}"
    headers = {"Referer": "https://finance.sina.com.cn"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.encoding = "gbk"
        data = r.text.split('"')
        if len(data) < 2 or not data[1]:
            return None
        fields = data[1].split(",")
        if len(fields) < 32:
            return None
        # 新浪实时字段: 0=名称, 1=今开, 2=昨收, 3=当前价, 4=最高, 5=最低,
        #              6~7=买1价/量, 8~9=卖1价/量, ..., 8=成交量(股), 30=日期, 31=时间
        today = fields[30].strip()
        close = float(fields[3])
        open_p = float(fields[1])
        high = float(fields[4])
        low = float(fields[5])
        volume = float(fields[8])  # 成交量(股)
        if close <= 0 or open_p <= 0:
            return None
        return {
            "date": today,
            "open": open_p,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    except Exception as e:
        print(f"  [实时行情获取失败] {symbol}: {e}")
        return None


def fetch_kline_data(etf):
    """用akshare获取K线历史数据 + 新浪实时接口补充当天数据"""
    import akshare as ak
    code = etf["code"]
    symbol = etf["symbol"]
    etf_type = etf["type"]

    print(f"  获取 {etf['name']} ({code}) 数据...", end=" ")

    if etf_type == "index":
        # 指数: 用stock_zh_index_daily
        df = ak.stock_zh_index_daily(symbol=symbol)
    elif etf_type == "futures":
        # 期货主连: 用futures_zh_daily_sina (symbol传品种代码如I0)
        df = ak.futures_zh_daily_sina(symbol=etf["code"])
    else:
        # ETF: 用fund_etf_hist_sina
        df = ak.fund_etf_hist_sina(symbol=symbol)

    print(f"共{len(df)}条历史数据", end="")

    # 统一列名
    if etf_type == "index":
        df = df.rename(columns={"date": "date", "open": "open", "high": "high",
                                "low": "low", "close": "close", "volume": "volume"})
    elif etf_type == "futures":
        # futures_zh_daily_sina列名: date, open, high, low, close, volume, hold, settle
        # 已有 date/open/high/low/close/volume，无需重命名
        pass
    else:
        # akshare ETF列名: date, open, high, low, close, volume
        pass

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # 统一从 2020-11-16 (科创50ETF上市日) 开始，便于历史复盘
    start_ts = pd.Timestamp(START_DATE)
    df = df[df["date"] >= start_ts].reset_index(drop=True)

    # ---- 补充当天实时数据 (期货历史数据已含当天，跳过) ----
    if etf_type != "futures":
        rt = fetch_realtime_quote(symbol)
        if rt:
            today_ts = pd.Timestamp(rt["date"])
            last_ts = df.iloc[-1]["date"] if len(df) > 0 else None
            if last_ts is None or today_ts > last_ts:
                new_row = pd.DataFrame([{
                    "date": today_ts,
                    "open": rt["open"],
                    "high": rt["high"],
                    "low": rt["low"],
                    "close": rt["close"],
                    "volume": rt["volume"],
                }])
                df = pd.concat([df, new_row], ignore_index=True)
                print(f" +实时[{rt['date']}]", end="")
    print(f" → 最终{len(df)}条")

    result = {}
    for period_info in PERIODS:
        period = period_info["name"]
        max_bars = period_info["max_bars"]

        if period == "daily":
            # 日K: 从起始日起的全部数据（不超过max_bars）
            df_p = df.tail(max_bars).copy()
        else:
            # 周K/月K: 重采样
            df_idx = df.set_index("date")
            if period == "weekly":
                df_p = df_idx.resample("W-FRI").agg({
                    "open": "first", "high": "max", "low": "min",
                    "close": "last", "volume": "sum"
                }).dropna().tail(max_bars).reset_index()
            else:  # monthly
                df_p = df_idx.resample("ME").agg({
                    "open": "first", "high": "max", "low": "min",
                    "close": "last", "volume": "sum"
                }).dropna().tail(max_bars).reset_index()

        # 计算风险评级
        closes = df_p["close"].tolist()
        scores = calc_risk_score(closes, period)

        records = []
        for i in range(len(df_p)):
            row = df_p.iloc[i]
            # 相对上一交易日收盘价的涨跌幅
            prev_close = closes[i - 1] if i > 0 else None
            if prev_close and prev_close != 0:
                intraday_up = (float(row["high"]) - prev_close) / prev_close
                intraday_down = (float(row["low"]) - prev_close) / prev_close
                close_change = (float(row["close"]) - prev_close) / prev_close
            else:
                intraday_up = intraday_down = close_change = None

            records.append({
                "date": row["date"].strftime("%Y-%m-%d"),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
                "risk_score": scores[i],
                "risk_label": RISK_LABELS[scores[i]],
                "intraday_up": intraday_up,
                "intraday_down": intraday_down,
                "close_change": close_change,
            })

        result[period] = records
        risk_n = sum(1 for r in records if r["risk_score"] == 3)
        opp_n = sum(1 for r in records if r["risk_score"] == -3)
        print(f"    {period_info['label']}: {len(records)}条 ({records[0]['date']}~{records[-1]['date']}) 巨大风险={risk_n} 巨大机会={opp_n}")

    return result


def fetch_csindex_latest_pe():
    """从中证指数公司获取各指数最新PE(总股本口径)，作为权威数据源覆盖。

    中证指数公司仅提供最近20个交易日数据，无历史百分位，
    因此仅用于覆盖最新一天的PE值，百分位仍由乐咕乐股历史数据计算。
    创业板指(399006)由深交所发布，不在中证覆盖范围。
    返回: {index_code: {"date": "YYYY-MM-DD", "pe": float}}
    """
    from curl_cffi import requests as cffi_requests
    import time
    from io import BytesIO

    csindex_map = {
        "000016": "510050",   # 上证50
        "000300": "510300",   # 沪深300
        "000688": "588000",   # 科创50
        "000905": "510500",   # 中证500
        "000852": "000852",   # 中证1000
    }
    result = {}
    for cs_code, etf_code in csindex_map.items():
        url = f"https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/indicator/{cs_code}indicator.xls"
        for attempt in range(6):
            try:
                r = cffi_requests.get(url, impersonate="chrome", timeout=20)
                if r.status_code == 200:
                    df = pd.read_excel(BytesIO(r.content))
                    latest = df.iloc[0]
                    pe = float(latest["市盈率1（总股本）P/E1"])
                    dt = pd.to_datetime(str(latest["日期Date"]), format="%Y%m%d").strftime("%Y-%m-%d")
                    result[etf_code] = {"date": dt, "pe": round(pe, 2)}
                    print(f"  中证 {cs_code}: PE={pe:.2f} ({dt})")
                    break
            except Exception:
                time.sleep(2)
        else:
            print(f"  中证 {cs_code}: 获取失败")
        time.sleep(1)
    return result


def fetch_pe_data():
    """获取各标的的月度PE(TTM)数据，用于计算动态市盈率和百分位。

    数据来源: 乐咕乐股 (legulegu.com) 指数市盈率API
    使用 addTtmPe (滚动市盈率, 市值加权) 作为 PE(TTM) 值。
    铁矿石无PE数据，跳过。
    返回: {code: [{"date": "YYYY-MM-DD", "pe_ttm": float}, ...]}
    """
    import re
    import requests
    import py_mini_racer
    from akshare.stock_feature.stock_a_pe_and_pb import hash_code

    # 1. 生成 token (乐咕乐股的日期token校验)
    js = py_mini_racer.MiniRacer()
    js.eval(hash_code)
    token = js.call("hex", datetime.now().date().isoformat()).lower()

    # 2. 获取 CSRF token (从乐咕乐股指数列表页)
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    csrf_url = "https://legulegu.com/stockdata/index-basic-main-index"
    r = session.get(csrf_url, timeout=15)
    csrf_match = re.search(r'name="_csrf"\s+content="([^"]+)"', r.text)
    if not csrf_match:
        print("  无法获取CSRF token，PE数据获取失败")
        return {}
    csrf_token = csrf_match.group(1)
    api_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "X-CSRF-Token": csrf_token,
    }

    api_url = "https://legulegu.com/api/stockdata/index-basic-pe"
    result = {}
    for etf in UNDERLYINGS:
        code = etf["code"]
        index_code = PE_INDEX_MAP.get(code)
        if not index_code:
            print(f"  {etf['name']} ({code}): 无PE数据，跳过")
            continue
        try:
            print(f"  获取 {etf['name']} ({code}) PE数据 [{index_code}]...", end=" ")
            params = {"token": token, "indexCode": index_code}
            r = session.get(api_url, params=params, headers=api_headers, timeout=15)
            data = r.json()
            if "data" not in data or not data["data"]:
                print("无数据")
                continue
            df = pd.DataFrame(data["data"])
            # 使用 addTtmPe (滚动市盈率, 市值加权)，与中证指数公司发布口径一致
            records = []
            for _, row in df.iterrows():
                pe = row.get("addTtmPe")
                if pd.isna(pe) or pe is None or pe <= 0:
                    continue
                rec = {
                    "date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
                    "pe_ttm": round(float(pe), 2),
                }
                # 乐咕乐股API仅最新一条返回百分位，直接采用以保证一致
                q = row.get("addTtmPeQuantile")
                if q is not None and not pd.isna(q):
                    rec["quantile"] = round(float(q) * 100, 1)
                records.append(rec)
            result[code] = records
            print(f"{len(records)}条")
        except Exception as e:
            print(f"失败: {e}")

    # 中证指数公司数据仅有20天且无百分位，暂不覆盖以保持PE值与百分位口径一致
    # 如需启用中证最新PE覆盖，取消下方注释即可
    # print("  从中证指数公司获取最新PE...")
    # csindex_pe = fetch_csindex_latest_pe()
    # for code, info in csindex_pe.items():
    #     if code in result and result[code]:
    #         target_date = info["date"]
    #         for rec in result[code]:
    #             if rec["date"] == target_date:
    #                 rec["pe_ttm"] = info["pe"]
    #                 rec.pop("quantile", None)
    #                 break
    #         else:
    #             result[code].append({"date": target_date, "pe_ttm": info["pe"]})
    return result


def save_pe_data(pe_data, output_dir="/workspace/kline_data"):
    """保存PE数据到 pe_data.json"""
    path = os.path.join(output_dir, "pe_data.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pe_data, f, ensure_ascii=False, indent=2)
    print(f"PE数据已保存到 {path}")


# 图表布局 (与 build_workbook.py 一致) - 数据列 A-K，图表从 L 列开始
CHART_DIR = "/workspace/kline_charts"
CHART_ANCHORS = [
    ("daily",   1, "L"),    # 日K图置顶
    ("weekly",  20, "L"),   # 周K图
    ("monthly", 39, "L"),   # 月K图
]
CHART_DISPLAY_W = 760
CHART_DISPLAY_H = 280


def draw_chart(records, title, outfile, period_cn="日"):
    """从records生成K线图PNG"""
    n = len(records)
    fig_w = max(12, min(20, n * 0.05))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(fig_w, 6.5), gridspec_kw={
        "height_ratios": [3, 1], "hspace": 0.12
    }, sharex=True)

    dates = [mdates.date2num(pd.Timestamp(r["date"])) for r in records]
    opens = [r["open"] for r in records]
    highs = [r["high"] for r in records]
    lows = [r["low"] for r in records]
    closes = [r["close"] for r in records]
    scores = [r["risk_score"] for r in records]
    width = 0.6

    for i in range(n):
        d = dates[i]
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        color = "#d32f2f" if c >= o else "#388e3c"
        ax1.plot([d, d], [l, h], color=color, linewidth=0.5, zorder=2)
        body_bottom = min(o, c)
        body_height = max(abs(c - o), 1e-6)
        rect = Rectangle((d - width / 2, body_bottom), width, body_height,
                         facecolor=color, edgecolor=color, zorder=3)
        ax1.add_patch(rect)

    # 巨大风险/机会标记
    handles = []
    risk_idx = [i for i, s in enumerate(scores) if s == 3]
    opp_idx = [i for i, s in enumerate(scores) if s == -3]
    if risk_idx:
        ax1.scatter([dates[i] for i in risk_idx], [highs[i] * 1.015 for i in risk_idx],
                    marker="v", s=80, color="#ff1744", edgecolors="black", linewidths=0.7, zorder=10)
        handles.append(Line2D([0], [0], marker="v", color="w", markerfacecolor="#ff1744",
                               markeredgecolor="black", markersize=9, label="巨大风险"))
    if opp_idx:
        ax1.scatter([dates[i] for i in opp_idx], [lows[i] * 0.985 for i in opp_idx],
                    marker="^", s=80, color="#00c853", edgecolors="black", linewidths=0.7, zorder=10)
        handles.append(Line2D([0], [0], marker="^", color="w", markerfacecolor="#00c853",
                               markeredgecolor="black", markersize=9, label="巨大机会"))

    ax1.set_title(title, fontsize=12, fontweight="bold")
    ax1.set_ylabel("价格", fontsize=10)
    if handles:
        ax1.legend(handles=handles, loc="upper left", fontsize=8)

    # 风险评分柱状图
    colors_map = {-3: "#00c853", -2: "#69f0ae", -1: "#b9f6ca",
                  0: "#bdbdbd", 1: "#ffab91", 2: "#ff5252", 3: "#d50000"}
    bar_colors = [colors_map[s] for s in scores]
    ax2.bar(dates, scores, width=width, color=bar_colors, edgecolor="none")
    ax2.axhline(y=0, color="gray", linewidth=0.5)
    ax2.axhline(y=3, color="#d50000", linewidth=0.5, linestyle="--", alpha=0.4)
    ax2.axhline(y=-3, color="#00c853", linewidth=0.5, linestyle="--", alpha=0.4)
    ax2.set_ylabel("风险评分", fontsize=9)
    ax2.set_ylim(-3.5, 3.5)
    ax2.set_yticks(range(-3, 4))

    if period_cn == "月":
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    else:
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right", fontsize=7)

    fig.tight_layout()
    fig.savefig(outfile, dpi=130, bbox_inches="tight")
    plt.close(fig)


def regenerate_charts(ws, kline_data, etf_name):
    """重新生成K线图PNG并替换工作表中的旧图"""
    os.makedirs(CHART_DIR, exist_ok=True)
    period_cn_map = {"daily": "日", "weekly": "周", "monthly": "月"}

    # 生成3张图表PNG
    chart_paths = {}
    for period, anchor_row, col_letter in CHART_ANCHORS:
        records = kline_data[period]
        cn = period_cn_map[period]
        title = f"{etf_name} · {cn}K线 · 巨大风险/机会标注"
        outfile = os.path.join(CHART_DIR, f"{ws.title.split('-')[-1]}_{period}.png")
        # 用ETF code作为文件名 (从sheet名提取)
        outfile = os.path.join(CHART_DIR, f"{etf_name}_{period}.png")
        draw_chart(records, title, outfile, cn)
        chart_paths[period] = outfile

    # 清除旧图片
    ws._images = []

    # 添加新图片
    for period, anchor_row, col_letter in CHART_ANCHORS:
        path = chart_paths[period]
        if os.path.exists(path):
            img = XlImage(path)
            img.width = CHART_DISPLAY_W
            img.height = CHART_DISPLAY_H
            ws.add_image(img, f"{col_letter}{anchor_row}")

    # 更新统计区
    stats_font = Font(name="微软雅黑", size=9, color="333333")
    stats_bold_font = Font(name="微软雅黑", size=9, bold=True, color="1F4E78")
    stats_fill = PatternFill("solid", fgColor="F8F9FA")

    for period, anchor_row, _ in CHART_ANCHORS:
        records = kline_data[period]
        cn = period_cn_map[period]
        closes = [r["close"] for r in records]
        highs = [r["high"] for r in records]
        lows = [r["low"] for r in records]
        total = len(records)
        risk_n = sum(1 for r in records if r["risk_score"] == 3)
        opp_n = sum(1 for r in records if r["risk_score"] == -3)
        ret_pct = (closes[-1] / closes[0] - 1) * 100 if closes[0] else 0

        stats_row = anchor_row + 14
        stats_lines = [
            (f"【{cn}K统计】", stats_bold_font),
            (f"数据条数: {total}条 ({records[0]['date']} ~ {records[-1]['date']})", stats_font),
            (f"最高价: {max(highs):.4f}  最低价: {min(lows):.4f}", stats_font),
            (f"期初价: {closes[0]:.4f}  期末价: {closes[-1]:.4f}  涨幅: {ret_pct:+.2f}%", stats_font),
            (f"巨大风险: {risk_n}天 ({risk_n/total*100:.1f}%)  巨大机会: {opp_n}天 ({opp_n/total*100:.1f}%)", stats_font),
        ]
        for i, (text, font) in enumerate(stats_lines):
                r = stats_row + i
                cell = ws.cell(row=r, column=12, value=text)
                cell.font = font
                cell.fill = stats_fill
                cell.alignment = Alignment(horizontal="left", vertical="center")
                ws.merge_cells(start_row=r, start_column=12, end_row=r, end_column=20)

    print(f"  {etf_name} 图表已重新生成")


def update_worksheet(ws, kline_data, etf):
    """更新工作表中的K线数据"""
    for period_info in PERIODS:
        period = period_info["name"]
        records = kline_data[period]
        start_row = period_info["start_row"]
        end_row = period_info["end_row"]
        hdr_row = period_info["hdr_row"]

        # 更新副标题
        risk_n = sum(1 for r in records if r["risk_score"] == 3)
        opp_n = sum(1 for r in records if r["risk_score"] == -3)
        header_text = f"【{period_info['label']}数据】共{len(records)}条 · 巨大风险={risk_n}天 · 巨大机会={opp_n}天"
        ws.cell(row=hdr_row, column=1, value=header_text)

        # 清除旧数据 (start_row到end_row, A-K列 共11列)
        for r in range(start_row, end_row + 1):
            for c in range(1, 12):
                cell = ws.cell(row=r, column=c)
                cell.value = None
                cell.fill = PatternFill(fill_type=None)
                cell.font = Font(name="微软雅黑", size=10)
                cell.border = BORDER

        # 写入新数据
        for idx, rec in enumerate(records):
            row = start_row + idx
            ws.cell(row=row, column=1, value=rec["date"]).alignment = CENTER
            ws.cell(row=row, column=2, value=rec["open"]).number_format = "0.0000"
            ws.cell(row=row, column=3, value=rec["high"]).number_format = "0.0000"
            ws.cell(row=row, column=4, value=rec["low"]).number_format = "0.0000"
            ws.cell(row=row, column=5, value=rec["close"]).number_format = "0.0000"
            ws.cell(row=row, column=6, value=rec["volume"]).number_format = "#,##0"
            ws.cell(row=row, column=7, value=rec["risk_score"]).alignment = CENTER
            label_cell = ws.cell(row=row, column=8, value=rec["risk_label"])
            label_cell.alignment = CENTER

            # I/J/K: 期中涨幅, 期中跌幅, 收盘涨跌 (百分比)
            for col_idx, key in [(9, "intraday_up"), (10, "intraday_down"), (11, "close_change")]:
                val = rec.get(key)
                if val is not None:
                    cell = ws.cell(row=row, column=col_idx, value=val)
                    cell.number_format = "0.00%"
                    cell.alignment = CENTER

            # 风险高亮 (A-K 共11列)
            score = rec["risk_score"]
            if score == 3:
                for c in range(1, 12):
                    ws.cell(row=row, column=c).fill = RISK3_FILL
                ws.cell(row=row, column=8).font = WARN_FONT
            elif score == 2:
                for c in range(1, 12):
                    ws.cell(row=row, column=c).fill = RISK2_FILL
            elif score == -3:
                for c in range(1, 12):
                    ws.cell(row=row, column=c).fill = OPP3_FILL
                ws.cell(row=row, column=8).font = OK_FONT
            elif score == -2:
                for c in range(1, 12):
                    ws.cell(row=row, column=c).fill = OPP2_FILL

            for c in range(1, 12):
                ws.cell(row=row, column=c).border = BORDER

    print(f"  {etf['name']} 工作表已更新")


def log_update(wb, update_type, action, result, remark):
    """记录更新日志"""
    ws = wb["更新日志"]
    next_row = ws.max_row + 1
    if next_row < 4:
        next_row = 4
    ws.cell(row=next_row, column=1, value=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    ws.cell(row=next_row, column=2, value=update_type)
    ws.cell(row=next_row, column=3, value=action)
    ws.cell(row=next_row, column=4, value=result)
    ws.cell(row=next_row, column=5, value=remark)


def main():
    print("=" * 60)
    print("期权时空容错交易系统 - Python数据更新")
    print("=" * 60)

    # 检查akshare
    try:
        import akshare as ak
        print(f"akshare版本: {ak.__version__}")
    except ImportError:
        print("正在安装akshare...")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "akshare", "-q",
                       "-i", "https://mirrors.aliyun.com/pypi/simple/"])
        import akshare as ak
        print(f"akshare版本: {ak.__version__}")

    # 打开工作簿
    if not os.path.exists(XLSX_FILE):
        print(f"错误: 找不到文件 {XLSX_FILE}")
        return

    print(f"\n打开工作簿: {XLSX_FILE}")
    wb = openpyxl.load_workbook(XLSX_FILE)

    success_count = 0
    fail_detail = ""

    for etf in UNDERLYINGS:
        print(f"\n--- {etf['name']} ({etf['code']}) ---")
        try:
            # 获取数据
            kline_data = fetch_kline_data(etf)

            # 更新工作表数据
            ws = wb[etf["sheet"]]
            update_worksheet(ws, kline_data, etf)

            # 重新生成K线图并替换旧图
            regenerate_charts(ws, kline_data, etf["name"])

            success_count += 1
        except Exception as e:
            fail_detail += f"{etf['name']}: {e}\n"
            print(f"  错误: {e}")

    # 记录日志
    total = len(UNDERLYINGS)
    if success_count == total:
        log_update(wb, "Python", "抓取K线数据", "成功", f"{total}/{total}个标的数据已更新")
    else:
        log_update(wb, "Python", "抓取K线数据", "部分成功",
                   f"{success_count}/{total}成功。{fail_detail[:100]}")

    # 保存
    print(f"\n保存工作簿: {XLSX_FILE}")
    wb.save(XLSX_FILE)

    # 修复 openpyxl 的 XML 命名空间 bug (同 build_workbook.py)
    import zipfile, shutil
    tmp_path = XLSX_FILE + ".tmp"
    with zipfile.ZipFile(XLSX_FILE, "r") as zin, zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item.startswith("xl/worksheets/sheet") and item.endswith(".xml"):
                xml = data.decode("utf-8")
                # 如果有 r:id 但根元素未声明 xmlns:r，则添加声明
                if "r:id=" in xml and 'xmlns:r=' not in xml[:300]:
                    xml = xml.replace(
                        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"',
                        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"',
                        1
                    )
                    data = xml.encode("utf-8")
            zout.writestr(item, data)
    shutil.move(tmp_path, XLSX_FILE)

    print("\n" + "=" * 60)
    print(f"更新完成! 成功: {success_count}/6")
    if fail_detail:
        print(f"失败详情:\n{fail_detail}")
    print("=" * 60)
    print(f"\n请打开 {XLSX_FILE} 查看最新数据")

    # 获取PE数据并保存
    print("\n--- 获取PE(TTM)数据 ---")
    try:
        pe_data = fetch_pe_data()
        save_pe_data(pe_data)
    except Exception as e:
        print(f"PE数据获取失败: {e}")


if __name__ == "__main__":
    main()
