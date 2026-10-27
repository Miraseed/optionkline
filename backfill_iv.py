"""
一次性回填 6 个期权标的的历史"约30天 ATM IV"每日序列, 不依赖逐日累积。

三个数据源(均为一次请求拿到完整历史):
  1) 上交所 4 个 ETF 期权(510050/510300/510500/588000): 官方《期权风险指标》接口,
     官方已算好全链 IV/Delta, 无需反推:
     http://query.sse.com.cn/commonQuery.do?isPagination=false&trade_date=YYYYMMDD
         &sqlId=SSE_ZQPZ_YSP_GGQQZSXT_YSHQ_QQFXZB_DATE_L&contractSymbol=
     需带 Referer: http://www.sse.com.cn/ ; 实测可回溯到 2015-02-10(50ETF 期权上市首日)。
  2) 中金所 000852(中证1000股指期权): 官方日行情 XML(含结算价/delta, 无 IV):
     http://www.cffex.com.cn/sj/hqsj/rtj/YYYYMM/DD/index.xml ; IV 由结算价 BS 反推。
  3) 大商所 I0(铁矿石期权): 官方站被 WAF 拦截(412)不可达, 改用新浪"商品期权合约日线"
     FutureOptionAllService.getOptionDayline?symbol=iYYMMC/Pxxxx, 单合约一次返回从上市
     到到期的完整序列(实测 i2005C700 自 2019-12-09 起), IV 由收盘价 Black-76 反推。

口径: 到期天数落在 15~45 天中、最接近 30 天的月份, 取最接近标的价的行权价,
     看涨/看跌 IV 取均值。与 update_options.py 的 pick_atm_iv() 完全一致, 保证历史与
     实时快照同口径。

用法:
    python3 backfill_iv.py                     # 增量回填(跳过已有日期)
    python3 backfill_iv.py --source all        # 三个源全部回填
    python3 backfill_iv.py --force             # 全部重跑
    python3 backfill_iv.py --from 2015-02-01 --to 2018-12-31 --workers 6
"""
import argparse
import calendar
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from update_options import (DATA_DIR, HIST_DIR, RISK_FREE, bj_now, dce_expiry,
                            implied_vol, third_friday)

# 各标的期权上市日(早于该日期的请求无意义)
SSE_TARGETS = {
    "510050": {"name": "50ETF", "start": "2015-02-09"},
    "510300": {"name": "300ETF", "start": "2019-12-23"},
    "510500": {"name": "500ETF", "start": "2022-09-19"},
    "588000": {"name": "科创50", "start": "2023-06-05"},
}

# 固定期限口径(与 update_options.pick_atm_iv 一致)
TENOR_MIN, TENOR_MAX, TENOR_TARGET = 15, 45, 30

RISK_URL = ("http://query.sse.com.cn/commonQuery.do?isPagination=false&trade_date={ds}"
            "&sqlId=SSE_ZQPZ_YSP_GGQQZSXT_YSHQ_QQFXZB_DATE_L&contractSymbol=")
HEADERS = {"Referer": "http://www.sse.com.cn/", "User-Agent": "Mozilla/5.0"}

CONTRACT_RE = re.compile(r"^(\d{6})([CP])(\d{2})(\d{2})M(\d+)$")
_expiry_cache = {}


def expiry_of(y, m):
    """上交所ETF期权到期日: 到期月第四个星期三(节假日顺延导致的差异不影响15~45天窗口)"""
    key = (y, m)
    if key not in _expiry_cache:
        weds = [d for d in range(1, calendar.monthrange(y, m)[1] + 1)
                if datetime(y, m, d).weekday() == 2]
        _expiry_cache[key] = datetime(y, m, weds[3]).date()
    return _expiry_cache[key]


def load_spots(code):
    """{日期: 收盘价}"""
    path = os.path.join(DATA_DIR, f"{code}_daily.json")
    with open(path, encoding="utf-8") as f:
        return {r["date"]: r["close"] for r in json.load(f) if r.get("close")}


def fetch_date(d_iso):
    """取某交易日全部合约的风险指标(入参 YYYY-MM-DD, 含重试)"""
    url = RISK_URL.format(ds=d_iso.replace("-", ""))
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            j = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
            return d_iso, j.get("result") or []
        except Exception as e:      # 网络抖动/限流: 退避重试
            last = e
            time.sleep(1 + attempt * 2)
    print(f"  !! {d_iso} 取数失败: {last}", flush=True)
    return d_iso, None


def pick_iv(rows, code, spot, trade_date):
    """单标的单日: 返回约30天 ATM IV(百分比) 或 None。

    注: 标的日线是前复权价, 历史日期与当时真实行权价差一个分红调整因子, 不能用来判 ATM。
    因此优先用官方 DELTA_VALUE 定位 ATM(看涨 delta 最接近 0.5), 无 delta 时才退回现价法。
    """
    chains = defaultdict(lambda: {"C": {}, "P": {}, "dC": {}, "dP": {}})
    for r in rows:
        m = CONTRACT_RE.match(r.get("CONTRACT_ID") or "")
        if not m or m.group(1) != code:
            continue
        side = m.group(2)
        k = int(m.group(5)) / 1000.0
        ym = int(m.group(3)) * 100 + int(m.group(4))
        try:
            iv = float(r.get("IMPLC_VOLATLTY") or 0)
            delta = float(r.get("DELTA_VALUE") or 0)
        except ValueError:
            continue
        if iv > 0.005:          # 官方对无成交/深度实值合约给 0, 剔除
            chains[ym][side][k] = iv
            chains[ym]["d" + side][k] = delta
    best = None
    for ym, sides in chains.items():
        y, mo = 2000 + ym // 100, ym % 100
        days_left = (expiry_of(y, mo) - trade_date).days
        if days_left < 0:
            continue
        strikes = set(sides["C"]) | set(sides["P"])
        if not strikes:
            continue
        # ATM 判定: 看涨 delta 最接近 0.5 / 看跌 delta 最接近 -0.5
        scored = []
        for k in strikes:
            d = [abs(sides["dC"][k] - 0.5) if k in sides["dC"] else 9,
                 abs(sides["dP"][k] + 0.5) if k in sides["dP"] else 9]
            scored.append((min(d), k))
        atm = min(scored)[1]
        if min(scored)[0] >= 9 and spot is not None:      # 完全没有 delta 时退回现价法
            atm = min(strikes, key=lambda k: abs(k - spot))
        ivs = [sides[s][atm] for s in ("C", "P") if atm in sides[s]]
        if not ivs:
            continue
        key = (0 if TENOR_MIN <= days_left <= TENOR_MAX else 1, abs(days_left - TENOR_TARGET))
        if best is None or key < best[0]:
            best = (key, round(sum(ivs) / len(ivs) * 100, 2))
    return best[1] if best else None


def trading_days(date_from, date_to):
    """以上交所标的的日线日期为准, 取区间内交易日(期权上市前也保留, 由各标的自身上市日过滤)"""
    days = set()
    for code in SSE_TARGETS:
        days.update(load_spots(code).keys())
    return sorted(d for d in days if date_from and date_to and date_from <= d <= date_to)


# ============ 中金所股指期权: 官方日行情 XML + BS 反推 ============
CFFEX_URL = "http://www.cffex.com.cn/sj/hqsj/rtj/{ym}/{dd}/index.xml"
# 标的代码 -> (期权品种, 期权上市日)
CFFEX_TARGETS = {"000852": ("MO", "2022-07-22")}      # 中证1000股指期权
CFFEX_RE = re.compile(r"^([A-Z]+)(\d{2})(\d{2})-([CP])-(\d+(?:\.\d+)?)$")


def _to_float(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def fetch_cffex_date(d_iso):
    """中金所某交易日全部期权/期货日行情(XML, 无 IV 字段但含结算价与 delta)"""
    y, m, d = d_iso.split("-")
    url = CFFEX_URL.format(ym=y + m, dd=d)
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            root = ET.fromstring(urllib.request.urlopen(req, timeout=30).read())
            rows = []
            for el in root.iter("dailydata"):
                rows.append({
                    "code": (el.findtext("instrumentid") or "").strip(),
                    "settle": _to_float(el.findtext("settlementprice")),
                    "close": _to_float(el.findtext("closeprice")),
                    "delta": _to_float(el.findtext("delta")),
                    "vol": _to_float(el.findtext("volume")) or 0,
                })
            return d_iso, rows
        except Exception as e:
            last = e
            time.sleep(1 + attempt * 2)
    print(f"  !! {d_iso} 中金所取数失败: {last}", flush=True)
    return d_iso, None


def cffex_pick(rows, product, spot, trade_date):
    """单日 MO/HO/IO: 近30天 ATM IV(%). ATM 用官方 delta 定, IV 由结算价 BS 反推"""
    if not spot:
        return None
    chains = defaultdict(lambda: {"C": {}, "P": {}, "dC": {}, "dP": {}})
    for r in rows:
        m = CFFEX_RE.match(r["code"])
        if not m or m.group(1) != product:
            continue
        side = m.group(4)
        k = float(m.group(5))
        ym = int(m.group(2)) * 100 + int(m.group(3))
        px = r["settle"] if r["settle"] and r["settle"] > 0 else r["close"]
        if not px or px <= 0:
            continue
        chains[ym][side][k] = px
        if r["delta"] is not None:
            chains[ym]["d" + side][k] = r["delta"]
    best = None
    for ym, sides in chains.items():
        exp = third_friday(2000 + ym // 100, ym % 100)
        days_left = (exp - trade_date).days
        if days_left <= 0:
            continue
        T = days_left / 365.0
        strikes = set(sides["C"]) | set(sides["P"])
        if not strikes:
            continue
        scored = []
        for k in strikes:
            d = [abs(sides["dC"][k] - 0.5) if k in sides["dC"] else 9,
                 abs(sides["dP"][k] + 0.5) if k in sides["dP"] else 9]
            scored.append((min(d), k))
        atm = min(scored)[1]
        if min(scored)[0] >= 9:      # 无 delta 时退回现价法
            atm = min(strikes, key=lambda k: abs(k - spot))
        ivs = []
        for s in ("C", "P"):
            if atm in sides[s]:
                iv = implied_vol(sides[s][atm], spot, atm, T, RISK_FREE, s == "C")
                if iv is not None:
                    ivs.append(iv)
        if not ivs:
            continue
        key = (0 if TENOR_MIN <= days_left <= TENOR_MAX else 1, abs(days_left - TENOR_TARGET))
        if best is None or key < best[0]:
            best = (key, round(sum(ivs) / len(ivs), 2))
    return best[1] if best else None


def run_cffex(args):
    code = "000852"
    product, start = CFFEX_TARGETS[code]
    spots = load_spots(code)
    days = [d for d in sorted(spots) if d >= start and args.date_from <= d <= args.date_to]
    hist_dates = {r.get("date") for r in load_hist(code)}
    todo = days if args.force else [d for d in days if d not in hist_dates]
    print(f"中金所 {product}: 区间 {days[0] if days else '-'} ~ {days[-1] if days else '-'}, "
          f"交易日 {len(days)}, 待回填 {len(todo)}", flush=True)
    filled = {}
    failed = []
    with ThreadPoolExecutor(args.workers) as ex:
        for i, (d_iso, rows) in enumerate(ex.map(fetch_cffex_date, todo), 1):
            if rows is None:
                failed.append(d_iso)
            elif rows:
                iv = cffex_pick(rows, product, spots.get(d_iso),
                                datetime.strptime(d_iso, "%Y-%m-%d").date())
                if iv is not None:
                    filled[d_iso] = iv
            if i % 200 == 0 or i == len(todo):
                merge_history(code, filled, overwrite=args.force)
                filled = {}
                print(f"  进度 {i}/{len(todo)}", flush=True)
    hist = load_hist(code)
    vals = [r["atm_iv"] for r in hist if r.get("atm_iv") is not None]
    print(f"  [{code}] 共 {len(hist)} 条 ({hist[0]['date'] if hist else '-'} ~ "
          f"{hist[-1]['date'] if hist else '-'}), 均值 IV="
          f"{round(sum(vals) / len(vals), 2) if vals else None}%")
    if failed:
        print(f"  失败 {len(failed)} 天: {', '.join(failed[:20])}")


# ============ 大商所铁矿石期权: 新浪合约日线 + Black-76 反推 ============
# 说明: 大商所官网整站被 WAF 拦截(412), 官方日行情接口不可达, 无法像上交所/中金所那样
#      用官方 IV 字段。改用新浪"商品期权合约日线": 单合约一次返回从上市到到期的完整序列
#      (实测 i2005C700 自 2019-12-09 起、i2309 各档行权价齐全), 因此无需逐日累积,
#      一次回溯即可补齐 2019-12 至今。IV 由收盘价按 Black-76 反推, 与实时快照同模型。
DCE_START = "2019-12-09"          # 铁矿石期权上市日
DCE_CACHE_DIR = os.path.join(DATA_DIR, "option_cache", "dce")
DCE_FUT_DIR = os.path.join(DATA_DIR, "option_cache", "dce_fut")
SINA_OPT_URL = ("https://stock.finance.sina.com.cn/futures/api/jsonp.php/"
                "var%20_x=/FutureOptionAllService.getOptionDayline?symbol=")
SINA_FUT_URL = ("https://stock.finance.sina.com.cn/futures/api/jsonp.php/"
                "var%20_x=/InnerFuturesNewService.getDailyKLine?symbol=")


def _jsonp(url):
    """取新浪 JSONP: 合约不存在返回 [], 网络失败返回 None"""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            txt = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
            i, j = txt.find("["), txt.rfind("]")
            if i < 0 or j < i:
                return [] if "null" in txt else None
            return json.loads(txt[i:j + 1])
        except Exception:
            time.sleep(0.5 + attempt * 1.5)
    return None


def _cache_path(kind, sym):
    d = DCE_FUT_DIR if kind == "fut" else DCE_CACHE_DIR
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, sym + ".json")


def _load_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def dce_fut_closes(mm):
    """某月份铁矿石期货合约日线 {日期: 收盘价}"""
    sym = "i" + mm
    path = _cache_path("fut", sym)
    data = _load_cache(path)
    if data is not None:
        return data
    rows = _jsonp(SINA_FUT_URL + sym)
    if rows is None:
        return {}
    data = {r["d"]: float(r["c"]) for r in rows if r.get("c")}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def dce_opt_line(mm, side, k):
    """某期权合约日线 {日期: [收盘价, 成交量]}; 不存在返回 {}, 网络失败返回 None"""
    sym = f"i{mm}{side}{k}"
    path = _cache_path("opt", sym)
    data = _load_cache(path)
    if data is not None:
        return data
    rows = _jsonp(SINA_OPT_URL + sym)
    if rows is None:
        return None
    data = {r["d"]: [float(r["c"]), float(r.get("v") or 0)] for r in rows if r.get("c")}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def month_range(first_ym, last_ym):
    y, m = first_ym // 100, first_ym % 100
    out = []
    while y * 100 + m <= last_ym:
        out.append(f"{y % 100:02d}{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _probe_month(mm, workers):
    """探一个期权月份: 返回 {月份, 到期日, 期货收盘, 各档行权价的看涨/看跌日线}

    行权价范围按"到期前一段时间的标的期货价格"圈定, 避免无谓请求。
    """
    y, m = 2000 + int(mm[:2]), int(mm[2:])
    guess = dce_expiry(y, m)
    fut = dce_fut_closes(mm)
    if not fut or guess is None:
        return None
    win = [c for d, c in fut.items()
           if (guess - timedelta(days=55)).isoformat() <= d <= (guess + timedelta(days=25)).isoformat()]
    if not win:
        return None
    ks = list(range(int(min(win) * 0.85 // 10) * 10, int(max(win) * 1.15 // 10) * 10 + 11, 10))
    ks = [k for k in ks if k > 0]
    lines = {"C": {}, "P": {}}
    jobs = [(s, k) for s in "CP" for k in ks]

    def _job(t):
        s, k = t
        return s, k, dce_opt_line(mm, s, k)

    with ThreadPoolExecutor(workers) as ex:
        for s, k, d in ex.map(_job, jobs):
            if d:
                lines[s][k] = d
    if not lines["C"] and not lines["P"]:
        return None
    lasts = [max(d) for s in "CP" for d in lines[s].values() if d]
    expiry = datetime.strptime(max(max(lasts), guess.isoformat()), "%Y-%m-%d").date() \
        if lasts else guess
    return {"month": mm, "expiry": expiry, "fut": fut, "lines": lines}


def _dce_iv(meta, d, fut_px, days_left):
    """单日 ATM IV(%): 先用"看涨+看跌都有报价"的行权价(两者均值), 缺失时再退回单边"""
    T = days_left / 365.0
    pairs, singles = [], []
    for k in set(meta["lines"]["C"]) | set(meta["lines"]["P"]):
        c = meta["lines"]["C"].get(k, {}).get(d)
        p = meta["lines"]["P"].get(k, {}).get(d)
        if not c and not p:
            continue
        dist = abs(k - fut_px)
        if c and p:
            pairs.append((0 if (c[1] > 0 and p[1] > 0) else 1, dist, k, c, p))
        else:
            px = c or p
            singles.append((0 if px[1] > 0 else 1, dist, k, c, p))
    pairs.sort()
    for _, _, k, c, p in pairs[:5]:
        ivs = [iv for iv in (implied_vol(c[0], fut_px, k, T, RISK_FREE, True, fut=True),
                             implied_vol(p[0], fut_px, k, T, RISK_FREE, False, fut=True))
               if iv is not None]
        if len(ivs) == 2:
            return round(sum(ivs) / 2, 2)
    singles.sort()
    for _, _, k, c, p in singles[:3]:
        ivs = [iv for iv in (implied_vol(c[0], fut_px, k, T, RISK_FREE, True, fut=True) if c else None,
                             implied_vol(p[0], fut_px, k, T, RISK_FREE, False, fut=True) if p else None)
               if iv is not None]
        if ivs:
            return round(sum(ivs) / len(ivs), 2)
    return None


def run_dce(args):
    code = "I0"
    spots = load_spots(code)
    days = [d for d in sorted(spots)
            if DCE_START <= d <= args.date_to and d >= args.date_from]
    if not days:
        print("大商所 I0: 区间内无交易日", flush=True)
        return
    end = datetime.strptime(args.date_to, "%Y-%m-%d").date()
    first_ym = int(days[0][:4]) * 100 + int(days[0][5:7])
    ey, em = end.year, end.month + 4
    if em > 12:
        ey, em = ey + (em - 1) // 12, (em - 1) % 12 + 1
    last_ym = ey * 100 + em
    months = month_range(first_ym, last_ym)
    print(f"大商所 I0: {days[0]} ~ {days[-1]} 共 {len(days)} 个交易日, "
          f"探测 {len(months)} 个期权月份(并发 {args.workers})", flush=True)

    metas = {}
    t0 = time.time()
    for i, mm in enumerate(months, 1):
        meta = _probe_month(mm, args.workers)
        if meta:
            metas[mm] = meta
        if i % 12 == 0 or i == len(months):
            print(f"  探测 {i}/{len(months)}  已用 {time.time() - t0:.0f}s  "
                  f"有效月份 {len(metas)}", flush=True)
    if not metas:
        print("大商所 I0: 未取到任何期权月份数据", flush=True)
        return

    filled = {}
    for d in days:
        trade_date = datetime.strptime(d, "%Y-%m-%d").date()
        best = None
        for mm, meta in metas.items():
            dl = (meta["expiry"] - trade_date).days
            if dl <= 0:
                continue
            key = (0 if TENOR_MIN <= dl <= TENOR_MAX else 1, abs(dl - TENOR_TARGET))
            if best is None or key < best[0]:
                best = (key, mm, dl)
        if best is None:
            continue
        mm, dl = best[1], best[2]
        meta = metas[mm]
        fut_px = meta["fut"].get(d)
        if not fut_px:
            continue
        iv = _dce_iv(meta, d, fut_px, dl)
        if iv is not None:
            filled[d] = iv

    merge_history(code, filled, overwrite=args.force, source="dce_backfill",
                  drop_source="dce_backfill")
    hist = load_hist(code)
    vals = [r["atm_iv"] for r in hist if r.get("atm_iv") is not None]
    print(f"  [{code} 铁矿石] 共 {len(hist)} 条 ({hist[0]['date'] if hist else '-'} ~ "
          f"{hist[-1]['date'] if hist else '-'}), 有值 {len(vals)} 条, 均值 IV="
          f"{round(sum(vals) / len(vals), 2) if vals else None}%")


def load_hist(code):
    path = os.path.join(HIST_DIR, f"{code}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def merge_history(code, filled, overwrite=False, source="sse_backfill", drop_source=None):
    """并入 iv_history: 已有(实时)条目优先; overwrite=True 时用回填口径覆盖其 ATM IV

    drop_source: 合并前先剔除该来源的旧条目(用于整段重建, 避免历史脏值残留)。
    """
    path = os.path.join(HIST_DIR, f"{code}.json")
    hist = load_hist(code)
    if drop_source:
        hist = [r for r in hist if r.get("source") != drop_source]
    have = {r.get("date"): r for r in hist}
    added = 0
    for d, iv in filled.items():
        if d in have:
            # 实时条目带 PCR/HV 等字段, 默认保留原值; overwrite 时统一成回填口径
            if overwrite or have[d].get("source") == source:
                have[d]["atm_iv"] = iv
            continue
        have[d] = {"date": d, "atm_iv": iv, "source": source}
        added += 1
    out = sorted(have.values(), key=lambda r: r.get("date") or "")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return added, len(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["sse", "cffex", "dce", "all"], default="sse",
                    help="sse=上交所4个ETF期权(官方风险指标); cffex=中金所股指期权; "
                         "dce=大商所铁矿石期权; all=全部")
    ap.add_argument("--from", dest="date_from", default="2015-02-01")
    ap.add_argument("--to", dest="date_to", default=bj_now().strftime("%Y-%m-%d"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="忽略已有历史, 全部重取")
    ap.add_argument("--flush", type=int, default=200, help="每N个交易日落盘一次")
    args = ap.parse_args()

    if args.source in ("cffex", "all"):
        run_cffex(args)
    if args.source in ("dce", "all"):
        run_dce(args)
    if args.source in ("sse", "all"):
        run_sse(args)


def run_sse(args):
    spots = {c: load_spots(c) for c in SSE_TARGETS}
    days = trading_days(args.date_from, args.date_to)
    hist_dates = {c: {r.get("date") for r in load_hist(c)} for c in SSE_TARGETS}

    def need(d):
        if args.force:
            return True
        return any(d >= SSE_TARGETS[c]["start"] and d not in hist_dates[c] for c in SSE_TARGETS)

    todo = [d for d in days if need(d)]
    print(f"区间 {args.date_from} ~ {args.date_to}: 交易日 {len(days)}, 待回填 {len(todo)} "
          f"(并发 {args.workers})", flush=True)
    if not todo:
        return

    filled = {c: {} for c in SSE_TARGETS}
    failed = []
    t0 = time.time()
    with ThreadPoolExecutor(args.workers) as ex:
        for i, (d_iso, rows) in enumerate(ex.map(fetch_date, todo), 1):
            if rows is None:
                failed.append(d_iso)
            elif rows:
                trade_date = datetime.strptime(d_iso, "%Y-%m-%d").date()
                for c in SSE_TARGETS:
                    if d_iso < SSE_TARGETS[c]["start"]:
                        continue
                    iv = pick_iv(rows, c, spots[c].get(d_iso), trade_date)
                    if iv is not None:
                        filled[c][d_iso] = iv
            if i % 200 == 0 or i == len(todo):
                el = time.time() - t0
                eta = el / i * (len(todo) - i)
                print(f"  进度 {i}/{len(todo)}  已用 {el:.0f}s  预计剩余 {eta:.0f}s", flush=True)
                for c in SSE_TARGETS:
                    merge_history(c, filled[c], overwrite=args.force)
                    filled[c] = {}     # 落盘后清空, 避免重复合并

    print("\n回填结果:")
    for c, meta in SSE_TARGETS.items():
        hist = load_hist(c)
        vals = [r["atm_iv"] for r in hist if r.get("atm_iv") is not None]
        first = hist[0]["date"] if hist else None
        last = hist[-1]["date"] if hist else None
        avg = round(sum(vals) / len(vals), 2) if vals else None
        print(f"  [{c} {meta['name']}] 共 {len(hist)} 条 ({first} ~ {last}), "
              f"有值 {len(vals)} 条, 均值 IV={avg}%")
    if failed:
        print(f"\n失败 {len(failed)} 天(可重跑补漏): {', '.join(failed[:20])}"
              + (" ..." if len(failed) > 20 else ""))


if __name__ == "__main__":
    main()
