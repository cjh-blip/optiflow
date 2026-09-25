## autosave.ps1 -- detect DIALux save-confirmation popups and answer Yes.
##
## BetterGI-style "detect target -> respond automatically", but driven by UIA
## (DIALux is WPF, UIA tree is readable; no screen capture / OCR needed).
##
## Detection feature (conservative, hard gate):
##   A visible top-level window of the DIALux pid (not the main window) that
##   contains BOTH an AutomationId='Yes' AND AutomationId='No' button, AND at
##   least one Edit control. That shape = save/overwrite confirmation dialog
##   ("save file X ?"), where Yes keeps work and No loses it. We answer Yes.
##
## Safety: ANY other popup shape is left alone. Same rule as the preflight
## cancel_file_dialogs: never click through dialogs that may discard work,
## except the one shape we positively identify as a save prompt.
##
## Output (one line per find):
##   STEP find-save-dialog ok hwnd=<n> saved=<count>
##   STEP answer-save-dialog ok
## ASCII ONLY (PS 5.1 reads UTF-8 .ps1 as GBK; CJK breaks parsing).
param(
    [string]$ProcessName = 'DIALux_x64',
    [int]$TimeoutMs = 3000
)
$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

if (-not ([System.Management.Automation.PSTypeName]'SVP.U').Type) {
Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
namespace SVP {
  public class U {
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr h, ref int pid);
    [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int max);
    public static string Txt(IntPtr h) { StringBuilder b = new StringBuilder(1024); GetWindowText(h, b, 1024); return b.ToString(); }
    public delegate bool EnumProc(IntPtr h, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr lParam);
    public static List<IntPtr> Tops() {
      List<IntPtr> r = new List<IntPtr>();
      EnumWindows(delegate(IntPtr h, IntPtr lp) { r.Add(h); return true; }, IntPtr.Zero);
      return r;
    }
  }
}
"@
}

$proc = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if ($proc -eq $null) { Write-Output 'STEP find-save-dialog ok hwnd=0 saved=0'; exit 0 }
$top = [IntPtr]$proc.MainWindowHandle

$saved = 0
foreach ($h in [SVP.U]::Tops()) {
    if (-not [SVP.U]::IsWindowVisible($h)) { continue }
    $dpid = 0
    [void][SVP.U]::GetWindowThreadProcessId($h, [ref]$dpid)
    if ($dpid -ne $proc.Id -or $h -eq $top) { continue }

    # shape check: Yes + No buttons AND an Edit, all inside this popup
    $w = [System.Windows.Automation.AutomationElement]::FromHandle($h)
    $condYes = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'Yes')
    $condNo = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'No')
    $condEdit = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Edit)

    $btnYes = $w.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condYes)
    $btnNo = $w.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condNo)
    $edit = $w.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condEdit)
    if ($btnYes -eq $null -or $btnNo -eq $null -or $edit -eq $null) { continue }

    # yes, answer the save prompt
    try {
        $pat = $btnYes.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        $pat.Invoke()
        $saved++
        Write-Output ("STEP find-save-dialog ok hwnd=" + $h + " saved=" + $saved)
    } catch {
        Write-Output ("STEP find-save-dialog fail hwnd=" + $h + " err=" + $_)
    }
}
Write-Output 'STEP answer-save-dialog ok'
exit 0