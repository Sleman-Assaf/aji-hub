# tools/iis/make_gateway_cert.ps1
#
# The certificate path for a web server that is NOT joined to the domain
# (issue_domain_cert.ps1 is the domain-joined path — it needs AD CS and a
# machine account, neither of which a workgroup server has).
#
# Makes a self-signed certificate covering every gateway hostname plus the
# server's IP, binds it to the gateway's :443, and exports the .cer that
# client machines need to trust. Run as Administrator:
#
#   powershell -ExecutionPolicy Bypass -File tools\iis\make_gateway_cert.ps1 `
#       -HostName @('ai.ajgroup.com.local','radar.ajgroup.com.local','ajihub.ajgroup.com.local') `
#       -SiteIp 172.30.0.4
#
# Afterwards, two things this script cannot do from a workgroup machine —
# both are one-line asks to whoever administers the domain:
#
#   DNS   — the three A records, so the names resolve for everyone:
#             Add-DnsServerResourceRecordA -ZoneName ajgroup.com.local -Name ajihub -IPv4Address 172.30.0.4
#             (and the same for ai / radar)
#           Interim, per test PC, C:\Windows\System32\drivers\etc\hosts:
#             172.30.0.4  ajihub.ajgroup.com.local ai.ajgroup.com.local radar.ajgroup.com.local
#
#   TRUST — push the exported aji-gateway.cer into "Trusted Root
#           Certification Authorities" by Group Policy (or import it manually
#           per PC: double-click -> Install -> Local Machine -> Trusted Root).
#           Until then browsers show a one-time warning; everything still works.

param(
    [Parameter(Mandatory=$true)][string[]]$HostName,
    [string]$SiteIp    = '',
    [string]$SiteName  = 'AJi Gateway',
    [int]   $HttpsPort = 443,
    [int]   $Years     = 5
)
$ErrorActionPreference = 'Stop'
function Say($m)  { Write-Host "==> $m" -ForegroundColor Cyan }
function Good($m) { Write-Host "    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }


# Accept the names however they arrived: a real array (direct invocation), or
# one comma-joined string / stray tokens (powershell.exe -File flattens
# arrays into separate arguments). Everything is split, trimmed and rejoined
# into a clean list before use.
$HostName = @($HostName | ForEach-Object { "$_" -split ',' } |
              ForEach-Object { $_.Trim().Trim(' ''"()@') } |
              Where-Object { $_ })
if (-not $HostName) { throw 'No hostnames given. Example: -HostName "ai.x,radar.x,ajihub.x"' }

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this from an Administrator PowerShell.'
}
if (-not $SiteIp) {
    $SiteIp = (Get-NetIPAddress -AddressFamily IPv4 |
               Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
               Select-Object -First 1).IPAddress
}

# One SAN certificate for all the names and the raw IP: the gateway has one
# :443 and presents one certificate whichever name (or the bare IP) the
# browser used.
$AllNames = @($HostName) + @($SiteIp) + @($env:COMPUTERNAME)
Say ("Creating a certificate for: " + ($AllNames -join ', '))
$cert = New-SelfSignedCertificate `
    -DnsName $AllNames `
    -CertStoreLocation Cert:\LocalMachine\My `
    -FriendlyName 'AJi Gateway' `
    -KeyLength 2048 -KeyExportPolicy Exportable `
    -NotAfter (Get-Date).AddYears($Years)
Good ("Thumbprint: " + $cert.Thumbprint)

# Self-signed means self-trusted nowhere — put it in this machine's own root
# store too, so tools running ON the server (health checks, curl) accept it.
Say 'Trusting it on this machine...'
$root = New-Object System.Security.Cryptography.X509Certificates.X509Store('Root', 'LocalMachine')
$root.Open('ReadWrite'); $root.Add($cert); $root.Close()

# Bind to every AJi site: each hostname site's SNI binding gets the same SAN
# certificate, and the fallback's bare :443 binding carries it for requests
# by IP or server name.
Import-Module WebAdministration
$sites = Get-Website | Where-Object { $_.Name -like 'AJi*' }
if (-not $sites) {
    Warn 'No AJi sites found - run tools\iis\setup_iis_proxy.ps1 first.'
} else {
    foreach ($site in $sites) {
        $bindings = Get-WebBinding -Name $site.Name -Protocol https -Port $HttpsPort -ErrorAction SilentlyContinue
        if (-not $bindings) {
            Warn "$($site.Name): no https :$HttpsPort binding - re-run setup_iis_proxy.ps1."
            continue
        }
        foreach ($b in $bindings) {
            $b.AddSslCertificate($cert.Thumbprint, 'My')
            $hostPart = ($b.bindingInformation -split ':')[-1]
            Good ("Bound to '" + $site.Name + "'" +
                  $(if ($hostPart) { " ($hostPart, SNI)" } else { ' (default binding)' }))
        }
    }
}

# Export what clients must trust.
$cerPath = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'certs\aji-gateway.cer'
New-Item -ItemType Directory -Force -Path (Split-Path $cerPath) | Out-Null
Export-Certificate -Cert $cert -FilePath $cerPath | Out-Null
Good "Exported $cerPath - distribute this to client PCs (Trusted Root)."

# The 443 firewall door, while we are here.
if (-not (Get-NetFirewallRule -DisplayName 'AJi Gateway HTTPS' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName 'AJi Gateway HTTPS' -Direction Inbound `
        -Protocol TCP -LocalPort $HttpsPort -Action Allow | Out-Null
    Good "Firewall: inbound $HttpsPort allowed."
}

Write-Host ''
Write-Host '════════════════════════════════════════════════════════════════' -ForegroundColor Yellow
Write-Host ' Certificate made, trusted locally, bound to :443, firewall open.' -ForegroundColor Yellow
Write-Host ' Still needed from the DOMAIN admin (one line each):'
Write-Host '   - the three DNS A records (commands at the top of this file)'
Write-Host '   - certs\aji-gateway.cer pushed to Trusted Root via GPO'
Write-Host ' Interim testing without them: hosts-file entries + accept the'
Write-Host ' browser warning once.'
Write-Host '════════════════════════════════════════════════════════════════' -ForegroundColor Yellow
