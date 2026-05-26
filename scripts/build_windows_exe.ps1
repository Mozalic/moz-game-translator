[CmdletBinding()]
param(
    [string]$Name = "moz-game-translator",
    [string]$OutputDirectory = "dist\exe",
    [switch]$Console
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$EntryDirectory = Join-Path $Root "build\pyinstaller"
$EntryPath = Join-Path $EntryDirectory "pyinstaller_entry.py"
$SourcePath = Join-Path $Root "src"
$DistPath = Join-Path $Root $OutputDirectory

New-Item -ItemType Directory -Force -Path $EntryDirectory | Out-Null

@"
from jp_game_translator.qt_gui import main

if __name__ == "__main__":
    raise SystemExit(main())
"@ | Set-Content -Path $EntryPath -Encoding ASCII

$windowMode = "--windowed"
if ($Console) {
    $windowMode = "--console"
}

$pyinstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--onefile",
    $windowMode,
    "--name",
    $Name,
    "--paths",
    $SourcePath,
    "--collect-data",
    "jp_game_translator",
    "--distpath",
    $DistPath,
    "--workpath",
    $EntryDirectory,
    "--specpath",
    $EntryDirectory,
    $EntryPath
)

python -m PyInstaller @pyinstallerArgs

$exePath = Join-Path $DistPath ($Name + ".exe")
if (-not (Test-Path -LiteralPath $exePath)) {
    throw "Expected executable was not created: $exePath"
}

Write-Host "Built $exePath"
