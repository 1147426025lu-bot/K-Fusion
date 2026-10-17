# measure_inmate_cyclictest (serial cyclictest driver for the Pi4 NuttX inmate)
# Drive the NuttX inmate's **cyclictest** over a serial port (Windows host).
#
# Why this exists: cyclictest is the SAME tool family the root cell / K-Fusion
# mainline uses (`manifest_cyclictest.env`), so this makes the two
# sides of the Pi4 comparison use one yardstick instead of cyclictest-vs-rtlat.
#
# NuttX's cyclictest differs from upstream rt-tests in its option semantics:
#   -n / -m take an ARGUMENT (0=clock_nanosleep / clock_gettime  -> no device needed;
#                               1=devtimer / timer-API             -> needs -T /dev/timerN)
#   -q quiet, -p priority, -h histogram max (us), -l loops, -i interval (us)
# Defaults are -n 0 -m 0, i.e. clock_nanosleep + clock_gettime: /dev/timer is NOT used.
#
# Capture is ADAPTIVE (stops on '# Histogram Overflows') and FLUSHES INCREMENTALLY,
# so a 1-hour run still yields a partial log if the host sleeps / the task dies.
# A fixed capture window is fragile: a 15-min run once overran it by a few seconds
# and the whole result was lost.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File measure_inmate_cyclictest.ps1 `
#       -Port COM11 -IntervalUs 1000 -Loops 3600000 -Priority 200 -HistMax 200
#
# NOTE: content is ASCII-only on purpose (Windows PowerShell 5.1 reads .ps1 as ANSI
#       unless a BOM is present; ASCII avoids mojibake).

param(
  [Parameter(Mandatory=$true)][string]$Port,
  [int]$IntervalUs = 1000,
  [long]$Loops = 3600000,
  [int]$Priority = 200,
  [int]$HistMax = 200,
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
$log   = Join-Path $OutDir "pi4_inmate_cyclictest_$stamp.log"

$runSec = [math]::Ceiling($Loops * $IntervalUs / 1000000.0)
$capSec = if ($MaxSec -gt 0) { $MaxSec } else { $runSec + 180 }

$cmd = "cyclictest -n 0 -m 0 -q -p $Priority -h $HistMax -l $Loops -i $IntervalUs"

Write-Host "=== inmate cyclictest serial measurement ==="
Write-Host "port=$Port baud=$Baud interval=${IntervalUs}us loops=$Loops prio=$Priority hist=${HistMax}us"
Write-Host "expected run ~${runSec}s; capture ceiling ${capSec}s (stops early on '# Histogram Overflows')"
Write-Host "cmd: $cmd"
Write-Host "log=$log"

$header = "=== PI4 INMATE CYCLICTEST ===`r`nCMD: $cmd`r`nSTART: $(Get-Date -Format o)`r`n--- OUTPUT ---`r`n"
Set-Content -Path $log -Value $header -Encoding UTF8

$sp = New-Object System.IO.Ports.SerialPort($Port, $Baud, [System.IO.Ports.Parity]::None, 8, [System.IO.Ports.StopBits]::One)
$sb = New-Object System.Text.StringBuilder
$written = 0
$tail = ""
$done = $false
$sw = [System.Diagnostics.Stopwatch]::StartNew()
try {
  $sp.Open()
  Start-Sleep -Milliseconds 1200
  $sp.Write("`r"); Start-Sleep -Milliseconds 400
  $sp.Write("$cmd`r")
  while ($sw.Elapsed.TotalSeconds -lt $capSec) {
    try {
      $c = $sp.ReadExisting()
      if ($c) {
        [void]$sb.Append($c)
        $tail += $c
        if ($tail.Length -gt 256) { $tail = $tail.Substring($tail.Length - 256) }
      }
    } catch {}
    # incremental flush (~every 4 KB) so a partial log survives
    if ($sb.Length - $written -gt 4000) {
      Add-Content -Path $log -Value ($sb.ToString($written, $sb.Length - $written)) -NoNewline -Encoding UTF8
      $written = $sb.Length
    }
    if ($tail -match '# Histogram Overflows') { $done = $true; break }
    Start-Sleep -Milliseconds 30
  }
} finally {
  if ($sp.IsOpen) { $sp.Close() }
}
if ($sb.Length -gt $written) {
  Add-Content -Path $log -Value ($sb.ToString($written, $sb.Length - $written)) -NoNewline -Encoding UTF8
}

$text = $sb.ToString()
Write-Host ("captured {0:n1}s, DONE={1}, {2} chars" -f $sw.Elapsed.TotalSeconds, $done, $text.Length)

$lines = $text -split "`r?`n"
foreach ($pat in @('^# Total:', '^# Min Latencies:', '^# Avg Latencies:', '^# Max Latencies:', '^# Histogram Overflows:')) {
  $hit = ($lines | Where-Object { $_ -match $pat } | Select-Object -Last 1)
  if ($hit) { Write-Host $hit }
}

if (-not $done) {
  Write-Warning "Did not see '# Histogram Overflows' within ${capSec}s -- log may be truncated. Check cell/NSH/wiring."
}

Write-Host "raw log: $log"
Write-Output $log
