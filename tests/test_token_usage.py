from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jp_game_translator.translation.token_usage import latest_token_usage_run, load_token_usage_runs


class TokenUsageTest(unittest.TestCase):
    def test_load_token_usage_runs_aggregates_request_and_summary_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "token_usage.jsonl"
            records = [
                {
                    "timestamp": "2026-05-24T10:00:00Z",
                    "run_id": "entries-1",
                    "scope": "entries",
                    "event": "start",
                    "provider": "fake",
                    "request_count": 2,
                    "item_count": 4,
                },
                {
                    "timestamp": "2026-05-24T10:00:01Z",
                    "run_id": "entries-1",
                    "scope": "entries",
                    "event": "request",
                    "provider": "fake",
                    "model": "model-a",
                    "request_index": 1,
                    "request_count": 2,
                    "item_count": 2,
                    "changed": 2,
                    "elapsed_seconds": 1.25,
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                    "prompt_cache_hit_tokens": 4,
                    "prompt_cache_miss_tokens": 6,
                },
                {
                    "timestamp": "2026-05-24T10:00:02Z",
                    "run_id": "entries-1",
                    "scope": "entries",
                    "event": "request",
                    "provider": "fake",
                    "model": "model-a",
                    "request_index": 2,
                    "request_count": 2,
                    "item_count": 2,
                    "changed": 1,
                    "elapsed_seconds": 2.5,
                    "prompt_tokens": 5,
                    "completion_tokens": 7,
                    "total_tokens": 12,
                    "failed": True,
                },
                {
                    "timestamp": "2026-05-24T10:00:03Z",
                    "run_id": "entries-1",
                    "scope": "entries",
                    "event": "summary",
                    "provider": "fake",
                    "model": "model-a",
                    "request_count": 2,
                    "item_count": 4,
                    "changed": 3,
                    "elapsed_seconds": 3.75,
                    "wall_seconds": 3.9,
                    "prompt_tokens": 15,
                    "completion_tokens": 27,
                    "total_tokens": 42,
                    "prompt_cache_hit_tokens": 4,
                    "prompt_cache_miss_tokens": 6,
                    "state": "finished",
                    "failed_requests": 1,
                },
            ]
            with log_path.open("w", encoding="utf-8") as handle:
                handle.write("not json\n")
                for record in records:
                    handle.write(json.dumps(record))
                    handle.write("\n")

            runs = load_token_usage_runs(log_path)

            self.assertEqual(len(runs), 1)
            run = runs[0]
            self.assertEqual(run.run_id, "entries-1")
            self.assertEqual(run.scope, "entries")
            self.assertEqual(run.provider, "fake")
            self.assertEqual(run.model, "model-a")
            self.assertEqual(run.state, "finished")
            self.assertEqual(run.completed_requests, 2)
            self.assertEqual(run.request_count, 2)
            self.assertEqual(run.item_count, 4)
            self.assertEqual(run.changed, 3)
            self.assertEqual(run.total_tokens, 42)
            self.assertEqual(run.prompt_tokens, 15)
            self.assertEqual(run.completion_tokens, 27)
            self.assertEqual(run.failed_requests, 1)
            self.assertEqual(run.prompt_cache_hit_rate, 0.4)
            self.assertEqual(len(run.requests), 2)

    def test_latest_token_usage_run_uses_workspace_log_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            log_path = workspace / "logs" / "token_usage.jsonl"
            log_path.parent.mkdir()
            records = [
                {"timestamp": "2026-05-24T10:00:00Z", "run_id": "terms-1", "scope": "terms", "event": "request"},
                {"timestamp": "2026-05-24T10:01:00Z", "run_id": "entries-2", "scope": "entries", "event": "request"},
            ]
            with log_path.open("w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record))
                    handle.write("\n")

            run = latest_token_usage_run(workspace)

            self.assertIsNotNone(run)
            assert run is not None
            self.assertEqual(run.run_id, "entries-2")


if __name__ == "__main__":
    unittest.main()
