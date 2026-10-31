# Render pptx slides to PNG using PowerPoint COM.
#
# Why COM instead of rebuilding pages by hand:
# COM goes through PowerPoint's own rendering engine, so the output is exactly
# what a student sees - fonts, layers, SmartArt, embedded objects. Rebuilding
# with python-pptx would drop SmartArt and charts, and those often carry
# information.
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads .ps1 using the
# system ANSI code page (GBK on Chinese Windows), so UTF-8 Chinese comments get
# mis-decoded byte-by-byte and can break the param block. Already hit once.
#
# Usage:
#   powershell -NoProfile -File render_pptx_slides.ps1 -Pptx <path> -Out <dir> [-Width 1600]
#
# Output: one PNG per slide, named slide_01.png, slide_02.png ...
param(
    [Parameter(Mandatory=$true)][string]$Pptx,
    [Parameter(Mandatory=$true)][string]$Out,
    [int]$Width = 1600,
    [string]$Pages = ""
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Pptx)) {
    Write-Output ("FAIL not found: " + $Pptx)
    exit 2
}
if (-not (Test-Path -LiteralPath $Out)) {
    New-Item -ItemType Directory -Force -Path $Out | Out-Null
}

$app = $null
$pres = $null
try {
    $app = New-Object -ComObject PowerPoint.Application
    # Open ReadOnly so the source file is not modified or locked.
    $pres = $app.Presentations.Open($Pptx, -1, 0, 0)
    $n = $pres.Slides.Count
    $wanted = @{}
    if (-not [string]::IsNullOrWhiteSpace($Pages)) {
        foreach ($part in $Pages.Split(',')) {
            $num = 0
            if ([int]::TryParse($part.Trim(), [ref]$num)) { $wanted[$num] = $true }
        }
    }
    for ($i = 1; $i -le $n; $i++) {
        if ($wanted.Count -gt 0 -and -not $wanted.ContainsKey($i)) { continue }
        $name = 'slide_{0:d2}.png' -f $i
        $dest = Join-Path $Out $name
        # Export(FileName, FilterName, ScaleWidth, ScaleHeight); 0 = keep aspect
        $pres.Slides.Item($i).Export($dest, 'PNG', $Width, 0)
    }
    Write-Output ("OK " + $n)
}
catch {
    Write-Output ("FAIL " + $_.Exception.Message)
    exit 1
}
finally {
    if ($pres) { try { $pres.Close() } catch { } }
    if ($app) {
        try { $app.Quit() } catch { }
        try { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null } catch { }
    }
}
