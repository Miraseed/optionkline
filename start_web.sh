#!/bin/bash
# 期权时空容错交易系统 - 一键启动脚本
# 启动 Flask 服务 + cpolar 公网隧道（国内服务，无需翻墙）

cd /workspace

# 检查依赖
python3 -c "import flask" 2>/dev/null || pip install flask --ignore-installed blinker -q
python3 -c "import akshare" 2>/dev/null || pip install akshare -q

# cpolar 路径
CPOLAR="/tmp/cpolar"
if [ ! -f "$CPOLAR" ]; then
  echo "下载 cpolar..."
  cd /tmp
  curl -sL -o cpolar.zip "https://www.cpolar.com/static/downloads/releases/3.3.12/cpolar-stable-linux-amd64.zip"
  unzip -o cpolar.zip
  chmod +x cpolar
  cd /workspace
fi

# 检查 authtoken（需在 https://dashboard.cpolar.com 注册获取）
# 免费版：每次重启 URL 会变
# 基础版(¥9/月)：支持固定子域名，将下方 SUBDOMAIN 设为你想要的名字
SUBDOMAIN=""
AUTHTOKEN="MjhiMWM0ZWUtOWQwNy00OWIyLTk4ZmItOGU2YWQ0ODBmNjU4"

# 配置 authtoken
$CPOLAR authtoken "$AUTHTOKEN" > /dev/null 2>&1

# 启动 Flask 后端（后台）
echo "启动 Flask 服务..."
python3 app.py &
FLASK_PID=$!
sleep 3

# 启动 cpolar 隧道
echo "启动 cpolar 公网隧道..."
if [ -n "$SUBDOMAIN" ]; then
  HTTPS_PROXY=http://127.0.0.1:18080 HTTP_PROXY=http://127.0.0.1:18080 \
    $CPOLAR http 5000 --subdomain "$SUBDOMAIN" > /tmp/cpolar.log 2>&1 &
else
  HTTPS_PROXY=http://127.0.0.1:18080 HTTP_PROXY=http://127.0.0.1:18080 \
    $CPOLAR http 5000 > /tmp/cpolar.log 2>&1 &
fi
TUNNEL_PID=$!

# 等待隧道建立并提取公网URL
sleep 8
PUBLIC_URL=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    for t in d.get('tunnels',[]):
        if t['public_url'].startswith('https'):
            print(t['public_url']); break
except: print('')
" 2>/dev/null)

# 如果 API 没返回，从 web 页面解析
if [ -z "$PUBLIC_URL" ]; then
  sleep 3
  PUBLIC_URL=$(curl -s http://127.0.0.1:4040/http/in 2>/dev/null | grep -oP 'https://[a-z0-9]+\.r[0-9]+\.cpolar\.top' | head -1)
fi

echo ""
echo "============================================"
echo "  期权时空容错交易系统 已启动"
echo "============================================"
echo "  登录密码: option2024"
echo "  本地访问: http://localhost:5000"
echo "  公网访问: ${PUBLIC_URL:-请查看 http://127.0.0.1:4040}"
echo "  停止服务: kill $FLASK_PID $TUNNEL_PID"
echo ""
echo "  【固定域名设置】"
echo "  当前为 cpolar 免费版，每次重启 URL 会变。"
echo "  如需固定子域名（如 option-kline.cpolar.top）："
echo "  1. 登录 https://dashboard.cpolar.com 升级基础版(¥9/月)"
echo "  2. 修改本脚本 SUBDOMAIN=\"option-kline\""
echo "  3. 重启即可使用固定域名"
echo "============================================"
echo ""

wait
