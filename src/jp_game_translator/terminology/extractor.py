from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, List, Set

from jp_game_translator.core.models import TextEntry


TERM_RE = re.compile(r"[\u30a1-\u30ffー]{2,}|[\u4e00-\u9fff々〆ヵヶ]{2,}(?:[\u30a1-\u30ffー]{0,8})?")
STOP_TERMS: Set[str] = {
    "いい",
    "そう",
    "これ",
    "それ",
    "あれ",
    "ここ",
    "そこ",
    "どう",
    "ため",
    "よう",
}


@dataclass
class TermCandidate:
    source: str
    count: int
    examples: List[str]


def extract_term_candidates(entries: Iterable[TextEntry], min_count: int = 2, limit: int = 200) -> List[TermCandidate]:
    counts: Counter[str] = Counter()
    examples = {}

    for entry in entries:
        for match in TERM_RE.finditer(entry.source):
            term = match.group(0).strip()
            if not _keep_term(term):
                continue
            counts[term] += 1
            examples.setdefault(term, [])
            if len(examples[term]) < 3:
                examples[term].append(entry.source)

    candidates = [
        TermCandidate(source=term, count=count, examples=examples.get(term, []))
        for term, count in counts.items()
        if count >= min_count
    ]
    candidates.sort(key=lambda item: (-item.count, -len(item.source), item.source))
    return candidates[:limit]


def _keep_term(term: str) -> bool:
    if term in STOP_TERMS:
        return False
    if len(term) < 2 or len(term) > 24:
        return False
    if term.isdigit():
        return False
    return True
