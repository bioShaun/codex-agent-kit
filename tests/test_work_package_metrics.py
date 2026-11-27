"""Regression coverage for telemetry from several turns of one child."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MetricsTests(unittest.TestCase):
    def test_partial_turn_only_affects_its_selected_entry(self):
        work = ROOT / ".work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as directory:
            base = Path(directory)
            stamp = "2026-01-01T00:00:00Z"
            for thread, parent in (("root", None), ("child", "root")):
                events = [{"type": "session_meta", "payload": {
                    "id": thread, "parent_thread_id": parent, "source": "cli"}}]
                for event in ("task_started", "task_complete"):
                    events.append({"timestamp": stamp, "type": "event_msg",
                                   "payload": {"type": event, "turn_id": "done"}})
                events.append({"timestamp": stamp, "type": "token_usage_record", "payload": {
                    "thread_id": thread, "turn_id": "done", "response_id": "r",
                    "usage": {"input_tokens": 5, "cached_input_tokens": 2, "output_tokens": 1}}})
                (base / thread).write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
            entries = [{"role": "root", "rollout": "root", "turn_ids": ["done"]},
                       {"role": "child", "rollout": "child", "turn_ids": ["missing"]},
                       {"role": "child", "rollout": "child", "turn_ids": ["done"]}]
            path = base / "spec.json"
            path.write_text(json.dumps({"work_id": "w", "project": "p", "task_type": "test",
                                        "started_at": stamp, "outcome": "UNKNOWN",
                                        "threads": entries}), encoding="utf-8")
            output = subprocess.check_output(
                [sys.executable, ROOT / "bundle/work-package-metrics.py", "--spec", path])
            result = json.loads(output)
            self.assertIsNone(result["tokens"])
            self.assertIsNone(result["threads"][1]["tokens"])
            self.assertEqual(result["threads"][2]["tokens"], result["threads"][0]["tokens"])
            self.assertEqual(result["known_usage"]["input_tokens"], 10)
