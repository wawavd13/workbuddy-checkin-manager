param([string]$RequestFile, [string]$DataDir)
# UTF-8 BOM is intentional: Windows PowerShell 5.1 must preserve Chinese paths.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8
$OutputEncoding = $Utf8
$Root = $PSScriptRoot
$Names = @('WorkBuddyAutoSignin', 'WorkBuddyGrowthPoll')
$Runner = Join-Path $Root 'runner.py'
$Upstream = Join-Path $env:USERPROFILE 'Tools\workbuddy-auto-signin\signin.py'
$Pythonw = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe'
$System32 = Join-Path $env:SystemRoot 'System32'
$BackupRoot = Join-Path $Root 'backups'
$Original = Join-Path $BackupRoot 'original'
# Worker defaults cover the source layout; a packaged exe sends its own definition in the request.
# $Runner is read at call time: the test harness reassigns it to simulate registered tasks.
$WorkerProgram = $Pythonw
$WorkerArguments = $null

function Get-WorkerArgument([string]$Mode) {
    if ($WorkerProgram -ieq $Pythonw) { return ('-I "{0}" --mode {1}' -f $Runner, $Mode) }
    return [string]$WorkerArguments[$Mode]
}

function Set-WorkerFromRequest($Request) {
    if (-not $Request.worker) { return }
    $program = [string]$Request.worker.program
    $daily = [string]$Request.worker.arguments.daily
    $poll = [string]$Request.worker.arguments.poll
    if (-not $program -or -not $daily -or -not $poll) { throw 'worker 定义必须包含 program 与 daily/poll 参数。' }
    if ($program -cne $Pythonw) {
        foreach ($value in @($daily, $poll)) {
            if ($value -cnotmatch '^--mode\s+(daily|poll)$') { throw 'worker 参数必须是 --mode daily 或 --mode poll。' }
        }
    }
    $script:WorkerProgram = $program
    $script:WorkerArguments = @{ daily = $daily; poll = $poll }
}

function Assert-Name([string]$Name) {
    if ($Name -cnotin $Names) { throw '拒绝访问非目标任务。' }
}
function Read-Task([string]$Name) {
    Assert-Name $Name
    try { $tasks = @(Get-ScheduledTask -TaskName $Name -TaskPath '\' -ErrorAction Stop) }
    catch {
        # Only explicit not-found is absence; permission/provider errors remain errors.
        if ($_.FullyQualifiedErrorId -like '*NoMatching*' -or
            $_.Exception.HResult -eq -2147024894 -or
            $_.Exception.HResult -eq -2147216625) { return $null }
        throw
    }
    if ($tasks.Count -eq 0 -or ($tasks.Count -eq 1 -and $null -eq $tasks[0])) { return $null }
    if ($tasks.Count -ne 1 -or $tasks[0].TaskPath -ne '\' -or $tasks[0].TaskName -cne $Name) {
        throw "任务身份不匹配：$Name"
    }
    return $tasks[0]
}
function Test-Action($Actions) {
    $items = @($Actions)
    if ($items.Count -ne 1) { return $false }
    $action = $items[0]
    $execute = [string]$action.Execute
    $arguments = [string]$action.Arguments
    $isPython = ($execute -ieq $Pythonw -or $execute -ieq ($Pythonw -replace 'pythonw.exe$', 'python.exe'))
    $originalPattern = '^(?:-I\s+)?(?:"' + [regex]::Escape($Upstream) + '"|' + [regex]::Escape($Upstream) + ')\s+(silent|silent-poll|silent-growth)$'
    if ($isPython -and $arguments -imatch $originalPattern) { return $true }
    if ($execute -ieq $WorkerProgram -and $arguments -imatch '^--mode\s+(daily|poll)$') { return $true }
    if ($isPython) {
        $runnerPattern = '^-I\s+(?:"' + [regex]::Escape($Runner) + '"|' + [regex]::Escape($Runner) + ')\s+--mode\s+(daily|poll)$'
        return [bool]($arguments -imatch $runnerPattern)
    }
    return $false
}
function Test-Runner($Actions) {
    if (-not (Test-Action $Actions)) { return $false }
    $action = @($Actions)[0]
    $arguments = [string]$action.Arguments
    if ([string]$action.Execute -ieq $WorkerProgram -and $arguments -imatch '^--mode\s+(daily|poll)$') { return $true }
    if ([string]$action.Execute -ieq $Pythonw) {
        $pattern = '^-I\s+(?:"' + [regex]::Escape($Runner) + '"|' + [regex]::Escape($Runner) + ')\s+--mode\s+(daily|poll)$'
        return [bool]($arguments -imatch $pattern)
    }
    return $false
}
function Assert-Managed($Task) {
    if ($null -ne $Task -and -not (Test-Action $Task.Actions)) {
        throw "拒绝覆盖意外同名任务：$($Task.TaskName)；动作不是允许的上游 signin.py 或 runner.py。"
    }
}
function Format-Date($Value) {
    if ($null -eq $Value -or ([datetime]$Value).Year -lt 2000) { return $null }
    return ([datetime]$Value).ToString('yyyy-MM-dd HH:mm:ss')
}
function Query-Tasks {
    $rows = @()
    foreach ($name in $Names) {
        $row = @{ name=$name; state='unknown'; enabled=$null; last_run=$null;
            last_result=$null; next_run=$null; times=@(); managed=$false }
        try {
            $task = Read-Task $name
            if ($null -eq $task) { $row.state='missing'; $row.error='任务不存在。' }
            else {
                $row.enabled = [bool]$task.Settings.Enabled
                $row.recognized = [bool](Test-Action $task.Actions)
                $row.managed = [bool](Test-Runner $task.Actions)
                $row.times = @(foreach ($trigger in @($task.Triggers)) {
                    if ($trigger.StartBoundary) {
                        $time = ([datetime]$trigger.StartBoundary).ToString('HH:mm')
                        $repeat = $null
                        if ($trigger.Repetition) { $repeat = [string]$trigger.Repetition.Interval }
                        if ($repeat) { "$time (repeat $repeat)" } else { $time }
                    } else { '非定时触发器' }
                }) | Sort-Object -Unique
                $row.times = @($row.times)
                $info = Get-ScheduledTaskInfo -TaskName $name -TaskPath '\' -ErrorAction Stop
                if ($null -eq $info) { throw '未读取到运行信息。' }
                $row.last_run = Format-Date $info.LastRunTime
                $row.last_result = [long]$info.LastTaskResult
                $row.next_run = Format-Date $info.NextRunTime
                $row.state = ([string]$task.State).ToLowerInvariant()
                if (-not $row.recognized) { $row.error='意外同名任务：管理写操作将被拒绝。' }
            }
        } catch { $row.state='unknown'; $row.error=$_.Exception.Message }
        $rows += $row
    }
    return ,$rows
}
function Write-JsonFile([string]$Path, $Value) {
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 30), $Utf8)
}
function Take-Snapshot {
    $rows = @()
    foreach ($name in $Names) {
        $task = Read-Task $name
        Assert-Managed $task
        $xml = $null
        if ($null -ne $task) {
            $xml = Export-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction Stop
            if (-not $xml) { throw "无法备份任务：$name" }
        }
        $rows += @{ name=$name; exists=($null -ne $task); enabled=if ($task) { [bool]$task.Settings.Enabled } else { $null }; xml=$xml }
    }
    return ,$rows
}
function Save-Snapshot($Snapshot, [string]$Destination) {
    # Destinations are always new, never reuse or overwrite an original backup.
    if (Test-Path -LiteralPath $Destination) { throw "备份目录已存在，拒绝覆盖：$Destination" }
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    $manifest = @()
    foreach ($row in $Snapshot) {
        if ($row.exists) { [IO.File]::WriteAllText((Join-Path $Destination ($row.name + '.xml')), $row.xml, $Utf8) }
        $manifest += @{ name=$row.name; exists=[bool]$row.exists; enabled=$row.enabled }
    }
    Write-JsonFile (Join-Path $Destination 'manifest.json') @{ version=1; tasks=$manifest }
}
function Load-Snapshot([string]$Directory) {
    $manifest = [IO.File]::ReadAllText((Join-Path $Directory 'manifest.json'), $Utf8) | ConvertFrom-Json
    if ($manifest.version -ne 1 -or @($manifest.tasks).Count -ne 2) { throw '原始备份清单无效。' }
    $seen = @(); $rows = @()
    foreach ($row in $manifest.tasks) {
        Assert-Name $row.name
        if ($row.name -cin $seen -or $row.exists -isnot [bool]) { throw '原始备份身份无效。' }
        $seen += $row.name
        $xml = $null
        if ($row.exists) {
            if ($row.enabled -isnot [bool]) { throw '原始备份启用状态无效。' }
            $xml = [IO.File]::ReadAllText((Join-Path $Directory ($row.name + '.xml')), $Utf8)
            $settings = New-Object System.Xml.XmlReaderSettings
            $settings.DtdProcessing = [System.Xml.DtdProcessing]::Prohibit
            $settings.XmlResolver = $null
            $reader = [System.Xml.XmlReader]::Create((New-Object IO.StringReader($xml)), $settings)
            try { $doc = New-Object System.Xml.XmlDocument; $doc.XmlResolver=$null; $doc.Load($reader) } finally { $reader.Dispose() }
            $actions = @($doc.SelectNodes("/*[local-name()='Task']/*[local-name()='Actions']/*"))
            if ($actions.Count -ne 1 -or $actions[0].LocalName -ne 'Exec' -or
                -not (Test-Action @(@{Execute=$actions[0].Command; Arguments=$actions[0].Arguments}))) {
                throw '原始备份包含不允许的任务动作。'
            }
        }
        $rows += @{name=$row.name; exists=$row.exists; enabled=$row.enabled; xml=$xml}
    }
    return ,$rows
}
function Restore-Snapshot($Snapshot) {
    $errors = @()
    foreach ($row in $Snapshot) {
        try {
            if ($row.exists) {
                Register-ScheduledTask -TaskName $row.name -TaskPath '\' -Xml $row.xml -Force -ErrorAction Stop | Out-Null
                if ($row.enabled) { Enable-ScheduledTask -TaskName $row.name -TaskPath '\' -ErrorAction Stop | Out-Null }
                else { Disable-ScheduledTask -TaskName $row.name -TaskPath '\' -ErrorAction Stop | Out-Null }
            } elseif ($null -ne (Read-Task $row.name)) {
                Unregister-ScheduledTask -TaskName $row.name -TaskPath '\' -Confirm:$false -ErrorAction Stop
            }
        } catch { $errors += "$($row.name): $($_.Exception.Message)" }
    }
    return $errors
}
function Assert-Times($Request) {
    $all = @()
    foreach ($name in $Names) {
        $values = @($Request.times.$name)
        if ($name -eq $Names[0] -and $values.Count -eq 0) { throw '签到时间不能为空。' }
        foreach ($time in $values) {
            if ($time -isnot [string] -or $time -cnotmatch '^(?:[01][0-9]|2[0-3]):[0-5][0-9]$' -or $time -cin $all) { throw '时间无效或重复。' }
            $all += $time
        }
    }
    if ($all.Count -ge 97) { throw '两个任务总触发器数必须小于97。' }
    foreach ($key in @('catch_up','allow_battery')) {
        if ($Request.config.$key -isnot [bool]) { throw "配置字段必须为布尔值：$key" }
    }
}
function Apply-Tasks($Request, $Before) {
    Assert-Times $Request
    if (-not (Test-Path -LiteralPath $WorkerProgram -PathType Leaf)) { throw "找不到 worker 程序：$WorkerProgram" }
    if ($WorkerProgram -ieq $Pythonw -and -not (Test-Path -LiteralPath $Runner -PathType Leaf)) { throw '找不到 runner.py。' }
    $userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited -ErrorAction Stop
    $settingsArgs = @{StartWhenAvailable=[bool]$Request.config.catch_up; WakeToRun=$false; Hidden=$true;
        AllowStartIfOnBatteries=[bool]$Request.config.allow_battery;
        DontStopIfGoingOnBatteries=[bool]$Request.config.allow_battery;
        MultipleInstances='IgnoreNew'; ExecutionTimeLimit=(New-TimeSpan -Minutes 10); ErrorAction='Stop'}
    $settings = New-ScheduledTaskSettingsSet @settingsArgs
    foreach ($name in $Names) {
        $mode = if ($name -eq $Names[0]) { 'daily' } else { 'poll' }
        $action = New-ScheduledTaskAction -Execute $WorkerProgram -Argument (Get-WorkerArgument $mode) -WorkingDirectory $System32 -ErrorAction Stop
        $triggers = @(foreach ($time in @($Request.times.$name)) { New-ScheduledTaskTrigger -Daily -At $time -ErrorAction Stop })
        $taskArgs = @{Action=$action; Settings=$settings; Principal=$principal; Description='WorkBuddy check-in manager'; ErrorAction='Stop'}
        if ($triggers.Count) { $taskArgs.Trigger=$triggers }
        $definition = New-ScheduledTask @taskArgs
        Register-ScheduledTask -TaskName $name -TaskPath '\' -InputObject $definition -Force -ErrorAction Stop | Out-Null
        $previous = @($Before | Where-Object { $_.name -ceq $name })[0]
        if ($previous.exists -and -not $previous.enabled) { Disable-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction Stop | Out-Null }
        $registered = Read-Task $name
        if ($null -eq $registered) { throw "注册后任务不存在：$name" }
        Assert-Managed $registered
    }
}
function Invoke-Transaction($Request) {
    $before = Take-Snapshot
    if ($Request.operation -eq 'apply') { Assert-Times $Request }
    if ($Request.operation -eq 'enable' -and ($Request.enabled -isnot [bool] -or @($before | Where-Object { -not $_.exists }).Count)) { throw '启用状态必须为布尔值且两个任务均须存在。' }
    $restore = $null
    if ($Request.operation -eq 'restore') { $restore = Load-Snapshot $Original }
    [IO.Directory]::CreateDirectory($BackupRoot) | Out-Null
    if (-not (Test-Path -LiteralPath $Original)) {
        $staging = Join-Path $BackupRoot ('original-' + [guid]::NewGuid().ToString('N'))
        Save-Snapshot $before $staging
        [IO.Directory]::Move($staging, $Original)
    } else { Load-Snapshot $Original | Out-Null }
    $transaction = Join-Path $BackupRoot ('transaction-' + [guid]::NewGuid().ToString('N'))
    Save-Snapshot $before $transaction
    try {
        switch ($Request.operation) {
            'apply' { Apply-Tasks $Request $before }
            'enable' {
                foreach ($name in $Names) {
                    if ($Request.enabled) { Enable-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction Stop | Out-Null }
                    else { Disable-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction Stop | Out-Null }
                    $task = Read-Task $name
                    if ($null -eq $task -or [bool]$task.Settings.Enabled -ne $Request.enabled) { throw "启用状态校验失败：$name" }
                }
            }
            'restore' { $failures = @(Restore-Snapshot $restore); if ($failures.Count) { throw ($failures -join '; ') } }
            default { throw '不允许的写操作。' }
        }
        return @{ok=$true; transaction_backup=$transaction; original_backup=$Original; tasks=(Query-Tasks)}
    } catch {
        $message = $_.Exception.Message
        $rollback = @(Restore-Snapshot $before)
        return @{ok=$false; error=$message; rolled_back=($rollback.Count -eq 0); rollback_errors=$rollback; transaction_backup=$transaction}
    }
}
function Invoke-Request($Request) {
    switch ($Request.operation) {
        'query' { return @{ok=$true; tasks=(Query-Tasks)} }
        'backup_exists' {
            if (-not (Test-Path -LiteralPath $Original)) { return @{ok=$true; exists=$false} }
            Load-Snapshot $Original | Out-Null
            return @{ok=$true; exists=$true}
        }
        { $_ -in @('apply','enable','restore') } { return Invoke-Transaction $Request }
        default { throw '不允许的操作。' }
    }
}
if ($MyInvocation.InvocationName -ne '.') {
    $mutex = $null; $locked = $false
    try {
        if ($RequestFile) {
            $allowed = @(((Join-Path $Root 'data') + '\'))
            if ($DataDir) { $allowed += (([IO.Path]::GetFullPath($DataDir).TrimEnd('\')) + '\') }
            $path = [IO.Path]::GetFullPath($RequestFile)
            $inside = @($allowed | Where-Object { $path.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) }).Count
            if (-not $inside) { throw '请求文件必须位于管理器 data 目录。' }
            $raw = [IO.File]::ReadAllText($path, $Utf8)
        } else { $raw = [Console]::In.ReadToEnd() }
        if ($raw.Length -gt 65536) { throw '请求过大。' }
        $request = $raw | ConvertFrom-Json
        Set-WorkerFromRequest $request
        $mutex = New-Object Threading.Mutex($false, 'Local\WorkBuddyCheckinManagerScheduler')
        try { $locked = $mutex.WaitOne(5000) } catch [Threading.AbandonedMutexException] { $locked=$true }
        if (-not $locked) { throw '另一调度操作正在执行，请稍后重试。' }
        $result = Invoke-Request $request
    } catch {
        $message = $_.Exception.Message
        if ($message -match 'denied|拒绝访问|0x80070005') { $message = "权限不足（拒绝访问），未自动提权：$message" }
        $result = @{ok=$false; error=$message}
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        if ($null -ne $mutex) { $mutex.Dispose() }
    }
    [Console]::WriteLine(($result | ConvertTo-Json -Depth 30 -Compress))
    if (-not $result.ok) { exit 1 }
}
