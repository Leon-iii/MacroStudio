param(
    [string] $Package = "$PSScriptRoot/../dist/MacroStudio",
    [switch] $RequireNoPython,
    [switch] $MotionShowcase,
    [switch] $VisionShowcase,
    [switch] $PythonShowcase
)

$ErrorActionPreference = "Stop"
if (([int]$MotionShowcase.IsPresent + [int]$VisionShowcase.IsPresent + [int]$PythonShowcase.IsPresent) -gt 1) {
    throw "Select one showcase."
}
if ($RequireNoPython) {
    if (Get-Command python, python3, py -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\Microsoft\WindowsApps\*' }) {
        throw "This environment already has a Python command."
    }
}

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class MacroStudioTestWindow {
    [StructLayout(LayoutKind.Sequential)]
    private struct Point { public int X; public int Y; }
    [DllImport("user32.dll")]
    private static extern bool ScreenToClient(IntPtr window, ref Point point);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool PostMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
    public static bool Click(IntPtr window, int x, int y) {
        Point point = new Point { X = x, Y = y };
        if (!ScreenToClient(window, ref point)) return false;
        IntPtr coordinates = new IntPtr((point.Y << 16) | (point.X & 0xffff));
        return PostMessage(window, 0x0201, new IntPtr(1), coordinates)
            && PostMessage(window, 0x0202, IntPtr.Zero, coordinates);
    }
}
'@
$findChildren = [Windows.Automation.TreeScope]::Children
$findDescendants = [Windows.Automation.TreeScope]::Descendants
$root = [Windows.Automation.AutomationElement]::RootElement

function Wait-For($Description, [scriptblock] $Check) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    do {
        $result = & $Check
        if ($result) { return $result }
        Start-Sleep -Milliseconds 100
    } while ($timer.Elapsed.TotalSeconds -lt 15)
    throw "Timed out waiting for $Description."
}

function Named($Window, $Name, $Scope = $findDescendants) {
    $condition = [Windows.Automation.PropertyCondition]::new(
        [Windows.Automation.AutomationElement]::NameProperty, $Name)
    $Window.FindFirst($Scope, $condition)
}

function Button($Window, $Name) {
    $element = [Windows.Automation.AutomationElement]
    $condition = [Windows.Automation.AndCondition]::new(
        [Windows.Automation.PropertyCondition]::new($element::NameProperty, $Name),
        [Windows.Automation.PropertyCondition]::new($element::ControlTypeProperty,
                                                   [Windows.Automation.ControlType]::Button))
    $Window.FindFirst($findDescendants, $condition)
}

function Invoke-FileAction($Name, $MenuName = "파일") {
    Click-Element (Named $window $MenuName)
    $action = Wait-For "file menu action $Name" {
        $item = Named $window $Name
        if (-not $item) {
            $popup = $root.FindAll($findChildren, $pidCondition)
            foreach ($candidate in $popup) {
                $item = Named $candidate $Name
                if ($item) { break }
            }
        }
        $item
    }
    Click-Element $action
}

function Click-Element($Element) {
    $bounds = $Element.Current.BoundingRectangle
    $owner = $Element
    while ($owner -and -not $owner.Current.NativeWindowHandle) {
        $owner = [Windows.Automation.TreeWalker]::RawViewWalker.GetParent($owner)
    }
    if (-not $owner -or $owner.Current.ProcessId -ne $app.Id -or
        -not [MacroStudioTestWindow]::Click([IntPtr]$owner.Current.NativeWindowHandle,
            [int]($bounds.X + $bounds.Width / 2), [int]($bounds.Y + $bounds.Height / 2))) {
        throw "Cannot click the file menu control."
    }
}

function Enter-Path($Title, $Path) {
    $dialog = Wait-For $Title {
        $child = Named $window $Title
        if ($child) { $child } else { Named $root $Title $findChildren }
    }
    $edit = Wait-For "file name field" {
        $all = $dialog.FindAll($findDescendants, [Windows.Automation.Condition]::TrueCondition)
        $value = $null
        foreach ($control in $all) {
            if ($control.Current.ControlType.Id -eq [Windows.Automation.ControlType]::Edit.Id -and
                ($control.Current.AutomationId -eq "1148" -or $control.Current.Name -match '파일 이름|File name') -and
                $control.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern, [ref]$value)) {
                return $value
            }
        }
    }
    $edit.SetValue($Path)
    $accept = Wait-For "file dialog submit button" {
        $condition = [Windows.Automation.PropertyCondition]::new(
            [Windows.Automation.AutomationElement]::AutomationIdProperty, "1")
        $button = $dialog.FindFirst($findDescendants, $condition)
        if ($button -and $button.Current.IsEnabled -and $button.Current.NativeWindowHandle) { $button }
    }
    if (-not [MacroStudioTestWindow]::PostMessage([IntPtr]$accept.Current.NativeWindowHandle,
                                                0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)) {
        throw "Cannot submit the file dialog."
    }
}

function Execution-Log {
    $condition = [Windows.Automation.PropertyCondition]::new(
        [Windows.Automation.AutomationElement]::NameProperty, "실행 로그")
    foreach ($log in $window.FindAll($findDescendants, $condition)) {
        $pattern = $null
        if ($log.TryGetCurrentPattern([Windows.Automation.TextPattern]::Pattern, [ref]$pattern)) {
            $pattern.DocumentRange.GetText(-1)
        }
        if ($log.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) {
            $pattern.Current.Value
        }
    }
}

$packageRoot = (Resolve-Path -LiteralPath $Package).Path
$scratch = Join-Path ([IO.Path]::GetTempPath()) ("MacroStudio-test-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $scratch | Out-Null
$original = Join-Path $scratch "original.macroproj"
$example = if ($PythonShowcase) { "python-showcase.macroproj" }
    elseif ($VisionShowcase) { "vision-find-click.macroproj" }
    elseif ($MotionShowcase) { "mouse_motion_showcase.macroproj" }
    else { "mixed-loop.macroproj" }
Copy-Item -LiteralPath (Join-Path $packageRoot "examples/$example") $original
$sourceProject = Get-Content -Raw -Encoding utf8 -LiteralPath $original | ConvertFrom-Json

$start = [Diagnostics.ProcessStartInfo]::new((Join-Path $packageRoot "MacroStudio.exe"))
$start.UseShellExecute = $false
$start.WorkingDirectory = $scratch
$start.EnvironmentVariables["PATH"] = "$env:SystemRoot\System32;$env:SystemRoot"
$start.EnvironmentVariables.Remove("PYTHONPATH")
$start.EnvironmentVariables.Remove("PYTHONHOME")
$existing = @(Get-Process -Name MacroStudio -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
$app = [Diagnostics.Process]::Start($start)
try {
    $pidCondition = [Windows.Automation.PropertyCondition]::new(
        [Windows.Automation.AutomationElement]::ProcessIdProperty, $app.Id)
    $window = Wait-For "Macro Studio window" {
        $root.FindAll($findChildren, $pidCondition) |
            Where-Object { $_.Current.Name -like "* | Macro Studio" } |
            Select-Object -First 1
    }
    Wait-For "Blockly editor" {
        $verify = Named $window "검증"
        if ($verify -and $verify.Current.IsEnabled) { $true }
    } | Out-Null
    Invoke-FileAction "열기…"
    Enter-Path "프로젝트 열기" $original
    Wait-For "opened project" { $window.Current.Name.StartsWith("$($sourceProject.name) | Macro Studio") } | Out-Null

    Invoke-FileAction "저장"
    Wait-For "saved project" { $window.Current.Name -like "original*Macro Studio" } | Out-Null
    $saved = Get-Content -Raw -Encoding utf8 -LiteralPath $original | ConvertFrom-Json
    if ($saved.name -ne "original" -or
        $saved.workspace.blocks.blocks.Count -ne $sourceProject.workspace.blocks.blocks.Count) {
        throw "Saved Blockly project did not match the example."
    }
    if ($MotionShowcase -and ($saved.settings.default_mouse_motion_profile_id -ne "builtin.precision" -or
        $saved.workspace.blocks.blocks[0].next.block.next.block.fields.PROFILE -ne "builtin.direct")) {
        throw "Saved project did not retain its motion profiles."
    }
    if ($PythonShowcase) {
        if ($saved.schema_version -ne 2 -or $saved.editor.active_mode -ne "python" -or
            $saved.editor.python.source -ne $sourceProject.editor.python.source -or
            $saved.service_requirements.'actions.run' -ne "1") {
            throw "Saved project did not retain its Python draft and service versions."
        }
        (Button $window "검증").GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
        Wait-For "validated Python source" {
            $text = (Execution-Log) -join "`n"
            if ($text -match '검증·변환 실패') { throw "Runner log: $text" }
            $text -match 'Python 검증 완료'
        } | Out-Null
        $authoring = Join-Path $scratch "authoring.zip"
        Invoke-FileAction "LLM 작성 자료 내보내기…" "Python"
        Enter-Path "LLM 작성 자료 내보내기" $authoring
        Wait-For "authoring archive" { Test-Path -LiteralPath $authoring } | Out-Null
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip = [IO.Compression.ZipFile]::OpenRead($authoring)
        try {
            if (-not $zip.GetEntry("project-context.json") -or
                -not $zip.GetEntry("MACRO_STUDIO_PYTHON_API.md") -or
                @($zip.Entries | Where-Object FullName -like "examples/*.py").Count -lt 8) {
                throw "Authoring assets are missing from the frozen editor."
            }
        }
        finally { $zip.Dispose() }
        Remove-Item -LiteralPath $authoring
    }
    if ($VisionShowcase) {
        $finder = $saved.workspace.blocks.blocks[0].next.block
        $imageId = $finder.fields.IMAGE
        if ($finder.type -ne "ms_vision_find" -or -not $imageId -or
            $saved.image_assets.PSObject.Properties[$imageId].Value.png -ne
            $sourceProject.image_assets.PSObject.Properties[$imageId].Value.png) {
            throw "Saved project did not retain the template and its block reference."
        }
        (Button $window "검증").GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
        Wait-For "compiled vision project" {
            $text = (Execution-Log) -join "`n"
            if ($text -match '검증 실패') { throw "Runner log: $text" }
            $text -match '검증 완료'
        } | Out-Null
        "PASS: packaged editor opened, saved and compiled the vision project"
        return
    }
    $action = Button $window "시작"
    $action.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
    $runner = Wait-For "separate bundled Runner" {
        Get-Process -Name MacroStudio -ErrorAction SilentlyContinue |
            Where-Object { $_.Id -ne $app.Id -and $_.Id -notin $existing } |
            Select-Object -First 1
    }
    foreach ($process in $app, $runner) {
        Wait-For "packaged Python in process $($process.Id)" {
            $process.Refresh()
            $process.Modules | Where-Object ModuleName -match '^python\d+\.dll$' |
                Where-Object FileName -like "$packageRoot\_internal\*"
        } | Out-Null
    }
    Wait-For "dry-run input counters" {
        $text = (Execution-Log) -join "`n"
        if ($MotionShowcase -and $text -match '이동 · 마우스 X 360 Y 140') { return $true }
        if (-not $MotionShowcase -and $text -match '누름 · scan:' -and $text -match '해제 · scan:') { return $true }
        if ($text -match 'Runner 연결이 끊겼습니다|검증 실패') { throw "Runner log: $text" }
    } | Out-Null
    if (-not $MotionShowcase) {
        $stop = Button $window "정지"
        $stop.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
    }
    Wait-For "Runner exit" { $runner.HasExited -and (Button $window "시작").Current.IsEnabled } | Out-Null
    "PASS: packaged editor, open/save and separate dry-run Runner"
}
finally {
    if (-not $app.HasExited) {
        $null = $app.CloseMainWindow()
        if (-not $app.WaitForExit(3000)) { $app.Kill() }
    }
    Remove-Item -LiteralPath $original -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $scratch -ErrorAction SilentlyContinue
}
