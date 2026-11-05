#!/usr/bin/env sh
# ============================================================
#  MAPAS 前端一键启动脚本（macOS / Linux）
#  用法：sh dev.sh   或   ./dev.sh
# ============================================================
set -e
cd "$(dirname "$0")"

if command -v pnpm >/dev/null 2>&1; then
  PKG="pnpm"
elif command -v npm >/dev/null 2>&1; then
  PKG="npm"
else
  echo "[错误] 没有找到 Node.js（pnpm / npm）。请先安装 Node.js 20.19+ 或 22.12+"
  exit 1
fi

if [ ! -d node_modules ]; then
  echo "[1/2] 首次运行：安装前端依赖（使用 $PKG）..."
  "$PKG" install
fi

echo "[2/2] 启动前端开发服务器..."
echo "浏览器打开：http://localhost:5173"
echo
echo "提示：前端需要连接本地桥接，请另开终端执行："
echo "  cd ../.. && python code/web/bridge/web_bridge.py --port 8787 --no-token"
echo
exec "$PKG" dev
