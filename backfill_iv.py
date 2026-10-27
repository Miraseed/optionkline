"""
一次性回填: 还原上交所 4 个 ETF 期权过去 N 个月的"近月 ATM IV"每日序列。
数据: option_sse_codes_sina(合约清单) + option_sse_greeks_sina(行权价) + option_sse_daily_sina(合约日线)
方法: 每个到期月取该月最后约25个交易日, 用标的收盘价选最近行权价, 由看涨/看跌收盘价 BS 反推 IV 后取均值。
输出: 合并进 kline_data/options/iv_history/{code}.json (仅补齐缺失日期, 已存在的实时数据优先)
用法: python3 backfill_iv.py [months]   (默认12个月)
注: 中金所/大商所无合约日线历史源, 只能靠逐日累积。
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import akshare as ak

from update_options import DATA_DIR, HIST_DIR, RISK_FREE, bj_now, implied_vol

SSE_TARGETS = {
    "510050": "50ETF",
    "510300": "300ETF",
    "510500": "500ETF",
    "588000": "科创50",
}
MONTHS_BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 12
# 只取到期前 15~45 天(同一期限口径, 避免末日轮IV失真与远月期限结构混样)
DAYS_LEFT_MIN, DAYS_LEFT_MAX = 15, 45
POOL = 6                  # 并发请求数(避免触发限流)


def load_bars(code):
    """返回 {日期: 收盘}"""
    path = os.path.join(DATA_DIR, f"{code}_daily.json")
    with open(path, encoding="utf-8") as f:
        arr = json.load(f)
    return {r["date"]: r["close"] for r in arr if r.get("close")}


def months_list(n):
    now = bj_now()
    y, m = now.year, now.month
    out = []
    for _ in range(n + 1):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return sorted(out)


def greeks_info(codes):
    """批量取 {合约代码: (行权价, 是否看涨)}"""
    def one(oc):
        try:
            g = ak.option_sse_greeks_sina(symbol=oc)
            if g is None or g.empty:
                return None
            d = dict(zip(g.iloc[:, 0], g.iloc[:, 1]))
            k = float(str(d.get("行权价", "")).replace(",", "") or 0)
            name = str(d.get("交易代码", ""))
            if k <= 0:
                return None
            return oc, (k, "C" in name.upper().replace("CALL", "C"))
        except Exception:
            return None
    with ThreadPoolExecutor(POOL) as ex:
        out = [r for r in ex.map(one, codes) if r]
    return dict(out)


def daily_closes(oc):
    try:
        d = ak.option_sse_daily_sina(symbol=oc)
        if d is None or d.empty:
            return oc, {}
        return oc, {str(r["日期"])[:10]: float(r["收盘"]) for _, r in d.iterrows() if r["收盘"]}
    except Exception:
        return oc, {}


def sse_expiry(y, m):
    """上交所ETF期权到期日: 到期月第四个星期三(遇节假日顺延, 此处近似)"""
    import calendar
    weds = [d for d in range(1, calendar.monthrange(y, m)[1] + 1)
            if datetime(y, m, d).weekday() == 2]
    return datetime(y, m, weds[3]).date()


def backfill_one(code, sse_sym):
    spots = load_bars(code)
    if not spots:
        print(f"  [{code}] 无K线, 跳过")
        return {}
    by_date = {}   # date -> (days_left, atm_iv)；近月优先(先到先得)
    for m in months_list(MONTHS_BACK):
        try:
            ed = ak.option_sse_expire_day_sina(trade_date=m, symbol=sse_sym)
            ds = ed[0] if ed else None
        except Exception:
            ds = None
        # 已到期月份该接口返回 None, 用"第四个星期三"规则补算
        exp = datetime.strptime(str(ds), "%Y-%m-%d").date() if ds \
            else sse_expiry(int(m[:4]), int(m[4:]))
        try:
            calls = ak.option_sse_codes_sina(symbol="看涨期权", trade_date=m, underlying=code)
            puts = ak.option_sse_codes_sina(symbol="看跌期权", trade_date=m, underlying=code)
        except Exception:
            continue  # 新浪对更早月份的合约清单会返回空, akshare 解析报错
        ccodes = [str(x) for x in (calls["期权代码"] if calls is not None and not calls.empty else [])]
        pcodes = [str(x) for x in (puts["期权代码"] if puts is not None and not puts.empty else [])]
        if not ccodes or not pcodes:
            continue
        info = greeks_info(ccodes + pcodes)
        with ThreadPoolExecutor(POOL) as ex:
            prices = dict(ex.map(daily_closes, list(info.keys())))
        # 该月到期前 15~45 天的交易日
        dates = sorted({d for oc in prices for d in prices[oc]
                        if datetime.strptime(d, "%Y-%m-%d").date() <= exp
                        and DAYS_LEFT_MIN <= (exp - datetime.strptime(d, "%Y-%m-%d").date()).days <= DAYS_LEFT_MAX})
        for ds in dates:
            d0 = datetime.strptime(ds, "%Y-%m-%d").date()
            S = spots.get(ds)
            if not S:
                continue
            T = (exp - d0).days / 365.0
            if T <= 0:
                continue
            ks = sorted({k for k, _ in info.values()})
            atm_k = min(ks, key=lambda k: abs(k - S))
            ivs = []
            for oc, (k, is_call) in info.items():
                if k != atm_k:
                    continue
                px = prices.get(oc, {}).get(ds)
                iv = implied_vol(px, S, k, T, RISK_FREE, is_call)
                if iv is not None:
                    ivs.append(iv)
            if not ivs:
                continue
            days_left = (exp - d0).days
            prev = by_date.get(ds)
            if prev is None or days_left < prev[0]:
                by_date[ds] = (days_left, round(sum(ivs) / len(ivs), 2))
    return {d: v[1] for d, v in by_date.items()}


def merge_history(code, filled):
    """把回填结果并入 iv_history: 已有(实时)条目优先, 只补缺失日期"""
    path = os.path.join(HIST_DIR, f"{code}.json")
    hist = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            hist = []
    have = {r.get("date") for r in hist}
    added = 0
    for d, iv in filled.items():
        if d in have:
            continue
        hist.append({"date": d, "atm_iv": iv, "source": "backfill"})
        added += 1
    hist.sort(key=lambda r: r.get("date") or "")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(hist[-1000:], f, ensure_ascii=False)
    return added, len(hist)


def main():
    print(f"回填范围: {MONTHS_BACK} 个月, 标的 {list(SSE_TARGETS)}")
    for code, sym in SSE_TARGETS.items():
        filled = backfill_one(code, sym)
        added, total = merge_history(code, filled)
        n = len(filled)
        avg = round(sum(filled.values()) / n, 2) if n else None
        print(f"  [{code}] 还原 {n} 个交易日, 新增 {added} 条, 历史共 {total} 条, 均值 IV={avg}")


if __name__ == "__main__":
    main()
