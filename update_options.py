"""
期权数据抓取模块 - IV/PCR/到期倒计时/波动率微笑/期限结构/IV历史累积
数据源:
  上交所(510050/510300/510500/588000): akshare option_sse_codes_sina + option_sse_greeks_sina(自带IV)
  中金所(000852 中证1000期权): option_cffex_zz1000_spot_sina(T型报价, IV用BS反推)
  大商所(I0 铁矿石期权): option_commodity_contract_table_sina(T型报价, IV用Black-76反推)
输出:
  kline_data/options/{code}_snapshot.json   当前快照(每轮覆盖)
  kline_data/options/iv_history/{code}.json IV/PCR历史(每日一条, 当日覆盖式更新)
用法: python3 update_options.py   (建议在 update_data_pages.py 之后跑, K线文件最新)
"""
import json
import math
import os
import sys
from datetime import datetime, timedelta

import akshare as ak

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "kline_data")
OPT_DIR = os.path.join(DATA_DIR, "options")
HIST_DIR = os.path.join(OPT_DIR, "iv_history")
os.makedirs(HIST_DIR, exist_ok=True)

BJ_TZ = __import__("datetime").timezone(timedelta(hours=8))
RISK_FREE = 0.015  # 无风险利率(IV反推用)

# 有期权的标的: code -> 数据源
OPTION_TARGETS = {
    "510050": {"ex": "sse", "sse_sym": "50ETF"},
    "510300": {"ex": "sse", "sse_sym": "300ETF"},
    "510500": {"ex": "sse", "sse_sym": "500ETF"},
    "588000": {"ex": "sse", "sse_sym": "科创50"},
    "000852": {"ex": "cffex"},
    "I0": {"ex": "dce"},
}


def bj_now():
    return datetime.now(BJ_TZ)


def to_float(v):
    """清洗 '1,234.5' / '18.50%' / '-' 等格式为 float, 失败返回 None"""
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("%", "")
    if s in ("", "-", "--", "nan", "None"):
        return None
    try:
        f = float(s)
        return f if f == f else None  # 过滤 NaN
    except ValueError:
        return None


# ============ BS/Black-76 定价与 IV 反推 ============
def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(S, K, T, r, sigma, is_call, fut=False):
    if T <= 0:
        return (max(S - K, 0.0) if is_call else max(K - S, 0.0))
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if fut:
        return math.exp(-r * T) * (S * norm_cdf(d1) - K * norm_cdf(d2))
    return S * norm_cdf(d1) - math.exp(-r * T) * K * norm_cdf(d2)


def implied_vol(price, S, K, T, r, is_call, fut=False):
    """二分法反推隐含波动率(%), 无解返回 None"""
    if price is None or price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    lo, hi = 0.005, 3.0
    # 最低波动率下价格仍高于市价 → 无解(负时间价值)
    if bs_price(S, K, T, r, lo, is_call, fut) > price:
        return None
    if bs_price(S, K, T, r, hi, is_call, fut) < price:
        return None
    for _ in range(50):
        mid = (lo + hi) / 2
        if bs_price(S, K, T, r, mid, is_call, fut) > price:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-5:
            break
    return round((lo + hi) / 2 * 100, 2)


# ============ 到期日规则 ============
def third_friday(y, m):
    """中金所: 到期月份第三个星期五(遇假日顺延, 此处近似)"""
    import calendar
    fridays = [d for d in range(1, calendar.monthrange(y, m)[1] + 1)
               if datetime(y, m, d).weekday() == 4]
    return datetime(y, m, fridays[2]).date()


def last_n_workday(y, m, n=5):
    """大商所商品期权: 标的期货交割月前第一月的倒数第5个交易日(工作日近似)"""
    import calendar
    cnt = 0
    for d in range(calendar.monthrange(y, m)[1], 0, -1):
        if datetime(y, m, d).weekday() < 5:
            cnt += 1
            if cnt == n:
                return datetime(y, m, d).date()
    return None


# ============ 标的现价与 HV ============
def load_closes(code, n=280):
    path = os.path.join(DATA_DIR, f"{code}_daily.json")
    try:
        with open(path, encoding="utf-8") as f:
            arr = json.load(f)
        return [r["close"] for r in arr if r.get("close")]
    except Exception:
        return []


def calc_hv(closes):
    """20日年化历史波动率(%) 及其在近250日的分位数(0-100)"""
    import statistics
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
            if closes[i - 1] > 0]
    if len(rets) < 21:
        return None, None
    win = 20
    hvs = [statistics.pstdev(rets[i - win:i]) * math.sqrt(252) * 100
           for i in range(win, len(rets) + 1)]
    hv20 = hvs[-1]
    sample = hvs[-250:]
    pct = round(sum(1 for x in sample if x <= hv20) / len(sample) * 100, 0)
    return round(hv20, 2), int(pct)


def underlying_spot(code):
    """标的现价: 优先K线最后一根(同轮已含实时当日bar), 失败返回 None"""
    closes = load_closes(code)
    return closes[-1] if closes else None


# ============ 上交所: 逐合约 greeks(自带IV) ============
def next_months(n=6):
    """当月起 n 个 YYYYMM"""
    today = bj_now().date()
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def fetch_sse_option(code, sse_sym):
    calls_all, puts_all, expiries = {}, {}, []
    for m in next_months(6):
        try:
            c = ak.option_sse_codes_sina(symbol="看涨期权", trade_date=m, underlying=code)
            p = ak.option_sse_codes_sina(symbol="看跌期权", trade_date=m, underlying=code)
        except Exception:
            continue
        if c is None or p is None or (c.empty and p.empty):
            continue
        for _, r in c.iterrows():
            calls_all[str(r["期权代码"])] = m
        for _, r in p.iterrows():
            puts_all[str(r["期权代码"])] = m
        try:
            ed = ak.option_sse_expire_day_sina(trade_date=m, symbol=sse_sym)
            if ed and ed[0]:
                expiries.append({"month": m, "expiry": str(ed[0]),
                                 "days_left": (datetime.strptime(str(ed[0]), "%Y-%m-%d").date() - bj_now().date()).days})
        except Exception:
            pass
    if not calls_all:
        raise RuntimeError("no sse contracts")

    # 逐合约 greeks(含IV/成交量和行权价)
    greeks = {}
    for oc in {**calls_all, **puts_all}:
        try:
            g = ak.option_sse_greeks_sina(symbol=oc)
            if g is None or g.empty:
                continue
            d = dict(zip(g.iloc[:, 0], g.iloc[:, 1]))
            iv = to_float(d.get("隐含波动率"))
            if iv is not None and iv < 3:  # 新浪返回小数形式(0.19=19%), 转百分比
                iv = round(iv * 100, 2)
            greeks[oc] = {
                "month": calls_all.get(oc) or puts_all.get(oc),
                "k": to_float(d.get("行权价")),
                "iv": iv,
                "vol": to_float(d.get("成交量")) or 0,
                "price": to_float(d.get("最新价")),
                "delta": to_float(d.get("Delta")),
            }
        except Exception:
            continue

    return build_snapshot(code, expiries, calls_all, puts_all, greeks, iv_source="sina_greeks")


# ============ 中金所/大商所: T型报价 + BS反推IV ============
def parse_tquote(df, is_call, iv_fn):
    """把 T 型报价 DataFrame 解析为 [{k, price, oi, iv}] 列表(剔除无效行)
    注: CFFEX/DCE T型报价无成交量列, 只有持仓量(oi); PCR 只能用持仓量口径"""
    cpre = "看涨合约" if is_call else "看跌合约"
    if f"{cpre}-标识" in df.columns:
        idcol = f"{cpre}-标识"
    elif f"{cpre}-看涨期权合约" in df.columns:
        idcol = f"{cpre}-看涨期权合约"
    else:
        idcol = f"{cpre}-看跌期权合约"
    rows = []
    for _, r in df.iterrows():
        k = to_float(r["行权价"])
        bid, ask = to_float(r[f"{cpre}-买价"]), to_float(r[f"{cpre}-卖价"])
        last = to_float(r[f"{cpre}-最新价"])
        oi = to_float(r[f"{cpre}-持仓量"])
        price = (bid + ask) / 2 if bid and ask and bid > 0 and ask > 0 else last
        if k is None:
            continue
        rows.append({"k": k, "price": price, "oi": oi or 0,
                     "iv": iv_fn(price, k, is_call), "code": str(r.get(idcol, ""))})
    return rows


def fetch_cffex_option(code):
    symbols = sorted(ak.option_cffex_zz1000_list_sina().get("中证1000指数", []))
    if not symbols:
        raise RuntimeError("no cffex months")
    S = underlying_spot(code)
    if not S:
        raise RuntimeError("no underlying spot")
    months_out, chains = [], {}
    for sym in symbols:
        ym = sym.replace("mo", "")
        exp = third_friday(2000 + int(ym[:2]), int(ym[2:]))
        T = max((exp - bj_now().date()).days, 0) / 365.0
        try:
            df = ak.option_cffex_zz1000_spot_sina(symbol=sym)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        iv_fn = lambda price, k, c: implied_vol(price, S, k, T, RISK_FREE, c)
        calls = parse_tquote(df, True, iv_fn)
        puts = parse_tquote(df, False, iv_fn)
        chains[ym] = {"expiry": exp.isoformat(), "days_left": (exp - bj_now().date()).days,
                      "calls": calls, "puts": puts}
        months_out.append(ym)
    return assemble_from_chains(code, months_out, chains, iv_source="bs")


def fetch_dce_option(code):
    contracts = []
    try:
        df = ak.option_commodity_contract_sina(symbol="铁矿石期权")
        contracts = sorted(df["合约"].astype(str).tolist())
    except Exception:
        raise RuntimeError("no dce contracts")
    S = underlying_spot(code)
    if not S:
        raise RuntimeError("no underlying spot")
    months_out, chains = [], {}
    for c in contracts:
        ym = c.replace("i", "")
        # 大商所: 到期日 = 交割月前第一月的倒数第5个交易日(工作日近似)
        y, m = 2000 + int(ym[:2]), int(ym[2:])
        if m == 1:
            y, m = y - 1, 12
        else:
            m -= 1
        exp = last_n_workday(y, m, 5)
        if not exp:
            continue
        T = max((exp - bj_now().date()).days, 0) / 365.0
        try:
            df = ak.option_commodity_contract_table_sina(symbol="铁矿石期权", contract=c)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        iv_fn = lambda price, k, cs: implied_vol(price, S, k, T, RISK_FREE, cs, fut=True)
        calls = parse_tquote(df, True, iv_fn)
        puts = parse_tquote(df, False, iv_fn)
        chains[ym] = {"expiry": exp.isoformat(), "days_left": (exp - bj_now().date()).days,
                      "calls": calls, "puts": puts}
        months_out.append(ym)
    return assemble_from_chains(code, months_out, chains, iv_source="bs")


# ============ 汇总: ATM/PCR/微笑/期限结构 ============
def chain_metrics(calls, puts, S):
    """单月链: ATM IV、PCR、微笑"""
    if not calls or not puts:
        return None
    vol_c = sum(c.get("vol", 0) or 0 for c in calls)
    vol_p = sum(p.get("vol", 0) or 0 for p in puts)
    oi_c = sum(c["oi"] for c in calls)
    oi_p = sum(p["oi"] for p in puts)
    pcr_vol = round(vol_p / vol_c, 3) if vol_c else None
    pcr_oi = round(oi_p / oi_c, 3) if oi_c else None
    # ATM: 行权价最接近现价
    atm_k = min((c["k"] for c in calls), key=lambda k: abs(k - S), default=None)
    atm_c = next((c for c in calls if c["k"] == atm_k), None)
    atm_p = next((p for p in puts if p["k"] == atm_k), None)
    ivs = [x["iv"] for x in (atm_c, atm_p) if x and x["iv"]]
    atm_iv = round(sum(ivs) / len(ivs), 2) if ivs else None
    # 微笑: 按行权价升序, put_iv 与 strikes 严格对齐(无值填 null)
    ks = [c["k"] for c in calls if c["iv"] is not None]
    civs = [c["iv"] for c in calls if c["iv"] is not None]
    pmap = {p["k"]: p["iv"] for p in puts if p["iv"] is not None}
    pivs = [pmap.get(k) for k in ks]
    smile = {"strikes": ks, "call_iv": civs, "put_iv": pivs}
    # ATM附近 ±4 档链条
    ks_all = sorted({c["k"] for c in calls})
    try:
        idx = ks_all.index(atm_k)
    except ValueError:
        idx = len(ks_all) // 2
    near = ks_all[max(0, idx - 4): idx + 5]
    atm_chain = []
    for k in near:
        cc = next((c for c in calls if c["k"] == k), {})
        pp = next((p for p in puts if p["k"] == k), {})
        atm_chain.append({
            "k": k,
            "call": {f: cc.get(f) for f in ("code", "price", "iv", "delta", "vol", "oi")},
            "put": {f: pp.get(f) for f in ("code", "price", "iv", "vol", "oi")},
        })
    return {"atm_k": atm_k, "atm_iv": atm_iv, "pcr_vol": pcr_vol, "pcr_oi": pcr_oi,
            "smile": smile, "atm_chain": atm_chain}


def assemble_from_chains(code, months, chains, iv_source):
    spot = underlying_spot(code)
    term, expiries = [], []
    for ym in months:
        ch = chains[ym]
        expiries.append({"month": ym, "expiry": ch["expiry"], "days_left": ch["days_left"]})
    term_structure, smile_near, atm_chain_near = [], [], []
    for ym in months[:4]:
        ch = chains[ym]
        m = chain_metrics(ch["calls"], ch["puts"], spot)
        if not m:
            continue
        term_structure.append({"month": ym, "expiry": ch["expiry"],
                               "days_left": ch["days_left"], "atm_iv": m["atm_iv"],
                               "pcr_vol": m["pcr_vol"], "pcr_oi": m["pcr_oi"]})
    # 微笑与ATM链只取最近月
    near_metrics, smile_near = None, []
    for ym in months:
        ch = chains[ym]
        m = chain_metrics(ch["calls"], ch["puts"], spot)
        if not m:
            continue
        smile_near = [{"month": ym, "expiry": ch["expiry"], **m["smile"]}]
        atm_chain_near = m["atm_chain"]
        near_metrics = m
        break
    atm_chain_near = near_metrics["atm_chain"] if near_metrics else []
    pcr_vol = next((t["pcr_vol"] for t in term_structure if t["pcr_vol"]), None)
    pcr_oi = next((t["pcr_oi"] for t in term_structure if t["pcr_oi"]), None)
    atm_iv = next((t["atm_iv"] for t in term_structure if t["atm_iv"]), None)
    closes = load_closes(code)
    hv20, hv_pct = calc_hv(closes)
    snap = {
        "code": code,
        "updated_at": bj_now().strftime("%Y-%m-%d %H:%M:%S"),
        "spot": spot,
        "iv_source": iv_source,
        "atm_iv": atm_iv,
        "hv20": hv20, "hv20_pct": hv_pct,
        "iv_hv_ratio": round(atm_iv / hv20, 3) if atm_iv and hv20 else None,
        "pcr_vol": pcr_vol, "pcr_oi": pcr_oi,
        "expiries": expiries,
        "near_month": {"month": smile_near[0]["month"], "expiry": smile_near[0]["expiry"],
                       "atm_k": near_metrics["atm_k"], "chain": atm_chain_near} if smile_near and near_metrics else None,
        "smile": smile_near,
        "term_structure": term_structure,
    }
    return snap


def build_snapshot(code, expiries, calls_all, puts_all, greeks, iv_source):
    """SSE: 用 greeks dict 组装(与 assemble_from_chains 同构)"""
    spot = underlying_spot(code)
    months = sorted({g["month"] for g in greeks.values() if g["month"]})
    expiry_map = {e["month"]: e for e in expiries}
    chains = {}
    for ym in months:
        calls = [{**greeks[oc], "oi": 0, "code": oc} for oc, m in calls_all.items() if m == ym and oc in greeks]
        puts = [{**greeks[oc], "oi": 0, "code": oc} for oc, m in puts_all.items() if m == ym and oc in greeks]
        if not calls or not puts:
            continue
        e = expiry_map.get(ym, {})
        chains[ym] = {"expiry": e.get("expiry"), "days_left": e.get("days_left"),
                      "calls": calls, "puts": puts}
    # SSE 的 chain_metrics 需要 price 字段(已有)
    snap = assemble_from_chains(code, months, chains, iv_source)
    return snap


# ============ IV 历史累积 ============
def append_history(code, snap):
    path = os.path.join(HIST_DIR, f"{code}.json")
    hist = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            hist = []
    today = bj_now().strftime("%Y-%m-%d")
    if snap.get("atm_iv") is None:
        return  # 无有效IV不写历史
    entry = {"date": today, "atm_iv": snap["atm_iv"],
             "pcr_vol": snap.get("pcr_vol"), "iv_hv_ratio": snap.get("iv_hv_ratio"),
             "hv20": snap.get("hv20")}
    if hist and hist[-1].get("date") == today:
        hist[-1] = entry  # 盘中多轮: 当日覆盖为最新
    else:
        hist.append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(hist[-1000:], f, ensure_ascii=False)


# ============ 主流程 ============
def run_one(code, cfg):
    if cfg["ex"] == "sse":
        snap = fetch_sse_option(code, cfg["sse_sym"])
    elif cfg["ex"] == "cffex":
        snap = fetch_cffex_option(code)
    else:
        snap = fetch_dce_option(code)
    path = os.path.join(OPT_DIR, f"{code}_snapshot.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, separators=(",", ":"))
    append_history(code, snap)
    size_kb = round(os.path.getsize(path) / 1024, 1)
    pcr = snap.get("pcr_vol") if snap.get("pcr_vol") else snap.get("pcr_oi")
    pcr_label = "PCR量" if snap.get("pcr_vol") else "PCR仓"
    print(f"  [{code}] IV={snap.get('atm_iv')} HV={snap.get('hv20')} {pcr_label}={pcr} "
          f"IV/HV={snap.get('iv_hv_ratio')} 距到期={snap['expiries'][0]['days_left'] if snap.get('expiries') else '-'}天 ({size_kb}KB)")


def main():
    failed = []
    for code, cfg in OPTION_TARGETS.items():
        try:
            run_one(code, cfg)
        except Exception as e:
            failed.append(code)
            print(f"  [{code}] 失败: {str(e)[:80]}")
    if failed:
        print(f"[期权] 失败标的: {failed}")
    # 期权快照更新后重算复盘报告, 让 IV/HV 等提示反映本轮最新数据
    try:
        from update_data_pages import generate_review
        generate_review()
    except Exception as e:
        print(f"[期权] 复盘报告生成失败: {str(e)[:80]}")
    # 至少成功一半才算通过(护栏)
    if len(failed) >= len(OPTION_TARGETS):
        print("[期权] 全部失败, 退出码1")
        sys.exit(1)


if __name__ == "__main__":
    main()
