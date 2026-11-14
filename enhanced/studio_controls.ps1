param([long]$Window, [string]$Action, [string]$Control)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$root = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Window)
if (!$root -or (Get-Process -Id $root.Current.ProcessId).ProcessName -ne 'RobloxStudioBeta') {
    throw 'Studio window is unavailable'
}
$frame = $root.Current.BoundingRectangle
$controls = @()
$matches = @()
foreach ($item in $root.FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.Condition]::TrueCondition)) {
    try {
        $info = $item.Current
        $box = $info.BoundingRectangle
        if ((!$info.Name -and !$info.AutomationId) -or $info.IsOffscreen -or $box.IsEmpty -or $box.Top -gt $frame.Top + 210) { continue }
        $kind = $info.ControlType.ProgrammaticName
        if ($kind -notin @('ControlType.Button','ControlType.TabItem','ControlType.MenuItem','ControlType.CheckBox','ControlType.RadioButton')) { continue }
        $invoke = $null; $select = $null
        $invokable = $item.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)
        $selectable = $kind -eq 'ControlType.TabItem' -and $item.TryGetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern,[ref]$select)
        $record = @{name=$info.Name; id=$info.AutomationId; kind=$kind.Split('.')[-1]; enabled=$info.IsEnabled;
            actionable=($invokable -or $selectable); x=[int]($box.Left+$box.Width/2-$frame.Left);
            y=[int]($box.Top+$box.Height/2-$frame.Top)}
        if ($Action -eq 'list') { $controls += $record }
        elseif ($info.Name -ceq $Control -or ($info.AutomationId -and $info.AutomationId -ceq $Control)) {
            $matches += @{record=$record;invoke=$invoke;select=$select}
        }
    } catch [Windows.Automation.ElementNotAvailableException] { continue }
}
if ($Action -eq 'list') {
    @{controls=$controls; pid=$root.Current.ProcessId} | ConvertTo-Json -Depth 4 -Compress
    exit
}
if ($matches.Count -eq 0) { @{missing=$true; pid=$root.Current.ProcessId} | ConvertTo-Json -Compress; exit }
if ($matches.Count -ne 1) { throw "Expected one visible ribbon control named or identified by '$Control'; found $($matches.Count)" }
$match = $matches[0]
if ($Action -eq 'click') {
    if (!$match.record.enabled) { throw 'Ribbon control is disabled' }
    if ($match.invoke) { $match.invoke.Invoke() }
    elseif ($match.select) { $match.select.Select() }
    else { throw 'This Studio control does not expose an action through Windows accessibility' }
}
$match.record.pid = $root.Current.ProcessId
$match.record | ConvertTo-Json -Compress
