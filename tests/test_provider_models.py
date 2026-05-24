from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jp_game_translator.translation.providers import (
    OpenAICompatibleProvider,
    ProviderConfig,
    load_provider_config,
    _parse_term_translation_response,
    _parse_translation_response,
)


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ProviderModelsTest(unittest.TestCase):
    def test_load_provider_config_accepts_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "providers.json"
            config_path.write_text(
                '\ufeff{"providers":{"deepseek":{"type":"openai_compatible","base_url":"https://api.example/v1","model":"model-a","api_key":"key"}}}',
                encoding="utf-8",
            )

            config = load_provider_config(config_path, "deepseek")

            self.assertEqual(config.name, "deepseek")
            self.assertEqual(config.base_url, "https://api.example/v1")
            self.assertEqual(config.model, "model-a")

    def test_list_models_reads_openai_compatible_response(self) -> None:
        provider = OpenAICompatibleProvider(
            ProviderConfig(
                name="test",
                type="openai_compatible",
                base_url="https://example.test/v1",
                model="",
                api_key="key",
            )
        )
        with patch("urllib.request.urlopen", return_value=_FakeResponse({"data": [{"id": "b"}, {"id": "a"}]})):
            self.assertEqual(provider.list_models(), ["a", "b"])

    def test_test_connection_uses_models_when_model_empty(self) -> None:
        provider = OpenAICompatibleProvider(
            ProviderConfig(
                name="test",
                type="openai_compatible",
                base_url="https://example.test/v1",
                model="",
                api_key="key",
            )
        )
        with patch("urllib.request.urlopen", return_value=_FakeResponse({"data": [{"id": "model-a"}]})):
            self.assertIn("fetched 1 models", provider.test_connection())

    def test_parse_term_translation_response(self) -> None:
        result = _parse_term_translation_response('{"items":[{"source":"魔王城","target":"魔王城"}]}')
        self.assertEqual(result, {"魔王城": "魔王城"})

    def test_parse_translation_response_extracts_json_from_markdown_fence(self) -> None:
        result = _parse_translation_response(
            '```json\n{"items":[{"id":"1","translation":"你好"}]}\n```\n'
        )
        self.assertEqual(result, {"1": "你好"})

    def test_parse_translation_response_accepts_compact_arrays(self) -> None:
        self.assertEqual(_parse_translation_response('{"items":[["1","hello"]]}'), {"1": "hello"})
        self.assertEqual(_parse_term_translation_response('{"items":[["term","译名"]]}'), {"term": "译名"})

    def test_translate_terms_prints_request_prompt(self) -> None:
        provider = OpenAICompatibleProvider(
            ProviderConfig(
                name="test",
                type="openai_compatible",
                base_url="https://example.test/v1",
                model="model-a",
                api_key="key",
            )
        )
        payload = {
            "choices": [{"message": {"content": '{"items":[{"source":"魔王城","target":"魔王城"}]}'}}],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
                "prompt_cache_hit_tokens": 5,
                "prompt_cache_miss_tokens": 6,
            },
        }
        with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)), patch("builtins.print") as printed:
            result = provider.translate_terms_with_stats(["魔王城"], "zh-Hans", system_prompt="TERM SYSTEM")

        printed_text = "\n".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        self.assertIn("JGT prompt: kind=term_translation", printed_text)
        self.assertIn("items=1", printed_text)
        self.assertIn("user_chars=", printed_text)
        self.assertIn("魔王城", printed_text)
        self.assertEqual(result.total_tokens, 18)
        self.assertEqual(result.prompt_tokens, 11)
        self.assertEqual(result.completion_tokens, 7)
        self.assertEqual(result.prompt_cache_hit_tokens, 5)
        self.assertEqual(result.prompt_cache_miss_tokens, 6)
        self.assertGreaterEqual(result.elapsed_seconds, 0)


if __name__ == "__main__":
    unittest.main()
