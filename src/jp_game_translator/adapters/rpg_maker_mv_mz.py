from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

from jp_game_translator.core.models import DetectionResult, ExtractionBundle, TextEntry

from .base import GameAdapter

JsonPathPart = Union[str, int]

JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
PROTECTED_SYSTEM_TABLES = {
    "armorTypes",
    "elements",
    "equipTypes",
    "skillTypes",
    "switches",
    "variables",
    "weaponTypes",
}


class RpgMakerMvMzAdapter(GameAdapter):
    adapter_id = "rpg_maker_mv_mz"
    engine_name = "RPG Maker MV/MZ"

    def detect(self, game_dir: Path) -> Optional[DetectionResult]:
        data_dir = self._find_data_dir(game_dir)
        if data_dir is None:
            return None

        reasons = ["found RPG Maker data directory: %s" % data_dir.relative_to(game_dir)]
        confidence = 0.75

        if (data_dir / "System.json").exists():
            confidence += 0.15
            reasons.append("found System.json")
        if (game_dir / "www" / "js" / "rpg_core.js").exists() or (game_dir / "js" / "rpg_core.js").exists():
            confidence += 0.05
            reasons.append("found RPG Maker MV core script")
        if (game_dir / "www" / "js" / "rmmz_core.js").exists() or (game_dir / "js" / "rmmz_core.js").exists():
            confidence += 0.05
            reasons.append("found RPG Maker MZ core script")

        return DetectionResult(
            adapter_id=self.adapter_id,
            engine_name=self.engine_name,
            confidence=min(confidence, 0.99),
            reasons=reasons,
        )

    def extract(self, game_dir: Path, workspace: Path) -> ExtractionBundle:
        data_dir = self._require_data_dir(game_dir)
        entries: List[TextEntry] = []

        for json_file in sorted(data_dir.glob("*.json")):
            document = self._read_json(json_file)
            relative_file = json_file.relative_to(game_dir).as_posix()
            for path, value in self._walk_strings(document):
                if self._is_protected_translation_path(relative_file, path):
                    continue
                if not self._is_translatable(value):
                    continue
                pointer = self._json_pointer(path)
                entry_id = self._entry_id(relative_file, pointer, value)
                entries.append(
                    TextEntry(
                        id=entry_id,
                        source=value,
                        context="%s%s" % (relative_file, pointer),
                        file=relative_file,
                        adapter_id=self.adapter_id,
                        metadata={
                            "json_path": list(path),
                            "json_pointer": pointer,
                        },
                    )
                )

        return ExtractionBundle(
            adapter_id=self.adapter_id,
            engine_name=self.engine_name,
            entries=entries,
            notes=["RPG Maker MV/MZ JSON text extraction completed."],
        )

    def apply(
        self,
        game_dir: Path,
        workspace: Path,
        output_dir: Path,
        entries: Iterable[TextEntry],
        overwrite: bool = False,
        progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        progress = progress or (lambda _message: None)
        if output_dir.exists():
            if not overwrite:
                raise FileExistsError("output directory already exists: %s" % output_dir)
            if output_dir.resolve() == game_dir.resolve():
                raise ValueError("output directory must be different from source game directory")
            shutil.rmtree(str(output_dir))

        grouped: Dict[str, List[TextEntry]] = {}
        for entry in entries:
            if entry.adapter_id != self.adapter_id:
                continue
            if entry.status == "rejected":
                continue
            if not entry.translation:
                continue
            if self._is_protected_entry(entry):
                continue
            grouped.setdefault(entry.file, []).append(entry)

        total_entries = sum(len(file_entries) for file_entries in grouped.values())
        total_files = len(grouped)
        progress(
            "[apply] pending=%s, files=%s, adapter=%s, output=%s"
            % (total_entries, total_files, self.adapter_id, output_dir)
        )
        progress("[apply] copy start: source=%s, output=%s." % (game_dir, output_dir))
        shutil.copytree(str(game_dir), str(output_dir))
        progress("[apply] copy done: completed=0/%s; files=%s." % (total_entries, total_files))

        completed_entries = 0
        applied_entries = 0
        skipped_entries = 0
        for file_index, relative_file in enumerate(sorted(grouped), start=1):
            file_entries = grouped[relative_file]
            target_file = output_dir / Path(relative_file)
            if not target_file.exists():
                completed_entries += len(file_entries)
                skipped_entries += len(file_entries)
                progress(
                    "[apply] file %s/%s skipped: missing target; completed=%s/%s; applied=%s; skipped=%s; file=%s."
                    % (
                        file_index,
                        total_files,
                        completed_entries,
                        total_entries,
                        applied_entries,
                        skipped_entries,
                        relative_file,
                    )
                )
                continue
            document = self._read_json(target_file)
            file_applied = 0
            file_skipped = 0
            for entry in file_entries:
                path = entry.metadata.get("json_path")
                if not isinstance(path, list):
                    file_skipped += 1
                    continue
                self._set_path(document, path, entry.translation)
                file_applied += 1
            self._write_json(target_file, document)
            completed_entries += len(file_entries)
            applied_entries += file_applied
            skipped_entries += file_skipped
            progress(
                "[apply] file %s/%s done: applied=%s/%s; completed=%s/%s; skipped=%s; file=%s."
                % (
                    file_index,
                    total_files,
                    file_applied,
                    len(file_entries),
                    completed_entries,
                    total_entries,
                    skipped_entries,
                    relative_file,
                )
            )
        progress(
            "[apply] finished: applied=%s/%s; completed=%s/%s; skipped=%s; output=%s."
            % (applied_entries, total_entries, completed_entries, total_entries, skipped_entries, output_dir)
        )

    def _find_data_dir(self, game_dir: Path) -> Optional[Path]:
        candidates = [
            game_dir / "www" / "data",
            game_dir / "data",
        ]
        for candidate in candidates:
            if candidate.is_dir() and any(candidate.glob("*.json")):
                return candidate
        return None

    def _require_data_dir(self, game_dir: Path) -> Path:
        data_dir = self._find_data_dir(game_dir)
        if data_dir is None:
            raise FileNotFoundError("RPG Maker MV/MZ data directory was not found")
        return data_dir

    def _read_json(self, path: Path) -> Any:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)

    def _write_json(self, path: Path, document: Any) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")

    def _walk_strings(self, node: Any, path: Sequence[JsonPathPart] = ()) -> Iterator[Tuple[Tuple[JsonPathPart, ...], str]]:
        if isinstance(node, dict):
            for key, value in node.items():
                yield from self._walk_strings(value, tuple(path) + (str(key),))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from self._walk_strings(value, tuple(path) + (index,))
        elif isinstance(node, str):
            yield tuple(path), node

    def _is_translatable(self, value: str) -> bool:
        if not value or not value.strip():
            return False
        if not JAPANESE_RE.search(value):
            return False
        if len(value) > 5000:
            return False
        return True

    def _is_protected_entry(self, entry: TextEntry) -> bool:
        path = entry.metadata.get("json_path")
        if not isinstance(path, list):
            return False
        return self._is_protected_translation_path(entry.file, path)

    def _is_protected_translation_path(self, relative_file: str, path: Sequence[JsonPathPart]) -> bool:
        if path and path[-1] == "note":
            return True
        normalized_file = relative_file.replace("\\", "/")
        if normalized_file.endswith("data/System.json") and path and path[0] in PROTECTED_SYSTEM_TABLES:
            return True
        return False

    def _entry_id(self, relative_file: str, pointer: str, value: str) -> str:
        digest = hashlib.sha1(("%s|%s|%s" % (relative_file, pointer, value)).encode("utf-8")).hexdigest()
        return digest[:16]

    def _json_pointer(self, path: Sequence[JsonPathPart]) -> str:
        parts: List[str] = []
        for part in path:
            text = str(part).replace("~", "~0").replace("/", "~1")
            parts.append(text)
        return "/" + "/".join(parts)

    def _set_path(self, document: Any, path: Sequence[JsonPathPart], value: str) -> None:
        if not path:
            raise ValueError("cannot replace the whole JSON document with a string")
        node = document
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = value
