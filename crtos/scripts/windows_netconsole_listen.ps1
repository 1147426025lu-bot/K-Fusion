# Listen for Pi netconsole (UDP 6666). Run as Administrator once for firewall, then normal user OK.
# Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File windows_netconsole_listen.ps1

param(
    [int]$Port = 6666,
    [string]$LogFile = "",
    [int]$HangSilenceSec = 20,
    [switch]$AddFirewallRule
)

if ($AddFirewallRule) {
    if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Error "Run PowerShell as Administrator for -AddFirewallRule"
        exit 1
    }
    $name = "Pi Netconsole UDP $Port"
    $existing = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    if (-not $existing) {
        New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol UDP `
            -LocalPort $Port -Action Allow -Profile Any | Out-Null
        Write-Host "Firewall: allowed inbound UDP $Port"
    } else {
        Write-Host "Firewall rule already exists: $name"
    }
    exit 0
}

$endpoint = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
$udp = New-Object System.Net.Sockets.UdpClient $Port
$udp.Client.ReceiveTimeout = 3000

if ($LogFile -eq "") {
    $LogFile = Join-Path $PSScriptRoot "..\logs\netconsole-$(Get-Date -Format 'yyyyMMdd-HHmmss').log"
}
$LogFile = [System.IO.Path]::GetFullPath($LogFile)
$logDir = Split-Path $LogFile -Parent
if ($logDir -and -not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

Write-Host "Log file: $LogFile"
Write-Host "Hang detect: ${HangSilenceSec}s silence after 'BLR arch_entry'"

Write-Host "Listening UDP $Port (Ctrl+C to stop)"
Write-Host "If nothing appears: run AS ADMIN:"
Write-Host "  powershell -ExecutionPolicy Bypass -File $PSCommandPath -AddFirewallRule"
Write-Host ""
Write-Host "Pi test (second SSH window):"
Write-Host "  sudo bash crtos/scripts/setup_netconsole__网络控制台.sh 192.168.137.1"
Write-Host "  echo hi | sudo tee /dev/kmsg"
Write-Host ""

$dots = 0
$blrSeen = $false
$lastActivity = Get-Date
function Write-LogLine($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}
Write-LogLine "=== netconsole capture start port=$Port ==="
try {
    while ($true) {
        try {
            $bytes = $udp.Receive([ref]$endpoint)
            $text = [System.Text.Encoding]::UTF8.GetString($bytes).TrimEnd([char]0)
            Write-LogLine "$($endpoint.Address): $text"
            $lastActivity = Get-Date
            $dots = 0
            if ($text -match "BLR arch_entry") {
                $blrSeen = $true
                Write-LogLine ">>> MARK: BLR seen — silence ${HangSilenceSec}s => HANG <<<"
            }
        } catch [System.Net.Sockets.SocketException] {
            if ($_.Exception.SocketErrorCode -eq [System.Net.Sockets.SocketError]::TimedOut) {
                if ($blrSeen -and ((Get-Date) - $lastActivity).TotalSeconds -ge $HangSilenceSec) {
                    Write-LogLine ">>> HANG DETECTED: no UDP for ${HangSilenceSec}s after BLR <<<"
                    Write-LogLine ">>> Log saved: $LogFile <<<"
                    break
                }
                $dots++
                if ($dots -ge 10) {
                    Write-Host ("[{0}] still waiting for UDP..." -f (Get-Date -Format "HH:mm:ss"))
                    $dots = 0
                } else {
                    Write-Host "." -NoNewline
                }
            } else {
                throw
            }
        }
    }
} finally {
    $udp.Close()
}
