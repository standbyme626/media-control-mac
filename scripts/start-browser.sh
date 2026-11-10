#!/bin/bash
# 启动受控浏览器（独立 profile + CDP），供 music-bridge 通过 CDP 控制。
set -u

BROWSER="${MB_BROWSER:-/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge}"
PROFILE="${MB_PROFILE:-$HOME/.music-web-profile}"
PORT="${MB_CDP_PORT:-9222}"
START_URL="${MB_START_URL:-https://www.bilibili.com}"
LOG="/tmp/music-web-browser.log"

if [ ! -x "$BROWSER" ]; then
  echo "找不到浏览器：$BROWSER"
  echo "安装：brew install --cask google-chrome 或 brew install --cask microsoft-edge"
  echo "也可以用 MB_BROWSER 指定可执行文件路径"
  exit 1
fi

# 清掉上次残留的单例锁，否则新实例会直接退出
rm -f "$PROFILE"/SingletonLock "$PROFILE"/SingletonSocket "$PROFILE"/SingletonCookie 2>/dev/null

nohup "$BROWSER" \
  --remote-debugging-port="$PORT" \
  --remote-allow-origins=* \
  --user-data-dir="$PROFILE" \
  --no-first-run --no-default-browser-check \
  --autoplay-policy=no-user-gesture-required \
  --disable-features=Translate \
  "$START_URL" > "$LOG" 2>&1 &

echo "浏览器已启动 profile=$PROFILE CDP=$PORT"
for i in $(seq 1 15); do
  sleep 1
  if curl -s -m 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
    echo "CDP 就绪"
    exit 0
  fi
done
echo "CDP 未就绪，查看日志：tail -20 $LOG"
exit 1
