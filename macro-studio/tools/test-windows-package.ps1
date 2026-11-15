param(
    [string] $Package = "$PSScriptRoot/../dist/MacroStudio",
    [switch] $RequireNoPython
)

$ErrorActionPreference = "Stop"
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
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool PostMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
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

function Enter-Path($Title, $Path) {
    $dialog = Wait-For $Title {
        $child = Named $window $Title
        if ($child) { $child } else { Named $root $Title $findChildren }
    }
    $all = $dialog.FindAll($findDescendants, [Windows.Automation.Condition]::TrueCondition)
    $accept = $all | Where-Object {
        $_.Current.AutomationId -eq "1" -and $_.Current.Name -match '^(열기|저장|Open|Save)'
    } | Select-Object -First 1
    $value = $null
    foreach ($control in $all) {
        if ($control.Current.ControlType.Id -eq [Windows.Automation.ControlType]::Edit.Id -and
            $control.Current.Name -match '파일 이름|File name' -and
            $control.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern, [ref]$value)) {
            break
        }
    }
    if (-not $value -or -not $accept) { throw "Cannot find the file dialog controls." }
    $value.SetValue($Path)
    if (-not [MacroStudioTestWindow]::PostMessage([IntPtr]$accept.Current.NativeWindowHandle,
                                                0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)) {
        throw "Cannot submit the file dialog."
    }
}

$packageRoot = (Resolve-Path -LiteralPath $Package).Path
$scratch = Join-Path ([IO.Path]::GetTempPath()) ("MacroStudio-test-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $scratch | Out-Null
$original = Join-Path $scratch "original.macroproj"
Copy-Item -LiteralPath (Join-Path $packageRoot "examples/mixed-loop.macroproj") $original

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
    $window = Wait-For "Macro Studio window" { $root.FindFirst($findChildren, $pidCondition) }
    Wait-For "Blockly editor" {
        $verify = Named $window "검증"
        if ($verify -and $verify.Current.IsEnabled) { $true }
    } | Out-Null
    (Button $window "열기…").GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
    Enter-Path "프로젝트 열기" $original
    Wait-For "opened project" { $window.Current.Name -like "mixed-loop*Macro Studio" } | Out-Null

    (Button $window "저장").GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
    Wait-For "saved project" { $window.Current.Name -like "original*Macro Studio" } | Out-Null
    $saved = Get-Content -Raw -Encoding utf8 -LiteralPath $original | ConvertFrom-Json
    if ($saved.name -ne "original" -or $saved.workspace.blocks.blocks.Count -lt 2) {
        throw "Saved Blockly project did not match the example."
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
        $condition = [Windows.Automation.PropertyCondition]::new(
            [Windows.Automation.AutomationElement]::NameProperty, "실행 로그")
        foreach ($log in $window.FindAll($findDescendants, $condition)) {
            $pattern = $null
            $text = if ($log.TryGetCurrentPattern([Windows.Automation.TextPattern]::Pattern, [ref]$pattern)) {
                $pattern.DocumentRange.GetText(-1)
            }
            if ($log.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) {
                $text += $pattern.Current.Value
            }
            if ($text -match '누름 · scan:' -and $text -match '해제 · scan:') { return $true }
            if ($text -match 'Runner 연결이 끊겼습니다|검증 실패') { throw "Runner log: $text" }
        }
    } | Out-Null
    $stop = Button $window "정지"
    $stop.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
    Wait-For "Runner stop" { -not (Button $window "정지").Current.IsEnabled } | Out-Null
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
