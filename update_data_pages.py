"""
期权时空容错交易系统 - GitHub Pages 数据更新脚本
功能：拉取 7 个标的的 K 线数据 + PE(TTM) 数据，保存为 JSON 供 GitHub Pages 静态站点使用
用法：python3 update_data_pages.py
"""
import json
import os
import sys
import math
import gzip
import shutil
from datetime import datetime, timedelta, timezone

import pandas as pd

# ============ 配置 ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "kline_data")
os.makedirs(DATA_DIR, exist_ok=True)

# 从各标的上市首日开始取全量历史(数据源返回多长就保留多长), 便于复盘验证
START_DATE = "1990-01-01"

UNDERLYINGS = [
    {"code": "510050", "name": "上证50ETF", "symbol": "sh510050", "type": "etf"},
    {"code": "510300", "name": "沪深300ETF", "symbol": "sh510300", "type": "etf"},
    {"code": "588000", "name": "科创50ETF", "symbol": "sh588000", "type": "etf"},
    {"code": "159915", "name": "创业板ETF", "symbol": "sz159915", "type": "etf"},
    {"code": "510500", "name": "中证500ETF", "symbol": "sh510500", "type": "etf"},
    {"code": "000852", "name": "中证1000", "symbol": "sh000852", "type": "index"},
    {"code": "I0", "name": "铁矿石主连", "symbol": "nf_I0", "type": "futures"},
    {"code": "510880", "name": "红利ETF", "symbol": "sh510880", "type": "etf"},
    {"code": "513530", "name": "港股通红利ETF", "symbol": "sh513530", "type": "etf"},
    {"code": "513650", "name": "标普500ETF", "symbol": "sh513650", "type": "etf"},
    {"code": "159509", "name": "纳指科技ETF", "symbol": "sz159509", "type": "etf"},
]

PE_INDEX_MAP = {
    "510050": "000016.SH", "510300": "000300.SH", "588000": "000688.SH",
    "159915": "399006.SZ", "510500": "000905.SH", "000852": "000852.SH",
    "510880": None, "513530": None, "513650": None, "159509": None,
    "I0": None,
}

PERIODS = [
    # max_bars 放宽到"不截断": 覆盖各标的从上市首日至今的全部K线
    {"name": "daily", "max_bars": 10000},
    {"name": "weekly", "max_bars": 2000},
    {"name": "monthly", "max_bars": 500},
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

# 首屏快速预览: 每个周期只写"最近 N 根"到 _recent.json, 前端先渲染它, 再后台补全全部历史
# 0 = 不拆分(该周期本身很小, 全量即"最近")
RECENT_BARS = {"daily": 300, "weekly": 120, "monthly": 0}


def slim_records(records):
    """精简记录以减小传输体积: risk_label 可由 risk_score 推导故删除; 浮点保留 6 位"""
    out = []
    for r in records:
        out.append({
            "date": r["date"],
            "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"],
            "volume": r["volume"], "risk_score": r["risk_score"],
            "intraday_up": None if r["intraday_up"] is None else round(r["intraday_up"], 6),
            "intraday_down": None if r["intraday_down"] is None else round(r["intraday_down"], 6),
            "close_change": None if r["close_change"] is None else round(r["close_change"], 6),
        })
    return out


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
        scores.append(int(math.floor(score + 0.5)))   # 标准四舍五入(非银行家舍入)
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
    """获取期货主连实时行情（通过 akshare.futures_zh_realtime）
    注意：futures_zh_realtime 返回的 close 字段在盘中为 0（结算后才填），
    实时价格应使用 trade 字段（最新成交价）。
    """
    import akshare as ak
    try:
        symbol_map = {"nf_I0": "铁矿石", "I0": "铁矿石"}
        name = symbol_map.get(symbol, "铁矿石")
        df = ak.futures_zh_realtime(symbol=name)
        main_contract = df[df["name"].str.contains("连续")]
        if len(main_contract) == 0:
            main_contract = df.head(1)
        row = main_contract.iloc[0]
        # 用 trade 作为最新价（盘中 close=0），如果 trade 也无效则回退到 close
        latest_price = float(row["trade"]) if float(row.get("trade", 0) or 0) > 0 else float(row["close"])
        # 如果 trade 和 close 都为 0，说明数据完全无效，返回 None
        if latest_price <= 0:
            print(f"  [期货实时行情获取失败] {symbol}: 价格数据无效 trade={row.get('trade')} close={row.get('close')}")
            return None
        # open/high/low 用 trade 兜底（防止某些时段字段为 0）
        open_p = float(row["open"]) if float(row.get("open", 0) or 0) > 0 else latest_price
        high = float(row["high"]) if float(row.get("high", 0) or 0) > 0 else latest_price
        low = float(row["low"]) if float(row.get("low", 0) or 0) > 0 else latest_price
        # 确保 high >= latest_price >= low
        high = max(high, latest_price)
        low = min(low, latest_price)
        volume = float(row.get("volume", 0) or 0)
        return {
            "date": str(row["tradedate"]),
            "open": open_p,
            "high": high,
            "low": low,
            "close": latest_price,
            "volume": volume,
            "ticktime": str(row.get("ticktime", "") or ""),
        }
    except Exception as e:
        print(f"  [期货实时行情获取失败] {symbol}: {e}")
        return None


# ============ 期货交易时段(北京时间) ============
# 铁矿石(DCE): 日盘 09:00-11:30、13:30-15:00; 夜盘 21:00-23:00(周一至周五晚)
# 夜盘属于"下一交易日", 报价的交易日(夜盘归属)需按交易所口径处理
BJ_TZ = timezone(timedelta(hours=8))


def bj_now():
    return datetime.now(BJ_TZ)


def futures_session(dt=None):
    """返回当前期货时段: 'night' / 'day' / None(休市)"""
    dt = dt or bj_now()
    if dt.weekday() >= 5:
        return None
    mins = dt.hour * 60 + dt.minute
    if 1260 <= mins < 1380:      # 21:00-23:00 夜盘
        return "night"
    if 540 <= mins <= 690:       # 09:00-11:30
        return "day"
    if 810 <= mins <= 900:       # 13:30-15:00
        return "day"
    return None


def next_weekday(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def futures_quote_is_live(rt, max_age_min=20):
    """只接受当前交易时段内的新鲜报价。
    开盘前(08:00-09:00)和收盘后, akshare 仍会返回上一时段的价格,
    若直接写入会把旧价当成当日K线, 导致夜盘/次日开盘前数据错误。
    """
    if futures_session() is None:
        return False
    tick = str(rt.get("ticktime", "")).replace(":", "")
    if len(tick) >= 4:
        try:
            hh, mm = int(tick[:2]), int(tick[2:4])
            tick_dt = bj_now().replace(hour=hh, minute=mm, second=0, microsecond=0)
            age = (bj_now() - tick_dt).total_seconds() / 60
            if age < -5 or age > max_age_min:
                return False
        except ValueError:
            return False
    return True


def load_last_record(code, period="daily"):
    """读取本地已生成的最后一根K线(用于夜盘数据跨轮次保留)"""
    path = os.path.join(DATA_DIR, f"{code}_{period}.json")
    try:
        with open(path, encoding="utf-8") as f:
            arr = json.load(f)
        return arr[-1] if arr else None
    except Exception:
        return None


def fetch_etf_dividends(symbol):
    """新浪基金分红记录(累计值) → [(除息日'YYYY-MM-DD', 每份分红额)] 升序。失败返回 []"""
    import akshare as ak
    try:
        df = ak.fund_etf_dividend_sina(symbol=symbol)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    out, prev = [], 0.0
    for _, row in df.sort_values("日期").iterrows():
        cum = float(row["累计分红"])
        amt = round(cum - prev, 6)
        if amt > 0:
            out.append((str(row["日期"]), amt))
        prev = cum
    return out


def apply_qfq_adjustment(df, dividends):
    """前复权(比例法): 除息日 D 的真实持有收益 = (P_D + d)/P_{D-1}。
    将 D 之前的所有bar乘以 k = P_D/(P_D + d)(P_D为除息日原始收盘价),
    收益率序列即连续。最新bar因子=1(锚定真实价), 与实时行情合并保持一致。
    不复权数据的除息日假跌会污染 ROC/RSI 评级, 513530这类月度分红ETF尤其严重。"""
    if not dividends:
        return df
    ex_map = {}
    for d_str, amt in dividends:
        ex_map[d_str] = ex_map.get(d_str, 0.0) + amt
    date_list = [str(x)[:10] for x in df["date"]]
    close_raw = df["close"].tolist()
    fac = [1.0] * len(df)
    cum = 1.0
    n_used = 0
    for d_str in sorted(ex_map, reverse=True):
        d_amt = ex_map[d_str]
        idx_ex = None
        for i in range(len(date_list) - 1, -1, -1):
            if date_list[i] == d_str:
                idx_ex = i
                break
        if idx_ex is None or idx_ex == 0:
            continue
        p_ex = close_raw[idx_ex]
        if p_ex <= 0 or p_ex <= d_amt:
            continue
        cum *= p_ex / (p_ex + d_amt)
        for i in range(idx_ex):
            fac[i] = cum
        n_used += 1
    if n_used == 0:
        return df
    df = df.copy()
    for col in ("open", "high", "low", "close"):
        df[col] = [round(v * f, 6) for v, f in zip(df[col].tolist(), fac)]
    return df


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

    prev_tail = load_last_record(code) if etf_type == "futures" else None

    if etf_type == "futures":
        rt = fetch_futures_realtime_quote(symbol)
    else:
        rt = fetch_realtime_quote(symbol)
    if etf_type == "futures" and rt:
        # 期货特殊处理1: 只接受当前交易时段(含夜盘)内的新鲜报价。
        # 开盘前/收盘后数据源仍返回上一时段旧价, 直接写入会把旧价当成当日K线
        if not futures_quote_is_live(rt):
            print(" [非交易时段/行情过期, 跳过实时]", end="")
            rt = None
    if rt and etf_type == "futures" and futures_session() == "night":
        # 期货特殊处理2: 夜盘归属下一交易日(交易所口径), 避免把夜盘价写进当日已收盘K线
        try:
            if pd.Timestamp(rt["date"]).date() <= bj_now().date():
                rt["date"] = next_weekday(bj_now().date()).isoformat()
        except Exception:
            rt["date"] = next_weekday(bj_now().date()).isoformat()
    if rt:
        today_ts = pd.Timestamp(rt["date"])
        last_ts = df.iloc[-1]["date"] if len(df) > 0 else None
        if last_ts is None or today_ts > last_ts:
            open_p, high, low, close_p, volume = rt["open"], rt["high"], rt["low"], rt["close"], rt["volume"]
            # 期货特殊处理3: 同日K线已由夜盘写入时, 保留夜盘开盘价与高低点,
            # 避免白天首轮更新(数据源日线尚未含夜盘)把夜盘区间丢掉
            if prev_tail and str(prev_tail.get("date")) == str(rt["date"]):
                open_p = prev_tail.get("open", open_p)
                prev_high, prev_low = prev_tail.get("high"), prev_tail.get("low")
                if prev_high: high = max(high, prev_high)
                if prev_low: low = min(low, prev_low)
                volume = max(volume or 0, prev_tail.get("volume") or 0)
            new_row = pd.DataFrame([{"date": today_ts, "open": open_p, "high": high,
                "low": low, "close": close_p, "volume": volume}])
            df = pd.concat([df, new_row], ignore_index=True)
            print(f" +实时[{rt['date']}]", end="")
        elif last_ts == today_ts:
            # 同一天，更新最后一行
            df.loc[df.index[-1], "close"] = rt["close"]
            df.loc[df.index[-1], "high"] = max(df.loc[df.index[-1], "high"], rt["high"])
            df.loc[df.index[-1], "low"] = min(df.loc[df.index[-1], "low"], rt["low"])
            df.loc[df.index[-1], "volume"] = rt["volume"]
            print(f" =更新[{rt['date']}]", end="")
    elif etf_type == "futures" and prev_tail:
        # 期货特殊处理4: 休市(午休/收盘后/开盘前)或数据源日线滞后时,
        # 数据源还没有当日K线, 需把本地已写入的末根(夜盘/盘中)保留下来,
        # 否则生成结果会丢掉当日K线, 造成夜盘与开盘前数据缺失/跳变
        try:
            prev_ts = pd.Timestamp(prev_tail["date"])
            last_ts = df.iloc[-1]["date"] if len(df) > 0 else None
            if last_ts is None or prev_ts > last_ts:
                df = pd.concat([df, pd.DataFrame([{
                    "date": prev_ts,
                    "open": prev_tail["open"], "high": prev_tail["high"],
                    "low": prev_tail["low"], "close": prev_tail["close"],
                    "volume": prev_tail.get("volume", 0),
                }])], ignore_index=True)
                print(f" =保留[{prev_tail['date']}]", end="")
        except Exception as e:
            print(f"  [保留当日K线失败] {e}", end="")
    if etf_type == "etf":
        # ETF前复权: 消除除息日假跌对 ROC/RSI 评级的污染(分红记录获取失败则按不复权)
        divs = fetch_etf_dividends(symbol)
        if divs:
            df = apply_qfq_adjustment(df, divs)
            print(f" [前复权:{len(divs)}次分红]", end="")
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
    # 同步生成预压缩副本: 全量历史日线已达 1MB 级, gzip 后约 1/7,
    # 沙箱服务端在浏览器支持时直接下发 .gz, 避免公网隧道慢速传输
    if path.endswith(".json"):
        with open(path, "rb") as src, gzip.open(path + ".gz", "wb", compresslevel=9) as dst:
            shutil.copyfileobj(src, dst)


def generate_review():
    """收盘复盘报告: 各标的最新评级/较上一根变化/评级跳变/期权极端提示。
    读已落盘的K线与期权快照, 零额外网络请求。产出:
      kline_data/review_latest.json   前端展示(每轮覆盖)
      kline_data/review_archive/{date}_{session}.json  存档(每日job入库)
    """
    review_dir = os.path.join(DATA_DIR, "review_archive")
    os.makedirs(review_dir, exist_ok=True)
    now = bj_now()
    session = "day" if now.hour < 16 else "night"
    items, alerts = [], []
    for etf in UNDERLYINGS:
        code = etf["code"]
        ratings, prev = {}, {}
        spot = chg = None
        for period in ("daily", "weekly", "monthly"):
            try:
                with open(os.path.join(DATA_DIR, f"{code}_{period}.json"), encoding="utf-8") as f:
                    arr = json.load(f)
            except Exception:
                continue
            if len(arr) >= 1:
                ratings[period] = arr[-1].get("risk_score")
                spot = arr[-1].get("close") if spot is None else spot
                chg = arr[-1].get("close_change") if chg is None else chg
            if len(arr) >= 2:
                prev[period] = arr[-2].get("risk_score")
        if "daily" not in ratings:
            continue
        changes = {p: (ratings[p] - prev[p]) if (ratings.get(p) is not None and prev.get(p) is not None) else None
                   for p in ratings}
        jump = any(c is not None and abs(c) >= 2 for c in changes.values())
        item = {"code": code, "name": etf["name"], "spot": spot, "chg": chg,
                "ratings": ratings, "changes": changes, "jump": jump}
        # 期权提示
        opt_path = os.path.join(DATA_DIR, "options", f"{code}_snapshot.json")
        try:
            with open(opt_path, encoding="utf-8") as f:
                snap = json.load(f)
            iv = {"atm_iv": snap.get("atm_iv"), "iv_hv_ratio": snap.get("iv_hv_ratio"),
                  "pcr_vol": snap.get("pcr_vol"), "pcr_oi": snap.get("pcr_oi"),
                  "days_left": (snap.get("expiries") or [{}])[0].get("days_left")}
            item["iv"] = iv
            r = iv.get("iv_hv_ratio")
            if r is not None and r >= 1.4:
                alerts.append(f"{etf['name']} 权利金偏贵 (IV/HV {r})")
            elif r is not None and r <= 0.8:
                alerts.append(f"{etf['name']} 权利金偏宜 (IV/HV {r})")
            dl = iv.get("days_left")
            if dl is not None and dl <= 7:
                alerts.append(f"{etf['name']} 期权近月 {dl} 天后到期 (末日轮, IV失真)")
        except Exception:
            pass
        # 评级跳变 & 多周期共振
        for p, c in changes.items():
            if c is not None and abs(c) >= 2:
                pn = {"daily": "日", "weekly": "周", "monthly": "月"}[p]
                alerts.append(f"{etf['name']} {pn}评级跳变 {prev[p]:+d}→{ratings[p]:+d}")
        d, w = ratings.get("daily"), ratings.get("weekly")
        if d is not None and w is not None:
            if d <= -2 and w <= -1:
                alerts.append(f"{etf['name']} 日+周共振机会 (日{d:+d}/周{w:+d})")
            elif d >= 2 and w >= 1:
                alerts.append(f"{etf['name']} 日+周共振风险 (日{d:+d}/周{w:+d})")
        items.append(item)
    review = {
        "date": now.strftime("%Y-%m-%d"),
        "session": session,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "items": items,
        "alerts": alerts,
    }
    save_json(os.path.join(DATA_DIR, "review_latest.json"), review)
    save_json(os.path.join(review_dir, f"{review['date']}_{session}.json"), review)
    print(f"复盘报告已生成: {len(items)} 标的, {len(alerts)} 条提醒 ({session})")
    return review


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
    failed = []
    summary = {}   # 轻量摘要: 每个标的的日/周/月最后一根, 供前端秒开总览
    for etf in UNDERLYINGS:
        print(f"\n--- {etf['name']} ({etf['code']}) ---")
        try:
            kline_data = fetch_kline_data(etf)
            rec = {"name": etf["name"]}
            for period, records in kline_data.items():
                slim = slim_records(records)
                path = os.path.join(DATA_DIR, f"{etf['code']}_{period}.json")
                save_json(path, slim)                       # 全部历史(后台补全用)
                n = RECENT_BARS.get(period, 0)
                if n:
                    save_json(os.path.join(DATA_DIR, f"{etf['code']}_{period}_recent.json"), slim[-n:])  # 首屏预览用
                if slim:
                    rec[period] = slim[-1]
            summary[etf["code"]] = rec
            success += 1
        except Exception as e:
            print(f"  错误: {e}")
            failed.append(f"{etf['code']}:{str(e)[:40]}")
            # 回退: 用已存在的文件生成摘要, 避免该标的在总览里缺失
            try:
                rec = {"name": etf["name"]}
                for period in ("daily", "weekly", "monthly"):
                    p = os.path.join(DATA_DIR, f"{etf['code']}_{period}.json")
                    if os.path.exists(p):
                        with open(p, encoding="utf-8") as f:
                            arr = json.load(f)
                        if arr:
                            rec[period] = arr[-1]
                if len(rec) > 1:
                    summary[etf["code"]] = rec
            except Exception as e2:
                print(f"  摘要回退失败: {e2}")

    # 生成时间戳: 静态托管(GitHub Pages)时前端据此显示"数据 X 秒前更新"
    summary["_meta"] = {
        "generated_at": bj_now().strftime("%Y-%m-%d %H:%M:%S"),
        "failed_codes": failed,   # 本轮抓取失败的标的(前端/监控可据此告警)
    }
    save_json(os.path.join(DATA_DIR, "summary.json"), summary)
    print(f"摘要已写入 summary.json ({len(summary) - 1} 个标的)")

    print(f"\n--- 获取PE(TTM)数据 ---")
    try:
        pe_data = fetch_pe_data()
        save_json(os.path.join(DATA_DIR, "pe_data.json"), pe_data)
    except Exception as e:
        print(f"PE数据获取失败: {e}")

    # 收盘复盘报告(读已落盘数据, 零网络请求)
    try:
        generate_review()
    except Exception as e:
        print(f"复盘报告生成失败: {e}")

    print("\n" + "=" * 60)
    print(f"更新完成! K线: {success}/{len(UNDERLYINGS)} 成功"
          + (f" 失败: {', '.join(failed)}" if failed else ""))
    print("=" * 60)
    # 护栏: 超半数标的失败(数据源大面积异常/网络故障)时以非零码退出,
    # 让 deploy-site 工作流失败从而保留上一次的正常部署, 不发布残缺数据
    if success * 2 < len(UNDERLYINGS):
        sys.exit(1)


if __name__ == "__main__":
    main()
