from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List


GLOSSARY_COLUMNS = ["source", "target", "status", "note", "count"]


@dataclass
class GlossaryTerm:
    source: str
    target: str = ""
    status: str = "pending"
    note: str = ""
    count: int = 0


def load_glossary(path: Path) -> List[GlossaryTerm]:
    if not path.exists():
        return []
    terms: List[GlossaryTerm] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            terms.append(
                GlossaryTerm(
                    source=row.get("source", ""),
                    target=row.get("target", ""),
                    status=row.get("status", "pending"),
                    note=row.get("note", ""),
                    count=int(row.get("count") or 0),
                )
            )
    return terms


def save_glossary(path: Path, terms: Iterable[GlossaryTerm]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=GLOSSARY_COLUMNS, delimiter="\t")
        writer.writeheader()
        for term in terms:
            writer.writerow(
                {
                    "source": term.source,
                    "target": term.target,
                    "status": term.status,
                    "note": term.note,
                    "count": term.count,
                }
            )


def approved_glossary_map(terms: Iterable[GlossaryTerm]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for term in terms:
        if term.status == "approved" and term.source and term.target:
            result[term.source] = term.target
    return result
