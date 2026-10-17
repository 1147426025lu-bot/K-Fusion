# measure_inmate_serial__串口测inmate.ps1
# Drive the NuttX inmate's rtlat jitter measurement over a serial port (Windows host).
#
# The inmate (NSH) writes to the physical mini-UART on GPIO 14/15; only the host
# that owns the USB-TTL adapter can read it. This script opens the port, sends the
# rtlat command, captures the output, and saves a raw log.
#
# Capture is ADAPTIVE: it stops as soon as the "rtlat DONE" line arrives (with a
# safety ceiling), instead of using a fixed window. A fixed window is fragile --
# a 15-min run can overrun it by a few seconds and lose the whole result.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File measure_inmate_serial__串口测inmate.ps1 `
#       -Port COM11 -IntervalUs 1000 -Loops 60000 -Stride 3
#
#   -Stride 0 = do not emit per-sample lines (histogram only)
#   -Stride 1 = every sample; n>1 = every n-th sample (for the latency timeline)
#
# NOTE: content is ASCII-only on purpose (Windows PowerShell 5.1 reads .ps1 as ANSI
#       unless a BOM is present; ASCII avoids mojibake).

param(
  [Parameter(Mandatory=$true)][string]$Port,
  [int]$IntervalUs = 1000,
  [int]$Loops = 60000,
  [int]$BinNs = 100,
  [int]$Stride = 0,
  [int]$Baud = 115200,
  [int]$MaxSec = 0,
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

$runSec = [math]::Ceiling($Loops * $IntervalUs / 1000000.0)
# 串口导出逐样本会占额外时间（每行约 25 字节 @115200 ≈ 0.22ms）
$exportLines = 0
if ($Stride -gt 0) { $exportLines = [math]::Ceiling($Loops / [double]$Stride) }
$exportSec = [math]::Ceiling($exportLines * 0.25)
$autoMax = $runSec + $exportSec + 120
$capSec = if ($MaxSec -gt 0) { $MaxSec } else { $autoMax }

Write-Host "=== inmate rtlat serial measurement ==="
Write-Host "port=$Port baud=$Baud interval=${IntervalUs}us loops=$Loops bin=${BinNs}ns stride=$Stride"
Write-Host "run~${runSec}s export~${exportSec}s capture-ceiling=${capSec}s (stops early on 'rtlat DONE')"
Write-Host "log=$log"

$samples = 0
if ($Stride -gt 0) { $samples = $exportLines }

$sp = New-Object System.IO.Ports.SerialPort($Port, $Baud, [System.IO.Ports.Parity]::None, 8, [System.IO.Ports.StopBits]::One)
$sb = New-Object System.Text.StringBuilder
$done = $false
try {
  $sp.Open()
  Start-Sleep -Milliseconds 500
  $sp.Write("`r"); Start-Sleep -Milliseconds 300
  $cmd = "rtlat -i $IntervalUs -l $Loops -b $BinNs"
  if ($Stride -gt 0) { $cmd += " -d $Stride" }
  $sp.Write("$cmd`r")
  Write-Host "sent: $cmd"
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  while ($sw.Elapsed.TotalSeconds -lt $capSec) {
    try {
      $c = $sp.ReadExisting()
      if ($c) {
        [void]$sb.Append($c)
        if ($sb.ToString() -match 'rtlat DONE') { $done = $true; break }
      }
    } catch {}
    Start-Sleep -Milliseconds 50
  }
} finally {
  if ($sp.IsOpen) { $sp.Close() }
}

$text = $sb.ToString()
Set-Content -Path $log -Value $text -Encoding UTF8

Write-Host ("captured {0:n1}s, DONE={1}, {2} bytes" -f $sw.Elapsed.TotalSeconds, $done, $text.Length)

$result = ($text -split "`r?`n" | Where-Object { $_ -match 'rtlat RESULT' } | Select-Object -Last 1)
$stats  = ($text -split "`r?`n" | Where-Object { $_ -match 'rtlat STATS'  } | Select-Object -Last 1)
$clk    = ($text -split "`r?`n" | Where-Object { $_ -match 'rtlat CLKRES' } | Select-Object -Last 1)

if ($clk)    { Write-Host "CLKRES: $clk" }
if ($stats)  { Write-Host "STATS : $stats" }
if ($result) { Write-Host "RESULT: $result" }

if (-not $done) {
  Write-Warning "Did not see 'rtlat DONE' within ${capSec}s -- result may be truncated. Check wiring/NSH."
}
if (-not $result) {
  Write-Warning "No 'rtlat RESULT' line found. Check: cell running, NSH ready, wiring on GPIO14/15."
  Write-Host "raw log: $log"
  exit 2
}

Write-Host "raw log: $log"
Write-Output $log
