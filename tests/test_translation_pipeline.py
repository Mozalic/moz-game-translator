from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jp_game_translator.core.models import TextEntry
from jp_game_translator.core.project import GLOSSARY_FILE, load_workspace_entries, save_workspace_entries
from jp_game_translator.terminology.glossary import GlossaryTerm, load_glossary, save_glossary
from jp_game_translator.translation.pipeline import translate_glossary_terms, translate_workspace_all


class _FakeTermProvider:
    calls = []

    def __init__(self, _config):
        pass

    def translate_terms(self, terms, target_language, system_prompt=""):
        terms_list = list(terms)
        self.calls.append(terms_list)
        return {term: "%s_%s" % (term, target_language) for term in terms_list}


class _FakeEntryProvider:
    calls = []

    def __init__(self, _config):
        pass

    def translate_batch(self, entries, glossary, target_language, system_prompt=""):
        entries_list = list(entries)
        self.calls.append([entry.id for entry in entries_list])
        return {entry.id: "%s_%s" % (entry.source, target_language) for entry in entries_list}


class _FlakyEntryProvider:
    calls = []

    def __init__(self, _config):
        pass

    def translate_batch(self, entries, glossary, target_language, system_prompt=""):
        entries_list = list(entries)
        self.calls.append([entry.id for entry in entries_list])
        if len(entries_list) > 1:
            raise ValueError("Expecting ',' delimiter: line 1 column 4811 (char 4810)")
        return {entry.id: "%s_%s" % (entry.source, target_language) for entry in entries_list}


class _SecondBatchFailsProvider:
    calls = []

    def __init__(self, _config):
        pass

    def translate_batch(self, entries, glossary, target_language, system_prompt=""):
        entries_list = list(entries)
        ids = [entry.id for entry in entries_list]
        self.calls.append(ids)
        if ids == ["3", "4"]:
            raise RuntimeError("provider failed")
        return {entry.id: "%s_%s" % (entry.source, target_language) for entry in entries_list}


class _FatalBalanceEntryProvider:
    calls = []

    def __init__(self, _config):
        pass

    def translate_batch(self, entries, glossary, target_language, system_prompt=""):
        entries_list = list(entries)
        self.calls.append([entry.id for entry in entries_list])
        raise RuntimeError('provider HTTP error 402: {"error":{"message":"Insufficient Balance"}}')


class TranslationPipelineTest(unittest.TestCase):
    def test_translate_glossary_terms_translates_all_pending_terms_in_batches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            save_glossary(
                workspace / GLOSSARY_FILE,
                [
                    GlossaryTerm(source="term-1"),
                    GlossaryTerm(source="term-2"),
                    GlossaryTerm(source="term-3"),
                    GlossaryTerm(source="term-4"),
                    GlossaryTerm(source="term-5"),
                    GlossaryTerm(source="already", target="已有", status="approved"),
                    GlossaryTerm(source="skip", status="rejected"),
                ],
            )
            logs = []
            _FakeTermProvider.calls = []

            with patch(
                "jp_game_translator.translation.pipeline.load_provider_config",
                return_value=SimpleNamespace(name="fake"),
            ), patch("jp_game_translator.translation.pipeline.OpenAICompatibleProvider", _FakeTermProvider):
                changed = translate_glossary_terms(
                    workspace=workspace,
                    config_path=workspace / "providers.json",
                    provider_name="fake",
                    target_language="zh-Hans",
                    limit=2,
                    logger=logs.append,
                )

            self.assertEqual(changed, 5)
            self.assertEqual(_FakeTermProvider.calls, [["term-1", "term-2"], ["term-3", "term-4"], ["term-5"]])
            saved = {term.source: term for term in load_glossary(workspace / GLOSSARY_FILE)}
            self.assertEqual(saved["term-1"].target, "term-1_zh-Hans")
            self.assertEqual(saved["term-5"].target, "term-5_zh-Hans")
            self.assertEqual(saved["already"].target, "已有")
            self.assertEqual(saved["skip"].target, "")
            self.assertTrue(any("pending=5" in message for message in logs))
            self.assertTrue(any("request 3/3 done" in message for message in logs))

    def test_translate_workspace_all_reports_progress_per_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            save_workspace_entries(
                workspace,
                [
                    TextEntry(id="1", source="line-1", context="", file="a", adapter_id="test"),
                    TextEntry(id="2", source="line-2", context="", file="a", adapter_id="test"),
                    TextEntry(id="3", source="line-3", context="", file="a", adapter_id="test"),
                    TextEntry(id="4", source="line-4", context="", file="a", adapter_id="test"),
                    TextEntry(id="5", source="line-5", context="", file="a", adapter_id="test"),
                ],
            )
            logs = []
            _FakeEntryProvider.calls = []

            with patch(
                "jp_game_translator.translation.pipeline.load_provider_config",
                return_value=SimpleNamespace(name="fake"),
            ), patch("jp_game_translator.translation.pipeline.OpenAICompatibleProvider", _FakeEntryProvider):
                changed = translate_workspace_all(
                    workspace=workspace,
                    config_path=workspace / "providers.json",
                    provider_name="fake",
                    target_language="zh-Hans",
                    batch_size=2,
                    logger=logs.append,
                )

            self.assertEqual(changed, 5)
            self.assertEqual(_FakeEntryProvider.calls, [["1", "2"], ["3", "4"], ["5"]])
            entries = {entry.id: entry for entry in load_workspace_entries(workspace)}
            self.assertEqual(entries["1"].translation, "line-1_zh-Hans")
            self.assertEqual(entries["5"].translation, "line-5_zh-Hans")
            self.assertTrue(any("pending=5" in message for message in logs))
            self.assertTrue(any("request 3/3 done" in message for message in logs))
            usage_log = workspace / "logs" / "token_usage.jsonl"
            self.assertTrue(usage_log.exists())
            records = [json.loads(line) for line in usage_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[0]["event"], "start")
            self.assertEqual(records[-1]["event"], "summary")
            self.assertTrue(any(record["event"] == "request" for record in records))

    def test_translate_workspace_all_can_retranslate_existing_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            save_workspace_entries(
                workspace,
                [
                    TextEntry(
                        id="1",
                        source="line-1",
                        translation="old",
                        context="",
                        file="a",
                        adapter_id="test",
                        status="translated",
                    ),
                    TextEntry(id="2", source="line-2", context="", file="a", adapter_id="test"),
                ],
            )
            _FakeEntryProvider.calls = []

            with patch(
                "jp_game_translator.translation.pipeline.load_provider_config",
                return_value=SimpleNamespace(name="fake"),
            ), patch("jp_game_translator.translation.pipeline.OpenAICompatibleProvider", _FakeEntryProvider):
                changed = translate_workspace_all(
                    workspace=workspace,
                    config_path=workspace / "providers.json",
                    provider_name="fake",
                    target_language="zh-Hans",
                    batch_size=10,
                    include_translated=True,
                    logger=lambda _message: None,
                )

            self.assertEqual(changed, 2)
            self.assertEqual(_FakeEntryProvider.calls, [["1", "2"]])
            entries = {entry.id: entry for entry in load_workspace_entries(workspace)}
            self.assertEqual(entries["1"].translation, "line-1_zh-Hans")

    def test_translate_workspace_all_translates_duplicate_sources_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            save_workspace_entries(
                workspace,
                [
                    TextEntry(id="1", source="same-line", context="first", file="a", adapter_id="test"),
                    TextEntry(id="2", source="same-line", context="second", file="a", adapter_id="test"),
                    TextEntry(id="3", source="other-line", context="", file="a", adapter_id="test"),
                ],
            )
            logs = []
            _FakeEntryProvider.calls = []

            with patch(
                "jp_game_translator.translation.pipeline.load_provider_config",
                return_value=SimpleNamespace(name="fake"),
            ), patch("jp_game_translator.translation.pipeline.OpenAICompatibleProvider", _FakeEntryProvider):
                changed = translate_workspace_all(
                    workspace=workspace,
                    config_path=workspace / "providers.json",
                    provider_name="fake",
                    target_language="zh-Hans",
                    batch_size=10,
                    logger=logs.append,
                )

            self.assertEqual(changed, 3)
            self.assertEqual(_FakeEntryProvider.calls, [["1", "3"]])
            entries = {entry.id: entry for entry in load_workspace_entries(workspace)}
            self.assertEqual(entries["1"].translation, "same-line_zh-Hans")
            self.assertEqual(entries["2"].translation, "same-line_zh-Hans")
            self.assertEqual(entries["3"].translation, "other-line_zh-Hans")
            usage_log = workspace / "logs" / "token_usage.jsonl"
            records = [json.loads(line) for line in usage_log.read_text(encoding="utf-8").splitlines()]
            request_record = next(record for record in records if record["event"] == "request")
            self.assertEqual(request_record["item_count"], 3)
            self.assertEqual(request_record["unique_items"], 2)
            self.assertEqual(request_record["duplicate_items"], 1)
            self.assertTrue(any("duplicates=1" in message or "1 duplicates" in message for message in logs))

    def test_translate_workspace_all_splits_large_batches_by_character_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            save_workspace_entries(
                workspace,
                [
                    TextEntry(id="1", source="a" * 300, context="", file="a", adapter_id="test"),
                    TextEntry(id="2", source="b" * 300, context="", file="a", adapter_id="test"),
                    TextEntry(id="3", source="c" * 300, context="", file="a", adapter_id="test"),
                ],
            )
            _FakeEntryProvider.calls = []

            with patch(
                "jp_game_translator.translation.pipeline.load_provider_config",
                return_value=SimpleNamespace(name="fake"),
            ), patch("jp_game_translator.translation.pipeline.OpenAICompatibleProvider", _FakeEntryProvider), patch(
                "jp_game_translator.translation.pipeline.ENTRY_TRANSLATION_MAX_CHARS",
                20,
            ):
                changed = translate_workspace_all(
                    workspace=workspace,
                    config_path=workspace / "providers.json",
                    provider_name="fake",
                    target_language="zh-Hans",
                    batch_size=100,
                    logger=lambda _message: None,
                )

            self.assertEqual(changed, 3)
            self.assertEqual(_FakeEntryProvider.calls, [["1"], ["2"], ["3"]])

    def test_translate_workspace_all_stops_submitting_new_batches_when_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            save_workspace_entries(
                workspace,
                [
                    TextEntry(id="1", source="line-1", context="", file="a", adapter_id="test"),
                    TextEntry(id="2", source="line-2", context="", file="a", adapter_id="test"),
                    TextEntry(id="3", source="line-3", context="", file="a", adapter_id="test"),
                    TextEntry(id="4", source="line-4", context="", file="a", adapter_id="test"),
                ],
            )
            stop = {"value": False}
            logs = []
            _FakeEntryProvider.calls = []

            def logger(message: str) -> None:
                logs.append(message)
                if "request 1/2 done" in message:
                    stop["value"] = True

            with patch(
                "jp_game_translator.translation.pipeline.load_provider_config",
                return_value=SimpleNamespace(name="fake"),
            ), patch("jp_game_translator.translation.pipeline.OpenAICompatibleProvider", _FakeEntryProvider):
                changed = translate_workspace_all(
                    workspace=workspace,
                    config_path=workspace / "providers.json",
                    provider_name="fake",
                    target_language="zh-Hans",
                    batch_size=2,
                    concurrency=1,
                    logger=logger,
                    cancelled=lambda: stop["value"],
                )

            self.assertEqual(changed, 2)
            self.assertEqual(_FakeEntryProvider.calls, [["1", "2"]])
            self.assertTrue(any("[entries] stopped:" in message for message in logs))

    def test_translate_workspace_all_splits_batch_after_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            save_workspace_entries(
                workspace,
                [
                    TextEntry(id="1", source="line-1", context="", file="a", adapter_id="test"),
                    TextEntry(id="2", source="line-2", context="", file="a", adapter_id="test"),
                ],
            )
            logs = []
            _FlakyEntryProvider.calls = []

            with patch(
                "jp_game_translator.translation.pipeline.load_provider_config",
                return_value=SimpleNamespace(name="fake"),
            ), patch("jp_game_translator.translation.pipeline.OpenAICompatibleProvider", _FlakyEntryProvider):
                changed = translate_workspace_all(
                    workspace=workspace,
                    config_path=workspace / "providers.json",
                    provider_name="fake",
                    target_language="zh-Hans",
                    batch_size=2,
                    concurrency=1,
                    logger=logs.append,
                )

            self.assertEqual(changed, 2)
            self.assertEqual(_FlakyEntryProvider.calls, [["1", "2"], ["1"], ["2"]])
            self.assertTrue(any("splitting into 1 + 1" in message for message in logs))
            entries = {entry.id: entry for entry in load_workspace_entries(workspace)}
            self.assertEqual(entries["1"].translation, "line-1_zh-Hans")
            self.assertEqual(entries["2"].translation, "line-2_zh-Hans")

    def test_translate_workspace_all_persists_completed_batch_when_later_batch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            save_workspace_entries(
                workspace,
                [
                    TextEntry(id="1", source="line-1", context="", file="a", adapter_id="test"),
                    TextEntry(id="2", source="line-2", context="", file="a", adapter_id="test"),
                    TextEntry(id="3", source="line-3", context="", file="a", adapter_id="test"),
                    TextEntry(id="4", source="line-4", context="", file="a", adapter_id="test"),
                ],
            )
            logs = []
            _SecondBatchFailsProvider.calls = []

            with patch(
                "jp_game_translator.translation.pipeline.load_provider_config",
                return_value=SimpleNamespace(name="fake"),
            ), patch("jp_game_translator.translation.pipeline.OpenAICompatibleProvider", _SecondBatchFailsProvider):
                changed = translate_workspace_all(
                    workspace=workspace,
                    config_path=workspace / "providers.json",
                    provider_name="fake",
                    target_language="zh-Hans",
                    batch_size=2,
                    concurrency=1,
                    logger=logs.append,
                )

            self.assertEqual(changed, 2)
            entries = {entry.id: entry for entry in load_workspace_entries(workspace)}
            self.assertEqual(entries["1"].translation, "line-1_zh-Hans")
            self.assertEqual(entries["2"].translation, "line-2_zh-Hans")
            self.assertIsNone(entries["3"].translation)
            self.assertIsNone(entries["4"].translation)
            self.assertTrue(any("request 2/2 failed" in message for message in logs))
            self.assertTrue(any("skipped failed requests: 1" in message for message in logs))

    def test_translate_workspace_all_stops_after_fatal_provider_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            save_workspace_entries(
                workspace,
                [
                    TextEntry(id="1", source="line-1", context="", file="a", adapter_id="test"),
                    TextEntry(id="2", source="line-2", context="", file="a", adapter_id="test"),
                    TextEntry(id="3", source="line-3", context="", file="a", adapter_id="test"),
                    TextEntry(id="4", source="line-4", context="", file="a", adapter_id="test"),
                ],
            )
            logs = []
            _FatalBalanceEntryProvider.calls = []

            with patch(
                "jp_game_translator.translation.pipeline.load_provider_config",
                return_value=SimpleNamespace(name="fake"),
            ), patch("jp_game_translator.translation.pipeline.OpenAICompatibleProvider", _FatalBalanceEntryProvider):
                changed = translate_workspace_all(
                    workspace=workspace,
                    config_path=workspace / "providers.json",
                    provider_name="fake",
                    target_language="zh-Hans",
                    batch_size=2,
                    concurrency=1,
                    logger=logs.append,
                )

            self.assertEqual(changed, 0)
            self.assertEqual(_FatalBalanceEntryProvider.calls, [["1", "2"]])
            entries = {entry.id: entry for entry in load_workspace_entries(workspace)}
            self.assertIsNone(entries["1"].translation)
            self.assertIsNone(entries["3"].translation)
            self.assertTrue(any("fatal provider error" in message for message in logs))
            self.assertTrue(any("[entries] stopped:" in message for message in logs))


if __name__ == "__main__":
    unittest.main()
