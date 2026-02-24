# Sync buildings table from local PostgreSQL to Railway.
# Uses pg_dump custom format for efficiency.
param(
    [string]$LocalDb = "postgresql://postgres:postgres@localhost:5432/hpd_leads",
    [string]$RemoteUrl = $env:RAILWAY_DATABASE_URL
)

if (-not $RemoteUrl) {
    Write-Error "Set RAILWAY_DATABASE_URL environment variable"
    exit 1
}

$PGDUMP = "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe"
$PGRESTORE = "C:\Program Files\PostgreSQL\16\bin\pg_restore.exe"
$TMPFILE = "$env:TEMP\buildings_dump.dump"

Write-Host "$(Get-Date -Format 'HH:mm:ss') Dumping buildings table from local DB..."
$env:PGPASSWORD = "postgres"
& $PGDUMP --no-owner --no-acl -Fc -t buildings "$LocalDb" -f $TMPFILE 2>&1
if ($LASTEXITCODE -ne 0) { Write-Error "pg_dump failed"; exit 1 }

$dumpSize = (Get-Item $TMPFILE).Length / 1MB
Write-Host "$(Get-Date -Format 'HH:mm:ss') Dump size: $([math]::Round($dumpSize, 1)) MB"

Write-Host "$(Get-Date -Format 'HH:mm:ss') Truncating remote buildings table..."
$env:PGPASSWORD = ""
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" $RemoteUrl -c "TRUNCATE TABLE buildings CASCADE;" 2>&1
if ($LASTEXITCODE -ne 0) { Write-Warning "TRUNCATE may have failed, continuing..." }

Write-Host "$(Get-Date -Format 'HH:mm:ss') Restoring to Railway..."
& $PGRESTORE --no-owner --no-acl -d $RemoteUrl $TMPFILE 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "$(Get-Date -Format 'HH:mm:ss') Sync complete!"
} else {
    Write-Warning "pg_restore reported errors (may be non-fatal)"
}

# Verify
$result = & "C:\Program Files\PostgreSQL\16\bin\psql.exe" $RemoteUrl -t -c "SELECT COUNT(*) total, COUNT(unit_count) with_units, ROUND(AVG(churn_score),1) avg_score, COUNT(*) FILTER (WHERE churn_category='warm') warm, COUNT(*) FILTER (WHERE churn_category='hot') hot FROM buildings;" 2>&1
Write-Host "Remote buildings: $result"

Remove-Item $TMPFILE -ErrorAction SilentlyContinue
