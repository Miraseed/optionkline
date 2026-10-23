#!/bin/bash
# 沙箱进程守护: server.py 与 srv.us 隧道任一退出即自动拉起。
# 沙箱无 cron/systemd, 用 setsid 完全脱离会话树启动本脚本,
# 避免工具会话结束时后台进程被整体回收(2026-10-23 15:47 曾发生)。
cd "$(dirname "$0")" || exit 1
mkdir -p logs
LOG=logs/keepalive.log

echo "=== keepalive start $(date '+%F %T') ===" >> "$LOG"
while true; do
  # 1) 数据服务
  if ! pgrep -f "python3 server.py" >/dev/null 2>&1; then
    echo "[$(date '+%F %T')] restart server.py" >> "$LOG"
    setsid nohup python3 server.py 8000 >> logs/server.log 2>&1 &
  fi
  # 2) 公网隧道(tunnel.sh 自带断线重连, 仅在整个循环脚本不在时拉起)
  if ! pgrep -f "bash tunnel.sh" >/dev/null 2>&1; then
    if ! pgrep -f "nokey@srv.us" >/dev/null 2>&1; then
      echo "[$(date '+%F %T')] restart tunnel.sh" >> "$LOG"
      setsid nohup bash tunnel.sh >> /dev/null 2>&1 &
    fi
  fi
  sleep 60
done
