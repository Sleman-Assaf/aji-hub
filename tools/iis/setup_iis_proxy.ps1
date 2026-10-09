# tools/iis/setup_iis_proxy.ps1  (v2 — one IIS site per application)
#
# Publishes every Django application as ITS OWN SITE in IIS — visible in the
# Sites tree of IIS Manager, individually startable, stoppable and
# inspectable — all sharing ports 80/443 and told apart by hostname (IIS
# name-based hosting with SNI). Each site holds one thing: a web.config that
# reverse-proxies everything to that app's private Waitress on 127.0.0.1.
#
# What one run does:
#
#   1. IIS + Windows Authentication features        (skipped if present)
#   2. URL Rewrite and ARR modules                  (downloaded + installed)
#   3. ARR proxy mode on, identity server variables unlocked
#   4. One site per app in $Apps, bound to its hostname on :80 (and :443
#      with SNI once make_gateway_cert.ps1 / issue_domain_cert.ps1 binds the
#      certificate), plus the fallback site "AJi Gateway" that answers
#      requests by IP or bare server name and forwards them to $Default
#   5. Authentication per site: WindowsSSO (domain-joined) or AppLogin
#      (workgroup: anonymous pass-through, apps show their own login)
#   6. A per-app web.config forwarding to its backend, stamped with the
#      AD-verified username (under SSO) and the shared proxy token
#   7. iis_sso.json beside manage.py, matching that token
#
# Run from an ADMINISTRATOR PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File tools\iis\setup_iis_proxy.ps1
#
# Re-running is safe and is HOW you change things: edit $Apps, run again.
# Sites are created if missing and their web.configs rewritten (with a fresh
# shared token, also rewritten into iis_sso.json).
# IMPORTANT: each Django app must bind Waitress to 127.0.0.1 — the gateway
# being the only way in is half of what makes the identity header
# trustworthy; the proxy token is the other half.

param(
    [int]   $HttpPort  = 80,
    [int]   $HttpsPort = 443,
    [string]$SitesRoot = 'C:\inetpub\aji',
    # WindowsSSO: IIS authenticates against AD, apps trust X-Remote-User.
    #   Requires this server to be JOINED TO THE DOMAIN.
    # AppLogin:   anonymous pass-through; each app shows its own login page
    #   (the Hub's page checks AD over LDAP directly — works from workgroup).
    # Auto:       WindowsSSO when domain-joined, AppLogin otherwise.
    [ValidateSet('Auto','WindowsSSO','AppLogin')]
    [string]$AuthMode  = 'Auto'
)

# ── the applications: each becomes an IIS site of its own ────────────────────
$Apps = @(
    @{ Name = 'AJi AI';          Host = 'ai.ajgroup.com.local';     Backend = 'http://127.0.0.1:8000' },
    @{ Name = 'AJi TenderRadar'; Host = 'radar.ajgroup.com.local';  Backend = 'http://127.0.0.1:8001' },
    @{ Name = 'AJi Hub';         Host = 'ajihub.ajgroup.com.local'; Backend = 'http://127.0.0.1:8002' }
)
# Requests that match no hostname (server IP, bare computer name) land on the
# fallback site and are forwarded here — the Hub is the sensible front door.
$FallbackName    = 'AJi Gateway'
$FallbackBackend = 'http://127.0.0.1:8002'

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function Say($m)  { Write-Host "==> $m" -ForegroundColor Cyan }
function Good($m) { Write-Host "    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this from an Administrator PowerShell.'
}
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $ProjectRoot 'manage.py'))) {
    throw "Could not find manage.py two levels above $PSScriptRoot - keep this script in tools\iis\."
}
$Downloads = Join-Path $env:TEMP 'aji-gateway-setup'
New-Item -ItemType Directory -Force -Path $Downloads | Out-Null
$appcmd = "$env:windir\system32\inetsrv\appcmd.exe"

if ($AuthMode -eq 'Auto') {
    $joined = (Get-CimInstance Win32_ComputerSystem).PartOfDomain
    $AuthMode = if ($joined) { 'WindowsSSO' } else { 'AppLogin' }
    Say "This server is $(if ($joined) {'domain-joined'} else {'NOT domain-joined'}) - auth mode: $AuthMode."
}
if ($AuthMode -eq 'AppLogin') {
    Warn 'AppLogin mode: IIS passes requests through anonymously; each app shows'
    Warn 'its own login page (the Hub validates against AD over LDAP directly).'
    Warn 'For silent Windows SSO, join this server to the domain and re-run.'
}

# ── 1. IIS features ──────────────────────────────────────────────────────────
Say 'Ensuring IIS and Windows Authentication are installed...'
if (Get-Command Install-WindowsFeature -ErrorAction SilentlyContinue) {
    Install-WindowsFeature Web-Server, Web-Windows-Auth, Web-Http-Redirect,
        Web-Filtering, Web-Mgmt-Console | Out-Null            # Windows Server
} else {
    foreach ($f in 'IIS-WebServerRole','IIS-WindowsAuthentication','IIS-ManagementConsole') {
        Enable-WindowsOptionalFeature -Online -FeatureName $f -All -NoRestart | Out-Null
    }                                                          # Windows 10/11
}
Good 'IIS ready.'

# ── 2. URL Rewrite + ARR ─────────────────────────────────────────────────────
function Install-Msi($name, $url, $probePath) {
    if (Test-Path $probePath) { Say "$name already installed - skipping."; return }
    Say "Downloading $name..."
    $msi = Join-Path $Downloads "$name.msi"
    Invoke-WebRequest $url -OutFile $msi
    Say "Installing $name..."
    Start-Process msiexec.exe -Wait -ArgumentList "/i `"$msi`" /qn /norestart"
    if (-not (Test-Path $probePath)) { throw "$name did not install ($probePath missing)." }
    Good "$name installed."
}
Install-Msi 'URLRewrite' `
    'https://download.microsoft.com/download/1/2/8/128E2E22-C1B9-44A4-BE2A-5859ED1D4592/rewrite_amd64_en-US.msi' `
    "$env:windir\system32\inetsrv\rewrite.dll"
Install-Msi 'ARR3' `
    'https://download.microsoft.com/download/E/9/8/E9849D6A-020E-47E4-9FD0-A023E99B54EB/requestRouter_amd64.msi' `
    "$env:ProgramFiles\IIS\Application Request Routing\requestRouter.dll"
iisreset /restart | Out-Null

# ── 3. proxy mode + server variables ─────────────────────────────────────────
Say 'Enabling ARR proxy mode...'
& $appcmd set config -section:system.webServer/proxy /enabled:"True" `
    /preserveHostHeader:"True" /commit:apphost | Out-Null
Say 'Unlocking the forwarded-identity server variables...'
foreach ($v in 'HTTP_X_REMOTE_USER','HTTP_X_IIS_PROXY_TOKEN','HTTP_X_FORWARDED_PROTO') {
    & $appcmd set config -section:system.webServer/rewrite/allowedServerVariables `
        /+"[name='$v']" /commit:apphost 2>$null | Out-Null
}
foreach ($section in 'windowsAuthentication','anonymousAuthentication') {
    & $appcmd unlock config -section:system.webServer/security/authentication/$section | Out-Null
}
Good 'Proxy mode on; server variables allowed.'

# ── 4 + 5 + 6. the sites, their auth, their web.configs ─────────────────────
Import-Module WebAdministration
$Token = -join ((48..57)+(97..122) | Get-Random -Count 40 | ForEach-Object {[char]$_})

function Write-ProxyConfig($folder, $backend) {
    # One site, one job: everything that arrives goes to this app's backend,
    # stamped with who IIS says is asking (empty under anonymous) and the
    # token that proves the stamp came from this gateway.
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    @"
<?xml version="1.0" encoding="UTF-8"?>
<!-- Written by tools/iis/setup_iis_proxy.ps1 - re-run it rather than editing. -->
<configuration>
  <system.webServer>
    <rewrite>
      <rules>
        <rule name="proxy-all" stopProcessing="true">
          <match url="(.*)" />
          <serverVariables>
            <set name="HTTP_X_REMOTE_USER" value="{LOGON_USER}" />
            <set name="HTTP_X_IIS_PROXY_TOKEN" value="$Token" />
            <set name="HTTP_X_FORWARDED_PROTO" value="https" />
          </serverVariables>
          <action type="Rewrite" url="$backend/{R:1}" />
        </rule>
      </rules>
    </rewrite>
    <!-- SSE (AJiAI streaming) and long polls must flow, not be buffered. -->
    <urlCompression doStaticCompression="true" doDynamicCompression="false" />
    <security>
      <requestFiltering>
        <requestLimits maxAllowedContentLength="104857600" />
      </requestFiltering>
    </security>
  </system.webServer>
</configuration>
"@ | Set-Content -Path (Join-Path $folder 'web.config') -Encoding UTF8
}

function Set-SiteAuth($siteName) {
    if ($AuthMode -eq 'WindowsSSO') {
        & $appcmd set config "$siteName" -section:system.webServer/security/authentication/anonymousAuthentication `
            /enabled:"False" /commit:apphost | Out-Null
        & $appcmd set config "$siteName" -section:system.webServer/security/authentication/windowsAuthentication `
            /enabled:"True" /commit:apphost | Out-Null
        foreach ($p in 'Negotiate','NTLM') {
            & $appcmd set config "$siteName" -section:system.webServer/security/authentication/windowsAuthentication `
                /-"providers.[value='$p']" /commit:apphost 2>$null | Out-Null
        }
        # Negotiate (Kerberos) first, NTLM fallback — the order browsers expect.
        foreach ($p in 'Negotiate','NTLM') {
            & $appcmd set config "$siteName" -section:system.webServer/security/authentication/windowsAuthentication `
                /+"providers.[value='$p']" /commit:apphost | Out-Null
        }
    } else {
        & $appcmd set config "$siteName" -section:system.webServer/security/authentication/anonymousAuthentication `
            /enabled:"True" /commit:apphost | Out-Null
        & $appcmd set config "$siteName" -section:system.webServer/security/authentication/windowsAuthentication `
            /enabled:"False" /commit:apphost | Out-Null
    }
}

function Ensure-Site($name, $folder, $hostHeader) {
    # A site of its own in the Sites tree. Hostname sites bind :80 and :443
    # with their host header (443 with SNI, so every site can carry the same
    # SAN certificate); the fallback binds bare and answers whatever the
    # hostname sites do not.
    if (-not (Get-Website -Name $name -ErrorAction SilentlyContinue)) {
        Say "Creating site '$name'$(if ($hostHeader) { " for $hostHeader" })..."
        if ($hostHeader) {
            New-Website -Name $name -Port $HttpPort -HostHeader $hostHeader -PhysicalPath $folder | Out-Null
        } else {
            New-Website -Name $name -Port $HttpPort -PhysicalPath $folder | Out-Null
        }
    }
    if ($hostHeader -and -not (Get-WebBinding -Name $name -Protocol https -Port $HttpsPort -HostHeader $hostHeader -ErrorAction SilentlyContinue)) {
        # SslFlags 1 = SNI. The certificate itself is attached by
        # make_gateway_cert.ps1 / issue_domain_cert.ps1.
        New-WebBinding -Name $name -Protocol https -Port $HttpsPort -HostHeader $hostHeader -SslFlags 1 | Out-Null
    }
    if (-not $hostHeader -and -not (Get-WebBinding -Name $name -Protocol https -Port $HttpsPort -ErrorAction SilentlyContinue)) {
        New-WebBinding -Name $name -Protocol https -Port $HttpsPort | Out-Null
    }
    Set-SiteAuth $name
    Start-Website -Name $name -ErrorAction SilentlyContinue
}

foreach ($app in $Apps) {
    $folder = Join-Path $SitesRoot ($app.Host.Split('.')[0])
    Write-ProxyConfig $folder $app.Backend
    Ensure-Site $app.Name $folder $app.Host
    Good "$($app.Name)  ->  https://$($app.Host)  ->  $($app.Backend)"
}
$fallbackFolder = Join-Path $SitesRoot '_default'
Write-ProxyConfig $fallbackFolder $FallbackBackend
Ensure-Site $FallbackName $fallbackFolder $null
Good "$FallbackName (fallback: IP / server name)  ->  $FallbackBackend"

# Old single-site layout from v1 of this script: its root web.config would
# shadow nothing now (each app has its own site), but clear it if present so
# IIS Manager shows the truth.
$oldRoot = 'C:\inetpub\aji-gateway\web.config'
if (Test-Path $oldRoot) { Remove-Item $oldRoot; Warn 'Removed v1 combined web.config (C:\inetpub\aji-gateway).' }

# ── 7. iis_sso.json for the Hub ──────────────────────────────────────────────
$ssoPath = Join-Path $ProjectRoot 'iis_sso.json'
[ordered]@{
    enabled              = ($AuthMode -eq 'WindowsSSO')
    proxy_token          = $Token
    create_unknown_users = $false
    trusted_origins      = @($Apps | ForEach-Object { $_.Host }) + @("$env:COMPUTERNAME")
} | ConvertTo-Json | Set-Content -Path $ssoPath -Encoding UTF8
if ($AuthMode -eq 'WindowsSSO') { Good "Wrote $ssoPath (SSO enabled; token matches the sites)." }
else { Good "Wrote $ssoPath with enabled:false - apps keep their own login pages." }

iisreset /restart | Out-Null
Write-Host ''
Write-Host '════════════════════════════════════════════════════════════════' -ForegroundColor Yellow
Write-Host " Done (auth mode: $AuthMode). IIS Manager now lists each app as"   -ForegroundColor Yellow
Write-Host ' its own site:'                                                    -ForegroundColor Yellow
foreach ($app in $Apps) { Write-Host "   $($app.Name)  ->  https://$($app.Host)  ->  $($app.Backend)" }
Write-Host "   $FallbackName  ->  (requests by IP / server name)  ->  $FallbackBackend"
Write-Host ''
Write-Host ' Remaining steps:'
Write-Host '  1. Certificate onto all sites (SNI): tools\iis\make_gateway_cert.ps1'
Write-Host '     (or issue_domain_cert.ps1 when domain-joined).'
Write-Host '  2. Start each app on its own localhost port; the Hub:'
Write-Host '       tools\start_hub.ps1'
Write-Host '  3. Restart the Hub so the fresh iis_sso.json token is read.'
Write-Host '════════════════════════════════════════════════════════════════' -ForegroundColor Yellow
