# tools/install_mssql.ps1
#
# Installs everything the Hub needs to run on Microsoft SQL Server, on this
# Windows machine, in one pass:
#
#   1. SQL Server 2022 Express      (skipped if an instance already exists)
#   2. ODBC Driver 18 for SQL Server (what pyodbc connects through)
#   3. Mixed-mode authentication + TCP enabled on the instance
#   4. The `ajihub` database and an `ajihub` SQL login that owns it
#   5. mssql.json beside manage.py, with "enabled": false — ready for
#      `python manage.py migrate_to_mssql`
#
# Run from an ADMINISTRATOR PowerShell, from anywhere:
#
#   powershell -ExecutionPolicy Bypass -File tools\install_mssql.ps1
#
# It will ask for the two passwords it needs. To run unattended:
#
#   ... -File tools\install_mssql.ps1 -SaPassword "..." -DbPassword "..."
#
# Pointing the Hub at an EXISTING SQL Server elsewhere instead? Skip this
# script: create the database and login there (step 4's SQL is printed at the
# end for that purpose), then write mssql.json by hand — the format is
# documented in aji_hub/settings.py.

param(
    [string]$SaPassword = '',
    [string]$DbPassword = '',
    [string]$DbName     = 'ajihub',
    [string]$DbUser     = 'ajihub',
    [string]$Instance   = 'SQLEXPRESS'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is 10x faster silent

function Say($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Good($msg) { Write-Host "    $msg" -ForegroundColor Green }

# ── 0. preconditions ─────────────────────────────────────────────────────────

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this from an Administrator PowerShell (installing a service needs it).'
}

# The project root is one level above this script, wherever it was run from.
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $ProjectRoot 'manage.py'))) {
    throw "Could not find manage.py above $PSScriptRoot - keep this script in the project's tools\ folder."
}

if (-not $DbPassword) {
    $sec = Read-Host -AsSecureString "Choose a password for the Hub's database login '$DbUser'"
    $DbPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                  [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}
if ($DbPassword.Length -lt 10) { throw 'Database password must be at least 10 characters (SQL Server policy).' }

$Downloads = Join-Path $env:TEMP 'ajihub-mssql-setup'
New-Item -ItemType Directory -Force -Path $Downloads | Out-Null

# ── 1. SQL Server 2022 Express ───────────────────────────────────────────────

$svcName = "MSSQL`$$Instance"
$existing = Get-Service -Name $svcName -ErrorAction SilentlyContinue

if ($existing) {
    Say "SQL Server instance $Instance already installed - skipping installation."
} else {
    if (-not $SaPassword) {
        $sec = Read-Host -AsSecureString "Choose the SQL Server 'sa' administrator password"
        $SaPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                      [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    }
    if ($SaPassword.Length -lt 10) { throw "'sa' password must be at least 10 characters." }

    Say 'Downloading SQL Server 2022 Express bootstrapper (~6 MB)...'
    $ssei = Join-Path $Downloads 'SQL2022-SSEI-Expr.exe'
    Invoke-WebRequest 'https://go.microsoft.com/fwlink/p/?linkid=2216019' -OutFile $ssei

    Say 'Downloading the full installation media (~700 MB, a few minutes)...'
    $media = Join-Path $Downloads 'media'
    New-Item -ItemType Directory -Force -Path $media | Out-Null
    & $ssei /Action=Download /MediaPath=$media /MediaType=Core /Quiet | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Media download failed (exit $LASTEXITCODE)." }

    Say 'Extracting...'
    $core = Get-ChildItem $media -Filter 'SQLEXPR*.exe' | Select-Object -First 1
    & $core.FullName /q /x:"$media\extracted" | Out-Null

    Say "Installing SQL Server 2022 Express as instance $Instance (5-10 minutes)..."
    # Mixed mode from the start (SECURITYMODE=SQL), TCP on, current user as
    # Windows admin of the instance. /IACCEPTSQLSERVERLICENSETERMS is what
    # /QS requires to run with no prompts.
    & "$media\extracted\setup.exe" /Q /ACTION=Install /IACCEPTSQLSERVERLICENSETERMS `
        /FEATURES=SQLEngine /INSTANCENAME=$Instance `
        /SECURITYMODE=SQL /SAPWD="$SaPassword" `
        /SQLSYSADMINACCOUNTS="$env:USERDOMAIN\$env:USERNAME" `
        /TCPENABLED=1 /NPENABLED=0 `
        /SQLSVCSTARTUPTYPE=Automatic /BROWSERSVCSTARTUPTYPE=Automatic | Out-Null
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 3010) {
        throw "SQL Server setup failed (exit $LASTEXITCODE). Log: C:\Program Files\Microsoft SQL Server\160\Setup Bootstrap\Log\Summary.txt"
    }
    Good 'SQL Server 2022 Express installed.'
}

Start-Service $svcName -ErrorAction SilentlyContinue
Say "Service $svcName is $((Get-Service $svcName).Status)."

# ── 2. ODBC Driver 18 ────────────────────────────────────────────────────────

$odbc = Get-ItemProperty 'HKLM:\SOFTWARE\ODBC\ODBCINST.INI\ODBC Driver 18 for SQL Server' -ErrorAction SilentlyContinue
if ($odbc) {
    Say 'ODBC Driver 18 for SQL Server already installed - skipping.'
} else {
    Say 'Downloading ODBC Driver 18 for SQL Server...'
    $msi = Join-Path $Downloads 'msodbcsql18.msi'
    Invoke-WebRequest 'https://go.microsoft.com/fwlink/?linkid=2249006' -OutFile $msi   # x64
    Say 'Installing ODBC driver...'
    Start-Process msiexec.exe -Wait -ArgumentList "/i `"$msi`" /qn IACCEPTMSODBCSQLLICENSETERMS=YES"
    Good 'ODBC Driver 18 installed.'
}

# ── 3. mixed auth + TCP, in case the instance predates this script ──────────

Say 'Ensuring mixed-mode authentication and TCP are enabled...'
$reg = Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server' |
       Where-Object { $_.PSChildName -match "^MSSQL\d+\.$Instance$" } | Select-Object -First 1
if ($reg) {
    Set-ItemProperty "$($reg.PSPath)\MSSQLServer" -Name LoginMode -Value 2           # 2 = mixed
    $tcp = "$($reg.PSPath)\MSSQLServer\SuperSocketNetLib\Tcp"
    if (Test-Path $tcp) { Set-ItemProperty $tcp -Name Enabled -Value 1 }
    Restart-Service $svcName -Force
    Good 'Authentication mode and TCP confirmed; service restarted.'
}

# ── 4. the database and the login ────────────────────────────────────────────

# Escape single quotes for the SQL string literals ('' inside '...').
$pwSql = $DbPassword.Replace("'", "''")
$CreateSql = @"
IF DB_ID(N'$DbName') IS NULL CREATE DATABASE [$DbName];
IF NOT EXISTS (SELECT 1 FROM sys.sql_logins WHERE name = N'$DbUser')
    CREATE LOGIN [$DbUser] WITH PASSWORD = N'$pwSql', DEFAULT_DATABASE = [$DbName], CHECK_EXPIRATION = OFF;
ELSE
    ALTER LOGIN [$DbUser] WITH PASSWORD = N'$pwSql';
"@
$OwnSql = @"
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'$DbUser')
    CREATE USER [$DbUser] FOR LOGIN [$DbUser];
ALTER ROLE db_owner ADD MEMBER [$DbUser];
"@

Say "Creating database [$DbName] and login [$DbUser]..."
# .NET SqlClient over Windows authentication: nothing else to install, and the
# person running an admin PowerShell is a sysadmin of the instance (step 1).
Add-Type -AssemblyName System.Data
function Invoke-Sql([string]$database, [string]$sql) {
    $cn = New-Object System.Data.SqlClient.SqlConnection(
        "Server=localhost\$Instance;Database=$database;Integrated Security=True;TrustServerCertificate=True")
    $cn.Open()
    try {
        foreach ($stmt in ($sql -split "(?m)^GO\s*$")) {
            if ($stmt.Trim()) {
                $cmd = $cn.CreateCommand(); $cmd.CommandText = $stmt; [void]$cmd.ExecuteNonQuery()
            }
        }
    } finally { $cn.Close() }
}
Invoke-Sql 'master' $CreateSql
Invoke-Sql $DbName  $OwnSql
Good "Database [$DbName] ready; [$DbUser] is its owner."

# ── 5. mssql.json ────────────────────────────────────────────────────────────

$configPath = Join-Path $ProjectRoot 'mssql.json'
$config = [ordered]@{
    enabled           = $false
    host              = "localhost\$Instance"
    port              = ''
    database          = $DbName
    user              = $DbUser
    password          = $DbPassword
    driver            = 'ODBC Driver 18 for SQL Server'
    trust_certificate = $true
}
$config | ConvertTo-Json | Set-Content -Path $configPath -Encoding UTF8
Good "Wrote $configPath (enabled: false - SQLite is still in charge)."

Write-Host ''
Write-Host '════════════════════════════════════════════════════════════════' -ForegroundColor Yellow
Write-Host ' SQL Server is ready. To move the Hub onto it:'                    -ForegroundColor Yellow
Write-Host ''
Write-Host '   1.  pip install -r requirements.txt'
Write-Host '   2.  python manage.py migrate_to_mssql'
Write-Host '   3.  edit mssql.json: "enabled": true'
Write-Host '   4.  restart the Hub (Waitress)'
Write-Host ''
Write-Host ' The old db.sqlite3 is untouched and stays available as the'
Write-Host ' "sqlite" alias - switching back is flipping enabled to false.'
Write-Host '════════════════════════════════════════════════════════════════' -ForegroundColor Yellow
