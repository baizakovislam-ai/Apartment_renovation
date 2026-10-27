# Запуск проекта одной командой (вызывается из start.bat / start_lan.bat).
#   -Lan        открыть доступ другим компьютерам в той же Wi-Fi/локальной сети
#   -Port N     порт сервера (по умолчанию 8001)
#   -CheckOnly  всё подготовить и проверить, но сервер не запускать
param([switch]$Lan, [int]$Port = 8001, [switch]$CheckOnly)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$python = Join-Path (Get-Location) "venv\Scripts\python.exe"

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`nОШИБКА: $text" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $python)) {
    Step "Создаю виртуальное окружение venv"
    py -m venv venv
    if ($LASTEXITCODE) { Fail "Не удалось создать venv. Установите Python 3.12+ с python.org." }
}
if (-not (Test-Path ".env")) { Fail "Нет файла .env. Скопируйте .env.example в .env и заполните POSTGRES_* (см. docs/POSTGRESQL.md)." }

Step "Проверяю зависимости"
& $python -m pip install -q --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE) { Fail "Не удалось установить зависимости из requirements.txt." }

Step "Проверяю базу данных и применяю миграции"
& $python manage.py migrate --noinput
if ($LASTEXITCODE) { Fail "Нет подключения к PostgreSQL. Проверьте, что служба PostgreSQL запущена и данные в .env верны." }

$bind = "127.0.0.1"
$url = "http://127.0.0.1:$Port/"
if ($Lan) {
    $ip = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" -and $_.PrefixOrigin -in "Dhcp", "Manual" } |
        Sort-Object { $_.InterfaceAlias -notmatch "Wi-Fi|Беспровод|Ethernet" } |
        Select-Object -First 1 -ExpandProperty IPAddress
    if (-not $ip) { Fail "Не найден адрес в локальной сети. Подключитесь к Wi-Fi." }
    # Только для этого запуска: разрешаем адрес в Django, .env не меняется.
    $env:ALLOWED_HOSTS = $ip
    $env:CSRF_TRUSTED_ORIGINS = "http://${ip}:$Port"
    $bind = "0.0.0.0"
    $url = "http://${ip}:$Port/"
}

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "`nПорт $Port уже занят — похоже, сервер уже запущен. Открываю $url" -ForegroundColor Yellow
    Write-Host "Если это старый сервер, закройте его окно (Ctrl+C) и запустите скрипт снова."
    if (-not $CheckOnly) { Start-Process $url }
    exit 0
}

Write-Host "`nГотово. Адрес проекта: $url" -ForegroundColor Green
if ($Lan) { Write-Host "Отправьте этот адрес участникам из той же сети. Если Windows спросит про брандмауэр — разрешите для частных сетей." }
Write-Host "Демо-аккаунты: customer@example.com / foreman@example.com / executor@example.com, пароль RepairDemo2026!"
if ($CheckOnly) { Write-Host "(режим проверки — сервер не запущен)"; exit 0 }

Write-Host "Остановить сервер: Ctrl+C или закройте это окно.`n"
Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoProfile", "-Command", "Start-Sleep 3; Start-Process '$url'"
& $python manage.py runserver "${bind}:$Port"
