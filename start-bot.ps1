# FastChu 启动脚本（Windows）
#
# 作用：把依赖目录 .pkgs 加到 PYTHONPATH、确保 .env 存在、选一个能用的 python，
# 然后启动机器人。省掉每次手工设环境变量的麻烦。
#
# 用法：
#   .\start-bot.ps1            启动机器人
#   .\start-bot.ps1 -Check     只做环境自检，不启动
#   .\start-bot.ps1 -Port 8081 换端口（临时覆盖 .env）

[CmdletBinding()]
param(
    [switch]$Check,
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 控制台按 UTF-8 输出，中文日志重定向到文件时不会变乱码
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
    $OutputEncoding = [System.Text.UTF8Encoding]::new()
} catch { }

function Write-Step($text) { Write-Host "  $text" }
function Write-OK($text)   { Write-Host "  [OK] $text"   -ForegroundColor Green }
function Write-Warn($text) { Write-Host "  [!]  $text"   -ForegroundColor Yellow }
function Write-Err($text)  { Write-Host "  [X]  $text"   -ForegroundColor Red }

Write-Host ""
Write-Host "FastChu 启动器" -ForegroundColor Cyan
Write-Host "  项目目录：$root"
Write-Host ""

# ---------------------------------------------------------------------------
# 1) 依赖目录
# ---------------------------------------------------------------------------
$deps = Join-Path $root '.pkgs'
if (-not (Test-Path $deps)) {
    Write-Err "找不到依赖目录 .pkgs"
    Write-Step "请先安装依赖："
    Write-Step "  python -m pip install --target .pkgs -r requirements.txt"
    Write-Step "（国内网络慢可以加：-i https://pypi.tuna.tsinghua.edu.cn/simple）"
    exit 1
}
$env:PYTHONPATH = $deps

# ---------------------------------------------------------------------------
# 2) 挑一个能 import 依赖的 python
# ---------------------------------------------------------------------------
$candidates = @()
$venv = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $venv) { $candidates += $venv }
$onPath = Get-Command python -ErrorAction SilentlyContinue
if ($onPath) { $candidates += $onPath.Source }
$candidates += "C:\Users\$env:USERNAME\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"

$python = $null
foreach ($candidate in $candidates) {
    if (-not (Test-Path $candidate)) { continue }
    & $candidate -c "import nonebot, fastapi, uvicorn, httpx, pydantic_settings" 2>$null
    if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
}

if (-not $python) {
    Write-Err "找不到可用的 python 解释器（或依赖不完整）"
    Write-Step "试过：$($candidates -join ' / ')"
    Write-Step "请运行：python -m pip install --target .pkgs -r requirements.txt"
    exit 1
}
$pyVersion = (& $python --version 2>&1)
Write-OK "python：$python（$pyVersion）"

# ---------------------------------------------------------------------------
# 3) .env
# ---------------------------------------------------------------------------
$envFile = Join-Path $root '.env'
$envExample = Join-Path $root '.env.example'
if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-OK "已从 .env.example 生成 .env（请按需修改 SUPERUSERS 等）"
    } else {
        Write-Warn "没有 .env 也没有 .env.example，将使用默认配置"
    }
} else {
    Write-OK ".env 已存在"
}

# ---------------------------------------------------------------------------
# 4) 缓存数据（缺失时首次查询会自动抓，这里只提示）
# ---------------------------------------------------------------------------
$songCache = Join-Path $root 'data\cache\song_list.json'
$trophyCache = Join-Path $root 'data\cache\trophy_list.json'
$regionCache = Join-Path $root 'data\songs\songs.union.json'
if (Test-Path $songCache) { Write-OK "曲库缓存：$( [math]::Round((Get-Item $songCache).Length/1MB,1) ) MB" }
else { Write-Warn "没有曲库缓存，首次查询会自动抓取（约 2.5 MB）" }
if (Test-Path $trophyCache) { Write-OK "称号缓存：$( [math]::Round((Get-Item $trophyCache).Length/1MB,1) ) MB" }
else { Write-Warn "没有称号缓存，首次查询会自动抓取（约 3.8 MB）" }
if (Test-Path $regionCache) { Write-OK "国服/日服并集：已就绪（定数双标可用）" }
else { Write-Warn "没有国服/日服并集，定数双标不可用（跑 scripts\build_song_regions.py 生成）" }

# ---------------------------------------------------------------------------
# 5) 端口
# ---------------------------------------------------------------------------
$hostAddress = '127.0.0.1'
$portNumber = 8080
if (Test-Path $envFile) {
    $envText = Get-Content $envFile -Raw
    if ($envText -match '(?m)^\s*HOST\s*=\s*(\S+)') { $hostAddress = $Matches[1] }
    if ($envText -match '(?m)^\s*PORT\s*=\s*(\d+)') { $portNumber = [int]$Matches[1] }
}
if ($Port -gt 0) { $portNumber = $Port }

$busy = Get-NetTCPConnection -LocalPort $portNumber -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Err "端口 $portNumber 已被占用（PID $($busy[0].OwningProcess)）"
    Write-Step "先停掉它，或用 -Port 换一个端口"
    exit 1
}
Write-OK "监听地址：http://${hostAddress}:${portNumber}"

Write-Host ""
Write-Host "  OneBot v11 反向 WebSocket 地址：" -ForegroundColor Cyan
Write-Host "    ws://${hostAddress}:${portNumber}/onebot/v11/ws" -ForegroundColor White
Write-Host ""
Write-Host "  在 NapCat 里新增「WebSocket 客户端」，地址填上面这个。"
Write-Host "  连通性自检（无需 QQ）："
Write-Host "    python scripts\verify_onebot_ws.py"
Write-Host ""

if ($Check) {
    Write-Host "自检完成（-Check 模式，不启动机器人）。" -ForegroundColor Cyan
    exit 0
}

# ---------------------------------------------------------------------------
# 6) 启动
# ---------------------------------------------------------------------------
$env:PYTHONIOENCODING = 'utf-8'
$env:HOST = $hostAddress
$env:PORT = $portNumber

Write-Host "启动机器人…（Ctrl+C 停止）" -ForegroundColor Cyan
Write-Host ("-" * 60)
& $python bot.py
