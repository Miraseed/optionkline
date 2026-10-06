"""生成6个标的的真实K线数据与风险评级图表。

数据来源: akshare (新浪财经 / 指数数据)
样本起始: 2020-11-16 (科创50ETF上市日)，便于历史复盘
周期: 日K / 周K / 月K
"""
import json
import os
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D
import matplotlib.font_manager as fm

# ---------- 字体 ----------
for fp in [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]:
    if os.path.exists(fp):
        fm.fontManager.addfont(fp)
        plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei", "WenQuanYi Micro Hei", "Noto Sans CJK SC"]
        break
plt.rcParams["axes.unicode_minus"] = False

# 样本起始日 = 科创50ETF上市日
START_DATE = pd.Timestamp("2020-11-16")

# ---------- 7个标的 ----------
ETFS = [
    {"code": "510050", "name": "上证50ETF",   "symbol": "sh510050", "type": "etf"},
    {"code": "510300", "name": "沪深300ETF",  "symbol": "sh510300", "type": "etf"},
    {"code": "588000", "name": "科创50ETF",   "symbol": "sh588000", "type": "etf"},
    {"code": "159915", "name": "创业板ETF",   "symbol": "sz159915", "type": "etf"},
    {"code": "510500", "name": "中证500ETF",  "symbol": "sh510500", "type": "etf"},
    {"code": "000852", "name": "中证1000",    "symbol": "sh000852", "type": "index"},
    {"code": "I0",     "name": "铁矿石主连",   "symbol": "nf_I0",    "type": "futures"},
]

OUT_DIR = "/workspace/kline_data"
CHART_DIR = "/workspace/kline_charts"
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(CHART_DIR, exist_ok=True)

RISK_LABELS = {
    -3: "巨大机会", -2: "较大机会", -1: "一般机会",
    0: "中性",
    1: "一般风险", 2: "较大风险", 3: "巨大风险",
}


def fetch_daily(etf: dict) -> pd.DataFrame:
    """从akshare获取日K原始数据，统一列名并按日期升序排列。"""
    import akshare as ak
    if etf["type"] == "index":
        df = ak.stock_zh_index_daily(symbol=etf["symbol"])
    elif etf["type"] == "futures":
        df = ak.futures_zh_daily_sina(symbol=etf["code"])
    else:
        df = ak.fund_etf_hist_sina(symbol=etf["symbol"])
    df = df.rename(columns={"date": "date", "open": "open", "high": "high",
                            "low": "low", "close": "close", "volume": "volume"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    # 截断到 2020-11-16 起
    df = df[df["date"] >= START_DATE].reset_index(drop=True)
    return df[["date", "open", "high", "low", "close", "volume"]]


def resample_period(daily: pd.DataFrame, period: str) -> pd.DataFrame:
    """把日K重采样为周K/月K；日K直接返回。"""
    if period == "daily":
        return daily.copy()
    df_idx = daily.set_index("date")
    if period == "weekly":
        df = df_idx.resample("W-FRI").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna().reset_index()
    else:  # monthly
        df = df_idx.resample("ME").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna().reset_index()
    return df


def calc_risk_rating(df: pd.DataFrame, period: str = "daily") -> pd.DataFrame:
    """计算风险评级: 风险是涨出来的，机会是跌出来的。"""
    if period == "monthly":
        roc_len, ma_len, rsi_len = 2, 4, 6
    elif period == "weekly":
        roc_len, ma_len, rsi_len = 3, 8, 10
    else:
        roc_len, ma_len, rsi_len = 5, 20, 14

    closes = df["close"].values
    scores, labels = [], []

    for i in range(len(closes)):
        close = closes[i]
        # ROC
        roc = (close / closes[i - roc_len] - 1) * 100 if i >= roc_len else 0
        # MA偏离
        if i >= ma_len:
            ma = closes[i - ma_len:i].mean()
            ma_dist = (close / ma - 1) * 100
        elif i >= 2:
            ma_dist = (close / closes[max(0, i - 2):i + 1].mean() - 1) * 100
        else:
            ma_dist = 0
        # RSI
        if i >= rsi_len:
            deltas = pd.Series(closes[i - rsi_len:i + 1]).diff().dropna()
            gains = deltas.clip(lower=0).mean()
            losses = (-deltas.clip(upper=0)).mean()
            rsi = 100 if losses == 0 else 100 - 100 / (1 + gains / losses)
        else:
            rsi = 50

        score = 0
        if roc > 4:     score += 1.5
        elif roc > 2.5: score += 1
        elif roc > 1:   score += 0.5
        elif roc < -4:     score -= 1.5
        elif roc < -2.5: score -= 1
        elif roc < -1:   score -= 0.5

        if ma_dist > 4:     score += 1
        elif ma_dist > 2:   score += 0.5
        elif ma_dist < -4:  score -= 1
        elif ma_dist < -2:  score -= 0.5

        if rsi > 70:  score += 0.5
        elif rsi < 30: score -= 0.5

        score = max(-3, min(3, round(score)))
        scores.append(score)
        labels.append(RISK_LABELS[score])

    df = df.copy()
    df["risk_score"] = scores
    df["risk_label"] = labels
    return df


def draw_candlestick(df: pd.DataFrame, title: str, outfile: str, period: str = "日"):
    """绘制K线图 + 风险评分柱状图。数据量大时适度压缩宽度以便肉眼查看。"""
    n = len(df)
    # 控制图表宽度：数据越多单根K线越窄，但整体宽度封顶以便查看
    fig_w = max(12, min(20, n * 0.05))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(fig_w, 6.5), gridspec_kw={
        "height_ratios": [3, 1], "hspace": 0.12
    }, sharex=True)

    dates = mdates.date2num(df["date"])
    width = 0.6

    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

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

    # 巨大风险 / 巨大机会 标记
    risk_mask = df["risk_score"] == 3
    opp_mask = df["risk_score"] == -3
    handles = []

    if risk_mask.any():
        rd = dates[risk_mask.values]
        rh = highs[risk_mask.values]
        ax1.scatter(rd, rh * 1.015, marker="v", s=80,
                    color="#ff1744", edgecolors="black", linewidths=0.7, zorder=10)
        handles.append(Line2D([0], [0], marker="v", color="w", markerfacecolor="#ff1744",
                               markeredgecolor="black", markersize=9, label="巨大风险"))
    if opp_mask.any():
        od = dates[opp_mask.values]
        ol = lows[opp_mask.values]
        ax1.scatter(od, ol * 0.985, marker="^", s=80,
                    color="#00c853", edgecolors="black", linewidths=0.7, zorder=10)
        handles.append(Line2D([0], [0], marker="^", color="w", markerfacecolor="#00c853",
                               markeredgecolor="black", markersize=9, label="巨大机会"))

    ax1.set_title(title, fontsize=12, fontweight="bold")
    ax1.set_ylabel("价格", fontsize=10)
    if handles:
        ax1.legend(handles=handles, loc="upper left", fontsize=8)

    # 风险评分柱状图
    colors_map = {-3: "#00c853", -2: "#69f0ae", -1: "#b9f6ca",
                  0: "#bdbdbd",
                  1: "#ffab91", 2: "#ff5252", 3: "#d50000"}
    bar_colors = [colors_map[s] for s in df["risk_score"]]
    ax2.bar(dates, df["risk_score"], width=width, color=bar_colors, edgecolor="none")
    ax2.axhline(y=0, color="gray", linewidth=0.5)
    ax2.axhline(y=3, color="#d50000", linewidth=0.5, linestyle="--", alpha=0.4)
    ax2.axhline(y=-3, color="#00c853", linewidth=0.5, linestyle="--", alpha=0.4)
    ax2.set_ylabel("风险评分", fontsize=9)
    ax2.set_ylim(-3.5, 3.5)
    ax2.set_yticks(range(-3, 4))

    # X轴日期格式
    if period == "月":
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    elif period == "周":
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    else:
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right", fontsize=7)

    fig.tight_layout()
    fig.savefig(outfile, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"  Chart: {outfile}")


def main():
    import akshare as ak
    print(f"akshare版本: {ak.__version__}")
    print(f"样本起始日: {START_DATE.strftime('%Y-%m-%d')} (科创50ETF上市日)\n")

    for etf in ETFS:
        code, name = etf["code"], etf["name"]
        print(f"=== {name} ({code}) ===")

        # 获取日K原始数据 (2020-11-16 起)
        daily = fetch_daily(etf)
        print(f"  日K原始: {len(daily)} 条 ({daily.iloc[0]['date'].date()} ~ {daily.iloc[-1]['date'].date()})")

        for period, label in [("daily", "日K线"), ("weekly", "周K线"), ("monthly", "月K线")]:
            df = resample_period(daily, period)
            df = calc_risk_rating(df, period)

            # 计算相对上一交易日收盘价的涨跌幅：期中涨幅、期中跌幅、收盘涨跌
            df["prev_close"] = df["close"].shift(1)
            df["intraday_up"] = (df["high"] - df["prev_close"]) / df["prev_close"]
            df["intraday_down"] = (df["low"] - df["prev_close"]) / df["prev_close"]
            df["close_change"] = (df["close"] - df["prev_close"]) / df["prev_close"]

            # 保存 JSON
            records = []
            for _, row in df.iterrows():
                # 首日无上一交易日收盘价，三个字段为 None
                iu = float(row["intraday_up"]) if pd.notna(row["intraday_up"]) else None
                idn = float(row["intraday_down"]) if pd.notna(row["intraday_down"]) else None
                cc = float(row["close_change"]) if pd.notna(row["close_change"]) else None
                records.append({
                    "date": row["date"].strftime("%Y-%m-%d"),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "risk_score": int(row["risk_score"]),
                    "risk_label": row["risk_label"],
                    "intraday_up": iu,
                    "intraday_down": idn,
                    "close_change": cc,
                })
            path = os.path.join(OUT_DIR, f"{code}_{period}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)

            risk_n = sum(1 for r in records if r["risk_score"] == 3)
            opp_n = sum(1 for r in records if r["risk_score"] == -3)
            print(f"  {label}: {len(records)} 条, 巨大风险={risk_n}, 巨大机会={opp_n}")

            # 生成图表
            period_cn = {"daily": "日", "weekly": "周", "monthly": "月"}[period]
            draw_candlestick(df, f"{name} · {label} · 巨大风险/机会标注",
                             os.path.join(CHART_DIR, f"{code}_{period}.png"), period_cn)

    print("\n=== 全部数据与图表生成完成 ===")


if __name__ == "__main__":
    main()
