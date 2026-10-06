"""期权时空容错交易系统 - Web 服务端

功能：
- 展示 7 个标的的日/周/月K线数据与风险评级
- K线图可视化（ECharts 蜡烛图）
- 交易日交易时间内每 5 分钟自动刷新数据
- 登录鉴权（密码保护）
- 支持本地访问与远程分享（cloudflared 公网隧道）

使用：python3 app.py
本地：http://localhost:5000
远程：通过 cloudflared 隧道获得的公网 URL
密码：环境变量 APP_PASSWORD 设置，默认 option2024
"""
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, jsonify, request, session, redirect, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET", "option-time-space-secret-key-2024")

# ============ 配置 ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KLINE_DIR = os.environ.get("KLINE_DIR", os.path.join(BASE_DIR, "kline_data"))
os.makedirs(KLINE_DIR, exist_ok=True)
REFRESH_INTERVAL = 300  # 默认5分钟（秒），可被前端动态修改
AUTH_PASSWORD = os.environ.get("APP_PASSWORD", "option2024")  # 登录密码

# 7 个标的（decimal_places: 交易所要求的行情小数位）
UNDERLYINGS = [
    {"code": "510050", "name": "上证50ETF",  "decimal_places": 3},
    {"code": "510300", "name": "沪深300ETF", "decimal_places": 3},
    {"code": "588000", "name": "科创50ETF",  "decimal_places": 3},
    {"code": "159915", "name": "创业板ETF",  "decimal_places": 3},
    {"code": "510500", "name": "中证500ETF", "decimal_places": 3},
    {"code": "000852", "name": "中证1000",   "decimal_places": 2},
    {"code": "I0",     "name": "铁矿石主连", "decimal_places": 1},
]

# 全局缓存与状态
_cache = {
    "data": {},
    "pe_data": {},
    "last_update": None,
    "refreshing": False,
}
_cache_lock = threading.Lock()


# ============ 登录鉴权 ============
def login_required(f):
    """装饰器：要求已登录才能访问"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            # API 请求返回 401，页面请求跳转登录页
            if request.path.startswith("/api/"):
                return jsonify({"error": "未登录"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    """登录页/登录接口"""
    if request.method == "POST":
        pwd = request.form.get("password") or (request.get_json(silent=True) or {}).get("password")
        if pwd == AUTH_PASSWORD:
            session["authenticated"] = True
            session.permanent = True
            return jsonify({"success": True})
        return jsonify({"success": False, "message": "密码错误"}), 401
    # GET: 如果已登录直接跳转主页
    if session.get("authenticated"):
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("authenticated", None)
    return redirect(url_for("login"))


# ============ 交易时间判断 ============
def is_trading_day(dt=None):
    if dt is None:
        dt = datetime.now()
    return dt.weekday() < 5


def is_trading_hours(dt=None):
    if dt is None:
        dt = datetime.now()
    if not is_trading_day(dt):
        return False
    t = dt.time()
    morning = t >= datetime.strptime("09:30", "%H:%M").time() and t <= datetime.strptime("11:30", "%H:%M").time()
    afternoon = t >= datetime.strptime("13:00", "%H:%M").time() and t <= datetime.strptime("15:00", "%H:%M").time()
    return morning or afternoon


def next_refresh_info():
    now = datetime.now()
    if is_trading_hours(now):
        secs = REFRESH_INTERVAL
        label = f"{secs}秒" if secs < 60 else f"{secs // 60}分钟"
        return f"交易时间内，每{label}自动刷新"
    elif is_trading_day(now):
        t = now.time()
        if t < datetime.strptime("09:30", "%H:%M").time():
            return "非交易时段，9:30后开始自动刷新"
        elif t < datetime.strptime("13:00", "%H:%M").time():
            return "午间休市，13:00后继续自动刷新"
        else:
            return "今日交易已结束"
    else:
        return "非交易日，数据仅供查看"


# ============ 数据加载 ============
def load_all_data():
    data = {}
    for u in UNDERLYINGS:
        code = u["code"]
        data[code] = {}
        for period in ["daily", "weekly", "monthly"]:
            path = os.path.join(KLINE_DIR, f"{code}_{period}.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data[code][period] = json.load(f)
            else:
                data[code][period] = []
    return data


def load_pe_data():
    """加载PE数据 {code: [{"date": ..., "pe_ttm": ...}]}"""
    path = os.path.join(KLINE_DIR, "pe_data.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def refresh_data_live():
    try:
        import akshare as ak
        import pandas as pd
    except ImportError:
        return False, "akshare 未安装，无法在线刷新。请先 pip install akshare"

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from update_excel_data import fetch_kline_data, fetch_pe_data, save_pe_data, UNDERLYINGS as ETF_LIST

    success = 0
    fail = []
    for etf in ETF_LIST:
        try:
            kline_data = fetch_kline_data(etf)
            for period, records in kline_data.items():
                path = os.path.join(KLINE_DIR, f"{etf['code']}_{period}.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(records, f, ensure_ascii=False, indent=2)
            success += 1
        except Exception as e:
            fail.append(f"{etf['name']}: {e}")

    # 同时更新PE数据
    try:
        pe_data = fetch_pe_data()
        save_pe_data(pe_data)
    except Exception as e:
        fail.append(f"PE数据: {e}")

    with _cache_lock:
        _cache["data"] = load_all_data()
        _cache["pe_data"] = load_pe_data()
        _cache["last_update"] = datetime.now()

    msg = f"成功更新 {success}/{len(ETF_LIST)} 个标的"
    if fail:
        msg += "；失败: " + "; ".join(fail)
    return success > 0, msg


def refresh_data():
    with _cache_lock:
        if _cache["refreshing"]:
            return False, "正在刷新中，请稍候..."
        _cache["refreshing"] = True
    try:
        ok, msg = refresh_data_live()
        return ok, msg
    finally:
        with _cache_lock:
            _cache["refreshing"] = False


# ============ 后台自动刷新线程 ============
def auto_refresh_loop():
    last_refresh = 0
    while True:
        try:
            now = time.time()
            if is_trading_hours() and (now - last_refresh) >= REFRESH_INTERVAL:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 自动刷新数据...")
                ok, msg = refresh_data()
                print(f"  结果: {msg}")
                last_refresh = time.time()
            time.sleep(10)
        except Exception as e:
            print(f"[自动刷新异常] {e}")
            time.sleep(30)


# ============ 路由 ============
@app.route("/")
@login_required
def index():
    return render_template("index.html", underlyings=UNDERLYINGS)


@app.route("/demo")
@login_required
def demo():
    """K线图样式对比演示页"""
    return render_template("demo.html")


@app.route("/api/data")
@login_required
def api_data():
    with _cache_lock:
        data = _cache["data"]
        pe_data = _cache.get("pe_data", {})
        last_update = _cache["last_update"]

    # 计算每个标的最新PE(TTM)及百分位
    pe_latest = {}
    for code in data:
        pe_records = pe_data.get(code)
        if pe_records:
            # 按日期排序取最新
            sorted_recs = sorted(pe_records, key=lambda x: x["date"])
            latest = sorted_recs[-1]
            # 优先使用乐咕乐股API返回的百分位，否则自行计算
            if "quantile" in latest and latest["quantile"] is not None:
                percentile = latest["quantile"]
            else:
                import bisect
                all_values = sorted(r["pe_ttm"] for r in sorted_recs)
                rank = bisect.bisect_right(all_values, latest["pe_ttm"])
                percentile = round(rank / len(all_values) * 100, 1)
            pe_latest[code] = {
                "pe_ttm": latest["pe_ttm"],
                "percentile": percentile,
            }

    return jsonify({
        "data": data,
        "pe_latest": pe_latest,
        "last_update": last_update.strftime("%Y-%m-%d %H:%M:%S") if last_update else None,
        "trading_hours": is_trading_hours(),
        "trading_day": is_trading_day(),
        "refresh_status": next_refresh_info(),
    })


@app.route("/api/kline/<code>/<period>")
@login_required
def api_kline(code, period):
    """返回 ECharts 蜡烛图格式数据"""
    with _cache_lock:
        records = _cache["data"].get(code, {}).get(period, [])
    dates = [r["date"] for r in records]
    values = [[r["open"], r["close"], r["low"], r["high"]] for r in records]
    volumes = [r["volume"] for r in records]
    risk_scores = [r["risk_score"] for r in records]
    risk_labels = [r.get("risk_label", "") for r in records]
    closes = [r["close"] for r in records]
    def ma(n):
        result = []
        for i in range(len(closes)):
            if i < n - 1:
                result.append(None)
            else:
                result.append(round(sum(closes[i-n+1:i+1]) / n, 4))
        return result

    # PE(TTM) 数据与历史百分位（铁矿石等无PE标的返回 null）
    pe_ttm, pe_percentile = get_pe_for_dates(code, dates)

    resp = {
        "dates": dates,
        "values": values,
        "volumes": volumes,
        "risk_scores": risk_scores,
        "risk_labels": risk_labels,
        "ma5": ma(5),
        "ma10": ma(10),
        "ma20": ma(20),
        "ma60": ma(60),
    }
    if pe_ttm is not None:
        resp["pe_ttm"] = pe_ttm
        resp["pe_percentile"] = pe_percentile
    return jsonify(resp)


@app.route("/api/refresh", methods=["POST"])
@login_required
def api_refresh():
    ok, msg = refresh_data()
    with _cache_lock:
        last_update = _cache["last_update"]
    return jsonify({
        "success": ok,
        "message": msg,
        "last_update": last_update.strftime("%Y-%m-%d %H:%M:%S") if last_update else None,
    })


@app.route("/api/set_refresh_interval", methods=["POST"])
@login_required
def api_set_refresh_interval():
    global REFRESH_INTERVAL
    seconds = request.json.get("seconds") if request.json else None
    if seconds is None:
        return jsonify({"success": False, "message": "缺少 seconds 参数"}), 400
    seconds = int(seconds)
    if seconds < 10:
        seconds = 10  # 最低10秒
    if seconds > 600:
        seconds = 600  # 最高10分钟
    REFRESH_INTERVAL = seconds
    label = f"{seconds}秒" if seconds < 60 else f"{seconds // 60}分钟"
    return jsonify({"success": True, "refresh_interval": seconds, "label": label})


@app.route("/api/status")
def api_status():
    with _cache_lock:
        last_update = _cache["last_update"]
        refreshing = _cache["refreshing"]
    return jsonify({
        "trading_hours": is_trading_hours(),
        "trading_day": is_trading_day(),
        "refresh_status": next_refresh_info(),
        "last_update": last_update.strftime("%Y-%m-%d %H:%M:%S") if last_update else None,
        "refreshing": refreshing,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "authenticated": bool(session.get("authenticated")),
    })


# ============ PE 数据匹配 ============
def get_pe_for_dates(code, dates):
    """为每个K线日期匹配 PE(TTM) 值和历史百分位。

    PE 数据为月度（乐咕乐股），取该日期之前最新的月度 PE 值。
    百分位 = 该 PE 值在全部历史 PE 数据中的排名百分位（升序）。
    返回 (pe_ttm_list, pe_percentile_list)，无数据时返回 (None, None)。
    """
    import bisect
    with _cache_lock:
        pe_records = _cache.get("pe_data", {}).get(code, [])
    if not pe_records:
        return None, None

    pe_records = sorted(pe_records, key=lambda x: x["date"])
    pe_dates = [r["date"] for r in pe_records]
    pe_values = [r["pe_ttm"] for r in pe_records]

    # 计算每个 PE 值的历史百分位（基于全部历史数据，升序排名）
    sorted_values = sorted(pe_values)
    n = len(sorted_values)
    percentile_of = {}
    for v in pe_values:
        if v not in percentile_of:
            rank = bisect.bisect_right(sorted_values, v)
            percentile_of[v] = round(rank / n * 100, 1)

    pe_ttm_list = []
    pe_percentile_list = []
    pe_idx = 0
    for d in dates:
        while pe_idx < len(pe_dates) and pe_dates[pe_idx] <= d:
            pe_idx += 1
        if pe_idx > 0:
            v = pe_values[pe_idx - 1]
            pe_ttm_list.append(v)
            pe_percentile_list.append(percentile_of[v])
        else:
            pe_ttm_list.append(None)
            pe_percentile_list.append(None)

    return pe_ttm_list, pe_percentile_list


# ============ 初始化 ============
def init_cache():
    with _cache_lock:
        _cache["data"] = load_all_data()
        _cache["pe_data"] = load_pe_data()
        _cache["last_update"] = datetime.now()
    print(f"数据加载完成: {len(_cache['data'])} 个标的, PE数据: {len(_cache['pe_data'])} 个标的")


if __name__ == "__main__":
    init_cache()

    # 如果数据目录为空，启动时自动获取一次数据
    has_data = any(
        os.path.exists(os.path.join(KLINE_DIR, f"{u['code']}_daily.json"))
        for u in UNDERLYINGS
    )
    if not has_data:
        print("数据文件不存在，启动时自动获取数据...")
        try:
            refresh_data_live()
        except Exception as e:
            print(f"启动时数据获取失败: {e}")

    t = threading.Thread(target=auto_refresh_loop, daemon=True)
    t.start()
    print("后台自动刷新线程已启动（交易时间内每5分钟刷新）")

    port = int(os.environ.get("PORT", 5000))
    print("\n" + "=" * 60)
    print("期权时空容错交易系统 - Web 服务已启动")
    print(f"  登录密码: {AUTH_PASSWORD}")
    print(f"  监听端口: {port}")
    print(f"  交易时间: 周一至周五 9:30-11:30 / 13:00-15:00")
    print(f"  自动刷新: 交易时间内每 5 分钟")
    print("=" * 60 + "\n")

    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
