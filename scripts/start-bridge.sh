#!/bin/bash
# 启动/重启 music-bridge
set -u
DIR="${MB_HOME:-$HOME/music-bridge}"
PORT="${MB_PORT:-8765}"

pkill -f "$DIR/bridge.py" 2>/dev/null
sleep 1
for p in $(lsof -ti tcp:"$PORT" 2>/dev/null); do kill -9 "$p" 2>/dev/null; done
sleep 1

cd "$DIR" || { echo "找不到目录：$DIR"; exit 1; }
nohup python3 bridge.py > bridge.log 2>&1 &
sleep 2
curl -s -m 4 "http://127.0.0.1:$PORT/bt" && echo " <- bridge up"
