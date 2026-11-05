#requires -version 5
# ============================================================
#  MAPAS 前端构建脚本（由 build.cmd 以 Bypass 执行策略调用）
#  等价于：pnpm typecheck && pnpm build
# ============================================================
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot\..
$webRoot = (Get-Location).Path

function Get-PackageManager {
  if (Get-Command pnpm.cmd -ErrorAction SilentlyContinue) { return 'pnpm' }
  if (Get-Command npm.cmd -ErrorAction SilentlyContinue) { return 'npm' }
  return $null
}

$pkg = Get-PackageManager
if ($null -eq $pkg) {
  Write-Host '[错误] 没有找到 Node.js（pnpm / npm）。' -ForegroundColor Red
  Write-Host '请先安装 Node.js 20.19+ 或 22.12+：https://nodejs.org'
  exit 1
}

if (-not (Test-Path (Join-Path $webRoot 'node_modules'))) {
  Write-Host '[1/3] 首次运行：安装前端依赖...' -ForegroundColor Yellow
  & "$pkg.cmd" install
  if ($LASTEXITCODE -ne 0) { exit 1 }
}

Write-Host '[2/3] 类型检查...' -ForegroundColor Yellow
& "$pkg.cmd" run typecheck
if ($LASTEXITCODE -ne 0) {
  Write-Host '[错误] 类型检查未通过。' -ForegroundColor Red
  exit 1
}

Write-Host '[3/3] 生产构建...' -ForegroundColor Yellow
& "$pkg.cmd" run build
if ($LASTEXITCODE -ne 0) {
  Write-Host '[错误] 构建失败。' -ForegroundColor Red
  exit 1
}

Write-Host ''
Write-Host '[OK] 构建完成：code\web\dist' -ForegroundColor Green
