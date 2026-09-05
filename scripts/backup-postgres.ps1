param(
    [string]$OutputDirectory = ".\backups"
)

$ErrorActionPreference = "Stop"
$resolvedDirectory = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\$OutputDirectory"))
New-Item -ItemType Directory -Path $resolvedDirectory -Force | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outputFile = Join-Path $resolvedDirectory "intralink-$timestamp.dump"
$containerFile = "/tmp/intralink-$timestamp.dump"

docker compose exec -T postgres pg_dump -U postgres -d intraservice -Fc -f $containerFile
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }

docker compose exec -T postgres pg_restore -l $containerFile | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Backup verification failed" }

docker compose cp "postgres:$containerFile" $outputFile
if ($LASTEXITCODE -ne 0) { throw "Could not copy backup from PostgreSQL container" }

docker compose exec -T postgres rm -f $containerFile
Write-Output $outputFile
