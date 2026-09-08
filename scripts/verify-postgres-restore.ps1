param(
    [Parameter(Mandatory = $true)]
    [string]$DumpPath,
    [string]$ScratchDatabase = "intralink_restore_verify"
)

$ErrorActionPreference = "Stop"

if ($ScratchDatabase -notmatch '^intralink_restore_[a-z0-9_]+$') {
    throw "ScratchDatabase must start with 'intralink_restore_' and contain only lowercase letters, digits, and underscores."
}

$resolvedDump = (Resolve-Path -LiteralPath $DumpPath).Path
$containerDump = "/tmp/restore-verify-$([Guid]::NewGuid().ToString('N')).dump"

try {
    docker compose cp $resolvedDump "postgres:$containerDump"
    if ($LASTEXITCODE -ne 0) { throw "Could not copy dump into PostgreSQL container" }

    docker compose exec -T postgres dropdb -U postgres --if-exists $ScratchDatabase
    if ($LASTEXITCODE -ne 0) { throw "Could not reset scratch database" }

    docker compose exec -T postgres createdb -U postgres $ScratchDatabase
    if ($LASTEXITCODE -ne 0) { throw "Could not create scratch database" }

    docker compose exec -T postgres pg_restore -U postgres --exit-on-error --no-owner -d $ScratchDatabase $containerDump
    if ($LASTEXITCODE -ne 0) { throw "Restore verification failed" }

    $revision = docker compose exec -T postgres psql -U postgres -d $ScratchDatabase -Atc "SELECT version_num FROM alembic_version"
    if ($LASTEXITCODE -ne 0 -or -not $revision.Trim()) {
        throw "Restored database has no Alembic revision"
    }

    Write-Output "Restore verified in scratch database '$ScratchDatabase' (revision $($revision.Trim()))."
}
finally {
    docker compose exec -T postgres dropdb -U postgres --if-exists $ScratchDatabase 2>$null
    docker compose exec -T postgres rm -f $containerDump 2>$null
}
