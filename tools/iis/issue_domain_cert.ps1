# tools/iis/issue_domain_cert.ps1
#
# Registers the gateway's site name properly in Active Directory, end to end:
#
#   1. DNS      — an A record for the name (hub.aji.local -> this server) in
#                 AD-integrated DNS, so the name resolves everywhere
#   2. CERT     — a certificate for that name, ISSUED BY THE DOMAIN CA
#                 (AD Certificate Services). Every domain-joined machine
#                 already trusts the enterprise CA through AD itself, so no
#                 browser warnings and nothing to distribute
#   3. SPN      — HTTP/hub.aji.local on this computer's AD account, which is
#                 what lets browsers use Kerberos (silent SSO) instead of
#                 falling back to NTLM when they visit that name
#   4. BINDING  — the new certificate onto the gateway site's :443
#
# Run on the WEB SERVER, from an ADMINISTRATOR PowerShell, as a DOMAIN
# ADMIN (steps 1 and 3 write to AD):
#
#   powershell -ExecutionPolicy Bypass -File tools\iis\issue_domain_cert.ps1 `
#       -HostName @('ai.ajgroup.com.local','radar.ajgroup.com.local','ajihub.ajgroup.com.local') `
#       -SiteIp 172.30.0.4
#
# PREREQUISITE (one time, on the CA): the web server's computer account must
# be allowed to enroll for a web-server certificate. On the CA machine:
# certtmpl.msc -> duplicate "Web Server" as "AJi Web Server" -> Security tab ->
# add this server's computer account (or "Domain Computers") with Enroll ->
# certsrv.msc -> Certificate Templates -> New -> Certificate Template to
# Issue -> pick it. Then pass -Template "AJi Web Server" here. The built-in
# "WebServer" template works too once given Enroll permission the same way.

param(
    [Parameter(Mandatory=$true)][string[]]$HostName,     # one or many, e.g. @('ai.x','radar.x','ajihub.x')
    [string]$SiteIp   = '',                              # A record target; default: this server's IP
    [string]$Template = 'WebServer',                     # certificate template on the CA
    [string]$SiteName = 'AJi Gateway',                   # IIS site to bind
    [int]   $HttpsPort = 443,
    [string]$DnsServer = ''                              # default: this domain's DC
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

foreach ($h in $HostName) {
    if ($h.Split('.').Count -lt 3) { throw "Use fully qualified names inside your AD zone, e.g. ajihub.ajgroup.com.local (got: $h)." }
}

# ── 0. the AD/DNS management cmdlets themselves ──────────────────────────────
# Get-ADDomainController and Add-DnsServerResourceRecordA live in RSAT, which
# a member server does not carry by default. Install the two tool sets when
# the OS can (Windows Server); when it cannot, everything below falls back to
# the machine's own DNS client settings and printed instructions.
if (Get-Command Install-WindowsFeature -ErrorAction SilentlyContinue) {
    foreach ($feat in 'RSAT-AD-PowerShell', 'RSAT-DNS-Server') {
        try {
            $state = Get-WindowsFeature $feat -ErrorAction SilentlyContinue
            if ($state -and -not $state.Installed) {
                Say "Installing $feat (management cmdlets)..."
                Install-WindowsFeature $feat | Out-Null
            }
        } catch { Warn "Could not install ${feat}: $($_.Exception.Message)" }
    }
    Import-Module ActiveDirectory -ErrorAction SilentlyContinue
    Import-Module DnsServer -ErrorAction SilentlyContinue
}

if (-not $SiteIp) {
    $SiteIp = (Get-NetIPAddress -AddressFamily IPv4 |
               Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
               Select-Object -First 1).IPAddress
}
if (-not $DnsServer) {
    # Ask AD for a DC when the cmdlet exists; otherwise the DNS server this
    # machine already uses is, on a domain member, a DC anyway.
    if (Get-Command Get-ADDomainController -ErrorAction SilentlyContinue) {
        try {
            $DnsServer = (Get-ADDomainController -Discover -ErrorAction Stop).HostName |
                         Select-Object -First 1
        } catch { $DnsServer = $null }
    }
    if (-not $DnsServer) {
        $DnsServer = (Get-DnsClientServerAddress -AddressFamily IPv4 |
            Where-Object ServerAddresses | Select-Object -First 1).ServerAddresses[0]
    }
    if (-not $DnsServer) { throw 'Could not determine a DNS server; pass -DnsServer <DC name or IP>.' }
}

# ── 1. the DNS records, one per name ─────────────────────────────────────────
foreach ($h in $HostName) {
    # The zone is everything after the first label: ajihub.ajgroup.com.local
    # -> ajgroup.com.local.
    $parts = $h.Split('.')
    $RecordName = $parts[0]
    $ZoneName   = ($parts | Select-Object -Skip 1) -join '.'
    Say "DNS: $h -> $SiteIp (zone $ZoneName on $DnsServer)..."
    $existing = Resolve-DnsName $h -Server $DnsServer -ErrorAction SilentlyContinue
    if ($existing -and ($existing | Where-Object { $_.IPAddress -eq $SiteIp })) {
        Good 'Record already exists and points here.'
    } elseif (-not (Get-Command Add-DnsServerResourceRecordA -ErrorAction SilentlyContinue)) {
        Warn 'DNS management cmdlets not available on this machine. On the DC, run:'
        Warn "  Add-DnsServerResourceRecordA -ZoneName $ZoneName -Name $RecordName -IPv4Address $SiteIp"
    } else {
        try {
            Add-DnsServerResourceRecordA -ZoneName $ZoneName -Name $RecordName `
                -IPv4Address $SiteIp -ComputerName $DnsServer
            Good 'A record created.'
        } catch {
            Warn "Could not create the record from here ($($_.Exception.Message))."
            Warn "Create it on the DC instead:"
            Warn "  Add-DnsServerResourceRecordA -ZoneName $ZoneName -Name $RecordName -IPv4Address $SiteIp"
        }
    }
}

# ── 2. the certificate, from the domain CA ───────────────────────────────────
Say ("Requesting ONE certificate covering: " + ($HostName -join ', ') + " (template: $Template)...")
# A single SAN certificate for every site name plus this machine's FQDN: the
# gateway has one :443, so it presents one certificate whatever name the
# browser asked for — the names all have to be on it.
$Fqdn = "$env:COMPUTERNAME.$env:USERDNSDOMAIN".ToLower()
try {
    $req = Get-Certificate -Template $Template `
        -SubjectName "CN=$($HostName[0])" -DnsName ($HostName + $Fqdn) `
        -CertStoreLocation Cert:\LocalMachine\My
    $cert = $req.Certificate
    Good ("Issued by " + $cert.Issuer)
    Good ("Thumbprint: " + $cert.Thumbprint)
} catch {
    Write-Host ''
    Warn "The CA refused or was not found: $($_.Exception.Message)"
    Warn ''
    Warn 'The usual causes, in order:'
    Warn ' - No AD Certificate Services in the domain. Check with:  certutil -ADCA'
    Warn "   (If AJi has no CA, installing one on a DC is: Install-WindowsFeature ADCS-Cert-Authority"
    Warn '    + Install-AdcsCertificationAuthority -CAType EnterpriseRootCA — a domain-level decision.)'
    Warn " - The '$Template' template does not let this computer enroll. On the CA:"
    Warn '   certtmpl.msc -> template -> Security -> add this server, tick Enroll.'
    Warn ' - The template is not published: certsrv.msc -> Certificate Templates ->'
    Warn '   New -> Certificate Template to Issue.'
    Warn ''
    Warn 'Fix that and re-run this script. Nothing else below can proceed without the cert.'
    exit 1
}

# ── 3. the SPNs, for Kerberos ────────────────────────────────────────────────
# IIS runs the gateway under the machine account by default, so Kerberos
# tickets for HTTP/<each name> must be answerable by this computer. Without
# the SPN browsers silently fall back to NTLM — still signed in, just the
# older protocol; with it, real Kerberos.
foreach ($h in $HostName) {
    Say "SPN: HTTP/$h -> $env:COMPUTERNAME..."
    $spnOut = & setspn -Q "HTTP/$h" 2>&1
    if ($spnOut -match 'Existing SPN found') {
        Good 'SPN already registered.'
    } else {
        & setspn -S "HTTP/$h" "$env:COMPUTERNAME$" | Out-Null
        if ($LASTEXITCODE -eq 0) { Good 'SPN registered on the computer account.' }
        else { Warn "Could not register the SPN (needs domain admin). On a DC:  setspn -S HTTP/$h $env:COMPUTERNAME$" }
    }
}

# ── 4. bind it to every AJi site ─────────────────────────────────────────────
Import-Module WebAdministration
$sites = Get-Website | Where-Object { $_.Name -like 'AJi*' }
if (-not $sites) {
    Warn 'No AJi sites found - run tools\iis\setup_iis_proxy.ps1 first, then re-run this,'
    Warn "or bind by hand with the thumbprint above: $($cert.Thumbprint)"
} else {
    foreach ($site in $sites) {
        $bindings = Get-WebBinding -Name $site.Name -Protocol https -Port $HttpsPort -ErrorAction SilentlyContinue
        if (-not $bindings) { Warn "$($site.Name): no https :$HttpsPort binding - re-run setup_iis_proxy.ps1."; continue }
        foreach ($b in $bindings) {
            $b.AddSslCertificate($cert.Thumbprint, 'My')
            Good ("Bound to '" + $site.Name + "'")
        }
    }
}

Write-Host ''
Write-Host '════════════════════════════════════════════════════════════════' -ForegroundColor Yellow
Write-Host (" Done. " + (($HostName | ForEach-Object { "https://$_" }) -join ", ")) -ForegroundColor Yellow
Write-Host " Each name is registered in AD DNS, covered by one"                 -ForegroundColor Yellow
Write-Host ' domain-CA certificate every domain PC already trusts, and has'    -ForegroundColor Yellow
Write-Host ' the SPN for silent Kerberos sign-in.'                             -ForegroundColor Yellow
Write-Host ''
Write-Host " Last step: confirm the names are in trusted_origins in"
Write-Host ' iis_sso.json and restart the Hub. Tell people the new address -'
Write-Host ' and enjoy: they will never see a login page or a warning again.'
Write-Host '════════════════════════════════════════════════════════════════' -ForegroundColor Yellow
