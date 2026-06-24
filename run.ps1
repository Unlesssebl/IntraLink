# run.ps1

# Функция поиска свободного порта с использованием TcpListener
function Get-FreePort ([int]$startPort) {
    $port = $startPort
    while ($true) {
        try {
            $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $port)
            $listener.Start()
            $listener.Stop()
            return $port
        } catch {
            $port++
        }
    }
}

Write-Host "--- Проверка доступности портов на хосте ---" -ForegroundColor Cyan

$postgresPort = Get-FreePort 5432
$redisPort = Get-FreePort 6379
$litellmPort = Get-FreePort 4000
$coreApiPort = Get-FreePort 8000

# Вывод статуса портов
function Show-PortStatus($name, $default, $selected) {
    if ($default -eq $selected) {
        Write-Host "$($name): порт $default свободен" -ForegroundColor Green
    } else {
        Write-Host "$($name): порт $default ЗАНЯТ! Выбран альтернативный: $selected" -ForegroundColor Yellow
    }
}

Show-PortStatus "PostgreSQL" 5432 $postgresPort
Show-PortStatus "Redis" 6379 $redisPort
Show-PortStatus "LiteLLM" 4000 $litellmPort
Show-PortStatus "Core API" 8000 $coreApiPort

# Формируем содержимое для .env
$envContent = @"
# Автоматически сгенерированные порты для предотвращения конфликтов
POSTGRES_PORT=$postgresPort
REDIS_PORT=$redisPort
LITELLM_PORT=$litellmPort
CORE_API_PORT=$coreApiPort
"@

# Записываем в файл .env
Set-Content -Path .env -Value $envContent -Force
Write-Host "Файл .env обновлен." -ForegroundColor Green

Write-Host "`nЗапуск docker compose..." -ForegroundColor Cyan

# Пробрасываем аргументы, переданные в скрипт, в команду docker compose
if ($args.Count -gt 0) {
    docker compose up $args
} else {
    docker compose up
}
