# Fix unit_count and building_type for all buildings using PLUTO data.
# Root cause: PLUTO BBLs returned as "1000050010.00000000" but DB stores "1000050010"
# This script fetches PLUTO, normalizes BBLs, and updates buildings via psql.

param(
    [string]$DbHost = "localhost",
    [string]$DbUser = "postgres", 
    [string]$DbName = "hpd_leads",
    [string]$DbPassword = "postgres",
    [string]$AppToken = $env:NYC_OPEN_DATA_APP_TOKEN
)

$env:PGPASSWORD = $DbPassword
$PSQL = "C:\Program Files\PostgreSQL\16\bin\psql.exe"
$PLUTO_URL = "https://data.cityofnewyork.us/resource/64uk-42ks.json"
$BATCH = 5000
$tmpSqlFile = [System.IO.Path]::GetTempFileName() + ".sql"

Write-Host "$(Get-Date -Format 'HH:mm:ss') Fetching PLUTO data from NYC Open Data..."

$allRecords = @()
$offset = 0

do {
    $params = "`$select=bbl,unitsres,bldgclass,yearbuilt,assesstot,cd,council,ct2010&`$limit=$BATCH&`$offset=$offset"
    $url = "${PLUTO_URL}?${params}"
    if ($AppToken) { $url += "&`$app_token=$AppToken" }
    
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 60
        $chunk = $resp.Content | ConvertFrom-Json
        $allRecords += $chunk
        $offset += $chunk.Count
        if ($offset % 50000 -eq 0) {
            Write-Host "$(Get-Date -Format 'HH:mm:ss')   Fetched $($offset.ToString('N0')) PLUTO records..."
        }
    } catch {
        Write-Warning "Fetch failed at offset $offset: $($_.Exception.Message)"
        break
    }
} while ($chunk.Count -eq $BATCH)

Write-Host "$(Get-Date -Format 'HH:mm:ss') Total PLUTO records: $($allRecords.Count.ToString('N0'))"

# Normalize BBL: "1000050010.00000000" -> "1000050010"
function Normalize-BBL($bbl) {
    try {
        return [string][long][double]$bbl
    } catch {
        return $bbl.Trim()
    }
}

function Get-BuildingType($code) {
    if (-not $code) { return "NULL" }
    $c = $code.ToUpper()
    if ($c.StartsWith("D")) { return "'elevator_apartment'" }
    if ($c.StartsWith("C")) { return "'walkup_apartment'" }
    if ($c.StartsWith("A") -or $c.StartsWith("B")) { return "'residential_small'" }
    if ($c.StartsWith("R")) { return "'condo'" }
    if ($c.StartsWith("S")) { return "'mixed_use'" }
    if ($c.StartsWith("E") -or $c.StartsWith("F") -or $c.StartsWith("G")) { return "'commercial'" }
    return "'other'"
}

# Build PLUTO lookup dict
$plutoLookup = @{}
foreach ($p in $allRecords) {
    if ($p.bbl) {
        $normBbl = Normalize-BBL $p.bbl
        if ($normBbl) { $plutoLookup[$normBbl] = $p }
    }
}
Write-Host "$(Get-Date -Format 'HH:mm:ss') PLUTO lookup: $($plutoLookup.Count.ToString('N0')) unique BBLs"

# Get all BBLs from database
Write-Host "$(Get-Date -Format 'HH:mm:ss') Fetching BBLs from database..."
$bbls = & $PSQL -h $DbHost -U $DbUser -d $DbName -t -c "SELECT bbl FROM buildings ORDER BY bbl" 2>&1
$bbls = $bbls | Where-Object { $_ -match '\S' } | ForEach-Object { $_.Trim() }
Write-Host "$(Get-Date -Format 'HH:mm:ss') Buildings in DB: $($bbls.Count.ToString('N0'))"

# Build SQL update file
$matched = 0
$unmatched = 0
$sqlLines = @("BEGIN;")
$batchCount = 0

foreach ($bbl in $bbls) {
    $p = $plutoLookup[$bbl]
    if ($p) {
        $matched++
        
        # units
        $units = if ($p.unitsres) { try { [long][double]$p.unitsres } catch { "NULL" } } else { "NULL" }
        $yearBuilt = if ($p.yearbuilt) { try { [long][double]$p.yearbuilt } catch { "NULL" } } else { "NULL" }
        $assessVal = if ($p.assesstot) { try { [double]$p.assesstot } catch { "NULL" } } else { "NULL" }
        $bldgClass = if ($p.bldgclass) { "'" + $p.bldgclass.Replace("'","''") + "'" } else { "NULL" }
        $bldgType = Get-BuildingType $p.bldgclass
        $council = if ($p.council) { "'" + $p.council.Replace("'","''") + "'" } else { "NULL" }
        $cd = if ($p.cd) { "'" + $p.cd.Replace("'","''") + "'" } else { "NULL" }
        $census = if ($p.ct2010) { "'" + $p.ct2010.Replace("'","''") + "'" } else { "NULL" }
        
        $sqlLines += "UPDATE buildings SET unit_count=$units, building_class=$bldgClass, building_type=$bldgType, year_built=$yearBuilt, assessed_value=$assessVal, council_district=$council, community_board=$cd, census_tract=$census, updated_at=now() WHERE bbl='$bbl';"
        $batchCount++
        
        if ($batchCount % 10000 -eq 0) {
            $sqlLines += "COMMIT; BEGIN;"
            Write-Host "$(Get-Date -Format 'HH:mm:ss')   Prepared $($matched.ToString('N0')) updates..."
        }
    } else {
        $unmatched++
    }
}

$sqlLines += "COMMIT;"
$matchRate = if ($bbls.Count -gt 0) { [math]::Round(100.0 * $matched / $bbls.Count, 1) } else { 0 }
Write-Host "$(Get-Date -Format 'HH:mm:ss') Matched: $($matched.ToString('N0')), Unmatched: $($unmatched.ToString('N0')), Rate: $matchRate%"

# Write SQL to temp file
$sqlLines | Out-File -FilePath $tmpSqlFile -Encoding UTF8
Write-Host "$(Get-Date -Format 'HH:mm:ss') Executing $($matched.ToString('N0')) SQL updates..."

# Execute via psql
& $PSQL -h $DbHost -U $DbUser -d $DbName -f $tmpSqlFile -v ON_ERROR_STOP=0 2>&1 | Where-Object { $_ -notmatch "^UPDATE \d+$" } | Select-Object -Last 10

Remove-Item $tmpSqlFile -ErrorAction SilentlyContinue
Write-Host "$(Get-Date -Format 'HH:mm:ss') Done!"

# Verify
$result = & $PSQL -h $DbHost -U $DbUser -d $DbName -t -c "SELECT COUNT(*) total, COUNT(unit_count) with_units, COUNT(building_type) with_type, ROUND(AVG(unit_count),1) avg_units FROM buildings;" 2>&1
Write-Host "Verification: $result"
