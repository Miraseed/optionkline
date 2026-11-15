#!/usr/bin/env python3
"""生成铁矿石主连(I0)的主力合约切换区间数据, 供日K图横轴标注换月时点。

原理: 新浪 I0 "主力连续" 的 OHLC 与当时的主力月合约完全一致,
逐日把 I0 的 开/高/低/收 与各月合约比对即可精确还原它在跟踪哪个合约,
合约发生变化的那一天就是换月日。数据源无需 token。

用法:
  python update_contract_map.py           # 增量: 只重建最近 200 天
  python update_contract_map.py --full    # 全量: 2014 年至今
"""
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests

SINA = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
        "var%20_{sym}=/InnerFuturesNewService.getDailyKLine?symbol={sym}")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kline_data", "I0_contracts.json")
NAME = "铁矿石"


def fetch(sym):
    try:
        r = requests.get(SINA.format(sym=sym), timeout=25)
        m = re.search(r"\((\[.*\])\)", r.text, re.S)
        return sym, (json.loads(m.group(1)) if m else [])
    except Exception:
        return sym, []


def candidate_codes(start, end):
    """起止日期内到期的 01/05/09 合约(铁矿石主力只在这些月份间切换)"""
    codes = []
    for y in range(int(start[:4]) - 1, int(end[:4]) + 2):
        for m in (1, 5, 9):
            codes.append("I%02d%02d" % (y % 100, m))
    return codes


def same(a, b):
    return all(abs(float(a[k]) - float(b[k])) < 0.011 for k in "ohlc")


def match_days(i0_rows, contracts, start_date):
    """返回 start_date 之后每个交易日的 {'日期': 合约代码}"""
    by_day = {}
    for c, rows in contracts.items():
        for r in rows:
            by_day.setdefault(r["d"], []).append((c, r))
    mapping, prev = {}, None
    for r in i0_rows:
        if r["d"] < start_date:
            continue
        cands = [(c, cr) for c, cr in by_day.get(r["d"], []) if same(r, cr)]
        if not cands:
            if prev:
                mapping[r["d"]] = prev
            continue
        if prev and any(c == prev for c, _ in cands):
            mapping[r["d"]] = prev
        else:
            cands.sort(key=lambda x: float(x[1]["p"] or 0), reverse=True)
            mapping[r["d"]] = cands[0][0]
        prev = mapping[r["d"]]
    return mapping


def to_segments(mapping):
    segs = []
    for d in sorted(mapping):
        c = mapping[d]
        if not segs or segs[-1]["contract"] != c:
            segs.append({"start": d, "contract": c, "name": NAME + c[1:]})
    return segs


def main():
    full = "--full" in sys.argv
    _, i0 = fetch("I0")
    if not i0:
        sys.exit("I0 行情获取失败, 跳过")
    last = i0[-1]["d"]
    start = "2014-01-01" if full else (
        datetime.strptime(last, "%Y-%m-%d") - timedelta(days=200)).strftime("%Y-%m-%d")

    old = []
    if not full and os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8")).get("segments", [])
        except Exception:
            old = []
    # 保留重建窗口之前已确定的区间; 窗口内的区间整体重算
    keep = [s for s in old if s["start"] < start]

    codes = candidate_codes(start, last)
    with ThreadPoolExecutor(12) as ex:
        contracts = dict(ex.map(fetch, codes))
    contracts = {k: v for k, v in contracts.items() if v}
    if not contracts:
        sys.exit("候选合约行情全部获取失败, 跳过")

    mapping = match_days(i0, contracts, start)
    segs = keep + to_segments(mapping)
    # 相邻同合约去重
    merged = []
    for s in segs:
        if merged and merged[-1]["contract"] == s["contract"]:
            continue
        merged.append(s)

    out = {
        "symbol": "I0",
        "updated": last,
        "note": "主力合约由 I0 连续行情与各月合约 OHLC 逐日精确比对推断",
        "segments": merged,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("合约区间 %d 段, 最新: %s %s -> %s" % (
        len(merged), last, merged[-1]["name"] if merged else "-", OUT))


if __name__ == "__main__":
    main()
