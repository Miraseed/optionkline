#!/bin/bash
# 公网隧道：srv.us 反向 SSH，断线自动重连
# URL 由 SSH 公钥推导，重连后地址保持不变
# 用法: ./tunnel.sh   （依赖本机 8000 端口的 server.py）
cd "$(dirname "$0")" || exit 1
mkdir -p logs
export TERM=dumb
LOG=logs/tunnel.log

while true; do
  echo "=== connect $(date '+%F %T') ===" >> "$LOG"
  ssh -tt \
    -i ~/.ssh/id_ed25519 \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o ServerAliveInterval=10 \
    -o ServerAliveCountMax=3 \
    -o TCPKeepAlive=yes \
    -o ExitOnForwardFailure=yes \
    -o ProxyCommand="nc -X connect -x 127.0.0.1:18080 -w 10 %h %p" \
    -R 80:localhost:8000 nokey@srv.us >> "$LOG" 2>&1
  echo "=== disconnected rc=$? $(date '+%F %T') ===" >> "$LOG"
  sleep 5
done
