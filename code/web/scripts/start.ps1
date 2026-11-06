#requires -version 5
# ============================================================
#  MAPAS 一键启动：桥接 + 前端 + 浏览器
#  由 start.cmd 以 -ExecutionPolicy Bypass 调用（不改系统设置）
#
#  参数：
#    multi      桥接以 multi_fixed 模式启动（默认 single）
#    check      只检查环境（Python / Node / 端口占用），不启动任何进程
#    nobrowser  启动后不自动打开浏览器
# ============================================================
param(
  # 由 start.cmd 透传的位置参数（例如：multi / check / nobrowser）
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$Rest
)

$flags = @()
if ($null -ne $Rest) {
  foreach ($item in $Rest) { $flags += $item.ToLower() }
}
$Multi = $flags -contains 'multi'
$Check = $flags -contains 'check'
$NoBrowser = $flags -contains 'nobrowser'

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot\..
$webRoot = (Get-Location).Path
$repoRoot = (Resolve-Path (Join-Path $webRoot '..\..')).Path
$bridgePath = Join-Path $webRoot 'bridge\web_bridge.py'
$mode = if ($Multi) { 'multi_fixed' } else { 'single' }

function Write-Step($text) { Write-Host $text -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host $text -ForegroundColor Green }
function Write-Warn($text) { Write-Host $text -ForegroundColor Yellow }

function Test-Port([int]$port) {
  return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

function Wait-Url([string]$url, [int]$seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $deadline) {
    try {
      $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
      if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { return $true }
    } catch {
      # 还没起来，继续等
    }
    Start-Sleep -Milliseconds 800
  }
  return $false
}

# ---------- 环境检测 ----------
$pkg = $null
if (Get-Command pnpm.cmd -ErrorAction SilentlyContinue) { $pkg = 'pnpm' }
elseif (Get-Command npm.cmd -ErrorAction SilentlyContinue) { $pkg = 'npm' }

$pyExe = $null
foreach ($candidate in @('python', 'python3', 'py')) {
  $command = Get-Command $candidate -ErrorAction SilentlyContinue
  if ($null -ne $command) { $pyExe = $command.Source; break }
}

Write-Step '=== MAPAS 环境检测 ==='
Write-Host ('Python   : ' + $(if ($pyExe) { $pyExe } else { '未找到（无法启动桥接）' }))
Write-Host ('Node/pnpm: ' + $(if ($pkg) { $pkg } else { '未找到（无法启动前端）' }))
Write-Host ('数据根目录: ' + $repoRoot)
Write-Host ('桥接模式  : ' + $mode)
Write-Host ('8787 端口 : ' + $(if (Test-Port 8787) { '已有服务在运行' } else { '空闲' }))
Write-Host ('5173 端口 : ' + $(if (Test-Port 5173) { '已有服务在运行' } else { '空闲' }))

if ($Check) {
  if ($null -eq $pyExe) { Write-Warn '[检查] 没有找到 Python，请先安装 Python 3.10+'; exit 1 }
  if ($null -eq $pkg) { Write-Warn '[检查] 没有找到 Node.js（pnpm/npm），请先安装 Node 20.19+ / 22.12+'; exit 1 }
  Write-Ok '[检查通过] 环境齐全，可以双击 start.cmd 启动。'
  exit 0
}

# ---------- 1) 桥接 ----------
if (Test-Port 8787) {
  Write-Warn '[跳过] 8787 上已有桥接在运行（不会覆盖它）'
} else {
  if ($null -eq $pyExe) {
    Write-Warn '[错误] 没有找到 Python，无法启动桥接。请先安装 Python 3.10+。'
    exit 1
  }
  Write-Step '[1/3] 启动桥接（新窗口，免口令）...'
  Start-Process -FilePath $pyExe `
    -ArgumentList @("`"$bridgePath`"", '--execution-mode', $mode, '--port', '8787', '--no-token') `
    -WorkingDirectory $repoRoot -WindowStyle Normal
  if (Wait-Url 'http://127.0.0.1:8787/api/meta' 90) {
    Write-Ok '[OK] 桥接已就绪'
  } else {
    Write-Warn '[警告] 桥接 90 秒内未就绪，请看新打开的窗口里的报错。'
  }
}

# ---------- 2) 前端 ----------
if (Test-Port 5173) {
  Write-Warn '[跳过] 5173 上已有前端在运行（不会重复启动）'
} else {
  if ($null -eq $pkg) {
    Write-Warn '[错误] 没有找到 Node.js（pnpm/npm），无法启动前端。请先安装 Node 20.19+ / 22.12+。'
    exit 1
  }
  if (-not (Test-Path (Join-Path $webRoot 'node_modules'))) {
    Write-Step '[2/3] 首次运行：安装前端依赖（可能需要 1-2 分钟）...'
    & "$pkg.cmd" install
    if ($LASTEXITCODE -ne 0) {
      Write-Warn '[错误] 依赖安装失败，请看上面的报错。'
      exit 1
    }
  }
  Write-Step '[2/3] 启动前端开发服务器（新窗口）...'
  Start-Process -FilePath 'cmd.exe' `
    -ArgumentList @('/k', "`"$pkg.cmd`" run dev") `
    -WorkingDirectory $webRoot -WindowStyle Normal
  if (Wait-Url 'http://localhost:5173/' 120) {
    Write-Ok '[OK] 前端已就绪'
  } else {
    Write-Warn '[警告] 前端 120 秒内未就绪，请看新打开的窗口里的报错。'
  }
}

# ---------- 3) 打开浏览器 ----------
Write-Step '[3/3] 打开浏览器：http://localhost:5173'
if (-not $NoBrowser) {
  Start-Process 'http://localhost:5173'
}

Write-Host ''
Write-Ok '=== 启动完成 ==='
Write-Host '  - 桥接窗口、前端窗口不要关；关掉它们 = 停止服务'
Write-Host '  - 页面顶栏应显示「口令：本机免校验」'
Write-Host '  - 首次使用在顶栏「API Key」填 DeepSeek 密钥（填一次即可）'
