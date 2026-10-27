"""
TRAE 沙箱内运行的 Web 服务：
1) 提供 index.html + kline_data 静态访问
2) 后台线程按交易时段定时运行 update_data_pages.py 刷新K线（不再依赖 GitHub Actions）
用法: python3 server.py [port]
"""
import gzip
import json
import os
import re
import shutil
import sys
import subprocess
import threading
import time
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import unquote

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
UPDATE_LOG = os.path.join(LOG_DIR, "update.log")
STATUS_FILE = os.path.join(LOG_DIR, "status.json")

BJ = timezone(timedelta(hours=8))
_update_lock = threading.Lock()
# 上次数据刷新结果, 供 /api/status 查询, 页面用它显示"数据多久前更新"
_last_update = {"ts": 0, "ok": None, "duration": 0}


def bj_now():
    return datetime.now(BJ)


def in_trading_session():
    """北京时间 9:00-15:05 / 21:00-23:05（含铁矿石夜盘），周末不刷新"""
    now = bj_now()
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return (540 <= mins <= 905) or (1260 <= mins <= 1385)


def load_status():
    """重启后沿用上次刷新时间, 避免页面显示"--" """
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        for k in ("ts", "ok", "duration"):
            if k in d:
                _last_update[k] = d[k]
    except Exception:
        pass


def save_status():
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(_last_update, f)
    except Exception:
        pass


def run_update():
    if not _update_lock.acquire(blocking=False):
        return
    try:
        started = bj_now().strftime("%H:%M:%S")
        t0 = time.time()
        with open(UPDATE_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n===== 更新开始 {started} =====\n")
            f.flush()
            r = subprocess.run(
                [sys.executable, "update_data_pages.py"],
                cwd=BASE_DIR, stdout=f, stderr=subprocess.STDOUT,
            )
            # 期权数据(IV/PCR/微笑/期限结构), 失败不阻断主数据
            r2 = subprocess.run(
                [sys.executable, "update_options.py"],
                cwd=BASE_DIR, stdout=f, stderr=subprocess.STDOUT,
            )
            f.write(f"===== 更新结束 {bj_now().strftime('%H:%M:%S')} rc={r.returncode}/{r2.returncode} =====\n")
        _last_update.update(ts=time.time(), ok=(r.returncode == 0),
                            duration=round(time.time() - t0, 1))
        save_status()
        print(f"[updater] {started} 完成 rc={r.returncode}", flush=True)
    finally:
        _update_lock.release()


def updater_loop():
    # 启动即更新一次，之后交易时段每 3 分钟、非交易时段每 30 分钟
    run_update()
    last = time.time()
    while True:
        time.sleep(10)
        interval = 180 if in_trading_session() else 1800
        if time.time() - last >= interval:
            run_update()
            last = time.time()


# ==================== 15:15 收盘复盘提醒 ====================
# GitHub cron 常有滞后, 故收盘终版由沙箱服务锁存并直接推送企微, 不依赖 Actions
CLOSE_STATE_FILE = os.path.join(LOG_DIR, "close_review.json")
_whoami_cache = {"chat_id": None}
_close_attempts = {}


def _close_sent_date():
    try:
        with open(CLOSE_STATE_FILE, encoding="utf-8") as f:
            return json.load(f).get("date")
    except Exception:
        return None


def _mark_close_sent(date, ok):
    try:
        with open(CLOSE_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": date, "ok": ok,
                       "ts": bj_now().strftime("%Y-%m-%d %H:%M:%S")}, f)
    except Exception:
        pass


def _wecom_chat_id():
    """从 wecom-cli whoami 取授权真人 ID 作 chat_id, 成功后缓存"""
    if _whoami_cache["chat_id"]:
        return _whoami_cache["chat_id"]
    r = subprocess.run(["wecom-cli", "identity", "whoami"],
                       capture_output=True, text=True, timeout=120)
    ctx = ""
    try:
        ctx = json.loads(r.stdout).get("extra_identity_context", "")
    except Exception:
        ctx = r.stdout
    m = re.search(r"授权真人用户身份：\s*名字：(.+?)\s*ID：([^\s\\\"]+)", ctx)
    if not m:
        raise RuntimeError("whoami 未解析到授权人 ID")
    _whoami_cache["chat_id"] = m.group(2)
    return m.group(2)


def _close_markdown(rev):
    lines = [f"## 收盘复盘 · {rev.get('date')} (日盘)",
             f"> 数据锁存 {rev.get('locked_at') or rev.get('generated_at')}"
             f" · {len(rev.get('items', []))} 标的 · {len(rev.get('alerts', []))} 条提醒", ""]
    for it in rev.get("items", []):
        r = it.get("ratings") or {}
        c = it.get("changes") or {}
        parts = ["日", "周", "月"]
        keys = ["daily", "weekly", "monthly"]
        rt = " ".join(f"{parts[i]}{r[keys[i]]:+d}" if r.get(keys[i]) is not None else f"{parts[i]}-"
                      for i in range(3))
        ch_items = []
        for i in range(3):
            v = c.get(keys[i])
            if v:
                ch_items.append(f"{parts[i]}{'↑' if v > 0 else '↓'}{abs(v)}")
        ch = " ".join(ch_items)
        spot = f"{it['spot']}" if it.get("spot") is not None else "--"
        chg = f"({it['chg'] * 100:+.2f}%)" if it.get("chg") is not None else ""
        iv = it.get("iv") or {}
        ivs = ""
        if iv.get("atm_iv") is not None:
            ivs = f" · IV {iv['atm_iv']}%"
            if iv.get("iv_pct") is not None:
                ivs += f"(分位{iv['iv_pct']}%)"
            if iv.get("iv_hv_ratio") is not None:
                ivs += f"/HV {iv['iv_hv_ratio']}"
        lines.append(f"- **{it['name']}** {spot} {chg} · {rt}{(' · ' + ch) if ch else ''}{ivs}")
    alerts = rev.get("alerts") or []
    if alerts:
        lines += ["", f"**要留意（{len(alerts)}）**"]
        lines += [f"- {a}" for a in alerts]
    return "\n".join(lines)


def _send_wecom(chat_id, content):
    payload = json.dumps({"chat_id": chat_id, "msg_type": "markdown",
                          "markdown": {"content": content}}, ensure_ascii=False)
    r = subprocess.run(["wecom-cli", "message", "aibot", "send", "--json", payload],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"wecom send rc={r.returncode}: {(r.stderr or r.stdout)[:300]}")
    return r.stdout


def run_close_review():
    """锁存收盘终版复盘并推送企微; 返回 True 表示成功"""
    with open(UPDATE_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n===== 收盘复盘 {bj_now().strftime('%H:%M:%S')} =====\n")
        f.flush()
        r = subprocess.run([sys.executable, "update_data_pages.py", "--review-close"],
                           cwd=BASE_DIR, stdout=f, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        raise RuntimeError(f"review-close rc={r.returncode}")
    with open(os.path.join(BASE_DIR, "kline_data", "review_close_latest.json"), encoding="utf-8") as f:
        rev = json.load(f)
    _send_wecom(_wecom_chat_id(), _close_markdown(rev))
    print(f"[close-review] {rev.get('date')} 已锁存并推送企微", flush=True)
    return True


def close_review_loop():
    """工作日 15:15-16:00 触发一次收盘提醒; 失败最多重试 3 次, 每日只成功一次"""
    while True:
        try:
            now = bj_now()
            mins = now.hour * 60 + now.minute
            date = now.strftime("%Y-%m-%d")
            if now.weekday() < 5 and 915 <= mins < 960 and _close_sent_date() != date \
                    and _close_attempts.get(date, 0) < 3:
                try:
                    run_close_review()
                    _mark_close_sent(date, True)
                except Exception as e:
                    _close_attempts[date] = _close_attempts.get(date, 0) + 1
                    print(f"[close-review] {date} 第{_close_attempts[date]}次失败: {e}", flush=True)
                    if _close_attempts[date] >= 3:
                        _mark_close_sent(date, False)
        except Exception as e:
            print(f"[close-review] 调度异常: {e}", flush=True)
        time.sleep(30)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def do_GET(self):
        path_only = self.path.split("?")[0]
        if path_only == "/api/status":
            self.send_status()
            return
        # 浏览器支持 gzip 且存在较新的预压缩副本时, 直接下发 .gz(全量K线约省 85% 流量)
        if "gzip" in self.headers.get("Accept-Encoding", "").lower():
            rel = unquote(path_only).lstrip("/") or "index.html"
            src = os.path.join(BASE_DIR, rel)
            gz = src + ".gz"
            if rel.endswith((".json", ".html")) and os.path.isfile(src) and os.path.isfile(gz) \
                    and os.path.getmtime(gz) >= os.path.getmtime(src):
                self.send_gzip(gz, rel)
                return
        super().do_GET()

    def send_gzip(self, gz_path, rel):
        try:
            size = os.path.getsize(gz_path)
            self.send_response(200)
            ctype = "application/json; charset=utf-8" if rel.endswith(".json") else "text/html; charset=utf-8"
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(gz_path, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_status(self):
        """数据新鲜度: 上次成功刷新时间、距今秒数、是否正在更新"""
        now = time.time()
        ts = _last_update["ts"]
        trading = in_trading_session()
        body = json.dumps({
            "updated_at": datetime.fromtimestamp(ts, BJ).strftime("%Y-%m-%d %H:%M:%S") if ts else None,
            "age_seconds": int(now - ts) if ts else None,
            "ok": _last_update["ok"],
            "duration": _last_update["duration"],
            "updating": _update_lock.locked(),
            "in_trading_session": trading,
            "interval": 180 if trading else 1800,
            "server_time": bj_now().strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # K线数据与页面都禁用缓存，保证刷新即最新
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


def prepare_static_gzip():
    """index.html 由人工维护, 源文件更新时重新生成 gzip 副本"""
    src = os.path.join(BASE_DIR, "index.html")
    gz = src + ".gz"
    try:
        if os.path.isfile(src) and (not os.path.isfile(gz) or os.path.getmtime(gz) < os.path.getmtime(src)):
            with open(src, "rb") as f, gzip.open(gz, "wb", compresslevel=9) as g:
                shutil.copyfileobj(f, g)
    except Exception:
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    load_status()
    prepare_static_gzip()
    threading.Thread(target=updater_loop, daemon=True).start()
    threading.Thread(target=close_review_loop, daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[server] listening on 0.0.0.0:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
