param([string] $Python = ".venv/Scripts/python.exe")

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$previousPath = $env:Path
Push-Location $root
try {
    $Python = (Get-Command $Python -CommandType Application).Source
    & $Python -c "import sys, platform; sys.exit(not (sys.platform == 'win32' and platform.machine().upper() == 'AMD64' and sys.maxsize > 2**32))"
    if ($LASTEXITCODE -ne 0) { throw "Build requires x64 Python on Windows." }

    $env:Path = "$env:SystemRoot\System32;$env:SystemRoot;$(Split-Path $Python -Parent)"
    $name = "MacroStudio"
    & $Python -m PyInstaller --noconfirm --clean --onedir --name $name --hide-console hide-early `
        --paths src --specpath build --add-data "$root/src/macro_studio/editor:macro_studio/editor" `
        "$PSScriptRoot/windows_entry.py"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    $dist = Join-Path (Join-Path $root "dist") $name
    $examples = New-Item -ItemType Directory -Force -Path (Join-Path $dist "examples")
    Copy-Item -Path "examples/*.macroproj" -Destination $examples.FullName
    Copy-Item -LiteralPath "docs/WINDOWS.md" -Destination (Join-Path $dist "README.md")
    $archive = Join-Path $root "dist/MacroStudio-Windows-x64.zip"
    & $Python -c "import shutil; shutil.make_archive('dist/MacroStudio-Windows-x64', 'zip', root_dir='dist', base_dir='MacroStudio')"
    if ($LASTEXITCODE -ne 0) { throw "ZIP creation failed." }
    Get-FileHash -Algorithm SHA256 -LiteralPath $archive
}
finally {
    $env:Path = $previousPath
    Pop-Location
}
