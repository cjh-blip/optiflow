# dialux_driver.ps1 -- drive DIALux evo through UI Automation.
#
# ASCII ONLY. PowerShell 5.1 reads a UTF-8 .ps1 as GBK, so any CJK character in
# this file turns into a ParserError at a random later brace. Do not add Chinese
# comments here; put the Chinese explanations in driver.py instead.
#
# Verified chain (DIALux evo 14.0 / 5.14.0.5):
#   File (expand) -> Import (expand) -> ImportStf (invoke)
#   -> file dialog: set filename Edit, click Open (AutomationId '1')
#   -> main toolbar Save (AutomationId 'Save')
#
# Every step prints one machine-readable line to stdout:
#   STEP <name> <ok|fail> <detail>
# driver.py parses those lines; nothing else on stdout is contractual.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$StfPath,
    [string]$ProcessName = 'DIALux_x64',
    [int]$TimeoutMs = 15000
)

$ErrorActionPreference = 'Stop'

# Both assemblies are required. UIAutomationClient alone does not bring in
# TreeScope / ControlType, which fails with "cannot find type [TreeScope]".
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

# Win32 fallbacks. Needed because DIALux's file dialog exposes no UIA patterns
# (see the file-dialog section) and because SetForegroundWindow has no UIA
# equivalent. All documented user32 entry points.
Add-Type -Namespace Win -Name U -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern bool AttachThreadInput(int idAttach, int idAttachTo, bool fAttach);
[DllImport("kernel32.dll")] public static extern int GetCurrentThreadId();
[DllImport("user32.dll")] public static extern IntPtr GetDlgItem(IntPtr h, int id);
[DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
[DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr h, ref int pid);
[DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern IntPtr FindWindowEx(IntPtr parent, IntPtr after, string cls, string title);
[DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern bool SetWindowText(IntPtr h, string s);
[DllImport("user32.dll", EntryPoint="SendMessage")] public static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr w, IntPtr l);
[DllImport("user32.dll", CharSet=CharSet.Auto, EntryPoint="GetWindowText")] public static extern int GetWindowTextStr(IntPtr h, System.Text.StringBuilder s, int n);
'@

function Emit([string]$name, [string]$status, [string]$detail = '') {
    Write-Output ("STEP {0} {1} {2}" -f $name, $status, $detail)
}

function Fail([string]$name, [string]$detail) {
    Emit $name 'fail' $detail
    exit 1
}

# Poll for a descendant carrying this AutomationId. Polling (not a single
# FindFirst) because menus materialise their children only after the parent
# expands, and the file dialog appears asynchronously.
function Find-ById($root, [string]$id, [int]$timeout = 0) {
    if ($timeout -le 0) { $timeout = $TimeoutMs }
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty, $id)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $timeout) {
        $el = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
        if ($el -ne $null) { return $el }
        Start-Sleep -Milliseconds 150
    }
    return $null
}

function Invoke-El($el) {
    $p = $el.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $p.Invoke()
}

function Expand-El($el) {
    $p = $el.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
    $p.Expand()
}

# ---------------------------------------------------------------- attach

if (-not (Test-Path -LiteralPath $StfPath)) { Fail 'attach' 'stf-missing' }
$full = (Resolve-Path -LiteralPath $StfPath).Path

$proc = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if ($proc -eq $null) { Fail 'attach' 'no-window-process' }

$root = [System.Windows.Automation.AutomationElement]::RootElement
$pidCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$main = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $pidCond)
if ($main -eq $null) { Fail 'attach' 'main-window-not-found' }
Emit 'attach' 'ok' ("pid=" + $proc.Id)

# Restore + foreground. Not needed for Invoke/SetValue, but the screen recording
# needs DIALux visible, and a minimized window reports off-screen bounds
# (imageBounds x:-31993) which breaks any later screenshot.
try {
    $wp = $main.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern)
    $wp.SetWindowVisualState('Normal')
} catch { }
# SetForegroundWindow alone is unreliable: Windows refuses a foreground change
# requested by a process that does not already own the foreground, and it fails
# SILENTLY. Measured 2026-09-06: the run died at menu-import-stf because DIALux
# never actually became active, so the WPF popup never rendered its children and
# ImportStf stayed absent from the UIA tree for the full 15 s poll.
# The documented workaround is to attach our input queue to the current
# foreground thread first, which makes us a legitimate requester.
$hMain = [IntPtr]$proc.MainWindowHandle
$fgOk = $false
for ($try = 0; $try -lt 3 -and -not $fgOk; $try++) {
    $hFg = [Win.U]::GetForegroundWindow()
    $tidFg = 0
    if ($hFg -ne [IntPtr]::Zero) { [void][Win.U]::GetWindowThreadProcessId($hFg, [ref]$tidFg) }
    $tidMe = [Win.U]::GetCurrentThreadId()
    $attached = $false
    if ($tidFg -ne 0 -and $tidFg -ne $tidMe) {
        $attached = [Win.U]::AttachThreadInput($tidMe, $tidFg, $true)
    }
    [void][Win.U]::SetForegroundWindow($hMain)
    if ($attached) { [void][Win.U]::AttachThreadInput($tidMe, $tidFg, $false) }
    $sw0 = [Diagnostics.Stopwatch]::StartNew()
    while ($sw0.ElapsedMilliseconds -lt 1500) {
        Start-Sleep -Milliseconds 120
        if ([Win.U]::GetForegroundWindow() -eq $hMain) { $fgOk = $true; break }
    }
}
if ($fgOk) { Emit 'activate' 'ok' }
else { Fail 'activate' 'could-not-bring-dialux-to-foreground' }

# ---------------------------------------------------------------- menu

# WPF builds submenu items lazily and closes the whole menu the moment the window
# loses activation, so a single pass is not reliable enough for an unattended demo.
# Each attempt collapses File first (Expand on an already-expanded item can toggle
# it shut), then walks down and REQUIRES the next level to actually materialise.
$fileMenu = Find-ById $main 'File'
if ($fileMenu -eq $null) { Fail 'menu-file' 'not-found' }

$importMenu = $null
$importStf = $null
for ($attempt = 1; $attempt -le 3; $attempt++) {
    try { $fileMenu.GetCurrentPattern(
        [System.Windows.Automation.ExpandCollapsePattern]::Pattern).Collapse() } catch { }
    Start-Sleep -Milliseconds 200
    Expand-El $fileMenu
    Start-Sleep -Milliseconds 350
    $importMenu = Find-ById $main 'Import' 4000
    if ($importMenu -eq $null) { continue }
    Expand-El $importMenu
    Start-Sleep -Milliseconds 350
    $importStf = Find-ById $main 'ImportStf' 4000
    if ($importStf -ne $null) { break }
}
if ($importMenu -eq $null) { Fail 'menu-file' 'import-never-appeared-after-3-tries' }
Emit 'menu-file' 'ok'
Emit 'menu-import' 'ok'
if ($importStf -eq $null) { Fail 'menu-import-stf' 'not-found-after-3-tries' }
Invoke-El $importStf
Emit 'menu-import-stf' 'ok'

# ---------------------------------------------------------------- file dialog
#
# UIA is useless for this dialog, twice over. Probed 2026-09-05:
#   (a) all 37 descendants come back as ControlType.Pane with an EMPTY
#       supported-pattern list -- no ValuePattern to set the filename, no
#       InvokePattern to press Open;
#   (b) polling UIA for AutomationId '1' races the dialog's HWND binding and
#       returns an element whose NativeWindowHandle is still 0.
# Win32 sees the same dialog correctly -- a plain #32770 with the classic
# common-dialog control ids:
#     GetDlgItem(hDlg, 1)    -> Button        "Open"
#     GetDlgItem(hDlg, 2)    -> Button        "Cancel"
#     GetDlgItem(hDlg, 1148) -> ComboBoxEx32  filename field
#     GetDlgItem(hDlg, 1136) -> ComboBox      file-type filter
# So this whole section is Win32: find the dialog by structural signature
# (a visible #32770 in our process that owns both control 1 and control 1148),
# never by title -- the title is Chinese and this file must stay ASCII.
#
# GOTCHA: pass [NullString]::Value, never $null, for a P/Invoke string that
# must be a NULL pointer. PowerShell marshals $null as an EMPTY STRING, so
# FindWindowEx(..., $null) silently matches only windows with a blank title
# and returns 0 for every real dialog. Cost us three failed real-machine runs.

function Find-FileDialog([int]$ownerPid, [int]$timeout) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $timeout) {
        $h = [IntPtr]::Zero
        while ($true) {
            $h = [Win.U]::FindWindowEx([IntPtr]::Zero, $h, '#32770', [NullString]::Value)
            if ($h -eq [IntPtr]::Zero) { break }
            $wpid = 0
            [void][Win.U]::GetWindowThreadProcessId($h, [ref]$wpid)
            if ($wpid -ne $ownerPid) { continue }
            if (-not [Win.U]::IsWindowVisible($h)) { continue }
            if ([Win.U]::GetDlgItem($h, 1) -eq [IntPtr]::Zero) { continue }
            if ([Win.U]::GetDlgItem($h, 1148) -eq [IntPtr]::Zero) { continue }
            return $h
        }
        Start-Sleep -Milliseconds 150
    }
    return [IntPtr]::Zero
}

$hDlg = Find-FileDialog $proc.Id 10000
if ($hDlg -eq [IntPtr]::Zero) { Fail 'dialog' 'file-dialog-not-found' }
$hOpen = [Win.U]::GetDlgItem($hDlg, 1)
Emit 'dialog' 'ok' ("hdlg=" + $hDlg)

# The filename field is a ComboBoxEx32; WM_SETTEXT has to land on the inner Edit,
# so descend ComboBoxEx32 -> ComboBox -> Edit and write BOTH levels.
#
# Measured 2026-09-05 on the live dialog:
#   SetWindowText(ComboBoxEx32) -> combo text set, inner Edit still EMPTY
#   SendMessage(Edit, WM_SETTEXT) -> no effect at all
#   SetWindowText(Edit)         -> Edit text set
# Only SetWindowText works, and the two controls do not mirror each other, so
# write both and accept either one reading back non-empty.
$hCombo = [Win.U]::GetDlgItem($hDlg, 1148)
if ($hCombo -eq [IntPtr]::Zero) { Fail 'dialog-filename' 'no-1148' }
$hEdit = [Win.U]::FindWindowEx($hCombo, [IntPtr]::Zero, 'Edit', [NullString]::Value)
if ($hEdit -eq [IntPtr]::Zero) {
    $hInner = [Win.U]::FindWindowEx($hCombo, [IntPtr]::Zero, 'ComboBox', [NullString]::Value)
    if ($hInner -ne [IntPtr]::Zero) {
        $hEdit = [Win.U]::FindWindowEx($hInner, [IntPtr]::Zero, 'Edit', [NullString]::Value)
    }
}

[void][Win.U]::SetWindowText($hCombo, $full)
if ($hEdit -ne [IntPtr]::Zero) { [void][Win.U]::SetWindowText($hEdit, $full) }
Start-Sleep -Milliseconds 250

# Read it back. A silently-empty filename field means Open would just reopen the
# folder listing and the whole run would hang waiting for a title change.
$got = ''
foreach ($h in @($hCombo, $hEdit)) {
    if ($h -eq [IntPtr]::Zero) { continue }
    $sb = New-Object System.Text.StringBuilder 1024
    [void][Win.U]::GetWindowTextStr($h, $sb, 1024)
    if ($sb.ToString().Trim().Length -gt 0) { $got = $sb.ToString(); break }
}
if ($got.Length -eq 0) { Fail 'dialog-filename' 'settext-had-no-effect' }
Emit 'dialog-filename' 'ok'

$BM_CLICK = 0x00F5
[void][Win.U]::SendMessage($hOpen, $BM_CLICK, [IntPtr]::Zero, [IntPtr]::Zero)
Emit 'import' 'ok'

# Import is silent: no dialog, no status text. The only observable signal is the
# window title changing to <name>.evo, so poll for that instead of sleeping.
$stem = [IO.Path]::GetFileNameWithoutExtension($full)
$sw = [Diagnostics.Stopwatch]::StartNew()
$titled = $false
while ($sw.ElapsedMilliseconds -lt $TimeoutMs) {
    Start-Sleep -Milliseconds 300
    try {
        $t = $main.GetCurrentPropertyValue(
            [System.Windows.Automation.AutomationElement]::NameProperty)
        if ($t -is [string] -and $t.Contains($stem)) { $titled = $true; break }
    } catch { }
}
if (-not $titled) { Fail 'import-settled' 'title-unchanged' }

# Clean up after ourselves. Measured 2026-09-06: BM_CLICK on the Open button does
# perform the import (title changes, scene rebuilds) but the dialog window is NEVER
# DESTROYED -- it stays on screen above the DIALux main window with our path still in
# the filename field, so it looks exactly like the run is stuck on "import STF".
# Presumably BM_CLICK bypasses the normal input path the modern IFileDialog needs to
# dismiss itself.
# The import has already landed (the title changed), so pressing Cancel (control 2)
# only removes the window; it undoes nothing.
# This MUST happen before save: a visible modal dialog collapses the main window's
# UIA descendant count from 310 to 3, and Save would then not be found.
if ([Win.U]::IsWindowVisible($hDlg)) {
    $hCancel = [Win.U]::GetDlgItem($hDlg, 2)
    if ($hCancel -ne [IntPtr]::Zero) {
        [void][Win.U]::SendMessage($hCancel, $BM_CLICK, [IntPtr]::Zero, [IntPtr]::Zero)
    }
    $sw2 = [Diagnostics.Stopwatch]::StartNew()
    while ($sw2.ElapsedMilliseconds -lt 3000) {
        Start-Sleep -Milliseconds 150
        if (-not [Win.U]::IsWindowVisible($hDlg)) { break }
    }
    if ([Win.U]::IsWindowVisible($hDlg)) { Fail 'import-settled' 'dialog-still-on-screen' }
}
Emit 'import-settled' 'ok'

# ---------------------------------------------------------------- save

$saveBtn = Find-ById $main 'Save'
if ($saveBtn -eq $null) { Fail 'save' 'button-not-found' }
Invoke-El $saveBtn
Start-Sleep -Milliseconds 1500
Emit 'save' 'ok'

Emit 'done' 'ok'
exit 0
