"""
TRAE 沙箱内运行的 Web 服务：
1) 提供 index.html + kline_data 静态访问
2) 后台线程按交易时段定时运行 update_data_pages.py 刷新K线（不再依赖 GitHub Actions）
用法: python3 server.py [port]
"""
import json
import os
import sys
import subprocess
import threading
import time
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

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
            f.write(f"===== 更新结束 {bj_now().strftime('%H:%M:%S')} rc={r.returncode} =====\n")
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


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/status":
            self.send_status()
            return
        super().do_GET()

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


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    load_status()
    threading.Thread(target=updater_loop, daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[server] listening on 0.0.0.0:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
