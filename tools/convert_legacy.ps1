# Convert legacy Office binary formats to OOXML so study_agent can parse them.
#
# Why this step exists: legacy binary formats (.ppt / .doc / .xls) have no
# reliable pure-Python parser, and this project's extraction chain only
# supports OOXML (.pptx / .docx / .xlsx). A user's courseware folder had 9 .ppt
# files - a quarter of the material - which would otherwise be skipped.
#
# Implementation note: PowerShell has native COM support, so no pip install of
# pywin32 is needed.
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads .ps1 files
# using the system ANSI code page (GBK on Chinese Windows), so UTF-8 Chinese
# text here gets mis-decoded byte-by-byte and can corrupt the parse.
#
# Usage (invoke this script directly: the old tools/convert_legacy.py wrapper
# no longer exists):
#   powershell -NoProfile -File convert_legacy.ps1 -Root "G:\study_agent\raw" -DryRun
#   powershell -NoProfile -File convert_legacy.ps1 -Root "G:\study_agent\raw"
param(
    [Parameter(Mandatory=$true)][string]$Root,
    [switch]$DryRun,
    [switch]$KeepOriginal
)

$ErrorActionPreference = 'Stop'

# -1 is msoTrue / msoCTrue as an integer; PowerPoint's COM API wants
# MsoTriState, not a PowerShell boolean ("Invalid cast from 'System.Boolean'").
$MsoTrue = -1

$map = @{
    '.ppt' = @{ App = 'PowerPoint.Application'; Fmt = 24 }   # ppSaveAsOpenXMLPresentation
    '.doc' = @{ App = 'Word.Application';     Fmt = 16 }     # wdFormatDocumentDefault
    '.xls' = @{ App = 'Excel.Application';    Fmt = 51 }     # xlOpenXMLWorkbook
}

$targets = @(Get-ChildItem -LiteralPath $Root -Recurse -File |
    Where-Object { $map.ContainsKey($_.Extension.ToLower()) })

if ($targets.Count -eq 0) {
    Write-Output '[]'
    exit 0
}

$results = @()
$script:apps = @{}

function Get-OfficeApp($progId) {
    if (-not $script:apps.ContainsKey($progId)) {
        $script:apps[$progId] = New-Object -ComObject $progId
    }
    return $script:apps[$progId]
}

try {
    foreach ($f in $targets) {
        $ext = $f.Extension.ToLower()
        $cfg = $map[$ext]
        $dest = [System.IO.Path]::ChangeExtension($f.FullName, $ext + 'x')

        if ($DryRun) {
            $results += [pscustomobject]@{
                src = $f.FullName; dest = $dest; status = 'would-convert'; note = ''
            }
            continue
        }
        if (Test-Path -LiteralPath $dest) {
            $results += [pscustomobject]@{
                src = $f.FullName; dest = $dest; status = 'skipped'; note = 'dest exists'
            }
            continue
        }

        try {
            $app = Get-OfficeApp $cfg.App
            if ($cfg.App -eq 'PowerPoint.Application') {
                # PowerPoint refuses Visible=$false, and Visible must be an
                # MsoTriState integer. WithWindow on Open keeps it out of sight.
                $pres = $app.Presentations.Open($f.FullName, $MsoTrue, 0, 0)
                $pres.SaveAs($dest, $cfg.Fmt)
                $pres.Close()
            }
            elseif ($cfg.App -eq 'Word.Application') {
                $app.Visible = $false
                $doc = $app.Documents.Open($f.FullName, $false, $true)
                $doc.SaveAs([ref]$dest, [ref]$cfg.Fmt)
                $doc.Close([ref]$false)
            }
            else {
                $app.Visible = $false
                $app.DisplayAlerts = $false
                $wb = $app.Workbooks.Open($f.FullName, 0, $true)
                $wb.SaveAs($dest, $cfg.Fmt)
                $wb.Close($false)
            }

            if (-not $KeepOriginal) {
                Remove-Item -LiteralPath $f.FullName -Force
            }
            $results += [pscustomobject]@{
                src = $f.FullName; dest = $dest; status = 'converted'; note = ''
            }
        }
        catch {
            $results += [pscustomobject]@{
                src = $f.FullName; dest = $dest; status = 'failed'
                note = $_.Exception.Message
            }
        }
    }
}
finally {
    foreach ($k in $script:apps.Keys) {
        try { $script:apps[$k].Quit() } catch { }
        try { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($script:apps[$k]) | Out-Null } catch { }
    }
}

$results | ConvertTo-Json -Depth 4 -Compress
