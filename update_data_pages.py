"""
期权时空容错交易系统 - GitHub Pages 数据更新脚本
功能：拉取 7 个标的的 K 线数据 + PE(TTM) 数据，保存为 JSON 供 GitHub Pages 静态站点使用
用法：python3 update_data_pages.py
"""
import json
import os
import sys
from datetime import datetime

import pandas as pd

# ============ 配置 ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "kline_data")
os.makedirs(DATA_DIR, exist_ok=True)

START_DATE = "2020-11-16"

UNDERLYINGS = [
    {"code": "510050", "name": "上证50ETF", "symbol": "sh510050", "type": "etf"},
    {"code": "510300", "name": "沪深300ETF", "symbol": "sh510300", "type": "etf"},
    {"code": "588000", "name": "科创50ETF", "symbol": "sh588000", "type": "etf"},
    {"code": "159915", "name": "创业板ETF", "symbol": "sz159915", "type": "etf"},
    {"code": "510500", "name": "中证500ETF", "symbol": "sh510500", "type": "etf"},
    {"code": "000852", "name": "中证1000", "symbol": "sh000852", "type": "index"},
    {"code": "I0", "name": "铁矿石主连", "symbol": "nf_I0", "type": "futures"},
]

PE_INDEX_MAP = {
    "510050": "000016.SH", "510300": "000300.SH", "588000": "000688.SH",
    "159915": "399006.SZ", "510500": "000905.SH", "000852": "000852.SH",
    "I0": None,
}

PERIODS = [
    {"name": "daily", "max_bars": 1500},
    {"name": "weekly", "max_bars": 350},
    {"name": "monthly", "max_bars": 80},
]

PERIOD_PARAMS = {
    "daily": {"roc_len": 5, "ma_len": 20, "rsi_len": 14},
    "weekly": {"roc_len": 3, "ma_len": 8, "rsi_len": 10},
    "monthly": {"roc_len": 2, "ma_len": 4, "rsi_len": 6},
}

RISK_LABELS = {
    -3: "巨大机会", -2: "较大机会", -1: "一般机会", 0: "中性",
    1: "一般风险", 2: "较大风险", 3: "巨大风险",
}


def calc_risk_score(closes, period):
    params = PERIOD_PARAMS[period]
    roc_len, ma_len, rsi_len = params["roc_len"], params["ma_len"], params["rsi_len"]
    scores = []
    for i in range(len(closes)):
        score = 0.0
        if i >= roc_len:
            roc = (closes[i] / closes[i - roc_len] - 1) * 100
            if roc > 4: score += 1.5
            elif roc > 2.5: score += 1
            elif roc > 1: score += 0.5
            elif roc < -4: score -= 1.5
            elif roc < -2.5: score -= 1
            elif roc < -1: score -= 0.5
        if i >= ma_len:
            ma = sum(closes[i - ma_len:i]) / ma_len
            ma_dist = (closes[i] / ma - 1) * 100
            if ma_dist > 4: score += 1
            elif ma_dist > 2: score += 0.5
            elif ma_dist < -4: score -= 1
            elif ma_dist < -2: score -= 0.5
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
        today = fields[30].strip()
        close = float(fields[3])
        open_p = float(fields[1])
        high = float(fields[4])
        low = float(fields[5])
        volume = float(fields[8])
        if close <= 0 or open_p <= 0:
            return None
        return {"date": today, "open": open_p, "high": high, "low": low, "close": close, "volume": volume}
    except Exception as e:
        print(f"  [实时行情获取失败] {symbol}: {e}")
        return None


def fetch_futures_realtime_quote(symbol):
    """获取期货主连实时行情（通过 akshare.futures_zh_realtime，已正确解析新浪 nf_ 格式）"""
    import akshare as ak
    try:
        symbol_map = {"nf_I0": "铁矿石", "I0": "铁矿石"}
        name = symbol_map.get(symbol, "铁矿石")
        df = ak.futures_zh_realtime(symbol=name)
        main_contract = df[df["name"].str.contains("连续")]
        if len(main_contract) == 0:
            main_contract = df.head(1)
        row = main_contract.iloc[0]
        return {
            "date": str(row["tradedate"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        }
    except Exception as e:
        print(f"  [期货实时行情获取失败] {symbol}: {e}")
        return None


def fetch_kline_data(etf):
    import akshare as ak
    code = etf["code"]
    symbol = etf["symbol"]
    etf_type = etf["type"]
    print(f"  获取 {etf['name']} ({code}) 数据...", end=" ")

    if etf_type == "index":
        df = ak.stock_zh_index_daily(symbol=symbol)
    elif etf_type == "futures":
        df = ak.futures_zh_daily_sina(symbol=etf["code"])
    else:
        df = ak.fund_etf_hist_sina(symbol=symbol)

    print(f"共{len(df)}条历史数据", end="")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    start_ts = pd.Timestamp(START_DATE)
    df = df[df["date"] >= start_ts].reset_index(drop=True)

    if etf_type == "futures":
        rt = fetch_futures_realtime_quote(symbol)
    else:
        rt = fetch_realtime_quote(symbol)
    if rt:
        today_ts = pd.Timestamp(rt["date"])
        last_ts = df.iloc[-1]["date"] if len(df) > 0 else None
        if last_ts is None or today_ts > last_ts:
            new_row = pd.DataFrame([{"date": today_ts, "open": rt["open"], "high": rt["high"],
                "low": rt["low"], "close": rt["close"], "volume": rt["volume"]}])
            df = pd.concat([df, new_row], ignore_index=True)
            print(f" +实时[{rt['date']}]", end="")
        elif last_ts == today_ts:
            # 同一天，更新最后一行
            df.loc[df.index[-1], "close"] = rt["close"]
            df.loc[df.index[-1], "high"] = max(df.loc[df.index[-1], "high"], rt["high"])
            df.loc[df.index[-1], "low"] = min(df.loc[df.index[-1], "low"], rt["low"])
            df.loc[df.index[-1], "volume"] = rt["volume"]
            print(f" =更新[{rt['date']}]", end="")
    print(f" → 最终{len(df)}条")

    result = {}
    for period_info in PERIODS:
        period = period_info["name"]
        max_bars = period_info["max_bars"]
        if period == "daily":
            df_p = df.tail(max_bars).copy()
        else:
            df_idx = df.set_index("date")
            if period == "weekly":
                df_p = df_idx.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().tail(max_bars).reset_index()
            else:
                df_p = df_idx.resample("ME").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().tail(max_bars).reset_index()

        closes = df_p["close"].tolist()
        scores = calc_risk_score(closes, period)
        records = []
        for i in range(len(df_p)):
            row = df_p.iloc[i]
            prev_close = closes[i - 1] if i > 0 else None
            if prev_close and prev_close != 0:
                intraday_up = (float(row["high"]) - prev_close) / prev_close
                intraday_down = (float(row["low"]) - prev_close) / prev_close
                close_change = (float(row["close"]) - prev_close) / prev_close
            else:
                intraday_up = intraday_down = close_change = None
            records.append({
                "date": row["date"].strftime("%Y-%m-%d"),
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": int(row["volume"]), "risk_score": scores[i],
                "risk_label": RISK_LABELS[scores[i]],
                "intraday_up": intraday_up, "intraday_down": intraday_down, "close_change": close_change,
            })
        result[period] = records
        risk_n = sum(1 for r in records if r["risk_score"] == 3)
        opp_n = sum(1 for r in records if r["risk_score"] == -3)
        print(f"    {period}: {len(records)}条 巨大风险={risk_n} 巨大机会={opp_n}")
    return result


def fetch_pe_data():
    import re
    import requests
    import py_mini_racer
    from akshare.stock_feature.stock_a_pe_and_pb import hash_code

    js = py_mini_racer.MiniRacer()
    js.eval(hash_code)
    token = js.call("hex", datetime.now().date().isoformat()).lower()

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
            records = []
            for _, row in df.iterrows():
                pe = row.get("addTtmPe")
                if pd.isna(pe) or pe is None or pe <= 0:
                    continue
                rec = {"date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"), "pe_ttm": round(float(pe), 2)}
                q = row.get("addTtmPeQuantile")
                if q is not None and not pd.isna(q):
                    rec["quantile"] = round(float(q) * 100, 1)
                records.append(rec)
            result[code] = records
            print(f"{len(records)}条")
        except Exception as e:
            print(f"失败: {e}")
    return result


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    print("=" * 60)
    print("期权时空容错交易系统 - GitHub Pages 数据更新")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    try:
        import akshare as ak
        print(f"akshare版本: {ak.__version__}")
    except ImportError:
        print("正在安装akshare...")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "akshare", "-q"])
        import akshare as ak

    success = 0
    for etf in UNDERLYINGS:
        print(f"\n--- {etf['name']} ({etf['code']}) ---")
        try:
            kline_data = fetch_kline_data(etf)
            for period, records in kline_data.items():
                path = os.path.join(DATA_DIR, f"{etf['code']}_{period}.json")
                save_json(path, records)
            success += 1
        except Exception as e:
            print(f"  错误: {e}")

    print(f"\n--- 获取PE(TTM)数据 ---")
    try:
        pe_data = fetch_pe_data()
        save_json(os.path.join(DATA_DIR, "pe_data.json"), pe_data)
    except Exception as e:
        print(f"PE数据获取失败: {e}")

    print("\n" + "=" * 60)
    print(f"更新完成! K线: {success}/{len(UNDERLYINGS)} 成功")
    print("=" * 60)


if __name__ == "__main__":
    main()
