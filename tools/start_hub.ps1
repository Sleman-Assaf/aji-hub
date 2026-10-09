# tools/start_hub.ps1
#
# Starts everything the Hub needs behind the IIS gateway, from the project
# folder, using the project's own venv (no activation needed):
#
#   * Waitress on 127.0.0.1:8002  — the Hub itself (the gateway proxies
#     ajihub.ajgroup.com.local here; localhost-only on purpose: IIS being the
#     only way in is half of what makes the SSO header trustworthy)
#   * The media bridge            — group calls beyond 8 people, and P2P
#     calls between machines that cannot reach each other directly
#
# Run it directly to watch the logs in two windows, or register both lines as
# services with NSSM / Task Scheduler ("At startup", run whether user is
# logged on or not) using the same commands printed below.

param(
    [string]$Listen  = '127.0.0.1:8002',
    [int]   $Threads = 24
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { throw "No venv at $Py - create it and pip install -r requirements.txt first." }

Write-Host "Hub      : $Py -m waitress --listen=$Listen --threads=$Threads aji_hub.wsgi:application"
Write-Host "Bridge   : $Py manage.py run_media_bridge"
Write-Host '(Use these two exact command lines when registering Windows services.)'
Write-Host ''

# The step whose absence shows up as unstyled pages: the project serves static
# files by hashed name from a manifest, and the manifest must be rebuilt from
# the current sources every deploy — a stale one points every page at CSS
# filenames that no longer exist.
& $Py manage.py collectstatic --noinput | Select-Object -Last 1
& $Py manage.py migrate --noinput | Select-Object -Last 1

Start-Process -FilePath $Py -WorkingDirectory $ProjectRoot `
    -ArgumentList 'manage.py', 'run_media_bridge'
& $Py -m waitress "--listen=$Listen" "--threads=$Threads" aji_hub.wsgi:application
