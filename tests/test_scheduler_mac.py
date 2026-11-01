"""launchd backend tests; launchctl is stubbed, the real binary is never called."""
from datetime import datetime
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scheduler
import scheduler_mac
import storage

WORKER = {"program": "/Applications/WorkBuddyManager.app/Contents/MacOS/WorkBuddyManager",
          "arguments": {"daily": "--mode daily", "poll": "--mode poll"}}
TIMES = {"WorkBuddyAutoSignin": ["00:05"],
         "WorkBuddyGrowthPoll": ["01:00", "13:00"]}

PRINT_LOADED = "state = waiting\n\tpid = 0\n\tlast exit code = 0\n\truns = 3\n"
PRINT_CRASHED = "state = waiting\n\tlast exit code = 78\n"


class FakeLaunchctl:
    def __init__(self, print_result=(0, PRINT_LOADED), bootstrap_result=(0, "", "")):
        self.calls = []
        self.print_result = print_result
        self.bootstrap_result = bootstrap_result

    def __call__(self, *args):
        self.calls.append(args)
        verb = args[0]
        if verb == "print":
            rc, out = self.print_result[0], self.print_result[1]
            return _Result(rc, out)
        if verb == "bootstrap":
            rc, out, err = self.bootstrap_result
            return _Result(rc, out, err)
        return _Result(0, "", "")


class _Result:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class MacSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        (self.home / "Library" / "LaunchAgents").mkdir(parents=True)
        self.patches = [
            patch.object(scheduler_mac, "_home", lambda: self.home),
            patch.object(storage, "BASE_DIR", self.base),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def fake(self, **kwargs):
        fake = FakeLaunchctl(**kwargs)
        patcher = patch.object(scheduler_mac, "_launchctl", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def calls_for(self, fake, verb):
        return [c for c in fake.calls if c and c[0] == verb]

    def test_build_plist_shape(self):
        plist = scheduler_mac.build_plist("WorkBuddyAutoSignin", WORKER, TIMES["WorkBuddyAutoSignin"])
        self.assertEqual(plist["Label"], "com.wawavd13.workbuddy-manager.signin")
        self.assertEqual(plist["ProgramArguments"],
                         [WORKER["program"], "--mode", "daily"])
        self.assertEqual(plist["StartCalendarInterval"], [{"Hour": 0, "Minute": 5}])
        self.assertFalse(plist["RunAtLoad"])
        self.assertTrue(plist["StandardOutPath"].startswith(str(self.base / "data")))

    def test_apply_config_registers_and_bootstraps(self):
        fake = self.fake()
        result = scheduler_mac.apply_config(WORKER, TIMES, WORKER)
        self.assertTrue(result["ok"])
        for name in scheduler_mac.TASK_ORDER:
            path = scheduler_mac._plist_path(name)
            self.assertTrue(path.exists())
            with path.open("rb") as handle:
                data = plistlib.load(handle)
            self.assertEqual(data["Label"], scheduler_mac.TASK_LABELS[name])
        self.assertEqual(len(self.calls_for(fake, "bootstrap")), 2)
        self.assertEqual(len(self.calls_for(fake, "bootout")), 2)
        self.assertEqual([row["state"] for row in result["tasks"]], ["ready", "ready"])
        self.assertTrue(all(scheduler_mac._read_state()[name] for name in scheduler_mac.TASK_ORDER))

    def test_apply_config_snapshots_previous_plists(self):
        first = scheduler_mac._plist_path("WorkBuddyAutoSignin")
        first.write_bytes(b"<plist><dict><key>Label</key><string>old</string></dict></plist>")
        fake = self.fake()
        result = scheduler_mac.apply_config(WORKER, TIMES, WORKER)
        self.assertTrue(result["ok"])
        backup = Path(result["transaction_backup"])
        self.assertTrue((backup / first.name).exists())

    def test_apply_config_bootstrap_failure_is_reported_and_pauses(self):
        self.fake(bootstrap_result=(1, "", "Input/output error"))
        result = scheduler_mac.apply_config(WORKER, TIMES, WORKER)
        self.assertFalse(result["ok"])
        self.assertIn("bootstrap", result["error"])
        self.assertFalse(any(scheduler_mac._read_state().values()))

    def test_query_tasks_missing(self):
        self.fake()
        rows = scheduler_mac.query_tasks(WORKER)
        self.assertEqual([row["state"] for row in rows], ["missing", "missing"])
        self.assertIn("任务不存在", rows[0]["error"])

    def test_query_tasks_loaded_ready(self):
        self.fake()
        scheduler_mac.apply_config(WORKER, TIMES, WORKER)
        rows = scheduler_mac.query_tasks(WORKER)
        self.assertEqual(rows[0]["state"], "ready")
        self.assertTrue(rows[0]["managed"])
        self.assertEqual(rows[0]["times"], ["00:05"])
        self.assertIsNotNone(rows[0]["next_run"])
        self.assertEqual(rows[0]["last_result"], 0)

    def test_query_tasks_exit_code_parsed(self):
        self.fake(print_result=(0, PRINT_CRASHED))
        scheduler_mac.apply_config(WORKER, TIMES, WORKER)
        rows = scheduler_mac.query_tasks(WORKER)
        self.assertEqual(rows[0]["last_result"], 78)

    def test_query_tasks_paused_when_state_disabled(self):
        self.fake()
        scheduler_mac.apply_config(WORKER, TIMES, WORKER)
        scheduler_mac._write_state({name: False for name in scheduler_mac.TASK_ORDER})
        rows = scheduler_mac.query_tasks(WORKER)
        self.assertEqual([row["state"] for row in rows], ["paused", "paused"])
        self.assertIsNone(rows[0]["next_run"])
        self.assertIn("已暂停", rows[0]["error"])

    def test_query_tasks_unloaded_is_unknown(self):
        self.fake(print_result=(1, "", ""))
        scheduler_mac.apply_config(WORKER, TIMES, WORKER)
        rows = scheduler_mac.query_tasks(WORKER)
        self.assertEqual(rows[0]["state"], "unknown")
        self.assertIn("launchd", rows[0]["error"])

    def test_set_enabled_pause_and_resume(self):
        fake = self.fake()
        scheduler_mac.apply_config(WORKER, TIMES, WORKER)
        result = scheduler_mac.set_enabled(False, WORKER)
        self.assertTrue(result["ok"])
        self.assertEqual(len(self.calls_for(fake, "bootout")), 4)  # 2 in apply + 2 in pause
        self.assertEqual(len(self.calls_for(fake, "disable")), 2)
        rows = scheduler_mac.query_tasks(WORKER)
        self.assertEqual([row["state"] for row in rows], ["paused", "paused"])
        result = scheduler_mac.set_enabled(True, WORKER)
        self.assertTrue(result["ok"])
        rows = scheduler_mac.query_tasks(WORKER)
        self.assertEqual([row["state"] for row in rows], ["ready", "ready"])

    def test_set_enabled_requires_tasks(self):
        self.fake()
        result = scheduler_mac.set_enabled(False, WORKER)
        self.assertFalse(result["ok"])
        self.assertIn("保存并应用", result["error"])

    def test_restore_original_removes_and_restores_upstream(self):
        fake = self.fake()
        scheduler_mac.apply_config(WORKER, TIMES, WORKER)
        upstream = self.home / "Library" / "LaunchAgents" / scheduler_mac.UPSTREAM_PLIST_NAME
        upstream.write_bytes(b"<plist><dict><key>Label</key><string>upstream</string></dict></plist>")
        result = scheduler_mac.restore_original(WORKER)
        self.assertTrue(result["ok"])
        self.assertTrue(result["upstream_restored"])
        self.assertFalse(scheduler_mac._plist_path("WorkBuddyAutoSignin").exists())
        self.assertFalse(scheduler_mac._plist_path("WorkBuddyGrowthPoll").exists())
        upstream_bootstraps = [c for c in self.calls_for(fake, "bootstrap") if "workbuddy-auto-signin" in str(c)]
        self.assertEqual(len(upstream_bootstraps), 1)

    def test_backup_exists_after_apply(self):
        self.fake()
        with patch("sys.platform", "darwin"):
            self.assertFalse(scheduler.backup_exists())
            scheduler_mac.apply_config(WORKER, TIMES, WORKER)
            self.assertTrue(scheduler.backup_exists())

    def test_scheduler_dispatches_to_mac_backend(self):
        with patch("sys.platform", "darwin"), \
             patch.object(scheduler_mac, "apply_config", return_value={"ok": True, "via": "mac"}) as target:
            result = scheduler.apply_config({"signin_times": ["07:30"], "growth_times": []})
        self.assertEqual(result, {"ok": True, "via": "mac"})
        target.assert_called_once()
        validated, times, worker = target.call_args.args
        self.assertEqual(validated["signin_times"], ["07:30"])
        self.assertEqual(times["WorkBuddyAutoSignin"], ["07:30"])
        self.assertIn("--mode daily", worker["arguments"]["daily"])

    def test_next_run_is_future(self):
        stamp = scheduler_mac._next_run(["00:05"], now=datetime(2026, 11, 2, 3, 0))
        self.assertEqual(stamp, "2026-11-03 00:05:00")
        stamp = scheduler_mac._next_run(["00:05"], now=datetime(2026, 11, 2, 0, 0))
        self.assertEqual(stamp, "2026-11-02 00:05:00")


if __name__ == "__main__":
    unittest.main()
