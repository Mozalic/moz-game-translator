from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jp_game_translator.core.models import TextEntry
from jp_game_translator.terminology.extractor import extract_term_candidates


class TerminologyExtractorTest(unittest.TestCase):
    def test_extract_repeated_terms(self) -> None:
        entries = [
            TextEntry(id="1", source="魔王城へようこそ。勇者よ。", context="a", file="a", adapter_id="test"),
            TextEntry(id="2", source="魔王城には古代兵器がある。", context="b", file="b", adapter_id="test"),
            TextEntry(id="3", source="古代兵器を止めるのは勇者だけだ。", context="c", file="c", adapter_id="test"),
        ]
        candidates = extract_term_candidates(entries, min_count=2, limit=10)
        sources = {candidate.source for candidate in candidates}
        self.assertIn("魔王城", sources)
        self.assertIn("古代兵器", sources)


if __name__ == "__main__":
    unittest.main()
