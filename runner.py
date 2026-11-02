"""Isolated standalone worker; the UI is not its parent lifetime dependency."""
from __future__ import annotations

# python -I excludes cwd/PYTHONPATH. Explicitly trust only this installed source root.
from pathlib import Path
import sys
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import argparse
from datetime import datetime
import json
import os
import time

import credits
import storage
from engine import Engine


class AlreadyRunning(Exception):
    pass


class RunnerLock:
    """OS-backed file lock: released by the kernel on crash (no stale PID guessing)."""
    def __init__(self, path=None):
        self.path = Path(path) if path else storage.BASE_DIR / "runner.lock"
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, 2)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise AlreadyRunning() from None
        return self

    def __exit__(self, *args):
        if self.handle:
            try:
                self.handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()


def _stamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run_worker(mode, engine_factory=Engine):
    runtime_path, cancel_path = storage.BASE_DIR / "runtime.json", storage.BASE_DIR / "cancel.request"
    record = None
    code = 1
    try:
        with RunnerLock():
            # Old requests must never cancel an unrelated next worker.
            try:
                cancel_path.unlink()
            except FileNotFoundError:
                pass
            runtime = {"running": True, "pid": os.getpid(), "mode": mode, "stage": "starting",
                       "started_at": _stamp(), "finished_at": None}
            storage.atomic_write_json(runtime_path, runtime)

            def stage(value):
                runtime["stage"] = value
                storage.atomic_write_json(runtime_path, runtime)

            try:
                engine = engine_factory(cancel_check=cancel_path.exists, stage_callback=stage)
                code, record = engine.run(mode)
                # Keep the read-only credit cache current even when the UI is closed, but only for results
                # that confirm a check-in or a growth run: a SKIPPED patrol stays offline even when it ran
                # a growth slot. A credit failure must never change the check-in/growth outcome.
                credit_modes = {"checkin", "growth", "daily", "poll"}
                credit_results = {"SUCCESS", "CLAIMED", "ALREADY", "GROWTH"}
                if mode in credit_modes and record.get("result") in credit_results:
                    stage("刷新积分")
                    try:
                        record["credits_refreshed"] = bool(credits.refresh().get("ok"))
                    except Exception:
                        record["credits_refreshed"] = False
                storage.append_event(record)
            except Exception:
                code = 1
                record = {"recorded_at": _stamp(), "action": mode, "result": "ERROR",
                          "report": "运行失败，请检查本地配置与文件权限", "growth": None, "needs_attention": True}
                try:
                    storage.append_event(record)
                except OSError:
                    pass
            finally:
                runtime.update(running=False, stage="finished", finished_at=_stamp())
                storage.atomic_write_json(runtime_path, runtime)
                try:
                    cancel_path.unlink()
                except FileNotFoundError:
                    pass
    except AlreadyRunning:
        code = 3
        record = {"recorded_at": _stamp(), "action": mode, "result": "BUSY",
                  "report": "已有后台操作运行，未启动重复操作", "growth": None, "needs_attention": False}
    except OSError:
        code = 1
        record = {"recorded_at": _stamp(), "action": mode, "result": "ERROR",
                  "report": "本地存储不可用", "growth": None, "needs_attention": True}
    return code, record


def _utf8_streams():
    '''Pin stdout/stderr to UTF-8.

    The UI decodes worker output as UTF-8, but a worker spawned on Chinese Windows otherwise
    writes the system code page (GBK): the growth report then arrived as mojibake.'''
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv=None):
    _utf8_streams()
    parser = argparse.ArgumentParser(description="WorkBuddy 签到管理独立后台")
    parser.add_argument("--mode", choices=("status", "checkin", "growth", "daily", "poll"), required=True)
    args = parser.parse_args(argv)
    code, record = run_worker(args.mode)
    # One result line, even with pythonw (which normally has no stdout).
    if sys.stdout is not None:
        print(json.dumps(record, ensure_ascii=False, separators=(",", ":")), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
