from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List

from .models import TextEntry


def load_entries(path: Path) -> List[TextEntry]:
    entries: List[TextEntry] = []
    if not path.exists():
        return entries
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            entries.append(TextEntry.from_dict(json.loads(line)))
    return entries


def save_entries(path: Path, entries: Iterable[TextEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            handle.write(json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
