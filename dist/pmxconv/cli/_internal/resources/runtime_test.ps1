param([string]$GameRoot='F:\R5F\R5F', [string]$ReportRoot='outputs\runtime_initial', [int]$ClientTimeout=180)
$ErrorActionPreference='Stop'
function Read-LiveLog([string]$Path) {
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
    $reader=[IO.StreamReader]::new($stream)
    try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
}
$ReportRoot=$ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ReportRoot)
$scriptPath=Join-Path $GameRoot 'mods\R5F-PMXConv\scripts\vscripts\r5f_pmxconv\r5f_pmxconv__shared.gnut'
if (Get-Process r5apex,r5apex_dx12,r5apex_ds -ErrorAction SilentlyContinue) { throw 'Existing game process; test aborted without touching it.' }
if (Get-NetUDPEndpoint -LocalPort 37019 -ErrorAction SilentlyContinue) { throw 'Port 37019 occupied.' }
New-Item -ItemType Directory -Path $ReportRoot -Force | Out-Null
$original=[IO.File]::ReadAllBytes($scriptPath)
$before=@{}
foreach($realm in @('client','server')) { $before[$realm]=@(Get-ChildItem "$GameRoot\platform\logs\$realm" -Directory | ForEach-Object Name) }
$beforeScreens=@(Get-ChildItem "$GameRoot\r2\screenshots" -File -ErrorAction SilentlyContinue | ForEach-Object FullName)
$server=$null; $client=$null
$report=@{completed=$false; started=(Get-Date).ToString('o')}
try {
    $code=[Text.Encoding]::UTF8.GetString($original).Replace('PMXCONV_QA = false','PMXCONV_QA = true')
    [IO.File]::WriteAllText($scriptPath,$code,[Text.UTF8Encoding]::new($false))
    $serverArgs='-allowmultiple -dedicated -port 37019 -devsdk -dev +sv_allowSendTableTransmitToClients 1 +stringtable_compress 1 +net_usesocketsforloopback 1 +script_show_output 2 +script_show_warning 2 +spire_host_visibility 0 +sv_cheats 1 +sv_modPolicy 0 +launchplaylist survival_firingrange +map mp_rr_canyonlands_staging_mu1'
    $server=Start-Process "$GameRoot\r5apex_ds.exe" -ArgumentList $serverArgs -WorkingDirectory $GameRoot -WindowStyle Hidden -PassThru
    $report.server_pid=$server.Id
    Write-Output "Test server PID $($server.Id)"
    $deadline=(Get-Date).AddSeconds(80)
    do {
        Start-Sleep -Seconds 1
        $server.Refresh()
        if($server.HasExited) { throw 'Server exited during startup' }
        $serverLog=Get-ChildItem "$GameRoot\platform\logs\server" -Directory | Where-Object Name -NotIn $before.server | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        $ready=$serverLog -and (Test-Path "$($serverLog.FullName)\script.log") -and ((Read-LiveLog "$($serverLog.FullName)\script.log") -match '\[PMXCONV\] server ready')
        if($serverLog -and (Test-Path "$($serverLog.FullName)\error.log") -and ((Read-LiveLog "$($serverLog.FullName)\error.log") -match 'SCRIPT COMPILE ERROR|SCRIPT ERROR')) { throw 'Server script error' }
    } until($ready -or (Get-Date) -gt $deadline)
    if(!$ready) { throw 'Server readiness timed out' }
    $report.port_owner=@(Get-NetUDPEndpoint -LocalPort 37019 | ForEach-Object OwningProcess)
    if($server.Id -notin $report.port_owner) { throw 'Server port owner mismatch' }
    $report.server_command=(Get-CimInstance Win32_Process -Filter "ProcessId=$($server.Id)").CommandLine
    $clientArgs='-novid -nodiscord -allowmultiple -devsdk -dev -offline +cl_onlineAuthEnable 0 -language schinese +net_usesocketsforloopback 1 +script_show_output 2 +script_show_warning 2 -width 1280 -height 720 -window +connect 127.0.0.1:37019'
    $client=Start-Process "$GameRoot\r5apex.exe" -ArgumentList $clientArgs -WorkingDirectory $GameRoot -WindowStyle Hidden -PassThru
    $report.client_pid=$client.Id
    $report.client_command=(Get-CimInstance Win32_Process -Filter "ProcessId=$($client.Id)").CommandLine
    Write-Output "Test client PID $($client.Id)"
    $deadline=(Get-Date).AddSeconds($ClientTimeout)
    do {
        Start-Sleep -Seconds 1
        $client.Refresh()
        if($client.HasExited) { $report.client_exit=$client.ExitCode; throw 'Client exited' }
        $server.Refresh()
        if($server.HasExited) { throw 'Server exited during client QA' }
        if((Test-Path "$($serverLog.FullName)\error.log") -and ((Read-LiveLog "$($serverLog.FullName)\error.log") -match 'SCRIPT ERROR|SCRIPT COMPILE ERROR|There was a problem processing game logic')) { throw 'Server runtime error' }
        $clientLog=Get-ChildItem "$GameRoot\platform\logs\client" -Directory | Where-Object Name -NotIn $before.client | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if($clientLog -and (Test-Path "$($clientLog.FullName)\script.log")) {
            $text=Read-LiveLog "$($clientLog.FullName)\script.log"
            if($text -match '\[PMXCONV-QA\] COMPLETE') { $report.completed=$true; break }
            if($text -match '\[PMXCONV-QA\] FAIL|SCRIPT ERROR|SCRIPT COMPILE ERROR') { throw 'Client QA/script error' }
            if((Test-Path "$($clientLog.FullName)\error.log") -and ((Read-LiveLog "$($clientLog.FullName)\error.log") -match 'SCRIPT ERROR|SCRIPT COMPILE ERROR')) { throw 'Client script error' }
        }
    } until((Get-Date) -gt $deadline)
    if(!$report.completed) { $report.error='Client QA timed out' }
} catch { $report.error=$_.Exception.Message }
finally {
    foreach($process in @($client,$server)) { if($process) { $process.Refresh(); if(!$process.HasExited) { Stop-Process -Id $process.Id -Force }; $process.WaitForExit(10000) | Out-Null } }
    [IO.File]::WriteAllBytes($scriptPath,$original)
    foreach($realm in @('client','server')) {
        $log=Get-ChildItem "$GameRoot\platform\logs\$realm" -Directory | Where-Object Name -NotIn $before[$realm] | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if($log) { $report["${realm}_log"]=$log.FullName; New-Item -ItemType Directory "$ReportRoot\$realm" -Force | Out-Null; Get-ChildItem $log.FullName -File | Where-Object Extension -In @('.log','.txt') | Copy-Item -Destination "$ReportRoot\$realm" }
    }
    $screens=@(Get-ChildItem "$GameRoot\r2\screenshots" -File -ErrorAction SilentlyContinue | Where-Object FullName -NotIn $beforeScreens)
    if($screens.Count) { New-Item -ItemType Directory "$ReportRoot\screenshots" -Force | Out-Null; $screens | Copy-Item -Destination "$ReportRoot\screenshots" }
    $report.screenshots=@($screens | ForEach-Object Name)
    $report | ConvertTo-Json -Depth 5 | Set-Content "$ReportRoot\report.json" -Encoding utf8
    $report | ConvertTo-Json -Depth 5 | Write-Output
}
