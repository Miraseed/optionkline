#!/bin/bash
# 沙箱进程守护: server.py 退出即自动拉起。
# 沙箱无 cron/systemd, 用 setsid 完全脱离会话树启动本脚本,
# 避免工具会话结束时后台进程被整体回收(2026-10-23 15:47 曾发生)。
# 注: srv.us 公网隧道已不再支持固定 80 端口映射(URL 每次变化), 页面改为
# 直接读站点 JSON + 腾讯实时行情补齐当日K线, 不再依赖隧道。
cd "$(dirname "$0")" || exit 1
mkdir -p logs
LOG=logs/keepalive.log

echo "=== keepalive start $(date '+%F %T') ===" >> "$LOG"
while true; do
  # 用 [p] 防止 pgrep 匹配到自身命令行
  if ! pgrep -f "[p]ython3 server.py" >/dev/null 2>&1; then
    echo "[$(date '+%F %T')] restart server.py" >> "$LOG"
    setsid nohup python3 server.py 8000 >> logs/server.log 2>&1 < /dev/null &
  fi
  sleep 60
done
