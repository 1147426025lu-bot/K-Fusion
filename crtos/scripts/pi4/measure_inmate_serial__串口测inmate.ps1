# measure_inmate_serial__串口测inmate.ps1
# Drive the NuttX inmate's rtlat jitter measurement over a serial port (Windows host).
#
# The inmate (NSH) writes to the physical mini-UART on GPIO 14/15; only the host
# that owns the USB-TTL adapter can read it. This script opens the port, sends the
# rtlat command, captures the output, parses the "rtlat RESULT" line and saves a log.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File measure_inmate_serial__串口测inmate.ps1 `
#       -Port COM11 -IntervalUs 1000 -Loops 60000
#
# NOTE: content is ASCII-only on purpose (Windows PowerShell 5.1 reads .ps1 as ANSI
#       unless a BOM is present; ASCII avoids mojibake).

param(
  [Parameter(Mandatory=$true)][string]$Port,
  [int]$IntervalUs = 1000,
  [int]$Loops = 60000,
  [int]$Baud = 115200,
  [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $OutDir -or $OutDir -eq "") {
  $OutDir = Join-Path $scriptDir "..\..\results\raw"
}
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log   = Join-Path $OutDir "pi4_inmate_rtlat_$stamp.log"
$capSec = [math]::Ceiling($Loops * $IntervalUs / 1000000.0) + 8

Write-Host "=== inmate rtlat serial measurement ==="
Write-Host "port=$Port baud=$Baud interval=${IntervalUs}us loops=$Loops capture=${capSec}s"
Write-Host "log=$log"

$sp = New-Object System.IO.Ports.SerialPort($Port, $Baud, [System.IO.Ports.Parity]::None, 8, [System.IO.Ports.StopBits]::One)
$sb = New-Object System.Text.StringBuilder
try {
  $sp.Open()
  Start-Sleep -Milliseconds 500
  $sp.Write("`r"); Start-Sleep -Milliseconds 300
  $sp.Write("rtlat -i $IntervalUs -l $Loops`r")
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  while ($sw.Elapsed.TotalSeconds -lt $capSec) {
    try { $c = $sp.ReadExisting(); if ($c) { [void]$sb.Append($c) } } catch {}
    Start-Sleep -Milliseconds 50
  }
} finally {
  if ($sp.IsOpen) { $sp.Close() }
}

$text = $sb.ToString()
Set-Content -Path $log -Value $text -Encoding UTF8

$line = ($text -split "`r?`n" | Where-Object { $_ -match 'rtlat RESULT' } | Select-Object -Last 1)
if (-not $line) {
  Write-Warning "No 'rtlat RESULT' line found. Check: cell running, NSH ready, wiring on GPIO14/15."
  Write-Host "raw log: $log"
  exit 2
}
Write-Host "RESULT: $line"
Write-Output $line
