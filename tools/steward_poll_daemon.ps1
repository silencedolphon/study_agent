# steward_poll_daemon.ps1 -- keep the steward acceptance-poller running unattended.
#
# WHY THIS EXISTS
#   tools/steward_poll.py (loop version) calls sys.exit(0) the moment a card newly
#   enters "review", on purpose: the host then notifies the butler session to run
#   the pre-check. That design loses the poller after ONE delivery -- if nobody
#   restarts it, the next delivery wakes nobody. This daemon restarts it forever,
#   so overnight/unattended runs never lose the wake-up channel.
#
# DESIGN
#   * One long-running process; each iteration runs steward_poll.py and waits.
#   * There is no "arm" concept: once started it never needs restarting.
#   * MIN_RESTART_SEC guards against a crash-restart hot loop.
#   * Refuses to start a second instance (pid file).
#   * Read-only with respect to the ledger -- it only tails and logs.
#
# USAGE
#   Start (detached, survives this session):
#     Start-Process pwsh -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass',
#       '-File','G:\study_agent\tools\steward_poll_daemon.ps1' -WindowStyle Hidden
#   Stop:
#     Stop-Process -Id (Get-Content G:\study_agent\logs\steward_poll_daemon.pid)
#   Watch:
#     Get-Content G:\study_agent\logs\steward_poll_daemon.log -Tail 40 -Wait
#
# ASCII only on purpose: Windows PowerShell 5.1 reads BOM-less .ps1 as ANSI,
# which would corrupt non-ASCII source.

param(
    [int]$IntervalSec = 60,
    [int]$MinRestartSec = 5,
    [int]$MaxLogBytes = 20MB
)

$ErrorActionPreference = 'Continue'
$Root      = 'G:\study_agent'
$Python    = Join-Path $Root 'venv\Scripts\python.exe'
$Poller    = Join-Path $Root 'tools\steward_poll.py'
$LogDir    = Join-Path $Root 'logs'
$LogFile   = Join-Path $LogDir 'steward_poll_daemon.log'
$PidFile   = Join-Path $LogDir 'steward_poll_daemon.pid'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

foreach ($p in @($Python, $Poller)) {
    if (-not (Test-Path -LiteralPath $p)) { throw "missing required file: $p" }
}
if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }

# Single-instance guard: refuse to start if a live daemon already holds the pid file.
if (Test-Path -LiteralPath $PidFile) {
    $oldPid = (Get-Content -LiteralPath $PidFile -Raw -ErrorAction SilentlyContinue)
    if ($oldPid) {
        $oldPid = $oldPid.Trim()
        $alive = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($alive) {
            Write-Host "daemon already running (pid $oldPid); exiting."
            exit 0
        }
    }
}
[System.IO.File]::WriteAllText($PidFile, "$PID", $Utf8NoBom)

function Write-Log([string]$Text) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Text
    [System.IO.File]::AppendAllText($LogFile, $line + [Environment]::NewLine, $Utf8NoBom)
}

function Rotate-LogIfNeeded() {
    if (Test-Path -LiteralPath $LogFile) {
        $len = (Get-Item -LiteralPath $LogFile).Length
        if ($len -gt $MaxLogBytes) {
            $stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
            $rolled = "$LogFile.$stamp"
            Move-Item -LiteralPath $LogFile -Destination $rolled -Force
            Write-Log ("log rotated ({0:N1} MB) -> {1}" -f ($len / 1MB), (Split-Path -Leaf $rolled))
        }
    }
}

Write-Log "=== daemon start | pid=$PID | interval=${IntervalSec}s | minRestart=${MinRestartSec}s ==="
Write-Log "    poller = $Poller"

$round   = 0
$started = Get-Date
while ($true) {
    $round++
    Rotate-LogIfNeeded
    Write-Log "--- round #$round : launching poller ---"

    $t0 = Get-Date
    # NOTE: native stdout/stderr must NOT be piped through PowerShell (mangles
    # encoding and buffers for minutes). Redirect to file instead; the poller
    # itself runs with -u (unbuffered) so the log updates live.
    & $Python -X utf8 -u $Poller $IntervalSec *>> $LogFile
    $code = $LASTEXITCODE
    $secs = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)

    # exit 0 == the poller saw a card newly enter review and quit to wake the butler.
    if ($code -eq 0) {
        Write-Log "--- poller exited code=0 after ${secs}s: a card reached review (butler wake-up signal) -- restarting ---"
    } else {
        Write-Log "--- poller exited code=$code after ${secs}s (unexpected) -- restarting ---"
    }

    $elapsed = ((Get-Date) - $t0).TotalSeconds
    if ($elapsed -lt $MinRestartSec) {
        $nap = [int][math]::Ceiling($MinRestartSec - $elapsed)
        Write-Log "    hot-loop guard: sleeping ${nap}s before restart"
        Start-Sleep -Seconds $nap
    }
    $upMin = [math]::Round(((Get-Date) - $started).TotalMinutes, 1)
    Write-Log "    daemon uptime ${upMin} min"
}
