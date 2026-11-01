"""Pure Python tests plus PowerShell in-memory task mocks; never touches real tasks."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

PATH = Path(__file__).resolve().parents[1] / "scheduler.py"
sys.path.insert(0, str(PATH.parent))
SPEC = importlib.util.spec_from_file_location("scheduler_under_test", PATH)
scheduler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scheduler)


class SchedulerTests(unittest.TestCase):
    def test_defaults_and_grid(self):
        times = scheduler.build_schedule({})
        self.assertEqual(times[scheduler.TASK_NAMES[0]], ["00:05"])
        poll = times[scheduler.TASK_NAMES[1]]
        self.assertEqual(len(poll), 29)
        self.assertIn("01:00", poll)
        self.assertIn("01:05", poll)
        self.assertIn("23:05", poll)
        self.assertNotIn("00:05", poll)
        self.assertEqual(poll, sorted(set(poll)))

    def test_first_daily_time_and_dedup(self):
        cfg = {"signin_times": ["10:00", "00:05", "00:05"],
               "growth_times": ["00:05", "10:00", "01:05", "01:05"]}
        result = scheduler.build_schedule(cfg)
        self.assertEqual(result[scheduler.TASK_NAMES[0]], ["00:05", "10:00"])
        self.assertNotIn("10:00", result[scheduler.TASK_NAMES[1]])
        self.assertEqual(cfg["signin_times"], ["10:00", "00:05", "00:05"])

    def test_off_and_end_of_day(self):
        config = {"signin_times": ["23:59"], "growth": {"enabled": False}}
        self.assertEqual(scheduler.build_schedule(config)[scheduler.TASK_NAMES[1]], [])
        config = {"retry_enabled": False, "growth": {"enabled": False}}
        self.assertEqual(scheduler.build_schedule(config)[scheduler.TASK_NAMES[1]], [])
        grid = scheduler.build_schedule({"signin_times": ["00:00"], "retry_interval_minutes": 15,
                                         "growth": {"enabled": False}})
        self.assertEqual(len(grid[scheduler.TASK_NAMES[1]]), 95)
        self.assertEqual(grid[scheduler.TASK_NAMES[1]][-1], "23:45")

    def test_validation_without_launch(self):
        bad = [{"signin_times": []}, {"signin_times": ["24:00"]},
               {"signin_times": ["0:05"]}, {"signin_times": ["00:05;calc"]},
               {"retry_interval_minutes": 14}, {"retry_interval_minutes": 241},
               {"retry_interval_minutes": True}, {"retry_max_per_day": 0},
               {"catch_up": 1}, {"growth": {"enabled": "true"}},
               {"growth": {"token": "hidden"}}, {"password": "hidden"},
               {"signin_times": ["00:00"], "growth_times": ["00:01"], "retry_interval_minutes": 15}]
        with patch.object(scheduler, "_call") as call:
            for config in bad:
                with self.subTest(config=config):
                    self.assertFalse(scheduler.apply_config(config)["ok"])
            self.assertFalse(scheduler.set_enabled(1)["ok"])
            call.assert_not_called()

    def test_public_api(self):
        with patch.object(scheduler, "_call", return_value={"ok": True, "exists": True}) as call:
            self.assertTrue(scheduler.backup_exists())
            self.assertTrue(scheduler.apply_config({})["ok"])
            self.assertEqual(call.call_args.args, ("apply",))
            self.assertEqual(call.call_args.kwargs["config"]["retry_max_per_day"], 7)
            scheduler.set_enabled(False)
            call.assert_called_with("enable", enabled=False)
            scheduler.restore_original()
            call.assert_called_with("restore")
        for key in ("ROOT", "PS_SCRIPT", "PYTHONW", "RUNNER", "CONFIG_PATH", "ORIGINAL_BACKUP"):
            self.assertIsInstance(getattr(scheduler, key), str)

    def test_worker_definition_freezes_to_exe(self):
        definition = scheduler.worker_definition()
        self.assertEqual(definition["program"], scheduler.PYTHONW)
        self.assertIn("--mode daily", definition["arguments"]["daily"])
        self.assertIn(scheduler.RUNNER, definition["arguments"]["poll"])
        with patch.object(scheduler.storage, "FROZEN", True), \
             patch.object(scheduler.sys, "executable", "C:/apps/WorkBuddyManager.exe"):
            definition = scheduler.worker_definition()
        self.assertEqual(definition["program"], "C:/apps/WorkBuddyManager.exe")
        self.assertEqual(definition["arguments"], {"daily": "--mode daily", "poll": "--mode poll"})

    def test_query_error_is_unknown(self):
        with patch.object(scheduler, "_call", return_value={"ok": False, "error": "Access denied"}):
            tasks = scheduler.query_tasks()
        self.assertEqual([r["name"] for r in tasks], list(scheduler.TASK_NAMES))
        self.assertTrue(all(r["state"] == "unknown" and r["enabled"] is None for r in tasks))
        self.assertTrue(all(r["error"] == "Access denied" for r in tasks))
        json.dumps(tasks)

    def test_process_arguments_encoding_and_cleanup(self):
        with tempfile.TemporaryDirectory() as root:
            def fake_run(command, **kwargs):
                self.assertEqual(command[0], scheduler.POWERSHELL)
                self.assertEqual(command[command.index("-File") + 1], scheduler.PS_SCRIPT)
                request_path = Path(command[command.index("-RequestFile") + 1])
                self.assertEqual(Path(command[command.index("-DataDir") + 1]), Path(root) / "data")
                self.assertEqual(request_path.parent, Path(root) / "data")
                request = json.loads(request_path.read_text(encoding="utf-8"))
                self.assertEqual(request["operation"], "apply")
                self.assertEqual(request["config"]["signin_times"], ["00:05"])
                self.assertFalse(kwargs["shell"])
                self.assertEqual(kwargs["stdout"], subprocess.PIPE)
                self.assertEqual(kwargs["encoding"], "utf-8")
                self.assertEqual(kwargs["cwd"], scheduler.SYSTEM32)
                process = MagicMock(returncode=0)
                process.communicate.return_value = ('\ufeff{"ok":true}', "")
                return process
            with patch.object(scheduler.storage, "BASE_DIR", Path(root)), patch.object(scheduler.subprocess, "Popen", side_effect=fake_run):
                self.assertTrue(scheduler.apply_config({})["ok"])
            self.assertEqual(list((Path(root) / "data").iterdir()), [])
            self.assertFalse((Path(root) / "data" / "config.json").exists())

    def test_timeout_and_invalid_json(self):
        with tempfile.TemporaryDirectory() as root, patch.object(scheduler.storage, "BASE_DIR", Path(root)):
            process = MagicMock(returncode=0)
            process.communicate.side_effect = [subprocess.TimeoutExpired("ps", 30), ('{"ok":true}', '')]
            with patch.object(scheduler.subprocess, "Popen", return_value=process):
                self.assertTrue(scheduler.restore_original()["completion_unknown"])
                process.kill.assert_not_called()
                process.terminate.assert_not_called()
                self.assertEqual(process.communicate.call_args_list[0].kwargs, {"timeout": 30})
            process = MagicMock(returncode=1)
            process.communicate.return_value = ("bad", "denied")
            with patch.object(scheduler.subprocess, "Popen", return_value=process):
                self.assertFalse(scheduler.backup_exists())
            self.assertEqual(list((Path(root) / "data").iterdir()), [])


# Every ScheduledTasks command used below is shadowed before invoking production
# functions. Dot-sourcing only defines functions; it does not invoke any task API.
HARNESS = r'''
. $env:TEST_SCHEDULER_PS
$Root=$env:TEST_SCHEDULER_ROOT; $BackupRoot=Join-Path $Root 'backups'; $Original=Join-Path $BackupRoot 'original'
$script:Db=@{}; $script:Writes=0; $script:FailName=''; $script:FailEnable=''
function Make-Task($name,$enabled=$true,$arguments=$null) {
    if (-not $arguments) { $arguments='"'+$Upstream+'" silent' }
    return [pscustomobject]@{TaskName=$name;TaskPath='\';State='Ready';Settings=[pscustomobject]@{Enabled=$enabled};
        Actions=@([pscustomobject]@{Execute=$Pythonw;Arguments=$arguments});Triggers=@([pscustomobject]@{StartBoundary='2026-10-30T00:05:00'})}
}
foreach ($name in $Names) { $script:Db[$name]=Make-Task $name }
function Get-ScheduledTask($TaskName,$TaskPath,$ErrorAction) { return $script:Db[$TaskName] }
function Get-ScheduledTaskInfo($TaskName,$TaskPath,$ErrorAction) {
    return [pscustomobject]@{LastRunTime=[datetime]'2026-10-30T00:05:03';LastTaskResult=0;NextRunTime=[datetime]'2026-10-31T00:05:00'}
}
function Export-ScheduledTask($TaskName,$TaskPath,$ErrorAction) {
    return '<Task><Actions><Exec><Command>'+[Security.SecurityElement]::Escape($Pythonw)+'</Command><Arguments>'+[Security.SecurityElement]::Escape($script:Db[$TaskName].Actions[0].Arguments)+'</Arguments></Exec></Actions></Task>'
}
function Register-ScheduledTask($TaskName,$TaskPath,$Xml,$InputObject,[switch]$Force,$ErrorAction) {
    $script:Writes++; if ($TaskName -eq $script:FailName) { $script:FailName=''; throw 'mock register failure' }
    $script:Db[$TaskName]=Make-Task $TaskName
    if ($InputObject) { $script:Db[$TaskName].Actions=@($InputObject.Action) }
}
function Enable-ScheduledTask($TaskName,$TaskPath,$ErrorAction) {
    $script:Writes++; if ($TaskName -eq $script:FailEnable) { $script:FailEnable=''; throw 'mock enable denied' }
    $script:Db[$TaskName].Settings.Enabled=$true
}
function Disable-ScheduledTask($TaskName,$TaskPath,$ErrorAction) { $script:Writes++; $script:Db[$TaskName].Settings.Enabled=$false }
function Unregister-ScheduledTask($TaskName,$TaskPath,$Confirm,$ErrorAction) { $script:Writes++; $script:Db.Remove($TaskName) }
function New-ScheduledTaskPrincipal($UserId,$LogonType,$RunLevel,$ErrorAction) {
    if ($LogonType -ne 'Interactive' -or $RunLevel -ne 'Limited' -or -not $UserId) { throw 'bad principal' }; return @{}
}
function New-ScheduledTaskSettingsSet($StartWhenAvailable,$WakeToRun,$Hidden,$AllowStartIfOnBatteries,$DontStopIfGoingOnBatteries,$MultipleInstances,$ExecutionTimeLimit,$ErrorAction) {
    if ($WakeToRun -or -not $StartWhenAvailable -or -not $AllowStartIfOnBatteries -or -not $DontStopIfGoingOnBatteries) { throw 'bad settings' }; return @{}
}
function New-ScheduledTaskAction($Execute,$Argument,$WorkingDirectory,$ErrorAction) {
    if ($WorkingDirectory -ne $System32 -or
        ($Argument -cnotmatch '^-I ".+" --mode (daily|poll)$' -and $Argument -cnotmatch '^--mode (daily|poll)$')) { throw 'bad action' }
    return [pscustomobject]@{Execute=$Execute;Arguments=$Argument}
}
function New-ScheduledTaskTrigger([switch]$Daily,$At,$ErrorAction) { return $At }
function New-ScheduledTask($Action,$Settings,$Principal,$Description,$Trigger,$ErrorAction) { return @{Action=$Action} }
function Assert($condition,$message) { if (-not $condition) { throw $message } }
$req=[pscustomobject]@{operation='enable';enabled=$false}
switch ($env:TEST_SCENARIO) {
    'query' {
        $rows=Query-Tasks; Assert ($rows.Count -eq 2) 'rows'; Assert ($rows[0].last_run -eq '2026-10-30 00:05:03') 'date'
        Assert ($rows[0].recognized -and -not $rows[0].managed -and -not $rows[0].error) 'upstream label'
        $script:Db[$Names[0]].Actions[0].Arguments='-I "'+$Runner+'" --mode daily'
        $rows=Query-Tasks; Assert $rows[0].managed 'runner label'
        Assert ($script:Writes -eq 0) 'query wrote'; $script:Db.Remove($Names[1]); $rows=Query-Tasks; Assert ($rows[1].state -eq 'missing' -and $rows[1].error) 'missing'
        function Get-ScheduledTask($TaskName,$TaskPath,$ErrorAction) { throw 'Access denied' }
        $rows=Query-Tasks; Assert ($rows[0].state -eq 'unknown' -and $rows[0].enabled -eq $null) 'denied'
    }
    'guards' {
        $script:Db[$Names[0]].Actions[0].Arguments='"C:\unexpected.py" silent'
        try { Invoke-Transaction $req; throw 'guard missed' } catch { Assert ($_.Exception.Message -match '意外同名') 'guard error' }
        Assert ($script:Writes -eq 0) 'guard wrote'; Assert (-not (Test-Path $Original)) 'guard backed up'
        Assert (-not (Test-Action @(@{Execute=$Pythonw;Arguments=('"'+$Upstream+'" silent & calc')}))) 'injection guard'
        try { Read-Task 'OtherTask'; throw 'name missed' } catch { Assert ($_.Exception.Message -match '非目标') 'name guard' }
    }
    'enable_restore' {
        $result=Invoke-Transaction $req; Assert $result.ok 'enable failed'; Assert (-not $script:Db[$Names[0]].Settings.Enabled) 'not disabled'
        $first=[IO.File]::ReadAllText((Join-Path $Original 'manifest.json'))
        $req.enabled=$true; $result=Invoke-Transaction $req; Assert $result.ok 'reenable failed'
        Assert ($first -ceq [IO.File]::ReadAllText((Join-Path $Original 'manifest.json'))) 'original overwritten'
        $req.operation='restore'; $result=Invoke-Transaction $req; Assert $result.ok 'restore failed'
        Assert $script:Db[$Names[1]].Settings.Enabled 'restore state'
        Assert (@(Get-ChildItem $BackupRoot -Directory -Filter 'transaction-*').Count -eq 3) 'transaction count'
    }
    'rollback' {
        $script:Db[$Names[0]].Settings.Enabled=$false; $req.enabled=$true; $script:FailEnable=$Names[1]
        $result=Invoke-Transaction $req; Assert (-not $result.ok -and $result.rolled_back) 'rollback result'
        Assert (-not $script:Db[$Names[0]].Settings.Enabled -and $script:Db[$Names[1]].Settings.Enabled) 'rollback states'
        $req.operation='restore'; $script:FailName=$Names[1]; $result=Invoke-Transaction $req
        Assert (-not $result.ok -and $result.rolled_back) 'restore rollback'
    }
    'apply_exe' {
        $exe=Join-Path $Root 'WorkBuddyManager.exe'; [IO.File]::WriteAllText($exe,'x')
        $req=[pscustomobject]@{operation='apply';worker=[pscustomobject]@{program=$exe;arguments=[pscustomobject]@{daily='--mode daily';poll='--mode poll'}};
            config=[pscustomobject]@{catch_up=$true;allow_battery=$true};
            times=[pscustomobject]@{WorkBuddyAutoSignin=@('00:05');WorkBuddyGrowthPoll=@('01:00')}}
        Set-WorkerFromRequest $req
        $result=Invoke-Transaction $req; Assert $result.ok 'apply exe failed'
        Assert ($script:Db[$Names[0]].Actions[0].Execute -ieq $exe) 'exe program'
        Assert ($script:Db[$Names[0]].Actions[0].Arguments -ceq '--mode daily') 'exe arguments'
        $rows=Query-Tasks; Assert ($rows[0].managed -and $rows[1].managed) 'exe tasks managed'
    }
    'apply' {
        $Runner=$env:TEST_SCHEDULER_PS
        $req=[pscustomobject]@{operation='apply';config=[pscustomobject]@{catch_up=$true;allow_battery=$true};
            times=[pscustomobject]@{WorkBuddyAutoSignin=@('00:05');WorkBuddyGrowthPoll=@('01:00','01:05')}}
        $script:Db[$Names[0]].Settings.Enabled=$false
        $script:FailName=$Names[1]; $result=Invoke-Transaction $req; Assert (-not $result.ok -and $result.rolled_back) ('apply rollback: '+($result | ConvertTo-Json -Depth 10 -Compress))
        Assert (-not $script:Db[$Names[0]].Settings.Enabled) 'disabled not restored'
        $result=Invoke-Transaction $req; Assert $result.ok 'apply success'; Assert (-not $script:Db[$Names[0]].Settings.Enabled) 'disabled not preserved'
        Assert ($script:Db[$Names[1]].Actions[0].Arguments -match '--mode poll$') 'poll args'
    }
}
[Console]::WriteLine('{"ok":true}')
'''


@unittest.skipUnless(os.name == "nt", "PowerShell task mocks require Windows")
class PowerShellMockTests(unittest.TestCase):
    def test_bom_and_mock_operations(self):
        ps = PATH.with_suffix(".ps1")
        self.assertTrue(ps.read_bytes().startswith(b"\xef\xbb\xbf"))
        for scenario in ("query", "guards", "enable_restore", "rollback", "apply", "apply_exe"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as root:
                env = dict(os.environ, TEST_SCHEDULER_PS=str(ps), TEST_SCHEDULER_ROOT=root, TEST_SCENARIO=scenario)
                result = subprocess.run([scheduler.POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", HARNESS],
                                        cwd=scheduler.SYSTEM32, env=env, capture_output=True, encoding="utf-8", timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('"ok":true', result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
