# C-0054 probe: convert ONE legacy .ppt to .pptx via PowerPoint COM.
# Mirrors tools/convert_legacy.ps1 core logic, but writes ONLY into a temp dir
# and NEVER deletes the source. ASCII-only on purpose (see convert_legacy.ps1).
param(
    [Parameter(Mandatory=$true)][string]$Ppt,
    [Parameter(Mandatory=$true)][string]$Out
)
$ErrorActionPreference = 'Stop'
$MsoTrue = -1
if (-not (Test-Path -LiteralPath $Out)) { New-Item -ItemType Directory -Path $Out | Out-Null }
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$app = $null
try {
    $app = New-Object -ComObject PowerPoint.Application
    $ver = $app.Version
    $dest = Join-Path $Out ([System.IO.Path]::GetFileNameWithoutExtension($Ppt) + '.pptx')
    $pres = $app.Presentations.Open($Ppt, $MsoTrue, 0, 0)
    $slides = $pres.Slides.Count
    if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Force }
    $pres.SaveAs($dest, 24)
    $pres.Close()
    $sw.Stop()
    [pscustomobject]@{
        ok = $true; powerpoint_version = $ver; src = $Ppt; dest = $dest
        slides = $slides; seconds = [math]::Round($sw.Elapsed.TotalSeconds, 2)
        dest_bytes = (Get-Item -LiteralPath $dest).Length
    } | ConvertTo-Json -Compress
}
catch {
    [pscustomobject]@{ ok = $false; src = $Ppt; error = $_.Exception.Message } |
        ConvertTo-Json -Compress
}
finally {
    if ($app) {
        try { $app.Quit() } catch { }
        try { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null } catch { }
    }
}
