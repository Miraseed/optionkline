"""Update K-line data and charts using real data from akshare.

Reads real market data from kline_data/*.json (fetched via akshare),
calculates risk ratings, and regenerates candlestick charts.
"""
import json
import os
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
import matplotlib.font_manager as fm

# ---------- font setup ----------
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

KLINE_DIR = "kline_data"
CHART_DIR = "kline_charts"
os.makedirs(CHART_DIR, exist_ok=True)

ETFS = [
    {"code": "510050", "name": "上证50ETF"},
    {"code": "510300", "name": "沪深300ETF"},
    {"code": "588000", "name": "科创50ETF"},
    {"code": "159915", "name": "创业板ETF"},
    {"code": "510500", "name": "中证500ETF"},
    {"code": "000852", "name": "中证1000"},
]

# 风险评级参数 (Python标准)
PERIOD_PARAMS = {
    "daily":   {"roc_len": 5,  "ma_len": 20, "rsi_len": 14},
    "weekly":  {"roc_len": 3,  "ma_len": 8,  "rsi_len": 10},
    "monthly": {"roc_len": 2,  "ma_len": 4,  "rsi_len": 6},
}

RISK_LABELS = {
    -3: "巨大机会", -2: "较大机会", -1: "一般机会",
    0: "中性",
    1: "一般风险", 2: "较大风险", 3: "巨大风险",
}


def calc_risk_score(df, period):
    """Calculate risk score using ROC + MA deviation + RSI."""
    params = PERIOD_PARAMS[period]
    roc_len = params["roc_len"]
    ma_len = params["ma_len"]
    rsi_len = params["rsi_len"]

    closes = df["close"].values
    scores = []

    for i in range(len(closes)):
        score = 0.0

        # ROC (动量)
        if i >= roc_len:
            roc = (closes[i] / closes[i - roc_len] - 1) * 100
            if roc > 4:
                score += 1.5
            elif roc > 2.5:
                score += 1
            elif roc > 1:
                score += 0.5
            elif roc < -4:
                score -= 1.5
            elif roc < -2.5:
                score -= 1
            elif roc < -1:
                score -= 0.5

        # MA偏离
        if i >= ma_len:
            ma = sum(closes[i - ma_len:i]) / ma_len
            ma_dist = (closes[i] / ma - 1) * 100
            if ma_dist > 4:
                score += 1
            elif ma_dist > 2:
                score += 0.5
            elif ma_dist < -4:
                score -= 1
            elif ma_dist < -2:
                score -= 0.5
        elif i >= 2:
            ma = sum(closes[:i]) / i if i > 0 else closes[0]
            ma_dist = (closes[i] / ma - 1) * 100
            if ma_dist > 4:
                score += 1
            elif ma_dist > 2:
                score += 0.5
            elif ma_dist < -4:
                score -= 1
            elif ma_dist < -2:
                score -= 0.5

        # RSI
        if i >= rsi_len:
            gains = 0
            losses = 0
            for j in range(i - rsi_len + 1, i + 1):
                chg = closes[j] - closes[j - 1]
                if chg > 0:
                    gains += chg
                else:
                    losses -= chg
            gains /= rsi_len
            losses /= rsi_len
            if losses == 0:
                rsi = 100
            else:
                rsi = 100 - 100 / (1 + gains / losses)
            if rsi > 70:
                score += 0.5
            elif rsi < 30:
                score -= 0.5

        # 限制范围并四舍五入
        score = max(-3, min(3, score))
        scores.append(int(round(score)))

    return scores


def draw_candlestick(df, title, savepath, period_cn):
    """Draw candlestick chart with risk markers."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7),
                                    gridspec_kw={"height_ratios": [3, 1]},
                                    sharex=True)

    dates = range(len(df))
    opens = df["open"].values
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    volumes = df["volume"].values
    scores = df["risk_score"].values

    # 绘制K线
    for i in range(len(df)):
        color = "#d32f2f" if closes[i] < opens[i] else "#388e3c"
        # 影线
        ax1.plot([i, i], [lows[i], highs[i]], color=color, linewidth=0.8)
        # 实体
        body_bottom = min(opens[i], closes[i])
        body_height = abs(closes[i] - opens[i])
        if body_height < 0.0001:
            body_height = (highs[i] - lows[i]) * 0.01
        ax1.bar(i, body_height, bottom=body_bottom, width=0.6,
                color=color, edgecolor=color)

    # 标注巨大风险(3)和巨大机会(-3)
    for i in range(len(df)):
        if scores[i] == 3:
            ax1.annotate("▼", (i, highs[i]), textcoords="offset points",
                        xytext=(0, 8), ha="center", color="#c00000",
                        fontsize=9, fontweight="bold")
        elif scores[i] == -3:
            ax1.annotate("▲", (i, lows[i]), textcoords="offset points",
                        xytext=(0, -12), ha="center", color="#006100",
                        fontsize=9, fontweight="bold")

    ax1.set_title(title, fontsize=13, fontweight="bold")
    ax1.set_ylabel("价格", fontsize=10)
    ax1.grid(True, alpha=0.3)

    # 成交量
    for i in range(len(df)):
        color = "#d32f2f" if closes[i] < opens[i] else "#388e3c"
        ax2.bar(i, volumes[i], width=0.6, color=color, alpha=0.7)
    ax2.set_ylabel("成交量", fontsize=10)
    ax2.grid(True, alpha=0.3)

    # X轴日期标签 (每隔N条显示)
    n = len(df)
    step = max(1, n // 10)
    tick_positions = list(range(0, n, step))
    tick_labels = [df["date"].iloc[i] for i in tick_positions]
    ax2.set_xticks(tick_positions)
    ax2.set_xticklabels(tick_labels, rotation=30, ha="right", fontsize=8)

    plt.tight_layout()
    plt.savefig(savepath, dpi=100, bbox_inches="tight")
    plt.close()


def main():
    periods = [
        ("daily",   "日K线"),
        ("weekly",  "周K线"),
        ("monthly", "月K线"),
    ]

    for etf in ETFS:
        code, name = etf["code"], etf["name"]
        print(f"\n=== {name} ({code}) ===")

        for period, label in periods:
            path = os.path.join(KLINE_DIR, f"{code}_{period}.json")
            if not os.path.exists(path):
                print(f"  {label}: 数据文件缺失!")
                continue

            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f)

            if len(records) == 0:
                print(f"  {label}: 数据为空!")
                continue

            df = pd.DataFrame(records)
            df["date"] = pd.to_datetime(df["date"])

            # 计算风险评级
            scores = calc_risk_score(df, period)
            df["risk_score"] = scores
            df["risk_label"] = [RISK_LABELS[s] for s in scores]

            # 保存更新后的JSON (含risk_score和risk_label)
            updated = []
            for _, row in df.iterrows():
                updated.append({
                    "date": row["date"].strftime("%Y-%m-%d"),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                    "risk_score": int(row["risk_score"]),
                    "risk_label": row["risk_label"],
                })

            with open(path, "w", encoding="utf-8") as f:
                json.dump(updated, f, ensure_ascii=False)

            risk_n = sum(1 for r in updated if r["risk_score"] == 3)
            opp_n = sum(1 for r in updated if r["risk_score"] == -3)
            print(f"  {label}: {len(updated)}条 ({updated[0]['date']}~{updated[-1]['date']}) | 巨大风险={risk_n} 巨大机会={opp_n}")

            # 生成K线图
            period_cn = {"daily": "日", "weekly": "周", "monthly": "月"}[period]
            draw_candlestick(df, f"{name} · {label} · 巨大风险标注",
                            os.path.join(CHART_DIR, f"{code}_{period}.png"), period_cn)

    print("\n=== 真实数据更新完成 ===")


if __name__ == "__main__":
    main()
