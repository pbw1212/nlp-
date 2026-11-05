#requires -version 5
# ============================================================
#  MAPAS 前端启动脚本（由 dev.cmd 以 Bypass 执行策略调用）
#  作用：检测 Node/pnpm → 首次安装依赖 → 启动 Vite 开发服务器
# ============================================================
param(
  [switch]$Check
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot\..
$webRoot = (Get-Location).Path

function Get-PackageManager {
  if (Get-Command pnpm.cmd -ErrorAction SilentlyContinue) { return 'pnpm' }
  if (Get-Command npm.cmd -ErrorAction SilentlyContinue) { return 'npm' }
  return $null
}

$pkg = Get-PackageManager

if ($Check) {
  if ($null -eq $pkg) {
    Write-Host '[FAIL] 没有找到 Node.js（pnpm / npm）。请先安装 Node.js 20.19+ 或 22.12+：https://nodejs.org'
    exit 1
  }
  Write-Host "[OK] 找到 $pkg（将使用 $pkg 安装依赖与启动）"
  exit 0
}

if ($null -eq $pkg) {
  Write-Host '[错误] 没有找到 Node.js（pnpm / npm）。' -ForegroundColor Red
  Write-Host '请先安装 Node.js 20.19+ 或 22.12+：https://nodejs.org'
  exit 1
}

Write-Host "使用包管理器：$pkg" -ForegroundColor Cyan

if (-not (Test-Path (Join-Path $webRoot 'node_modules'))) {
  Write-Host '[1/2] 首次运行：安装前端依赖...' -ForegroundColor Yellow
  & "$pkg.cmd" install
  if ($LASTEXITCODE -ne 0) {
    Write-Host '[错误] 依赖安装失败，请看上面的报错。' -ForegroundColor Red
    exit 1
  }
}

Write-Host '[2/2] 启动前端开发服务器...' -ForegroundColor Yellow
Write-Host '浏览器打开：http://localhost:5173'
Write-Host ''
$repoRoot = (Resolve-Path (Join-Path $webRoot '..\..')).Path
Write-Host '提示：前端需要连接本地桥接。请另开一个终端执行：' -ForegroundColor DarkGray
Write-Host "  cd $repoRoot" -ForegroundColor DarkGray
Write-Host '  python code\web\bridge\web_bridge.py --port 8787 --no-token' -ForegroundColor DarkGray
Write-Host ''

& "$pkg.cmd" run dev
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Host ''
  Write-Host '[提示] 启动失败。如果上面的错误是 "Port 5173 is already in use"，' -ForegroundColor Yellow
  Write-Host '       说明已经有一个 dev server 在运行：先直接打开 http://localhost:5173；' -ForegroundColor Yellow
  Write-Host '       想用当前终端重新启动，就先结束占用进程再执行 .\dev.cmd：' -ForegroundColor Yellow
  Write-Host '       Get-NetTCPConnection -LocalPort 5173 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }' -ForegroundColor DarkGray
}
exit $code
