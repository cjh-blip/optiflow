## ASCII ONLY. Probe and place one Cuboid through DIALux FurnitureTool.
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][double]$Width,
    [Parameter(Mandatory = $true)][double]$Length,
    [Parameter(Mandatory = $true)][double]$Height,
    [Parameter(Mandatory = $true)][double]$CenterX,
    [Parameter(Mandatory = $true)][double]$CenterY,
    [Parameter(Mandatory = $true)][double]$BaseZ,
    [string]$ProcessName = 'DIALux_x64',
    [int]$TimeoutMs = 8000
)
$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

if (-not ([System.Management.Automation.PSTypeName]'FU.U').Type) {
Add-Type @"
using System;
using System.Runtime.InteropServices;
namespace FU {
  public class U {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, UIntPtr e);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(int a, int b, bool f);
    [DllImport("kernel32.dll")] public static extern int GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr h, ref int pid);
  }
}
"@
}

function Emit([string]$name, [string]$status, [string]$detail = '') {
    Write-Output ("STEP {0} {1} {2}" -f $name, $status, $detail)
}
function Fail([string]$name, [string]$detail) {
    Emit $name 'fail' $detail
    exit 1
}
function ById([string]$id, [int]$ms = $TimeoutMs) {
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty, $id)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $ms) {
        $el = $main.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
        if ($el -ne $null) { return $el }
        Start-Sleep -Milliseconds 120
    }
    return $null
}
function SetValue([object]$el, [string]$value) {
    $el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue($value)
}
function ClickPoint([int]$x, [int]$y) {
    [void][FU.U]::SetCursorPos($x, $y)
    Start-Sleep -Milliseconds 150
    [FU.U]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
    [FU.U]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
}

$proc = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 } |
        Sort-Object StartTime -Descending | Select-Object -First 1
if ($proc -eq $null) { Fail 'furniture-tool' 'no-window-process' }
$root = [System.Windows.Automation.AutomationElement]::RootElement
$pc = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$main = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $pc)
if ($main -eq $null) { Fail 'furniture-tool' 'main-window-not-found' }

$hMain = [IntPtr]$proc.MainWindowHandle
$hFg = [FU.U]::GetForegroundWindow()
$tidFg = 0
if ($hFg -ne [IntPtr]::Zero) { [void][FU.U]::GetWindowThreadProcessId($hFg, [ref]$tidFg) }
$tidMe = [FU.U]::GetCurrentThreadId()
$attached = $false
if ($tidFg -ne 0 -and $tidFg -ne $tidMe) {
    $attached = [FU.U]::AttachThreadInput($tidMe, $tidFg, $true)
}
[void][FU.U]::SetForegroundWindow($hMain)
if ($attached) { [void][FU.U]::AttachThreadInput($tidMe, $tidFg, $false) }
Start-Sleep -Milliseconds 500

$construction = ById 'MenuGotoConstructionMode'
if ($construction -eq $null) { Fail 'furniture-tool' 'construction-menu-missing' }
try { $construction.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Collapse() } catch { }
$construction.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Expand()
Start-Sleep -Milliseconds 600
$furnitureTool = ById 'MenuGotoShowFurnitureTool'
if ($furnitureTool -eq $null) { Fail 'furniture-tool' 'furniture-menu-item-missing' }
$furnitureTool.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
Start-Sleep -Milliseconds 1000
if ((ById 'CuboidWidth' 2000) -eq $null) { Fail 'furniture-tool' 'furniture-tool-not-active' }
Emit 'furniture-tool' 'ok'

# Use a stable top view before any future canvas calibration. These controls are
# optional because older projects may not expose the view toolbar.
$floor = ById 'buttonFloorPlan' 1000
if ($floor -ne $null) {
    try { $floor.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() } catch { }
    Start-Sleep -Milliseconds 800
}
$viewAll = ById 'CadCommandButtonViewAll' 1000
if ($viewAll -ne $null) {
    try { $viewAll.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() } catch { }
    Start-Sleep -Milliseconds 800
}

$widthEl = ById 'CuboidWidth'
$lengthEl = ById 'CuboidLength'
$heightEl = ById 'CuboidHeight'
if ($widthEl -eq $null -or $lengthEl -eq $null -or $heightEl -eq $null) {
    Fail 'furniture-dimensions' 'cuboid-dimension-control-missing'
}
try {
    SetValue $widthEl ([string]$Width)
    SetValue $lengthEl ([string]$Length)
    SetValue $heightEl ([string]$Height)
} catch {
    Fail 'furniture-dimensions' 'cuboid-dimension-set-failed'
}
Emit 'furniture-dimensions' 'ok'

# A point click creates one selected Cuboid. The click is only a creation
# gesture; exact world coordinates are written through the fields revealed by
# the selected object afterwards.
$drawPoint = ById 'aid_DrawPoint' 2000
$render = ById '_renderHost' 2000
if ($drawPoint -eq $null -or $render -eq $null) {
    Fail 'furniture-position' 'point-tool-or-render-host-missing'
}
try {
    $drawPoint.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    $rect = $render.Current.BoundingRectangle
    $clickX = [int]($rect.X + ($rect.Width / 2.0))
    $clickY = [int]($rect.Y + ($rect.Height / 2.0))
    ClickPoint $clickX $clickY
    Start-Sleep -Milliseconds 1000
} catch {
    Fail 'furniture-position' 'point-create-failed'
}
$xEl = ById 'PositionVectorControl_X' 2500
$yEl = ById 'PositionVectorControl_Y' 2500
$zEl = ById 'PositionVectorControl_Z' 2500
if ($xEl -eq $null -or $yEl -eq $null -or $zEl -eq $null) {
    Fail 'furniture-position' 'position-controls-missing-after-point'
}
try {
    SetValue $xEl ([string]$CenterX)
    SetValue $yEl ([string]$CenterY)
    SetValue $zEl ([string]$BaseZ)
} catch {
    Fail 'furniture-position' 'position-set-failed'
}
Emit 'furniture-position' 'ok'

$nameEl = ById 'EditableName' 1500
if ($nameEl -ne $null) {
    try { SetValue $nameEl $Name } catch { }
}
$objectName = ById 'ArrangementObjectName' 1000
if ($objectName -ne $null) {
    try { SetValue $objectName $Name } catch { }
}
$save = ById 'Save' 2000
if ($save -eq $null) { Fail 'furniture-save' 'save-button-missing' }
try { $save.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() } catch { Fail 'furniture-save' 'save-invoke-failed' }
Start-Sleep -Milliseconds 1000
Emit 'furniture-save' 'ok'
Emit 'furniture-done' 'ok'
