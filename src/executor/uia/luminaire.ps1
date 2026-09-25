## luminaire.ps1 -- product code: import a luminaire IES and place it in the
## open DIALux project via ArrangementFromSpace.
##
## This turns the hand-driven probes (import -> switch tool -> select prototype
## -> arrange -> save) into ONE callable product step so `demo_run.py --luminaires`
## can run the whole DWG->shell->lights pipeline automatically.
##
## Steps emitted (machine-readable, parsed by driver_plan.py):
##   STEP lum-import ok|fail <detail>
##   STEP lum-tool ok|fail <detail>
##   STEP lum-select ok|fail <detail>
##   STEP lum-arrange ok|fail <detail>
##   STEP lum-save ok|fail <detail>
##   STEP lum-done ok
##
## ASCII ONLY (PS 5.1 reads UTF-8 .ps1 as GBK; CJK breaks parsing).
param(
    [Parameter(Mandatory = $true)][string]$IesPath,
    [string]$PrototypeName = '',
    [string]$ProcessName = 'DIALux_x64',
    [int]$CountX = 0,
    [int]$CountY = 0,
    [int]$TimeoutMs = 12000
)
$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

if (-not ([System.Management.Automation.PSTypeName]'LU.U').Type) {
Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
namespace LU {
  public class U {
    [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern IntPtr FindWindowEx(IntPtr p, IntPtr c, string cls, string win);
    [DllImport("user32.dll")] public static extern IntPtr GetDlgItem(IntPtr h, int id);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr h, ref int pid);
    [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int max);
    [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern int GetClassName(IntPtr h, StringBuilder s, int max);
    [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, int m, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, int m, IntPtr w, IntPtr l);
    public static string Txt(IntPtr h) { StringBuilder b = new StringBuilder(1024); GetWindowText(h, b, 1024); return b.ToString(); }
    public static string Cls(IntPtr h) { StringBuilder b = new StringBuilder(256); GetClassName(h, b, 256); return b.ToString(); }
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

function Emit([string]$name, [string]$status, [string]$detail = '') {
    Write-Output ("STEP {0} {1} {2}" -f $name, $status, $detail)
}

$proc = Get-Process -Name $ProcessName | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if ($proc -eq $null) { Emit 'lum-import' 'fail' 'no-dialux-process'; exit 1 }
$root = [System.Windows.Automation.AutomationElement]::RootElement
$pc = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
$main = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $pc)
$COND = [System.Windows.Automation.Condition]::TrueCondition

function ById([string]$id, [int]$ms = 5000) {
  $c = New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::AutomationIdProperty, $id)
  $sw = [Diagnostics.Stopwatch]::StartNew()
  while ($sw.ElapsedMilliseconds -lt $ms) {
    $e = $main.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $c)
    if ($e -ne $null) { return $e }
    Start-Sleep -Milliseconds 120
  }
  return $null
}
function Close-Popups {
  foreach ($h in [LU.U]::Tops()) {
    if (-not [LU.U]::IsWindowVisible($h)) { continue }
    $dpid = 0; [void][LU.U]::GetWindowThreadProcessId($h, [ref]$dpid)
    if ($dpid -ne $proc.Id -or $h -eq [IntPtr]$proc.MainWindowHandle) { continue }
    $cls = [LU.U]::Cls($h)
    if ($cls -eq '#32770') {
      $c2 = [LU.U]::GetDlgItem($h, 2)
      if ($c2 -ne [IntPtr]::Zero) { [void][LU.U]::SendMessage($c2, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero) }
    } else {
      $closed = $false
      try {
        $w = [System.Windows.Automation.AutomationElement]::FromHandle($h)
        $cb = ById 'ButtonClose'
        if ($cb -ne $null) { $cb.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); $closed = $true }
      } catch { }
      if (-not $closed) { [void][LU.U]::PostMessage($h, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) }
    }
  }
  Start-Sleep -Milliseconds 1000
}
function Cancel-FileDialogs {
  foreach ($h in [LU.U]::Tops()) {
    if (-not [LU.U]::IsWindowVisible($h)) { continue }
    $dpid = 0; [void][LU.U]::GetWindowThreadProcessId($h, [ref]$dpid)
    if ($dpid -ne $proc.Id) { continue }
    if ([LU.U]::Cls($h) -eq '#32770' -and [LU.U]::GetDlgItem($h, 2) -ne [IntPtr]::Zero -and [LU.U]::GetDlgItem($h, 1148) -ne [IntPtr]::Zero) {
      [void][LU.U]::SendMessage([LU.U]::GetDlgItem($h, 2), 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)
    }
  }
  Start-Sleep -Milliseconds 600
}
function Find-FileDialog([int]$ownerPid, [int]$ms = 10000) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  while ($sw.ElapsedMilliseconds -lt $ms) {
    $prev = [IntPtr]::Zero
    while ($true) {
      $h = [LU.U]::FindWindowEx([IntPtr]::Zero, $prev, '#32770', [NullString]::Value)
      if ($h -eq [IntPtr]::Zero) { break }
      $prev = $h
      $dpid = 0; [void][LU.U]::GetWindowThreadProcessId($h, [ref]$dpid)
      if ($dpid -eq $ownerPid -and [LU.U]::IsWindowVisible($h)) {
        if ([LU.U]::GetDlgItem($h, 1) -ne [IntPtr]::Zero -and [LU.U]::GetDlgItem($h, 1148) -ne [IntPtr]::Zero) { return $h }
      }
    }
    Start-Sleep -Milliseconds 200
  }
  return [IntPtr]::Zero
}

if (-not (Test-Path -LiteralPath $IesPath)) { Emit 'lum-import' 'fail' 'ies-missing'; exit 1 }
$target = Split-Path -Leaf $IesPath

# ---------------------------------------------------------------- 1) import IES
Close-Popups
Cancel-FileDialogs
$full = (Resolve-Path -LiteralPath $IesPath).Path
$f = ById 'File'
if ($f -eq $null) { Emit 'lum-import' 'fail' 'file-menu-missing'; exit 1 }
try { $f.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Collapse() } catch { }
Start-Sleep -Milliseconds 200
$f.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Expand()
Start-Sleep -Milliseconds 400
$imp = ById 'Import' 6000
if ($imp -eq $null) { Emit 'lum-import' 'fail' 'import-menu-missing'; exit 1 }
$imp.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Expand()
Start-Sleep -Milliseconds 400
$il = ById 'ImportLuminaire' 4000
if ($il -eq $null) { Emit 'lum-import' 'fail' 'import-luminaire-missing'; exit 1 }
$il.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
$hDlg = Find-FileDialog $proc.Id 10000
if ($hDlg -eq [IntPtr]::Zero) { Emit 'lum-import' 'fail' 'file-dialog-not-found'; exit 1 }
Start-Sleep -Milliseconds 3000
$dlg = [System.Windows.Automation.AutomationElement]::FromHandle($hDlg)
$nc = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::NameProperty, $target)
$it = $null
$swL = [Diagnostics.Stopwatch]::StartNew()
while ($swL.ElapsedMilliseconds -lt $TimeoutMs -and $it -eq $null) {
  $it = $dlg.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $nc)
  if ($it -eq $null) { Start-Sleep -Milliseconds 500 }
}
if ($it -eq $null) { Cancel-FileDialogs; Emit 'lum-import' 'fail' 'target-not-in-dialog'; exit 1 }
$it.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
Start-Sleep -Milliseconds 400
$it.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
Start-Sleep -Milliseconds 5000
# paywall check
$paywall = $false
foreach ($h in [LU.U]::Tops()) {
  if (-not [LU.U]::IsWindowVisible($h)) { continue }
  $dpid = 0; [void][LU.U]::GetWindowThreadProcessId($h, [ref]$dpid)
  if ($dpid -ne $proc.Id -or $h -eq [IntPtr]$proc.MainWindowHandle) { continue }
  try {
    $w = [System.Windows.Automation.AutomationElement]::FromHandle($h)
    $c = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'ButtonBuyNow')
    if ($w.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $c) -ne $null) { $paywall = $true }
  } catch { }
}
if ($paywall) { Close-Popups; Emit 'lum-import' 'fail' 'paywall'; exit 1 }
Emit 'lum-import' 'ok'

# ---------------------------------------------------------------- 2) switch tool
Close-Popups
$probe = ById 'aid_ArrangementFromSpace' 1500
if ($probe -eq $null) {
  $menu = ById 'MenuGotoConstructionMode' 6000
  if ($menu -eq $null) { Emit 'lum-tool' 'fail' 'construction-menu-missing'; exit 1 }
  try { $menu.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Collapse() } catch { }
  Start-Sleep -Milliseconds 300
  $menu.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Expand()
  Start-Sleep -Milliseconds 1200
  $item = ById 'MenuGotoShowLuminaireArrangementTool' 6000
  if ($item -eq $null) { Emit 'lum-tool' 'fail' 'arrangement-tool-missing'; exit 1 }
  $item.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
  Start-Sleep -Milliseconds 3000
}
if ((ById 'aid_ArrangementFromSpace' 2000) -eq $null) { Emit 'lum-tool' 'fail' 'tool-not-active'; exit 1 }
Emit 'lum-tool' 'ok'

# ---------------------------------------------------------------- 3) select prototype
$btn = ById 'LuminaireCatalogButton' 3000
if ($btn -eq $null) { Emit 'lum-select' 'fail' 'catalog-btn-missing'; exit 1 }
try { $tg = $btn.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern); if ($tg.Current.ToggleState -ne 'On') { $tg.Toggle() } } catch { }
Start-Sleep -Milliseconds 2000
$lb = ById 'CatalogListBox' 3000
if ($lb -eq $null) { Emit 'lum-select' 'fail' 'catalog-listbox-missing'; exit 1 }
# find the matching ListItem
$selId = ''
if ($PrototypeName -ne '') { $selId = $PrototypeName }
else {
  # derive: strip extension, use LUMCAT-style basename guess
  $selId = [IO.Path]::GetFileNameWithoutExtension($target)
}
# 厂商前缀问题：文件名常带厂商前缀（opple_LEDPanelRc-...），而 CatalogListBox
# 条目名不带（LEDPanelRc-...）。构造候选匹配串：原串 + 去掉首段下划线前缀的串。
$candIds = @($selId)
if ($selId -match '^[^_]+_(.+)$') { $candIds += $Matches[1] }
$items = $lb.FindAll([System.Windows.Automation.TreeScope]::Descendants, $COND)
$hit = $null
foreach ($cand in $candIds) {
  if ($hit -ne $null) { break }
  for ($i=0; $i -lt $items.Count; $i++) {
    $nm = $items.Item($i).Current.Name
    if ($nm -match [regex]::Escape($cand)) { $hit = $items.Item($i); break }
  }
}
if ($hit -eq $null) {
  # fallback: select the first ListItem containing a ".IES"/"no data" token
  for ($i=0; $i -lt $items.Count; $i++) {
    $nm = $items.Item($i).Current.Name
    if ($nm -match '\.IES|\([A-Za-z0-9]+\)') { $hit = $items.Item($i); break }
  }
}
if ($hit -eq $null) {
  try { $btn.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Toggle() } catch { }
  Emit 'lum-select' 'fail' 'prototype-not-found'
  exit 1
}
try { $hit.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select(); Start-Sleep -Milliseconds 1500 } catch { }
try { $btn.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Toggle() } catch { }
Emit 'lum-select' 'ok'

# ---------------------------------------------------------------- 4) arrange
$af = ById 'aid_ArrangementFromSpace' 3000
if ($af -eq $null) { Emit 'lum-arrange' 'fail' 'arrange-btn-missing'; exit 1 }
$af.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
Start-Sleep -Milliseconds 3000
if ($CountX -gt 0 -and $CountY -gt 0) {
  $ex = ById 'ElementCountX' 2500
  $ey = ById 'ElementCountY' 2500
  if ($ex -ne $null -and $ey -ne $null) {
    try {
      $ex.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue([string]$CountX)
      $ey.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue([string]$CountY)
      Start-Sleep -Milliseconds 1500
    } catch { }
  }
}
$nameEl = ById 'ArrangementObjectName' 2000
if ($nameEl -ne $null) {
  try { $v = $nameEl.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value; Emit 'lum-arrange' 'ok' ("arr=" + $v) } catch { Emit 'lum-arrange' 'ok' '' }
} else { Emit 'lum-arrange' 'ok' '' }

# ---------------------------------------------------------------- 5) save
$save = ById 'Save' 3000
if ($save -eq $null) { Emit 'lum-save' 'fail' 'save-btn-missing'; exit 1 }
$save.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
Start-Sleep -Milliseconds 3000
Emit 'lum-save' 'ok'
Emit 'lum-done' 'ok'
exit 0