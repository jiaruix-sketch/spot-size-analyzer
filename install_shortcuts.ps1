param(
    [switch]$DesktopOnly,
    [string]$PythonwPath = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$appScript = Join-Path $projectRoot "knife_edge_app.py"
$launcherScript = Join-Path $projectRoot "launch_knife_edge.vbs"
if (-not (Test-Path -LiteralPath $appScript)) {
    throw "Application entry point not found: $appScript"
}
if (-not (Test-Path -LiteralPath $launcherScript)) {
    throw "Windowless launcher not found: $launcherScript"
}

$desktopDirectory = [Environment]::GetFolderPath("Desktop")
$profileDirectory = Split-Path -Parent $desktopDirectory
$pythonCandidates = @(
    (Join-Path $profileDirectory "miniconda3\pythonw.exe"),
    (Join-Path $profileDirectory "anaconda3\pythonw.exe"),
    (Join-Path $profileDirectory "AppData\Local\Programs\Python\Python313\pythonw.exe"),
    (Join-Path $profileDirectory "AppData\Local\Programs\Python\Python312\pythonw.exe"),
    (Join-Path $profileDirectory "AppData\Local\Programs\Python\Python311\pythonw.exe")
)
$commandPython = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($commandPython) {
    $pythonCandidates += Join-Path (Split-Path -Parent $commandPython) "pythonw.exe"
}
$pythonw = if ($PythonwPath) {
    [System.IO.Path]::GetFullPath($PythonwPath)
} else {
    $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $pythonw) {
    throw "Could not find pythonw.exe for the Desktop user."
}

$shortcutPaths = @(
    (Join-Path $desktopDirectory "Knife-Edge Spot Size Analyzer.lnk")
)
if (-not $DesktopOnly) {
    $shortcutPaths += Join-Path ([Environment]::GetFolderPath("Programs")) "Knife-Edge Spot Size Analyzer.lnk"
}

$shell = New-Object -ComObject WScript.Shell
$wscript = Join-Path $env:WINDIR "System32\wscript.exe"
foreach ($shortcutPath in $shortcutPaths) {
    $parent = Split-Path -Parent $shortcutPath
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $wscript
    $shortcut.Arguments = '"{0}"' -f $launcherScript
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.Description = "Knife-Edge Spot Size Analyzer"
    $shortcut.IconLocation = "$pythonw,0"
    $shortcut.WindowStyle = 1
    $shortcut.Save()
    Write-Output "Created: $shortcutPath"
}
