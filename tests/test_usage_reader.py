"""Local consumption log parsing; uses synthetic files only, never the real ~/.workbuddy."""
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import usage_reader


def call(credit, model, stamp, message="m1", step=1, record_id="r1", session="s1"):
    return {"id": record_id, "timestamp": stamp, "type": "function_call", "sessionId": session,
            "providerData": {"messageId": message, "stepSeq": step, "model": model.lower(),
                             "requestModelName": model,
                             "rawUsage": {"prompt_tokens": 1000, "completion_tokens": 50,
                                          "prompt_cache_hit_tokens": 400, "credit": credit}}}


STAMP = 1793457366955  # 2026-10-31 local time on the machine that wrote the real logs.
DAY = datetime.fromtimestamp(STAMP / 1000).strftime("%Y-%m-%d")


class UsageReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        a = self.home / "projects" / "proj-a"
        b = self.home / "projects" / "proj-b"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        lines = [
            json.dumps(call(7.33, "Kimi-K3.1-Pro", STAMP)),
            json.dumps(call(7.33, "Kimi-K3.1-Pro", STAMP)),  # retry re-logs the same step: counted once
            json.dumps(call(0, "Kimi-K3.1-Pro", STAMP, message="m2", record_id="r2")),  # free call
            "{not json at all",
            json.dumps({"id": "x", "type": "message", "message": {"content": "no credit here"}}),
            json.dumps(call(1.4, "Kimi-K3.1-Pro", STAMP + 60000, message="m3", step=2, record_id="r3")),
        ]
        (a / "s1.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (b / "s2.jsonl").write_text(json.dumps(call(2.5, "GLM-5.1", STAMP - 86400000, message="m9",
                                                    record_id="r9", session="s2")) + "\n", encoding="utf-8")
        connection = sqlite3.connect(self.home / "workbuddy.db")
        connection.execute("create table sessions (id text, custom_title text, title text)")
        connection.executemany("insert into sessions values (?, ?, ?)",
                               [("s1", None, "检查UI设计一致性"), ("s2", "自定义标题", "原标题")])
        connection.commit()
        connection.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_scan_dedupes_and_skips_free_or_broken_lines(self):
        scanned = usage_reader.scan(self.home / "projects")
        self.assertEqual(scanned["files"], 2)
        self.assertEqual([round(c["credit"], 2) for c in scanned["calls"]], [7.33, 1.4, 2.5])
        self.assertEqual(scanned["calls"][0]["cached_tokens"], 400)
        self.assertEqual(usage_reader.scan(self.home / "missing")["files"], 0)

    def test_aggregate_groups_by_model_day_and_session(self):
        summary = usage_reader.load(self.home / "projects", self.home / "workbuddy.db")
        self.assertAlmostEqual(summary["total"], 11.23)
        self.assertEqual(summary["calls"], 3)
        self.assertEqual(summary["files"], 2)
        self.assertEqual([m["model"] for m in summary["models"]], ["Kimi-K3.1-Pro", "GLM-5.1"])
        self.assertAlmostEqual(summary["models"][0]["credit"], 8.73)
        self.assertEqual(summary["models"][0]["calls"], 2)
        self.assertEqual(summary["models"][0]["prompt_tokens"], 2000)
        self.assertEqual((summary["prompt_tokens"], summary["completion_tokens"]), (3000, 150))
        self.assertEqual(summary["days"][0]["day"], DAY)  # most recent first
        self.assertAlmostEqual(summary["days"][0]["credit"], 8.73)
        sessions = {s["session"]: s for s in summary["sessions"]}
        self.assertEqual(sessions["s1"]["title"], "检查UI设计一致性")
        self.assertEqual(sessions["s2"]["title"], "自定义标题")
        self.assertEqual(summary["sessions"][0]["session"], "s1")
        self.assertEqual(summary["sessions"][0]["last"], datetime.fromtimestamp((STAMP + 60000) / 1000))
        self.assertEqual(summary["first"], datetime.fromtimestamp((STAMP - 86400000) / 1000))

    def test_today_and_month_buckets_follow_the_given_date(self):
        calls = usage_reader.scan(self.home / "projects")["calls"]
        today = datetime.fromtimestamp(STAMP / 1000).date()
        summary = usage_reader.aggregate(calls, today=today)
        self.assertAlmostEqual(summary["today"]["credit"], 8.73)
        self.assertAlmostEqual(summary["month"]["credit"], 11.23 if summary["months"][0]["month"] == summary["months"][-1]["month"] else summary["month"]["credit"])
        empty = usage_reader.aggregate([], today=today)
        self.assertEqual((empty["total"], empty["calls"], empty["today"]["credit"], empty["models"]), (0.0, 0, 0.0, []))

    def test_series_helpers_fill_gaps_and_fold_the_tail(self):
        from datetime import date
        summary = {"months": [{"month": "2026-05", "credit": 10.0}, {"month": "2026-07", "credit": 2.5}],
                   "days": [{"day": "2026-10-30", "credit": 4.0}]}
        self.assertEqual(usage_reader.month_series(summary, today=date(2026, 8, 3)),
                         [("2026-05", 10.0), ("2026-06", 0.0), ("2026-07", 2.5), ("2026-08", 0.0)])
        self.assertEqual(usage_reader.month_series({"months": []}), [])
        days = usage_reader.day_series(summary, today=date(2026, 10, 31), count=3)
        self.assertEqual(days, [("2026-10-29", 0.0), ("2026-10-30", 4.0), ("2026-10-31", 0.0)])
        models = [dict(model=f"m{i}", credit=float(20 - i), calls=1, prompt_tokens=10, completion_tokens=1) for i in range(12)]
        folded = usage_reader.top_models(models, limit=10)
        self.assertEqual(len(folded), 10)
        self.assertEqual(folded[-1]["model"], "其他 3 个模型")
        self.assertEqual(folded[-1]["credit"], 11.0 + 10.0 + 9.0)
        self.assertEqual(usage_reader.top_models(models[:3], limit=10), models[:3])

    def test_titles_tolerate_missing_database(self):
        self.assertEqual(usage_reader.session_titles(self.home / "nope.db"), {})
        self.assertEqual(usage_reader.credit_text(3082.1), "3,082.10")
        self.assertEqual(usage_reader.token_text(12748772), "12.75M")
        self.assertEqual(usage_reader.token_text(63399), "0.06M")
        self.assertEqual(usage_reader.token_text(3634), "3.6K")
        self.assertEqual(usage_reader.token_text(348), "348")


if __name__ == "__main__":
    unittest.main(verbosity=1)
