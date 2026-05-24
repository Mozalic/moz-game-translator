from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jp_game_translator.core.models import TextEntry
from jp_game_translator.translation.prompts import build_term_user_prompt, build_user_prompt, select_glossary_for_entries


class PromptCompressionTest(unittest.TestCase):
    def test_build_user_prompt_filters_glossary_to_current_batch(self) -> None:
        entries = [
            TextEntry(id="1", source="魔王城へ行く", context="", file="a", adapter_id="test"),
            TextEntry(id="2", source="村で休む", context="", file="a", adapter_id="test"),
        ]
        glossary = {
            "魔王城": "魔王城",
            "勇者": "勇者",
            "村": "村庄",
        }

        selected = select_glossary_for_entries(entries, glossary)
        self.assertEqual(selected, {"魔王城": "魔王城", "村": "村庄"})

        prompt = build_user_prompt(entries, glossary, "zh-Hans")
        payload = json.loads(prompt)
        self.assertEqual(payload["glossary"], selected)
        self.assertNotIn("\n", prompt)
        self.assertNotIn("context", payload["items"][0])
        self.assertNotIn("speaker", payload["items"][0])

    def test_build_term_user_prompt_uses_compact_term_list(self) -> None:
        prompt = build_term_user_prompt(["魔王城", "", "勇者"], "zh-Hans")
        self.assertEqual(json.loads(prompt), {"target_language": "zh-Hans", "items": ["魔王城", "勇者"]})
        self.assertNotIn("\n", prompt)


if __name__ == "__main__":
    unittest.main()
