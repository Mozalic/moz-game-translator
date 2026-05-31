from __future__ import annotations

import csv
import base64
import hashlib
import os
import io
import json
import ntpath
import re
import shutil
import struct
import subprocess
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple, Union

from jp_game_translator.core.models import DetectionResult, ExtractionBundle, TextEntry

from .base import GameAdapter

JsonPathPart = Union[str, int]

JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
MAX_TEXT_FILE_BYTES = 5 * 1024 * 1024
TEXT_SUFFIXES = {
    ".bytes",
    ".cfg",
    ".csv",
    ".ini",
    ".json",
    ".lang",
    ".po",
    ".properties",
    ".strings",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
UNITYPY_TEXT_FORMAT_PREFIX = "unity_text_asset_"
UNITY_MANAGED_LDSTR_FORMAT = "unity_managed_ldstr"
UNITY_SERIALIZED_RAW_STRING_FORMAT = "unity_serialized_raw_string"
UNITY_TYPETREE_STRING_FORMAT = "unity_typetree_string"
UNITY_DISPLAY_ALIAS_FORMAT = "unity_display_alias"
UNITY_CONTAINER_SUFFIXES = {
    ".assets",
    ".bundle",
}
DELIMITED_SUFFIXES = {
    ".csv": ",",
    ".tsv": "\t",
}
SKIPPED_DIR_NAMES = {
    ".git",
    "__pycache__",
    "il2cpp_data",
    "managed",
    "monobleedingedge",
    "plugins",
}
UNITY_DATA_MARKERS = (
    "globalgamemanagers",
    "globalgamemanagers.assets",
    "resources.assets",
    "sharedassets0.assets",
    "level0",
)
UNITY_SERIALIZED_RAW_MAX_STRING_BYTES = 1024
UNITY_SERIALIZED_RAW_MAX_FILE_BYTES = 512 * 1024 * 1024
UNITY_SERIALIZED_RAW_SAFE_SOURCES = {
    "\u30c7\u30fc\u30bf\u306a\u3057",
    "H\u30b9\u30c6\u30fc\u30bf\u30b9",
    "\u597d\u611f\u5ea6Lv",
    "\u521d\u4f53\u9a13\u306e\u76f8\u624b\u3000\u3000\u3000\u30b9\u30e9\u30a4\u30e0",
    "\u30aa\u30ca\u30cb\u30fc\u56de\u6570",
    "\u30d5\u30a7\u30e9\u56de\u6570",
    "\u7e1b\u3089\u308c\u305f\u56de\u6570",
    "\u540a\u308b\u3055\u308c\u305f\u56de\u6570",
    "\u907a\u7269\u30d7\u30ec\u30a4\u6570",
    "\u7dcf\u81a3\u5185\u5c04\u7cbe\u91cf",
    "\u7d76\u9802\u56de\u6570",
    "\u5b9f\u7e3e",
    "\u7dcf\u679a\u6570",
    "\u6700\u5927",
    "\u30bf\u30fc\u30f3",
    "\u30c9\u30ed\u30fc",
    "\u30de\u30ca",
    "\u30a8\u30ca\u30b8\u30fc",
    "\u30a2\u30f3\u30b3\u30e2\u30f3",
    "\u30b3\u30e2\u30f3",
    "\u30ec\u30a2",
    "\u5965\u7fa9",
    "\u4f1d\u8aac",
    "\u5263\u8853",
    "\u6b66\u8853",
    "\u6295\u5c04",
    "\u9b54\u8853",
    "\u9632\u5fa1",
    "\u7cbe\u795e",
    "\u767a\u60c5",
    "\u75c5\u6c17",
    "\u9053\u5177",
    "\u30e1\u30f3\u30bf\u30eb",
    "\u5e2b\u5320\u306e\u5bb6",
    "\u30bb\u30fc\u30d6\u30b9\u30ed\u30c3\u30c8",
    "\u30ed\u30fc\u30c9\u30b9\u30ed\u30c3\u30c8",
    "\u30aa\u30fc\u30c8\u30bb\u30fc\u30d6",
}
TMP_FONT_FALLBACK_NAMES = (
    "SimHei",
    "Microsoft YaHei",
    "Microsoft YaHei UI",
    "Arial Unicode MS",
)
TMP_FONT_FILE_CANDIDATES = (
    (Path("C:/Windows/Fonts/simhei.ttf"), ("SimHei",)),
    (Path("C:/Windows/Fonts/msyh.ttc"), ("Microsoft YaHei", "Microsoft YaHei UI")),
    (Path("C:/Windows/Fonts/simsun.ttc"), ("SimSun",)),
)
TMP_DYNAMIC_ATLAS_POPULATION_MODE = 1
TMP_GLYPH_SERIALIZED_SIZE = 52
TMP_CHARACTER_SERIALIZED_SIZE = 16
TMP_MAX_TABLE_ITEMS = 500_000
CJK_FONT_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
DEFAULT_UNITY_FONT_STRATEGY = "auto"
UNITY_FONT_STRATEGIES = {"none", "jgt-runtime", "asset-patch", "auto"}
UNITY_TYPETREE_STRING_SKIP_TYPES = {
    "AudioClip",
    "Cubemap",
    "Font",
    "Material",
    "Mesh",
    "Shader",
    "Sprite",
    "Texture2D",
    "TextAsset",
}


@dataclass
class UnityTextDocument:
    text: str
    encoding: str
    newline: str


@dataclass
class TmpFontAssetPatchInfo:
    name: str
    character_sequence: str
    source_font_file_offset: int
    atlas_population_mode_offset: int
    atlas_population_mode: int
    internal_dynamic_os_offset: int
    multi_atlas_enabled_offset: Optional[int]


@dataclass
class UnityResourceIndexItem:
    source: str
    kind: str
    path: str
    size: int = 0
    object_type: str = ""
    asset_name: str = ""
    path_id: Optional[int] = None
    text_entry_count: int = 0
    string_count: int = 0
    japanese_string_count: int = 0
    sample: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "kind": self.kind,
            "path": self.path,
            "size": self.size,
            "object_type": self.object_type,
            "asset_name": self.asset_name,
            "path_id": self.path_id,
            "text_entry_count": self.text_entry_count,
            "string_count": self.string_count,
            "japanese_string_count": self.japanese_string_count,
            "sample": list(self.sample),
            "metadata": dict(self.metadata),
        }


@dataclass
class UnityResourceIndex:
    game_dir: str
    generated_at: str
    data_dirs: List[str]
    items: List[UnityResourceIndexItem]
    notes: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": "jgt.unity_resource_index.v1",
            "game_dir": self.game_dir,
            "generated_at": self.generated_at,
            "data_dirs": list(self.data_dirs),
            "counts": self.counts(),
            "notes": list(self.notes),
            "items": [item.to_dict() for item in self.items],
        }

    def counts(self) -> Dict[str, int]:
        by_source: Dict[str, int] = {}
        by_kind: Dict[str, int] = {}
        text_entry_count = 0
        japanese_string_count = 0
        for item in self.items:
            by_source[item.source] = by_source.get(item.source, 0) + 1
            by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
            text_entry_count += item.text_entry_count
            japanese_string_count += item.japanese_string_count
        result = {
            "items": len(self.items),
            "text_entries": text_entry_count,
            "japanese_strings": japanese_string_count,
        }
        for key, value in sorted(by_source.items()):
            result["source.%s" % key] = value
        for key, value in sorted(by_kind.items()):
            result["kind.%s" % key] = value
        return result


class UnitySerializedReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def align(self) -> None:
        self.offset = (self.offset + 3) & ~3

    def skip(self, size: int) -> None:
        self._ensure(size)
        self.offset += size

    def read_int32(self) -> Tuple[int, int]:
        self._ensure(4)
        offset = self.offset
        value = struct.unpack_from("<i", self.data, self.offset)[0]
        self.offset += 4
        return offset, value

    def read_int64(self) -> Tuple[int, int]:
        self._ensure(8)
        offset = self.offset
        value = struct.unpack_from("<q", self.data, self.offset)[0]
        self.offset += 8
        return offset, value

    def read_bool(self) -> Tuple[int, bool]:
        self._ensure(1)
        offset = self.offset
        value = self.data[self.offset] != 0
        self.offset += 1
        self.align()
        return offset, value

    def read_string(self) -> Tuple[int, str]:
        offset, size = self.read_int32()
        if size < 0:
            raise ValueError("negative Unity string size")
        self._ensure(size)
        raw = self.data[self.offset : self.offset + size]
        value = raw.decode("utf-8")
        self.offset += size
        self.align()
        return offset, value

    def skip_float32(self) -> None:
        self.skip(4)

    def skip_pptr(self) -> None:
        self.skip(12)

    def _ensure(self, size: int) -> None:
        if self.offset + size > len(self.data):
            raise ValueError("Unity serialized data ended unexpectedly")


class UnityAdapter(GameAdapter):
    adapter_id = "unity"
    engine_name = "Unity"

    def detect(self, game_dir: Path) -> Optional[DetectionResult]:
        data_dirs = self._find_data_dirs(game_dir)
        reasons: List[str] = []
        confidence = 0.0

        if (game_dir / "UnityPlayer.dll").is_file():
            confidence += 0.35
            reasons.append("found UnityPlayer.dll")
        if (game_dir / "GameAssembly.dll").is_file():
            confidence += 0.15
            reasons.append("found GameAssembly.dll")
        if data_dirs:
            confidence += 0.35
            reasons.append(
                "found Unity data directory: %s"
                % ", ".join(self._display_relative(path, game_dir) for path in data_dirs[:3])
            )
        if any(self._has_marker(data_dir, "globalgamemanagers") for data_dir in data_dirs):
            confidence += 0.12
            reasons.append("found globalgamemanagers")
        if any(self._has_marker(data_dir, "resources.assets") for data_dir in data_dirs):
            confidence += 0.08
            reasons.append("found resources.assets")
        if any(self._managed_contains_unity_runtime(data_dir) for data_dir in data_dirs):
            confidence += 0.12
            reasons.append("found UnityEngine managed runtime")
        if any((data_dir / "StreamingAssets").is_dir() for data_dir in data_dirs):
            confidence += 0.08
            reasons.append("found StreamingAssets")
        executables = self._matching_executables(game_dir, data_dirs)
        if executables:
            confidence += 0.05
            reasons.append("found matching executable: %s" % ", ".join(path.name for path in executables[:3]))

        if confidence < 0.35 or not reasons:
            return None

        return DetectionResult(
            adapter_id=self.adapter_id,
            engine_name=self.engine_name,
            confidence=min(confidence, 0.99),
            reasons=reasons,
        )

    def extract(self, game_dir: Path, workspace: Path) -> ExtractionBundle:
        entries: List[TextEntry] = []
        notes: List[str] = []
        scanned_files = 0
        extracted_files = 0
        loose_entry_count = 0

        for text_file in self._find_loose_text_files(game_dir):
            scanned_files += 1
            document = self._read_text_document(text_file)
            if document is None:
                continue
            relative_file = text_file.relative_to(game_dir).as_posix()
            file_entries = self._extract_document_entries(relative_file, text_file, document)
            if file_entries:
                extracted_files += 1
                loose_entry_count += len(file_entries)
                entries.extend(file_entries)

        if extracted_files:
            notes.append(
                "Unity loose text extraction completed: %s entries from %s/%s candidate files."
                % (loose_entry_count, extracted_files, scanned_files)
            )
        else:
            notes.append(
                "Unity layout detected, but no supported loose text resources with Japanese text were found."
            )

        asset_entries, asset_notes = self._extract_unitypy_text_assets(game_dir)
        entries.extend(asset_entries)
        notes.extend(asset_notes)

        typetree_entries, typetree_notes = self._extract_unitypy_typetree_strings(game_dir)
        entries.extend(typetree_entries)
        notes.extend(typetree_notes)

        raw_entries, raw_notes = self._extract_unity_serialized_raw_strings(game_dir)
        entries.extend(raw_entries)
        notes.extend(raw_notes)

        managed_entries, managed_notes = self._extract_managed_ldstr_entries(game_dir, workspace)
        entries.extend(managed_entries)
        notes.extend(managed_notes)

        notes.append(
            "Unity apply patches loose JSON/CSV/TSV/text resources, UnityPy-readable TextAsset entries, "
            "UnityPy typetree string fields, raw serialized UTF-8 strings, and Mono/Cecil-readable managed ldstr strings. "
            "Verify patched output in-game before distribution."
        )
        notes.append(
            "Unity font fallback is configurable at apply time: none, jgt-runtime, asset-patch, or auto."
        )

        return ExtractionBundle(
            adapter_id=self.adapter_id,
            engine_name=self.engine_name,
            entries=entries,
            notes=notes,
        )

    def build_offline_resource_index(
        self,
        game_dir: Path,
        *,
        assetripper_export_dir: Optional[Path] = None,
        include_unitypy: bool = True,
    ) -> UnityResourceIndex:
        notes: List[str] = []
        items: List[UnityResourceIndexItem] = []
        data_dirs = [self._display_relative(path, game_dir) for path in self._find_data_dirs(game_dir)]

        loose_items = self._index_loose_text_resources(game_dir)
        items.extend(loose_items)
        notes.append("Indexed %s loose text resources." % len(loose_items))

        container_items = self._index_unity_containers(game_dir, include_unitypy=include_unitypy)
        items.extend(container_items)
        if include_unitypy:
            notes.append("Indexed %s UnityPy-readable container/object records." % len(container_items))
        else:
            notes.append("UnityPy object indexing disabled; only loose text and AssetRipper export files were indexed.")

        raw_items = self._index_unity_serialized_raw_string_files(game_dir)
        items.extend(raw_items)
        notes.append("Indexed %s Unity serialized raw string files." % len(raw_items))

        if assetripper_export_dir is not None:
            export_items = self._index_assetripper_export(game_dir, assetripper_export_dir)
            items.extend(export_items)
            notes.append("Indexed %s AssetRipper export text/YAML resources." % len(export_items))
        else:
            notes.append(
                "AssetRipper export directory was not provided; run AssetRipper externally and pass its export directory for full recovered-project indexing."
            )

        return UnityResourceIndex(
            game_dir=str(game_dir.resolve()),
            generated_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
            data_dirs=data_dirs,
            items=items,
            notes=notes,
        )

    def apply(
        self,
        game_dir: Path,
        workspace: Path,
        output_dir: Path,
        entries: Iterable[TextEntry],
        overwrite: bool = False,
        progress: Optional[Callable[[str], None]] = None,
        font_strategy: str = DEFAULT_UNITY_FONT_STRATEGY,
    ) -> None:
        progress = progress or (lambda _message: None)
        font_strategy = self._normalize_font_strategy(font_strategy)
        adapter_entries = [entry for entry in entries if entry.adapter_id == self.adapter_id]
        display_aliases = self._collect_display_aliases(adapter_entries, workspace, game_dir)
        if output_dir.exists():
            if not overwrite:
                raise FileExistsError("output directory already exists: %s" % output_dir)
            if output_dir.resolve() == game_dir.resolve():
                raise ValueError("output directory must be different from source game directory")
            shutil.rmtree(str(output_dir))

        grouped: Dict[str, List[TextEntry]] = {}
        translated_texts: List[str] = []
        translated_texts.extend(display_aliases.values())
        for entry in adapter_entries:
            if self._is_display_alias_entry(entry):
                continue
            if entry.status == "rejected":
                continue
            if not entry.translation:
                continue
            grouped.setdefault(entry.file, []).append(entry)
            translated_texts.append(entry.translation)

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

            file_applied = 0
            file_skipped = 0
            by_format: Dict[str, List[TextEntry]] = {}
            for entry in file_entries:
                by_format.setdefault(str(entry.metadata.get("format") or "text_line"), []).append(entry)
            for format_name, format_entries in by_format.items():
                applied, skipped = self._apply_format_entries(target_file, format_name, format_entries)
                file_applied += applied
                file_skipped += skipped

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

        self._apply_font_strategy(
            output_dir,
            translated_texts,
            progress,
            font_strategy,
            display_aliases,
        )
        self._apply_display_aliases(output_dir, display_aliases, progress)
        progress(
            "[apply] finished: applied=%s/%s; completed=%s/%s; skipped=%s; output=%s."
            % (applied_entries, total_entries, completed_entries, total_entries, skipped_entries, output_dir)
        )

    def _find_data_dirs(self, game_dir: Path) -> List[Path]:
        candidates: List[Path] = []
        if self._looks_like_data_dir(game_dir):
            candidates.append(game_dir)
        if game_dir.is_dir():
            for child in game_dir.iterdir():
                if child.is_dir() and child.name.endswith("_Data") and self._looks_like_data_dir(child):
                    candidates.append(child)

        unique: List[Path] = []
        seen = set()
        for candidate in candidates:
            key = str(candidate.resolve())
            if key not in seen:
                seen.add(key)
                unique.append(candidate)
        return unique

    def _looks_like_data_dir(self, directory: Path) -> bool:
        if not directory.is_dir():
            return False
        has_marker = any((directory / marker).exists() for marker in UNITY_DATA_MARKERS)
        has_runtime_dir = (directory / "Managed").is_dir() or (directory / "StreamingAssets").is_dir()
        return has_marker or (directory.name.endswith("_Data") and has_runtime_dir)

    def _has_marker(self, directory: Path, marker: str) -> bool:
        return (directory / marker).exists()

    def _managed_contains_unity_runtime(self, data_dir: Path) -> bool:
        managed = data_dir / "Managed"
        if not managed.is_dir():
            return False
        return any(managed.glob("UnityEngine*.dll"))

    def _matching_executables(self, game_dir: Path, data_dirs: Sequence[Path]) -> List[Path]:
        executables: List[Path] = []
        for data_dir in data_dirs:
            if data_dir.name.endswith("_Data"):
                expected = data_dir.with_name(data_dir.name[: -len("_Data")] + ".exe")
                if expected.is_file():
                    executables.append(expected)
        if game_dir.is_dir():
            executables.extend(path for path in game_dir.glob("*.exe") if path.is_file())
        unique: List[Path] = []
        seen = set()
        for path in executables:
            key = str(path.resolve())
            if key not in seen:
                seen.add(key)
                unique.append(path)
        return unique

    def _find_loose_text_files(self, game_dir: Path) -> List[Path]:
        candidates: List[Path] = []
        if not game_dir.is_dir():
            return candidates
        for path in game_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if self._is_under_skipped_dir(path, game_dir):
                continue
            try:
                if path.stat().st_size > MAX_TEXT_FILE_BYTES:
                    continue
            except OSError:
                continue
            candidates.append(path)
        return sorted(candidates, key=lambda item: item.relative_to(game_dir).as_posix().lower())

    def _find_unity_asset_containers(self, game_dir: Path) -> List[Path]:
        candidates: List[Path] = []
        seen = set()
        for data_dir in self._find_data_dirs(game_dir):
            for child in data_dir.iterdir():
                if child.is_file() and child.suffix.lower() in UNITY_CONTAINER_SUFFIXES:
                    try:
                        if child.stat().st_size < 64:
                            continue
                    except OSError:
                        continue
                    key = str(child.resolve())
                    if key not in seen:
                        seen.add(key)
                        candidates.append(child)

            streaming_assets = data_dir / "StreamingAssets"
            if streaming_assets.is_dir():
                for child in streaming_assets.rglob("*"):
                    if not child.is_file():
                        continue
                    if child.suffix.lower() != ".bundle":
                        continue
                    try:
                        if child.stat().st_size < 64:
                            continue
                    except OSError:
                        continue
                    key = str(child.resolve())
                    if key not in seen:
                        seen.add(key)
                        candidates.append(child)

        return sorted(candidates, key=lambda item: self._display_relative(item, game_dir).lower())

    def _find_unity_serialized_raw_files(self, game_dir: Path) -> List[Path]:
        candidates: List[Path] = []
        seen: Set[str] = set()
        for data_dir in self._find_data_dirs(game_dir):
            for child in data_dir.iterdir():
                if not child.is_file():
                    continue
                name = child.name.lower()
                suffix = child.suffix.lower()
                is_serialized_file = (
                    name in {"globalgamemanagers", "globalgamemanagers.assets", "resources.assets"}
                    or bool(re.fullmatch(r"level\d+", name))
                    or (suffix == ".assets" and not name.endswith(".ress"))
                )
                if not is_serialized_file:
                    continue
                try:
                    size = child.stat().st_size
                except OSError:
                    continue
                if size < 64 or size > UNITY_SERIALIZED_RAW_MAX_FILE_BYTES:
                    continue
                key = str(child.resolve())
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(child)
        return sorted(candidates, key=lambda item: self._display_relative(item, game_dir).lower())

    def _index_loose_text_resources(self, game_dir: Path) -> List[UnityResourceIndexItem]:
        items: List[UnityResourceIndexItem] = []
        for text_file in self._find_loose_text_files(game_dir):
            document = self._read_text_document(text_file)
            if document is None:
                continue
            relative_file = text_file.relative_to(game_dir).as_posix()
            entries = self._extract_document_entries(relative_file, text_file, document)
            samples = self._sample_japanese_strings([entry.source for entry in entries])
            japanese_count = len([entry for entry in entries if self._is_translatable(entry.source)])
            items.append(
                UnityResourceIndexItem(
                    source="loose",
                    kind="text_resource",
                    path=relative_file,
                    size=self._safe_file_size(text_file),
                    text_entry_count=len(entries),
                    string_count=len(entries),
                    japanese_string_count=japanese_count,
                    sample=samples,
                    metadata={"encoding": document.encoding, "newline": document.newline},
                )
            )
        return items

    def _index_unity_containers(self, game_dir: Path, *, include_unitypy: bool = True) -> List[UnityResourceIndexItem]:
        containers = self._find_unity_asset_containers(game_dir)
        if not include_unitypy:
            return [
                UnityResourceIndexItem(
                    source="unity_container",
                    kind="container",
                    path=container.relative_to(game_dir).as_posix(),
                    size=self._safe_file_size(container),
                )
                for container in containers
            ]

        try:
            import UnityPy  # type: ignore
        except ImportError:
            return [
                UnityResourceIndexItem(
                    source="unity_container",
                    kind="container",
                    path=container.relative_to(game_dir).as_posix(),
                    size=self._safe_file_size(container),
                    metadata={"warning": "UnityPy is not installed; object listing skipped."},
                )
                for container in containers
            ]

        items: List[UnityResourceIndexItem] = []
        for container in containers:
            relative_container = container.relative_to(game_dir).as_posix()
            try:
                environment = UnityPy.load(str(container))
            except Exception as exc:
                items.append(
                    UnityResourceIndexItem(
                        source="unity_container",
                        kind="container",
                        path=relative_container,
                        size=self._safe_file_size(container),
                        metadata={"warning": "UnityPy failed to load container: %s" % exc},
                    )
                )
                continue

            object_count = 0
            for obj in getattr(environment, "objects", []):
                object_count += 1
                item = self._index_unity_object(container, relative_container, obj)
                items.append(item)
            if object_count == 0:
                items.append(
                    UnityResourceIndexItem(
                        source="unity_container",
                        kind="container",
                        path=relative_container,
                        size=self._safe_file_size(container),
                        metadata={"warning": "UnityPy loaded container with no objects."},
                    )
                )
        return items

    def _index_unity_object(self, container: Path, relative_container: str, obj: Any) -> UnityResourceIndexItem:
        object_type = getattr(getattr(obj, "type", None), "name", str(getattr(obj, "type", "")))
        path_id = int(getattr(obj, "path_id", 0) or 0)
        asset_name = ""
        samples: List[str] = []
        text_entry_count = 0
        string_count = 0
        japanese_string_count = 0
        metadata: Dict[str, Any] = {}

        data = None
        try:
            data = obj.read()
            asset_name = str(getattr(data, "name", "") or getattr(data, "m_Name", "") or "")
        except Exception as exc:
            metadata["read_warning"] = str(exc)

        if object_type == "TextAsset" and data is not None:
            document = self._text_asset_document(self._text_asset_raw_script(data))
            if document is not None:
                entries = self._extract_text_asset_entries(relative_container, asset_name or str(path_id), path_id, document)
                text_entry_count = len(entries)
                string_count = len(entries)
                samples = self._sample_japanese_strings([entry.source for entry in entries])
                japanese_string_count = len([entry for entry in entries if self._is_translatable(entry.source)])
                metadata["encoding"] = document.encoding
                metadata["newline"] = document.newline
        else:
            tree_strings = self._unity_object_string_values(obj, data)
            string_count = len(tree_strings)
            japanese_values = [value for value in tree_strings if self._is_translatable(value)]
            japanese_string_count = len(japanese_values)
            samples = self._sample_japanese_strings(japanese_values)

        return UnityResourceIndexItem(
            source="unitypy",
            kind="asset_object",
            path=relative_container,
            size=self._safe_file_size(container),
            object_type=object_type,
            asset_name=asset_name,
            path_id=path_id,
            text_entry_count=text_entry_count,
            string_count=string_count,
            japanese_string_count=japanese_string_count,
            sample=samples,
            metadata=metadata,
        )

    def _unity_object_string_values(self, obj: Any, data: Any) -> List[str]:
        for reader in (
            lambda: obj.read_typetree(),
            lambda: data.read_typetree() if data is not None else None,
        ):
            try:
                tree = reader()
            except Exception:
                continue
            if tree is not None:
                return self._collect_nested_strings(tree)
        if data is None:
            return []
        values: List[str] = []
        for name in dir(data):
            if name.startswith("_"):
                continue
            try:
                value = getattr(data, name)
            except Exception:
                continue
            if isinstance(value, str):
                values.append(value)
        return values

    def _collect_nested_strings(self, value: Any, *, limit: int = 2000) -> List[str]:
        values: List[str] = []
        seen: Set[int] = set()

        def visit(item: Any) -> None:
            if len(values) >= limit:
                return
            if isinstance(item, str):
                values.append(item)
                return
            if isinstance(item, (bytes, bytearray, int, float, bool)) or item is None:
                return
            item_id = id(item)
            if item_id in seen:
                return
            seen.add(item_id)
            if isinstance(item, dict):
                for key, child in item.items():
                    visit(key)
                    visit(child)
                return
            if isinstance(item, (list, tuple, set)):
                for child in item:
                    visit(child)

        visit(value)
        return values

    def _index_unity_serialized_raw_string_files(self, game_dir: Path) -> List[UnityResourceIndexItem]:
        items: List[UnityResourceIndexItem] = []
        for serialized_file in self._find_unity_serialized_raw_files(game_dir):
            try:
                data = serialized_file.read_bytes()
            except OSError:
                continue
            strings = [text for _offset, _text_offset, _byte_length, text in self._iter_unity_serialized_raw_strings(data)]
            if not strings:
                continue
            relative_file = serialized_file.relative_to(game_dir).as_posix()
            items.append(
                UnityResourceIndexItem(
                    source="unity_serialized",
                    kind="serialized_file",
                    path=relative_file,
                    size=self._safe_file_size(serialized_file),
                    text_entry_count=len(strings),
                    string_count=len(strings),
                    japanese_string_count=len(strings),
                    sample=self._sample_japanese_strings(strings),
                    metadata={
                        "format": UNITY_SERIALIZED_RAW_STRING_FORMAT,
                        "patch_mode": "equal_or_shorter_utf8_padded",
                    },
                )
            )
        return items

    def _index_assetripper_export(self, game_dir: Path, export_dir: Path) -> List[UnityResourceIndexItem]:
        if not export_dir.is_dir():
            return [
                UnityResourceIndexItem(
                    source="assetripper",
                    kind="export_root",
                    path=str(export_dir),
                    metadata={"warning": "AssetRipper export directory does not exist."},
                )
            ]

        text_suffixes = TEXT_SUFFIXES | {".asset", ".prefab", ".unity", ".controller", ".anim", ".overridecontroller"}
        items: List[UnityResourceIndexItem] = []
        for path in export_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in text_suffixes:
                continue
            if self._safe_file_size(path) > MAX_TEXT_FILE_BYTES:
                continue
            document = self._read_text_document(path)
            if document is None:
                continue
            values = self._asset_export_candidate_strings(document.text)
            japanese_values = [value for value in values if self._is_translatable(value)]
            if not japanese_values:
                continue
            try:
                relative_path = path.relative_to(export_dir).as_posix()
            except ValueError:
                relative_path = str(path)
            items.append(
                UnityResourceIndexItem(
                    source="assetripper",
                    kind="export_text",
                    path=relative_path,
                    size=self._safe_file_size(path),
                    string_count=len(values),
                    japanese_string_count=len(japanese_values),
                    sample=self._sample_japanese_strings(japanese_values),
                    metadata={
                        "export_dir": str(export_dir),
                        "encoding": document.encoding,
                        "newline": document.newline,
                    },
                )
            )
        return items

    def _asset_export_candidate_strings(self, text: str) -> List[str]:
        values: List[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "//")):
                continue
            separator = self._find_separator(stripped)
            if separator >= 0:
                candidate = stripped[separator + 1 :].strip()
            else:
                candidate = stripped
            if len(candidate) >= 2:
                values.append(candidate.strip("'\""))
        return values

    def _sample_japanese_strings(self, values: Iterable[str], limit: int = 5) -> List[str]:
        samples: List[str] = []
        seen: Set[str] = set()
        for value in values:
            normalized = value.strip()
            if not normalized or normalized in seen or not self._is_translatable(normalized):
                continue
            seen.add(normalized)
            samples.append(normalized[:160])
            if len(samples) >= limit:
                break
        return samples

    def _safe_file_size(self, path: Path) -> int:
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0

    def _extract_unitypy_text_assets(self, game_dir: Path) -> Tuple[List[TextEntry], List[str]]:
        containers = self._find_unity_asset_containers(game_dir)
        if not containers:
            return [], ["No Unity asset containers were found for UnityPy extraction."]

        try:
            import UnityPy  # type: ignore
        except ImportError:
            return [], [
                "UnityPy is not installed; packed Unity .assets/.bundle TextAsset extraction was skipped. "
                "Install it with: python -m pip install UnityPy"
            ]

        entries: List[TextEntry] = []
        loaded_containers = 0
        text_asset_count = 0
        extracted_assets = 0
        failed_containers: List[str] = []
        for container in containers:
            relative_container = container.relative_to(game_dir).as_posix()
            try:
                environment = UnityPy.load(str(container))
            except Exception as exc:
                if len(failed_containers) < 5:
                    failed_containers.append("%s (%s)" % (relative_container, exc))
                continue

            loaded_containers += 1
            for obj in environment.objects:
                object_type = getattr(obj.type, "name", str(obj.type))
                if object_type != "TextAsset":
                    continue
                text_asset_count += 1
                try:
                    data = obj.read()
                except Exception:
                    continue
                asset_name = str(getattr(data, "name", "") or getattr(data, "m_Name", "") or obj.path_id)
                raw_script = self._text_asset_raw_script(data)
                document = self._text_asset_document(raw_script)
                if document is None:
                    continue
                asset_entries = self._extract_text_asset_entries(
                    relative_container=relative_container,
                    asset_name=asset_name,
                    path_id=int(getattr(obj, "path_id", 0)),
                    document=document,
                )
                if asset_entries:
                    extracted_assets += 1
                    entries.extend(asset_entries)

        notes = [
            "UnityPy TextAsset extraction completed: %s entries from %s/%s TextAssets in %s/%s containers."
            % (len(entries), extracted_assets, text_asset_count, loaded_containers, len(containers))
        ]
        if failed_containers:
            notes.append("Skipped Unity containers that UnityPy could not load: %s." % "; ".join(failed_containers))
        return entries, notes

    def _extract_managed_ldstr_entries(self, game_dir: Path, workspace: Path) -> Tuple[List[TextEntry], List[str]]:
        assemblies = self._find_managed_string_assemblies(game_dir)
        if not assemblies:
            return [], ["No managed Assembly-CSharp*.dll files were found for ldstr extraction."]

        tool_dir = workspace / ".jgt_tools" / "managed_strings"
        tool, tool_notes = self._prepare_managed_string_tool(game_dir, tool_dir)
        notes = list(tool_notes)
        if tool is None:
            notes.append("Managed ldstr extraction skipped because the Mono.Cecil string tool could not be built.")
            return [], notes

        entries: List[TextEntry] = []
        scanned_strings = 0
        extracted_strings = 0
        failed_assemblies: List[str] = []
        for assembly_path in assemblies:
            relative_assembly = assembly_path.relative_to(game_dir).as_posix()
            dump_path = tool_dir / ("%s.tsv" % hashlib.sha1(str(assembly_path).encode("utf-8")).hexdigest()[:12])
            result = subprocess.run(
                [str(tool.resolve()), "dump", str(assembly_path.resolve()), str(dump_path.resolve())],
                cwd=str(tool_dir.resolve()),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                if len(failed_assemblies) < 5:
                    detail = " ".join((result.stdout or "").strip().splitlines()[-2:])
                    failed_assemblies.append("%s (%s)" % (relative_assembly, detail or result.returncode))
                continue
            for line in dump_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                scanned_strings += 1
                parts = line.split("\t")
                if len(parts) < 4:
                    continue
                method_token_text, instruction_index_text, source_b64, context_b64 = parts[:4]
                source = self._decode_base64_text(source_b64)
                context = self._decode_base64_text(context_b64)
                if not self._is_translatable(source):
                    continue
                extracted_strings += 1
                location = "%s:%s" % (method_token_text, instruction_index_text)
                entries.append(
                    TextEntry(
                        id=self._entry_id(relative_assembly, "managed_ldstr", location, source),
                        source=source,
                        context="%s#%s" % (relative_assembly, context),
                        file=relative_assembly,
                        adapter_id=self.adapter_id,
                        metadata={
                            "format": UNITY_MANAGED_LDSTR_FORMAT,
                            "managed_context": context,
                            "method_token": int(method_token_text),
                            "instruction_index": int(instruction_index_text),
                        },
                    )
                )

        notes.append(
            "Managed ldstr extraction completed: %s entries from %s/%s strings in %s assemblies."
            % (len(entries), extracted_strings, scanned_strings, len(assemblies))
        )
        if failed_assemblies:
            notes.append("Skipped managed assemblies that could not be dumped: %s." % "; ".join(failed_assemblies))
        return entries, notes

    def _extract_unitypy_typetree_strings(self, game_dir: Path) -> Tuple[List[TextEntry], List[str]]:
        containers = self._find_unity_asset_containers(game_dir)
        if not containers:
            return [], ["No Unity asset containers were found for typetree string extraction."]

        try:
            import UnityPy  # type: ignore
        except ImportError:
            return [], [
                "UnityPy is not installed; Unity typetree string extraction was skipped. "
                "Install it with: python -m pip install UnityPy"
            ]

        entries: List[TextEntry] = []
        scanned_objects = 0
        extracted_objects = 0
        failed_containers: List[str] = []
        for container in containers:
            relative_container = container.relative_to(game_dir).as_posix()
            try:
                environment = UnityPy.load(str(container))
            except Exception as exc:
                if len(failed_containers) < 5:
                    failed_containers.append("%s (%s)" % (relative_container, exc))
                continue

            for obj in getattr(environment, "objects", []):
                object_type = getattr(getattr(obj, "type", None), "name", str(getattr(obj, "type", "")))
                if object_type in UNITY_TYPETREE_STRING_SKIP_TYPES:
                    continue
                scanned_objects += 1
                try:
                    tree = obj.read_typetree()
                except Exception:
                    continue
                path_id = int(getattr(obj, "path_id", 0) or 0)
                asset_name = self._unity_object_asset_name(obj)
                object_entries: List[TextEntry] = []
                for path, value in self._walk_typetree_strings(tree):
                    if not self._is_translatable(value):
                        continue
                    pointer = self._json_pointer(path)
                    object_entries.append(
                        TextEntry(
                            id=self._entry_id(relative_container, "typetree", "%s:%s" % (path_id, pointer), value),
                            source=value,
                            context="%s#%s:%s%s" % (relative_container, object_type, path_id, pointer),
                            file=relative_container,
                            adapter_id=self.adapter_id,
                            metadata={
                                "format": UNITY_TYPETREE_STRING_FORMAT,
                                "unity_container": relative_container,
                                "object_type": object_type,
                                "asset_name": asset_name,
                                "path_id": path_id,
                                "typetree_path": list(path),
                                "typetree_pointer": pointer,
                            },
                        )
                    )
                if object_entries:
                    extracted_objects += 1
                    entries.extend(object_entries)

        notes = [
            "UnityPy typetree string extraction completed: %s entries from %s/%s readable objects in %s containers."
            % (len(entries), extracted_objects, scanned_objects, len(containers))
        ]
        if failed_containers:
            notes.append("Skipped Unity containers for typetree extraction: %s." % "; ".join(failed_containers))
        return entries, notes

    def _unity_object_asset_name(self, obj: Any) -> str:
        try:
            data = obj.read()
        except Exception:
            return ""
        return str(getattr(data, "name", "") or getattr(data, "m_Name", "") or "")

    def _walk_typetree_strings(
        self,
        value: Any,
        path: Tuple[JsonPathPart, ...] = (),
    ) -> Iterator[Tuple[Tuple[JsonPathPart, ...], str]]:
        if isinstance(value, str):
            yield path, value
            return
        if isinstance(value, dict):
            for key, child in value.items():
                yield from self._walk_typetree_strings(child, path + (str(key),))
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                yield from self._walk_typetree_strings(child, path + (index,))

    def _extract_unity_serialized_raw_strings(self, game_dir: Path) -> Tuple[List[TextEntry], List[str]]:
        serialized_files = self._find_unity_serialized_raw_files(game_dir)
        if not serialized_files:
            return [], ["No Unity serialized raw files were found for UTF-8 string extraction."]

        entries: List[TextEntry] = []
        scanned_files = 0
        extracted_files = 0
        failed_files: List[str] = []
        for serialized_file in serialized_files:
            relative_file = serialized_file.relative_to(game_dir).as_posix()
            try:
                data = serialized_file.read_bytes()
            except OSError as exc:
                if len(failed_files) < 5:
                    failed_files.append("%s (%s)" % (relative_file, exc))
                continue
            scanned_files += 1
            file_entries: List[TextEntry] = []
            for string_offset, text_offset, byte_length, source in self._iter_unity_serialized_raw_strings(data):
                file_entries.append(
                    TextEntry(
                        id=self._entry_id(relative_file, "serialized_raw", str(string_offset), source),
                        source=source,
                        context="%s#raw_utf8:%s" % (relative_file, string_offset),
                        file=relative_file,
                        adapter_id=self.adapter_id,
                        metadata={
                            "format": UNITY_SERIALIZED_RAW_STRING_FORMAT,
                            "raw_string_offset": string_offset,
                            "raw_text_offset": text_offset,
                            "raw_byte_length": byte_length,
                            "encoding": "utf-8",
                            "patch_mode": "equal_or_shorter_utf8_padded",
                            "raw_patch_policy": (
                                "safe" if source in UNITY_SERIALIZED_RAW_SAFE_SOURCES else "candidate"
                            ),
                        },
                    )
                )
            if file_entries:
                extracted_files += 1
                entries.extend(file_entries)

        notes = [
            (
                "Unity serialized raw UTF-8 extraction completed: %s entries from %s/%s serialized files. "
                "Apply only patches built-in safe UI labels or entries explicitly marked raw_patch_policy=allow; "
                "shorter translations are padded, longer translations are skipped."
            )
            % (len(entries), extracted_files, scanned_files)
        ]
        if failed_files:
            notes.append("Skipped Unity serialized files that could not be read: %s." % "; ".join(failed_files))
        return entries, notes

    def _iter_unity_serialized_raw_strings(self, data: bytes) -> Iterator[Tuple[int, int, int, str]]:
        offset = 0
        data_length = len(data)
        while offset <= data_length - 8:
            try:
                byte_length = struct.unpack_from("<i", data, offset)[0]
            except struct.error:
                break
            if 0 < byte_length <= UNITY_SERIALIZED_RAW_MAX_STRING_BYTES:
                text_offset = offset + 4
                text_end = text_offset + byte_length
                aligned_end = (text_end + 3) & ~3
                if aligned_end <= data_length:
                    raw = data[text_offset:text_end]
                    try:
                        value = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        value = ""
                    if self._is_plausible_serialized_raw_string(value):
                        yield offset, text_offset, byte_length, value
                        offset = aligned_end
                        continue
            offset += 4

    def _is_plausible_serialized_raw_string(self, value: str) -> bool:
        if not value or not self._is_translatable(value):
            return False
        if "\x00" in value or "\ufffd" in value:
            return False
        if any(ord(char) < 32 and char not in "\r\n\t" for char in value):
            return False
        if len(value.strip()) == 0:
            return False
        return True

    def _find_managed_string_assemblies(self, game_dir: Path) -> List[Path]:
        assemblies: List[Path] = []
        seen: Set[str] = set()
        for data_dir in self._find_data_dirs(game_dir):
            managed_dir = data_dir / "Managed"
            if not managed_dir.is_dir():
                continue
            for name in ("Assembly-CSharp.dll", "Assembly-CSharp-firstpass.dll"):
                assembly = managed_dir / name
                if not assembly.is_file():
                    continue
                key = str(assembly.resolve())
                if key not in seen:
                    seen.add(key)
                    assemblies.append(assembly)
        return assemblies

    def _decode_base64_text(self, value: str) -> str:
        return base64.b64decode(value.encode("ascii")).decode("utf-8")

    def _encode_base64_text(self, value: str) -> str:
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    def _text_asset_document(self, raw_script: Any) -> Optional[UnityTextDocument]:
        if raw_script is None:
            return None
        if isinstance(raw_script, str):
            text = raw_script
            encoding = "utf-8"
        else:
            try:
                raw = bytes(raw_script)
            except TypeError:
                return None
            decoded = self._decode_text_bytes(raw)
            if decoded is None:
                return None
            text, encoding = decoded
        if not self._is_plausible_text(text):
            return None
        return UnityTextDocument(text=text, encoding=encoding, newline=self._detect_newline(text))

    def _text_asset_raw_script(self, data: Any) -> Any:
        raw_script = getattr(data, "script", None)
        if raw_script is None:
            raw_script = getattr(data, "m_Script", None)
        return raw_script

    def _decode_text_bytes(self, raw: bytes) -> Optional[Tuple[str, str]]:
        if not raw:
            return None
        encodings = ["utf-8-sig"] if raw.startswith(b"\xef\xbb\xbf") else ["utf-8"]
        encodings.extend(["utf-16", "cp932", "gbk"])
        for encoding in encodings:
            try:
                text = raw.decode(encoding)
            except UnicodeError:
                continue
            if self._is_plausible_text(text):
                return text, encoding
        return None

    def _extract_text_asset_entries(
        self,
        relative_container: str,
        asset_name: str,
        path_id: int,
        document: UnityTextDocument,
    ) -> List[TextEntry]:
        if self._is_control_only_text_asset(asset_name, document):
            return []

        virtual_file = "%s#%s" % (relative_container, asset_name)
        stripped = document.text.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(document.text)
            except ValueError:
                pass
            else:
                entries = self._extract_json_entries(virtual_file, parsed, document)
                return self._mark_text_asset_entries(entries, relative_container, asset_name, path_id)

        delimiter = self._guess_delimiter(document.text, asset_name)
        if delimiter is not None:
            try:
                entries = self._extract_delimited_entries(virtual_file, document, delimiter)
            except csv.Error:
                entries = self._extract_text_line_entries(virtual_file, document)
            else:
                entries = self._filter_text_asset_delimited_entries(asset_name, document, entries, delimiter)
        else:
            entries = self._extract_text_line_entries(virtual_file, document)
        entries = self._filter_text_asset_comment_line_entries(document, entries)
        return self._mark_text_asset_entries(entries, relative_container, asset_name, path_id)

    def _is_control_only_text_asset(self, asset_name: str, document: UnityTextDocument) -> bool:
        normalized_name = asset_name.strip().lower()
        if normalized_name in {"characterexpression", "charaexpression"}:
            return True
        first_line = next((line for line in document.text.splitlines() if line.strip()), "")
        if not first_line:
            return False
        try:
            header = next(csv.reader([first_line]))
        except csv.Error:
            return False
        normalized_header = {cell.strip().lstrip("\ufeff").lower() for cell in header}
        return (
            "\u8868\u60c5id" in normalized_header
            and "body" in normalized_header
            and ("face" in normalized_header or "facered" in normalized_header)
        )

    def _mark_text_asset_entries(
        self,
        entries: List[TextEntry],
        relative_container: str,
        asset_name: str,
        path_id: int,
    ) -> List[TextEntry]:
        for entry in entries:
            inner_format = str(entry.metadata.get("format") or "text_line")
            entry.file = relative_container
            if inner_format == UNITY_DISPLAY_ALIAS_FORMAT:
                entry.metadata["format"] = UNITY_DISPLAY_ALIAS_FORMAT
            else:
                entry.metadata["format"] = UNITYPY_TEXT_FORMAT_PREFIX + inner_format
            entry.metadata["inner_format"] = inner_format
            entry.metadata["unity_container"] = relative_container
            entry.metadata["asset_name"] = asset_name
            entry.metadata["path_id"] = path_id
        return entries

    def _filter_text_asset_comment_line_entries(
        self,
        document: UnityTextDocument,
        entries: List[TextEntry],
    ) -> List[TextEntry]:
        comment_lines = {
            line_index
            for line_index, line in enumerate(document.text.splitlines())
            if line.lstrip().startswith("#")
        }
        if not comment_lines:
            return entries

        filtered: List[TextEntry] = []
        for entry in entries:
            if entry.metadata.get("format") == "text_line":
                try:
                    line_index = int(entry.metadata.get("line", -1))
                except (TypeError, ValueError):
                    line_index = -1
                if line_index in comment_lines:
                    continue
            filtered.append(entry)
        return filtered

    def _guess_delimiter(self, text: str, asset_name: str) -> Optional[str]:
        suffix = Path(asset_name).suffix.lower()
        if suffix in DELIMITED_SUFFIXES:
            return DELIMITED_SUFFIXES[suffix]
        lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        sample = lines[:10]
        if not sample:
            return None
        comma_score = sum(1 for line in sample if line.count(",") >= 2)
        tab_score = sum(1 for line in sample if line.count("\t") >= 1)
        if comma_score >= max(2, len(sample) // 2):
            return ","
        if tab_score >= max(2, len(sample) // 2):
            return "\t"
        return None

    def _is_under_skipped_dir(self, path: Path, game_dir: Path) -> bool:
        try:
            parts = path.relative_to(game_dir).parts[:-1]
        except ValueError:
            parts = path.parts[:-1]
        return any(part.lower() in SKIPPED_DIR_NAMES for part in parts)

    def _read_text_document(self, path: Path) -> Optional[UnityTextDocument]:
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        if not raw:
            return None
        if self._looks_binary(raw):
            return None

        decoded = self._decode_text_bytes(raw)
        if decoded is None:
            return None
        text, encoding = decoded
        return UnityTextDocument(text=text, encoding=encoding, newline=self._detect_newline(text))

    def _looks_binary(self, raw: bytes) -> bool:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return False
        sample = raw[:4096]
        return b"\x00" in sample

    def _is_plausible_text(self, text: str) -> bool:
        if "\ufffd" in text:
            return False
        if not text:
            return False
        sample = text[:4096]
        control_count = sum(1 for char in sample if ord(char) < 32 and char not in "\r\n\t")
        return control_count <= max(4, len(sample) // 100)

    def _detect_newline(self, text: str) -> str:
        if "\r\n" in text:
            return "\r\n"
        if "\r" in text:
            return "\r"
        return "\n"

    def _extract_document_entries(
        self,
        relative_file: str,
        path: Path,
        document: UnityTextDocument,
    ) -> List[TextEntry]:
        suffix = path.suffix.lower()
        stripped = document.text.lstrip()
        if suffix == ".json" or stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(document.text)
            except ValueError:
                pass
            else:
                return self._extract_json_entries(relative_file, parsed, document)

        if suffix in DELIMITED_SUFFIXES:
            try:
                return self._extract_delimited_entries(
                    relative_file,
                    document,
                    DELIMITED_SUFFIXES[suffix],
                )
            except csv.Error:
                return self._extract_text_line_entries(relative_file, document)

        delimiter = self._guess_delimiter(document.text, path.name)
        if delimiter is not None:
            try:
                return self._extract_delimited_entries(relative_file, document, delimiter)
            except csv.Error:
                return self._extract_text_line_entries(relative_file, document)

        return self._extract_text_line_entries(relative_file, document)

    def _extract_json_entries(self, relative_file: str, document: Any, text_document: UnityTextDocument) -> List[TextEntry]:
        entries: List[TextEntry] = []
        for path, value in self._walk_strings(document):
            if not self._is_translatable(value):
                continue
            pointer = self._json_pointer(path)
            entries.append(
                TextEntry(
                    id=self._entry_id(relative_file, "json", pointer, value),
                    source=value,
                    context="%s%s" % (relative_file, pointer),
                    file=relative_file,
                    adapter_id=self.adapter_id,
                    metadata={
                        "format": "json",
                        "json_path": list(path),
                        "json_pointer": pointer,
                        "encoding": text_document.encoding,
                    },
                )
            )
        return entries

    def _extract_delimited_entries(
        self,
        relative_file: str,
        document: UnityTextDocument,
        delimiter: str,
    ) -> List[TextEntry]:
        entries: List[TextEntry] = []
        reader = csv.reader(io.StringIO(document.text), delimiter=delimiter)
        header_row: Optional[List[str]] = None
        for row_index, row in enumerate(reader):
            if self._is_comment_row(row):
                continue
            if row_index == 0 and self._looks_like_header_row(row):
                header_row = list(row)
                continue
            for column_index, value in enumerate(row):
                if self._should_skip_delimited_column(header_row, column_index):
                    continue
                if not self._is_translatable(value):
                    continue
                location = "%s:%s" % (row_index, column_index)
                entries.append(
                    TextEntry(
                        id=self._entry_id(relative_file, "delimited", location, value),
                        source=value,
                        context="%s:%s:%s" % (relative_file, row_index + 1, column_index + 1),
                        file=relative_file,
                        adapter_id=self.adapter_id,
                        metadata={
                            "format": "delimited",
                            "delimiter": delimiter,
                            "row": row_index,
                            "column": column_index,
                            "encoding": document.encoding,
                            "newline": document.newline,
                        },
                    )
                )
        return entries

    def _filter_text_asset_delimited_entries(
        self,
        asset_name: str,
        document: UnityTextDocument,
        entries: List[TextEntry],
        delimiter: str,
    ) -> List[TextEntry]:
        if asset_name != "EVENT":
            return entries

        rows = list(csv.reader(io.StringIO(document.text), delimiter=delimiter))
        filtered: List[TextEntry] = []
        alias_keys: Set[str] = set()
        for entry in entries:
            row_index = self._metadata_int(entry, "row")
            column_index = self._metadata_int(entry, "column")
            if row_index is None or column_index is None or row_index >= len(rows):
                continue
            row = rows[row_index]
            if self._is_translatable_event_cell(row, column_index):
                entry.metadata["event_patch_policy"] = "display"
                filtered.append(entry)
            elif self._is_event_display_alias_cell(row, column_index):
                key = entry.source.strip()
                if key in alias_keys or not self._is_safe_display_alias_key(key):
                    continue
                alias_keys.add(key)
                entry.metadata["format"] = UNITY_DISPLAY_ALIAS_FORMAT
                entry.metadata["display_alias_scope"] = "event_speaker"
                entry.metadata["event_patch_policy"] = "display_alias"
                entry.metadata["runtime_key"] = key
                filtered.append(entry)
        return filtered

    def _is_translatable_event_cell(self, row: Sequence[str], column_index: int) -> bool:
        if column_index <= 0 or not row:
            return False
        command = row[0].strip()
        if command == "choicescommand":
            return True
        if command.startswith("Text"):
            return column_index == 4
        return False

    def _is_event_display_alias_cell(self, row: Sequence[str], column_index: int) -> bool:
        if column_index != 1 or not row:
            return False
        return row[0].strip().startswith("Text")

    def _is_comment_row(self, row: Sequence[str]) -> bool:
        for value in row:
            stripped = value.strip()
            if not stripped:
                continue
            return stripped.startswith("#")
        return False

    def _looks_like_header_row(self, row: Sequence[str]) -> bool:
        non_empty = [value.strip().lstrip("\ufeff") for value in row if value.strip()]
        if len(non_empty) < 2:
            return False
        lowered = {value.lower() for value in non_empty}
        header_names = {
            "id",
            "name",
            "text",
            "description",
            "eventname",
            "category",
            "type",
            "price",
            "flag",
            "displayname",
        }
        if lowered & header_names:
            return True
        header_markers = ("id", "名", "説明", "フラグ", "場所", "効果", "テキスト")
        return sum(1 for value in non_empty if any(marker in value for marker in header_markers)) >= 2

    def _should_skip_delimited_column(self, header_row: Optional[Sequence[str]], column_index: int) -> bool:
        if header_row is None or column_index >= len(header_row):
            return False
        header = header_row[column_index].strip().lstrip("\ufeff")
        normalized = re.sub(r"[^a-z0-9]", "", header.lower())
        if not normalized and not header:
            return True
        exact_skip = {
            "id",
            "evntlabel",
            "eventname",
            "winevent",
            "loseevent",
            "flag",
            "conditionkey",
            "spriteaddress",
            "iconaddress",
            "thumbnailaddresskey",
            "cardbaseaddresskey",
            "address",
            "asset",
            "path",
        }
        if normalized in exact_skip:
            return True
        if normalized.endswith("id") or normalized.endswith("address") or normalized.endswith("key"):
            return True
        japanese_skip_markers = ("フラグ", "場所")
        return any(marker in header for marker in japanese_skip_markers)

    def _extract_text_line_entries(self, relative_file: str, document: UnityTextDocument) -> List[TextEntry]:
        entries: List[TextEntry] = []
        for line_index, line in enumerate(document.text.splitlines(keepends=True)):
            body, _ending = self._split_line_ending(line)
            for segment_index, start, end, quote in self._line_segments(body):
                value = body[start:end]
                if not self._is_translatable(value):
                    continue
                location = "%s:%s:%s" % (line_index, start, end)
                entries.append(
                    TextEntry(
                        id=self._entry_id(relative_file, "text_line", location, value),
                        source=value,
                        context="%s:%s" % (relative_file, line_index + 1),
                        file=relative_file,
                        adapter_id=self.adapter_id,
                        metadata={
                            "format": "text_line",
                            "line": line_index,
                            "segment": segment_index,
                            "span_start": start,
                            "span_end": end,
                            "quoted": quote is not None,
                            "quote": quote or "",
                            "encoding": document.encoding,
                            "newline": document.newline,
                        },
                    )
                )
        return entries

    def _line_segments(self, body: str) -> List[Tuple[int, int, int, Optional[str]]]:
        quoted_segments = self._quoted_segments(body)
        if quoted_segments:
            return [(index, start, end, quote) for index, (start, end, quote) in enumerate(quoted_segments)]

        list_marker_start = self._list_marker_value_start(body)
        if list_marker_start is not None:
            end = self._trim_trailing_space(body, len(body))
            return [(0, list_marker_start, end, None)] if list_marker_start < end else []

        separator = self._find_separator(body)
        if separator >= 0:
            start = separator + 1
            while start < len(body) and body[start] in " \t":
                start += 1
            end = self._trim_trailing_space(body, len(body))
            return [(0, start, end, None)] if start < end else []

        start = 0
        while start < len(body) and body[start] in " \t":
            start += 1
        end = self._trim_trailing_space(body, len(body))
        return [(0, start, end, None)] if start < end else []

    def _quoted_segments(self, body: str) -> List[Tuple[int, int, str]]:
        segments: List[Tuple[int, int, str]] = []
        index = 0
        while index < len(body):
            quote = body[index]
            if quote not in ("'", '"'):
                index += 1
                continue
            start = index + 1
            cursor = start
            escaped = False
            while cursor < len(body):
                char = body[cursor]
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    break
                cursor += 1
            if cursor >= len(body):
                break
            value = body[start:cursor]
            if self._is_translatable(value):
                segments.append((start, cursor, quote))
            index = cursor + 1
        return segments

    def _list_marker_value_start(self, body: str) -> Optional[int]:
        stripped_start = 0
        while stripped_start < len(body) and body[stripped_start] in " \t":
            stripped_start += 1
        if body[stripped_start : stripped_start + 2] in ("- ", "* "):
            return stripped_start + 2
        return None

    def _find_separator(self, body: str) -> int:
        candidates = [index for index in (body.find("="), body.find(":"), body.find("\t")) if index >= 0]
        if not candidates:
            return -1
        return min(candidates)

    def _trim_trailing_space(self, value: str, end: int) -> int:
        while end > 0 and value[end - 1] in " \t":
            end -= 1
        return end

    def _apply_format_entries(self, target_file: Path, format_name: str, entries: List[TextEntry]) -> Tuple[int, int]:
        if format_name == UNITY_DISPLAY_ALIAS_FORMAT:
            return 0, 0
        if format_name.startswith(UNITYPY_TEXT_FORMAT_PREFIX):
            return self._apply_unity_text_asset_entries(target_file, entries)
        if format_name == UNITY_TYPETREE_STRING_FORMAT:
            return self._apply_unity_typetree_string_entries(target_file, entries)
        if format_name == UNITY_SERIALIZED_RAW_STRING_FORMAT:
            return self._apply_unity_serialized_raw_string_entries(target_file, entries)
        if format_name == UNITY_MANAGED_LDSTR_FORMAT:
            return self._apply_managed_ldstr_entries(target_file, entries)
        if format_name == "json":
            return self._apply_json_entries(target_file, entries)
        if format_name == "delimited":
            return self._apply_delimited_entries(target_file, entries)
        return self._apply_text_line_entries(target_file, entries)

    def _is_display_alias_entry(self, entry: TextEntry) -> bool:
        return str(entry.metadata.get("format") or "") == UNITY_DISPLAY_ALIAS_FORMAT

    def _collect_display_aliases(
        self,
        entries: Sequence[TextEntry],
        workspace: Path,
        game_dir: Optional[Path] = None,
    ) -> Dict[str, str]:
        glossary = self._load_approved_glossary_aliases(workspace)
        aliases: Dict[str, str] = {}
        legacy_key_counts: Dict[str, int] = {}
        event_row_cache: Dict[Tuple[str, int, str], Optional[List[List[str]]]] = {}
        legacy_entries: List[TextEntry] = []

        for entry in entries:
            if self._is_legacy_event_speaker_key_entry(entry, game_dir, event_row_cache):
                legacy_entries.append(entry)
                key = entry.source.strip()
                legacy_key_counts[key] = legacy_key_counts.get(key, 0) + 1

        for entry in entries:
            if self._is_display_alias_entry(entry):
                key = str(entry.metadata.get("runtime_key") or entry.source).strip()
                if entry.status == "rejected":
                    continue
                target = (entry.translation or "").strip()
                if not target:
                    target = glossary.get(key, "")
                self._add_display_alias(aliases, key, target)
                continue

            if entry in legacy_entries:
                key = entry.source.strip()
                if legacy_key_counts.get(key, 0) < 2:
                    continue
                self._add_display_alias(aliases, key, glossary.get(key, ""))

        return aliases

    def _is_legacy_event_speaker_key_entry(
        self,
        entry: TextEntry,
        game_dir: Optional[Path] = None,
        event_row_cache: Optional[Dict[Tuple[str, int, str], Optional[List[List[str]]]]] = None,
    ) -> bool:
        metadata = entry.metadata
        if metadata.get("asset_name") != "EVENT":
            return False
        if self._metadata_int(entry, "column") != 1:
            return False
        if not self._is_safe_display_alias_key(entry.source.strip()):
            return False
        policy = str(metadata.get("event_patch_policy") or "")
        if policy not in {"control", "display_alias", ""}:
            return False
        if game_dir is not None:
            command = self._event_command_for_entry(entry, game_dir, event_row_cache)
            if command is not None:
                return command.startswith("Text")
        return True

    def _event_command_for_entry(
        self,
        entry: TextEntry,
        game_dir: Path,
        event_row_cache: Optional[Dict[Tuple[str, int, str], Optional[List[List[str]]]]] = None,
    ) -> Optional[str]:
        path_id = self._metadata_int(entry, "path_id")
        asset_name = str(entry.metadata.get("asset_name") or "")
        if path_id is None or not asset_name:
            return None
        cache = event_row_cache if event_row_cache is not None else {}
        key = (entry.file, path_id, asset_name)
        if key not in cache:
            cache[key] = self._load_text_asset_rows(game_dir / Path(entry.file), path_id, asset_name)
        rows = cache.get(key)
        if not rows:
            return None
        row_index = self._metadata_int(entry, "row")
        if row_index is None or row_index < 0 or row_index >= len(rows):
            return None
        row = rows[row_index]
        if not row:
            return None
        return row[0].strip()

    def _load_text_asset_rows(self, container: Path, path_id: int, asset_name: str) -> Optional[List[List[str]]]:
        try:
            import UnityPy  # type: ignore
        except ImportError:
            return None
        if not container.is_file():
            return None
        try:
            environment = UnityPy.load(str(container))
        except Exception:
            return None
        obj = self._find_text_asset_object(environment, path_id, asset_name)
        if obj is None:
            return None
        try:
            data = obj.read()
        except Exception:
            return None
        document = self._text_asset_document(self._text_asset_raw_script(data))
        if document is None:
            return None
        delimiter = self._guess_delimiter(document.text, asset_name)
        if delimiter is None:
            return None
        try:
            return list(csv.reader(io.StringIO(document.text), delimiter=delimiter))
        except csv.Error:
            return None

    def _add_display_alias(self, aliases: Dict[str, str], key: str, target: str) -> None:
        key = (key or "").strip()
        target = (target or "").strip()
        if not key or not target or key == target:
            return
        if not self._is_safe_display_alias_key(key):
            return
        if "\n" in target or "\r" in target or "\t" in target:
            return
        aliases.setdefault(key, target)

    def _is_safe_display_alias_key(self, value: str) -> bool:
        if not value or len(value) > 32:
            return False
        if not self._is_translatable(value):
            return False
        if any(character in value for character in "\r\n\t/\\{}[]<>"):
            return False
        if re.search(r"^(Ev_|EV_|Scene_|Flag_|CG_|SE_|BGM_)", value):
            return False
        return True

    def _load_approved_glossary_aliases(self, workspace: Path) -> Dict[str, str]:
        glossary_path = workspace / "glossary.tsv"
        if not glossary_path.is_file():
            return {}
        aliases: Dict[str, str] = {}
        try:
            with glossary_path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle, delimiter="\t")
                for row in reader:
                    if len(row) < 3:
                        continue
                    source = row[0].strip()
                    target = row[1].strip()
                    status = row[2].strip().lower()
                    if status != "approved":
                        continue
                    self._add_display_alias(aliases, source, target)
        except OSError:
            return {}
        return aliases

    def _apply_unity_typetree_string_entries(self, target_file: Path, entries: List[TextEntry]) -> Tuple[int, int]:
        try:
            import UnityPy  # type: ignore
        except ImportError:
            return 0, len(entries)

        try:
            environment = UnityPy.load(target_file.read_bytes())
        except Exception:
            return 0, len(entries)

        old_crc = self._unityfs_bundle_crc(target_file)
        applied = 0
        skipped = 0
        changed = False
        grouped: Dict[int, List[TextEntry]] = {}
        for entry in entries:
            path_id = self._metadata_int(entry, "path_id")
            if path_id is None:
                skipped += 1
                continue
            grouped.setdefault(path_id, []).append(entry)

        for obj in getattr(environment, "objects", []):
            path_id = int(getattr(obj, "path_id", 0) or 0)
            object_entries = grouped.pop(path_id, None)
            if not object_entries:
                continue
            try:
                tree = obj.read_typetree()
            except Exception:
                skipped += len(object_entries)
                continue
            object_changed = False
            object_applied = 0
            for entry in object_entries:
                typetree_path = entry.metadata.get("typetree_path")
                if not isinstance(typetree_path, list):
                    skipped += 1
                    continue
                current_value = self._get_path_value(tree, typetree_path)
                if current_value != entry.source:
                    skipped += 1
                    continue
                if self._set_path_value(tree, typetree_path, entry.translation or ""):
                    applied += 1
                    object_applied += 1
                    object_changed = True
                else:
                    skipped += 1
            if object_changed:
                try:
                    obj.save_typetree(tree)
                    changed = True
                except Exception:
                    skipped += object_applied
                    applied -= object_applied

        for remaining in grouped.values():
            skipped += len(remaining)

        if changed:
            self._save_unitypy_environment_file(environment, target_file)
            new_crc = self._unityfs_bundle_crc(target_file)
            if old_crc is not None and new_crc is not None and old_crc != new_crc:
                self._patch_addressables_catalog_crc(target_file, old_crc, new_crc)
        return applied, skipped

    def _apply_unity_serialized_raw_string_entries(self, target_file: Path, entries: List[TextEntry]) -> Tuple[int, int]:
        try:
            data = bytearray(target_file.read_bytes())
        except OSError:
            return 0, len(entries)

        applied = 0
        skipped = 0
        used_offsets: Set[int] = set()
        sorted_entries = sorted(entries, key=lambda entry: self._metadata_int(entry, "raw_string_offset") or -1)
        for entry in sorted_entries:
            if not entry.translation:
                skipped += 1
                continue
            if not self._is_safe_serialized_raw_entry(entry):
                skipped += 1
                continue
            string_offset = self._metadata_int(entry, "raw_string_offset")
            text_offset = self._metadata_int(entry, "raw_text_offset")
            byte_length = self._metadata_int(entry, "raw_byte_length")
            if string_offset is None or text_offset is None or byte_length is None:
                skipped += 1
                continue
            if string_offset in used_offsets:
                skipped += 1
                continue
            if string_offset < 0 or text_offset < 0 or byte_length <= 0:
                skipped += 1
                continue
            if string_offset + 4 > len(data) or text_offset + byte_length > len(data):
                skipped += 1
                continue
            current_length = struct.unpack_from("<i", data, string_offset)[0]
            if current_length != byte_length:
                skipped += 1
                continue
            current_raw = bytes(data[text_offset : text_offset + byte_length])
            try:
                current_value = current_raw.decode("utf-8")
            except UnicodeDecodeError:
                skipped += 1
                continue
            if current_value != entry.source:
                skipped += 1
                continue
            replacement = self._pad_utf8_bytes_to_length(entry.translation, byte_length)
            if replacement is None:
                skipped += 1
                continue
            data[text_offset : text_offset + byte_length] = replacement
            used_offsets.add(string_offset)
            applied += 1

        if applied:
            target_file.write_bytes(bytes(data))
        return applied, skipped

    def _is_safe_serialized_raw_entry(self, entry: TextEntry) -> bool:
        policy = str(entry.metadata.get("raw_patch_policy") or "").strip().lower().replace("-", "_")
        if policy in {"allow", "allowed", "safe"}:
            return True
        if policy in {"skip", "reject", "rejected", "unsafe"}:
            return False
        return entry.source in UNITY_SERIALIZED_RAW_SAFE_SOURCES

    def _pad_utf8_bytes_to_length(self, value: str, byte_length: int) -> Optional[bytes]:
        raw = value.encode("utf-8")
        if len(raw) > byte_length:
            return None
        remaining = byte_length - len(raw)
        if not remaining:
            return raw
        full_width_space = "\u3000".encode("utf-8")
        return raw + full_width_space * (remaining // len(full_width_space)) + b" " * (remaining % len(full_width_space))

    def _get_path_value(self, document: Any, path: Sequence[JsonPathPart]) -> Any:
        node = document
        try:
            for part in path:
                node = node[part]
        except (KeyError, IndexError, TypeError):
            return None
        return node

    def _set_path_value(self, document: Any, path: Sequence[JsonPathPart], value: str) -> bool:
        if not path:
            return False
        node = document
        try:
            for part in path[:-1]:
                node = node[part]
            node[path[-1]] = value
        except (KeyError, IndexError, TypeError):
            return False
        return True

    def _apply_managed_ldstr_entries(self, target_file: Path, entries: List[TextEntry]) -> Tuple[int, int]:
        game_root = self._game_root_for_managed_assembly(target_file)
        tool_dir = game_root / "JgtManagedStringTool"
        tool, _notes = self._prepare_managed_string_tool(game_root, tool_dir)
        if tool is None:
            return 0, len(entries)

        records: List[str] = []
        for entry in entries:
            if not entry.translation:
                continue
            method_token = self._metadata_int(entry, "method_token")
            instruction_index = self._metadata_int(entry, "instruction_index")
            if method_token is None or instruction_index is None:
                continue
            records.append(
                "%s\t%s\t%s\t%s"
                % (
                    method_token,
                    instruction_index,
                    self._encode_base64_text(entry.source),
                    self._encode_base64_text(entry.translation),
                )
            )
        if not records:
            return 0, len(entries)

        translations_path = tool_dir / ("%s.translations.tsv" % target_file.stem)
        translations_path.write_text("\n".join(records) + "\n", encoding="utf-8")
        backup_path = target_file.with_suffix(target_file.suffix + ".jgt_strings_bak")
        if not backup_path.exists():
            shutil.copy2(target_file, backup_path)

        result = subprocess.run(
            [str(tool.resolve()), "patch", str(target_file.resolve()), str(translations_path.resolve())],
            cwd=str(tool_dir.resolve()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            return 0, len(entries)

        match = re.search(r"patched=(\d+)\s+skipped=(\d+)", result.stdout or "")
        if match:
            return int(match.group(1)), int(match.group(2))
        return len(records), max(0, len(entries) - len(records))

    def _game_root_for_managed_assembly(self, assembly_path: Path) -> Path:
        try:
            managed_dir = assembly_path.parent
            data_dir = managed_dir.parent
            if managed_dir.name.lower() == "managed" and data_dir.name.endswith("_Data"):
                return data_dir.parent
        except IndexError:
            pass
        return assembly_path.parent

    def _apply_unity_text_asset_entries(self, target_file: Path, entries: List[TextEntry]) -> Tuple[int, int]:
        try:
            import UnityPy  # type: ignore
        except ImportError:
            return 0, len(entries)

        try:
            environment = UnityPy.load(target_file.read_bytes())
        except Exception:
            return 0, len(entries)

        old_crc = self._unityfs_bundle_crc(target_file)
        applied = 0
        skipped = 0
        changed = False
        for asset_entries in self._group_text_asset_entries(entries).values():
            first_entry = asset_entries[0]
            asset_name = str(first_entry.metadata.get("asset_name") or "")
            path_id = self._metadata_int(first_entry, "path_id")
            obj = self._find_text_asset_object(environment, path_id, asset_name)
            if obj is None:
                skipped += len(asset_entries)
                continue

            try:
                data = obj.read()
            except Exception:
                skipped += len(asset_entries)
                continue

            document = self._text_asset_document(self._text_asset_raw_script(data))
            if document is None:
                skipped += len(asset_entries)
                continue

            updated_text, asset_applied, asset_skipped = self._apply_text_asset_entries_to_document(
                document,
                asset_entries,
            )
            if asset_applied:
                if not self._set_text_asset_script(data, updated_text):
                    skipped += asset_applied + asset_skipped
                    continue
                data.save()
                changed = True
            applied += asset_applied
            skipped += asset_skipped

        if changed:
            self._save_unitypy_environment_file(environment, target_file)
            new_crc = self._unityfs_bundle_crc(target_file)
            if old_crc is not None and new_crc is not None and old_crc != new_crc:
                self._patch_addressables_catalog_crc(target_file, old_crc, new_crc)
        return applied, skipped

    def _normalize_font_strategy(self, font_strategy: str) -> str:
        normalized = (font_strategy or DEFAULT_UNITY_FONT_STRATEGY).strip().lower().replace("_", "-")
        aliases = {
            "off": "none",
            "disabled": "none",
            "jgt": "jgt-runtime",
            "runtime": "jgt-runtime",
            "managed": "jgt-runtime",
            "asset": "asset-patch",
            "assets": "asset-patch",
            "tmp-asset": "asset-patch",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized not in UNITY_FONT_STRATEGIES:
            raise ValueError(
                "unsupported Unity font strategy: %s; expected one of %s"
                % (font_strategy, ", ".join(sorted(UNITY_FONT_STRATEGIES)))
            )
        return normalized

    def _apply_font_strategy(
        self,
        output_dir: Path,
        translated_texts: Sequence[str],
        progress: Callable[[str], None],
        font_strategy: str,
        display_aliases: Optional[Dict[str, str]] = None,
    ) -> None:
        if font_strategy == "none":
            progress("[font] Unity font fallback disabled; no runtime DLL or TMP asset patch was installed.")
            return
        if font_strategy == "asset-patch":
            self._patch_tmp_font_assets_for_translations(output_dir, translated_texts, progress)
            return
        if font_strategy == "jgt-runtime":
            self._install_tmp_runtime_fallback(output_dir, translated_texts, progress)
            return

        if self._install_tmp_runtime_fallback(output_dir, translated_texts, progress):
            return
        progress("[font] TMP runtime fallback unavailable; trying TMP asset patch.")
        self._patch_tmp_font_assets_for_translations(output_dir, translated_texts, progress)

    def _patch_tmp_font_assets_for_translations(
        self,
        output_dir: Path,
        translated_texts: Sequence[str],
        progress: Callable[[str], None],
    ) -> Tuple[int, int, int]:
        required_chars = self._required_tmp_font_chars(translated_texts)
        if not required_chars:
            return (0, 0, 0)

        try:
            import UnityPy  # type: ignore
        except ImportError:
            progress("[font] TMP font fallback skipped: UnityPy is not installed.")
            return (0, 0, 0)

        patched_files = 0
        patched_tmp_assets = 0
        patched_font_sources = 0
        failed_files: List[str] = []
        for asset_file in self._find_unity_tmp_font_asset_files(output_dir):
            try:
                environment = UnityPy.load(str(asset_file))
            except Exception as exc:
                if len(failed_files) < 5:
                    failed_files.append("%s (%s)" % (self._display_relative(asset_file, output_dir), exc))
                continue

            source_font = self._select_tmp_source_font_object(environment)
            if source_font is None:
                continue

            file_target_tmp_assets = 0
            file_changed_tmp_assets = 0
            for obj in environment.objects:
                object_type = getattr(obj.type, "name", str(obj.type))
                if object_type != "MonoBehaviour":
                    continue
                try:
                    raw = obj.get_raw_data()
                except Exception:
                    continue
                patch_info = self._parse_tmp_font_asset_patch_info(raw)
                if patch_info is None:
                    continue
                if not self._should_patch_tmp_font_asset(patch_info, required_chars):
                    continue

                file_target_tmp_assets += 1
                patched_raw = self._patch_tmp_font_asset_raw(raw, patch_info, int(getattr(source_font, "path_id", 0)))
                if patched_raw != raw:
                    obj.set_raw_data(patched_raw)
                    file_changed_tmp_assets += 1

            source_changed = False
            if file_target_tmp_assets:
                source_changed = self._patch_unity_font_source_object(source_font)

            if file_changed_tmp_assets or source_changed:
                self._save_unitypy_environment_file(environment, asset_file)
                patched_files += 1
                patched_tmp_assets += file_changed_tmp_assets
                if source_changed:
                    patched_font_sources += 1

        if patched_files:
            progress(
                "[font] TMP dynamic atlas fallback patched: files=%s, tmp_fonts=%s, source_fonts=%s, chars=%s."
                % (patched_files, patched_tmp_assets, patched_font_sources, len(required_chars))
            )
        elif failed_files:
            progress("[font] TMP font fallback skipped for unreadable assets: %s." % "; ".join(failed_files))
        return (patched_files, patched_tmp_assets, patched_font_sources)

    def _install_tmp_runtime_fallback(
        self,
        output_dir: Path,
        translated_texts: Sequence[str],
        progress: Callable[[str], None],
    ) -> bool:
        required_chars = self._required_tmp_font_chars(translated_texts)
        if not required_chars:
            return False

        data_dir = self._runtime_fallback_data_dir(output_dir)
        if data_dir is None:
            progress("[font] TMP runtime fallback skipped: no compatible Unity Managed/Mono layout was found.")
            return False

        patch_dir = output_dir / "JgtTmpFontFallback"
        patch_dir.mkdir(parents=True, exist_ok=True)
        (patch_dir / "chars.txt").write_text("".join(sorted(required_chars)), encoding="utf-8")

        managed_dir = data_dir / "Managed"
        managed_patch_dir = managed_dir / "JgtTmpFontFallback"
        managed_patch_dir.mkdir(parents=True, exist_ok=True)
        (managed_patch_dir / "chars.txt").write_text("".join(sorted(required_chars)), encoding="utf-8")
        font_source_path = self._tmp_runtime_font_file()
        if font_source_path is not None:
            font_file_name = "JgtFallbackFont%s" % (font_source_path.suffix.lower() or ".ttf")
            shutil.copy2(font_source_path, patch_dir / font_file_name)
            shutil.copy2(font_source_path, managed_patch_dir / font_file_name)
        else:
            progress("[font] TMP runtime fallback did not find a bundled CJK font file; OS font-name fallback will be used.")

        source_path = patch_dir / "JgtTmpFontFallback.cs"
        with source_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(self._tmp_runtime_fallback_source())
        dll_path = managed_dir / "JgtTmpFontFallback.dll"
        if not self._compile_tmp_runtime_fallback(source_path, dll_path, managed_dir, progress):
            return False

        if not self._patch_managed_assembly_for_runtime_fallback(output_dir, managed_dir, dll_path, patch_dir, progress):
            return False

        self._disable_doorstop_loader(output_dir)
        progress(
            "[font] TMP runtime fallback installed: chars=%s, patch=%s."
            % (len(required_chars), self._display_relative(dll_path, output_dir))
        )
        return True

    def _apply_display_aliases(
        self,
        output_dir: Path,
        display_aliases: Dict[str, str],
        progress: Callable[[str], None],
    ) -> bool:
        if not display_aliases:
            return False

        data_dir = self._display_alias_data_dir(output_dir)
        if data_dir is None:
            progress("[alias] Unity display aliases skipped: no compatible Unity Managed/Mono layout was found.")
            return False

        managed_dir = data_dir / "Managed"
        patch_dir = output_dir / "JgtUnityDisplayAliases"
        patch_dir.mkdir(parents=True, exist_ok=True)
        managed_patch_dir = managed_dir / "JgtUnityDisplayAliases"
        managed_patch_dir.mkdir(parents=True, exist_ok=True)

        alias_text = self._display_alias_tsv(display_aliases)
        with (patch_dir / "display_aliases.tsv").open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(alias_text)
        with (managed_patch_dir / "display_aliases.tsv").open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(alias_text)

        source_path = patch_dir / "JgtUnityDisplayAliases.cs"
        with source_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(self._display_alias_runtime_source())
        dll_path = managed_dir / "JgtUnityDisplayAliases.dll"
        if not self._compile_display_alias_runtime(source_path, dll_path, managed_dir, progress):
            return False

        if not self._patch_managed_assembly_for_runtime_fallback(
            output_dir,
            managed_dir,
            dll_path,
            patch_dir,
            progress,
            bootstrap_type="JgtUnityDisplayAliasesBootstrap",
            install_method="InstallFromGame",
            log_prefix="[alias] Unity display aliases",
        ):
            return False

        self._disable_doorstop_loader(output_dir)
        progress(
            "[alias] Unity display aliases installed: aliases=%s, patch=%s."
            % (len(display_aliases), self._display_relative(dll_path, output_dir))
        )
        return True

    def _display_alias_data_dir(self, game_dir: Path) -> Optional[Path]:
        for data_dir in self._find_data_dirs(game_dir):
            managed_dir = data_dir / "Managed"
            if not (managed_dir / "UnityEngine.CoreModule.dll").is_file():
                continue
            if not (managed_dir / "mscorlib.dll").is_file():
                continue
            if not (managed_dir / "Assembly-CSharp.dll").is_file():
                continue
            return data_dir
        return None

    def _display_alias_tsv(self, display_aliases: Dict[str, str]) -> str:
        lines = []
        for source, target in sorted(display_aliases.items()):
            lines.append("%s\t%s" % (self._encode_base64_text(source), self._encode_base64_text(target)))
        return "\n".join(lines) + ("\n" if lines else "")

    def _compile_display_alias_runtime(
        self,
        source_path: Path,
        dll_path: Path,
        managed_dir: Path,
        progress: Callable[[str], None],
    ) -> bool:
        csc = self._find_csc()
        if csc is None:
            progress("[alias] Unity display aliases skipped: C# compiler csc.exe was not found.")
            return False

        references = [
            "mscorlib.dll",
            "netstandard.dll",
            "System.dll",
            "System.Core.dll",
            "UnityEngine.CoreModule.dll",
            "UnityEngine.dll",
        ]
        reference_args = []
        missing = []
        for reference in references:
            path = managed_dir / reference
            if path.is_file():
                reference_args.append("/reference:%s" % path)
            elif reference not in {"netstandard.dll", "UnityEngine.dll"}:
                missing.append(reference)
        if missing:
            progress("[alias] Unity display aliases skipped: missing managed references: %s." % ", ".join(missing))
            return False

        command = [
            str(csc),
            "/nologo",
            "/noconfig",
            "/nostdlib+",
            "/target:library",
            "/out:%s" % dll_path,
        ] + reference_args + [str(source_path)]
        result = subprocess.run(
            command,
            cwd=str(source_path.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stdout or "").strip().splitlines()
            progress(
                "[alias] Unity display aliases compile failed: %s."
                % (" | ".join(detail[-3:]) if detail else "csc exited with %s" % result.returncode)
            )
            return False
        return True

    def _runtime_fallback_data_dir(self, game_dir: Path) -> Optional[Path]:
        for data_dir in self._find_data_dirs(game_dir):
            managed_dir = data_dir / "Managed"
            if not (managed_dir / "Unity.TextMeshPro.dll").is_file():
                continue
            if not (managed_dir / "UnityEngine.CoreModule.dll").is_file():
                continue
            if not (managed_dir / "mscorlib.dll").is_file():
                continue
            return data_dir
        return None

    def _runtime_fallback_mono_runtime(self, game_dir: Path) -> Optional[Path]:
        embed_runtime = game_dir / "MonoBleedingEdge" / "EmbedRuntime"
        for name in ("mono-2.0-bdwgc.dll", "mono-2.0-sgen.dll"):
            candidate = embed_runtime / name
            if candidate.is_file():
                return candidate
        return None

    def _find_doorstop_winhttp(self, game_dir: Path) -> Optional[Path]:
        env_path = os.environ.get("JGT_DOORSTOP_WINHTTP")
        if env_path:
            candidate = Path(env_path)
            if candidate.is_file():
                return candidate

        local_candidate = game_dir / "winhttp.dll"
        if local_candidate.is_file():
            return local_candidate

        workspace_candidate = Path.cwd() / "tools" / "doorstop" / "winhttp.dll"
        if workspace_candidate.is_file():
            return workspace_candidate

        search_roots = [game_dir.parent, Path("C:/game")]
        seen = set()
        for search_root in search_roots:
            try:
                root_key = str(search_root.resolve())
            except OSError:
                root_key = str(search_root)
            if root_key in seen or not search_root.is_dir():
                continue
            seen.add(root_key)
            for candidate in search_root.rglob("winhttp.dll"):
                try:
                    if candidate.resolve() == local_candidate.resolve():
                        continue
                except OSError:
                    continue
                if (candidate.parent / "doorstop_config.ini").is_file() or (candidate.parent / "BepInEx").is_dir():
                    return candidate
        return None

    def _compile_tmp_runtime_fallback(
        self,
        source_path: Path,
        dll_path: Path,
        managed_dir: Path,
        progress: Callable[[str], None],
    ) -> bool:
        csc = self._find_csc()
        if csc is None:
            progress("[font] TMP runtime fallback skipped: C# compiler csc.exe was not found.")
            return False

        references = [
            "mscorlib.dll",
            "netstandard.dll",
            "System.dll",
            "System.Core.dll",
            "UnityEngine.CoreModule.dll",
            "UnityEngine.TextCoreFontEngineModule.dll",
            "UnityEngine.TextRenderingModule.dll",
            "UnityEngine.UI.dll",
            "Unity.TextMeshPro.dll",
        ]
        reference_args = []
        missing = []
        for reference in references:
            path = managed_dir / reference
            if path.is_file():
                reference_args.append("/reference:%s" % path)
            else:
                missing.append(reference)
        if missing:
            progress("[font] TMP runtime fallback skipped: missing managed references: %s." % ", ".join(missing))
            return False

        command = [
            str(csc),
            "/nologo",
            "/noconfig",
            "/nostdlib+",
            "/target:library",
            "/out:%s" % dll_path,
        ] + reference_args + [str(source_path)]
        result = subprocess.run(
            command,
            cwd=str(source_path.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stdout or "").strip().splitlines()
            progress(
                "[font] TMP runtime fallback compile failed: %s."
                % (" | ".join(detail[-3:]) if detail else "csc exited with %s" % result.returncode)
            )
            return False
        return True

    def _patch_managed_assembly_for_runtime_fallback(
        self,
        game_dir: Path,
        managed_dir: Path,
        fallback_dll: Path,
        patch_dir: Path,
        progress: Callable[[str], None],
        bootstrap_type: str = "JgtTmpFontFallbackBootstrap",
        install_method: str = "InstallFromGame",
        log_prefix: str = "[font] TMP runtime fallback",
    ) -> bool:
        assembly_path = managed_dir / "Assembly-CSharp.dll"
        if not assembly_path.is_file():
            progress("%s skipped: Assembly-CSharp.dll was not found." % log_prefix)
            return False

        mono_cecil = self._find_mono_cecil(game_dir)
        if mono_cecil is None:
            progress(
                "%s skipped: Mono.Cecil.dll was not found. "
                "Set JGT_MONO_CECIL to a local Mono.Cecil.dll and run apply again."
                % log_prefix
            )
            return False

        patcher_source = patch_dir / "JgtManagedAssemblyPatcher.cs"
        with patcher_source.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(self._managed_assembly_patcher_source())
        patcher_exe = patch_dir / "JgtManagedAssemblyPatcher.exe"
        if not self._compile_managed_assembly_patcher(patcher_source, patcher_exe, mono_cecil, managed_dir, progress):
            return False

        backup_path = assembly_path.with_suffix(assembly_path.suffix + ".jgt_bak")
        if not backup_path.exists():
            shutil.copy2(assembly_path, backup_path)

        result = subprocess.run(
            [str(patcher_exe), str(assembly_path), str(fallback_dll), bootstrap_type, install_method],
            cwd=str(patcher_exe.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        detail = (result.stdout or "").strip().splitlines()
        if result.returncode != 0:
            progress(
                "%s assembly patch failed: %s."
                % (
                    log_prefix,
                    " | ".join(detail[-3:]) if detail else "patcher exited with %s" % result.returncode,
                )
            )
            return False
        if detail:
            progress("%s assembly patch: %s." % (log_prefix, detail[-1]))
        return True

    def _compile_managed_assembly_patcher(
        self,
        source_path: Path,
        exe_path: Path,
        mono_cecil: Path,
        managed_dir: Path,
        progress: Callable[[str], None],
    ) -> bool:
        csc = self._find_csc()
        if csc is None:
            progress("[font] TMP runtime fallback skipped: C# compiler csc.exe was not found.")
            return False

        command = [
            str(csc),
            "/nologo",
            "/target:exe",
            "/out:%s" % exe_path,
            "/reference:%s" % mono_cecil,
        ]
        netstandard = managed_dir / "netstandard.dll"
        if netstandard.is_file():
            command.append("/reference:%s" % netstandard)
        command.append(str(source_path))
        result = subprocess.run(
            command,
            cwd=str(source_path.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stdout or "").strip().splitlines()
            progress(
                "[font] TMP runtime fallback patcher compile failed: %s."
                % (" | ".join(detail[-3:]) if detail else "csc exited with %s" % result.returncode)
            )
            return False

        mono_cecil_target = exe_path.parent / mono_cecil.name
        if mono_cecil.resolve() != mono_cecil_target.resolve():
            shutil.copy2(mono_cecil, mono_cecil_target)
        return True

    def _prepare_managed_string_tool(self, game_dir: Path, tool_dir: Path) -> Tuple[Optional[Path], List[str]]:
        notes: List[str] = []
        csc = self._find_csc()
        if csc is None:
            return None, ["Managed ldstr extraction skipped: C# compiler csc.exe was not found."]

        mono_cecil = self._find_mono_cecil(game_dir)
        if mono_cecil is None:
            return None, [
                "Managed ldstr extraction skipped: Mono.Cecil.dll was not found. "
                "Set JGT_MONO_CECIL to a local Mono.Cecil.dll and extract again."
            ]

        tool_dir.mkdir(parents=True, exist_ok=True)
        source_path = tool_dir / "JgtManagedStringTool.cs"
        source_text = self._managed_string_tool_source()
        if not source_path.exists() or source_path.read_text(encoding="utf-8") != source_text:
            with source_path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(source_text)

        exe_path = tool_dir / "JgtManagedStringTool.exe"
        if exe_path.is_file() and exe_path.stat().st_mtime >= source_path.stat().st_mtime:
            return exe_path, notes

        command = [
            str(csc),
            "/nologo",
            "/target:exe",
            "/out:%s" % exe_path.resolve(),
            "/reference:%s" % mono_cecil.resolve(),
        ]
        managed_dir = self._first_managed_dir(game_dir)
        if managed_dir is not None:
            netstandard = managed_dir / "netstandard.dll"
            if netstandard.is_file():
                command.append("/reference:%s" % netstandard.resolve())
        command.append(str(source_path.resolve()))
        result = subprocess.run(
            command,
            cwd=str(tool_dir.resolve()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            detail = (result.stdout or "").strip().splitlines()
            notes.append(
                "Managed ldstr string tool compile failed: %s."
                % (" | ".join(detail[-3:]) if detail else "csc exited with %s" % result.returncode)
            )
            return None, notes

        mono_cecil_target = tool_dir / mono_cecil.name
        if mono_cecil.resolve() != mono_cecil_target.resolve():
            shutil.copy2(mono_cecil, mono_cecil_target)
        return exe_path, notes

    def _first_managed_dir(self, game_dir: Path) -> Optional[Path]:
        for data_dir in self._find_data_dirs(game_dir):
            managed_dir = data_dir / "Managed"
            if managed_dir.is_dir():
                return managed_dir
        if game_dir.name.lower() == "managed" and game_dir.is_dir():
            return game_dir
        return None

    def _find_mono_cecil(self, game_dir: Path) -> Optional[Path]:
        env_path = os.environ.get("JGT_MONO_CECIL")
        if env_path:
            candidate = Path(env_path)
            if candidate.is_file():
                return candidate

        local_candidates = (
            game_dir / "Mono.Cecil.dll",
            game_dir / "BepInEx" / "core" / "Mono.Cecil.dll",
            Path.cwd() / "tools" / "Mono.Cecil.dll",
            Path.cwd() / "tools" / "mono-cecil" / "Mono.Cecil.dll",
        )
        for candidate in local_candidates:
            if candidate.is_file():
                return candidate

        search_roots = [game_dir.parent, Path("C:/game")]
        seen = set()
        for search_root in search_roots:
            try:
                root_key = str(search_root.resolve())
            except OSError:
                root_key = str(search_root)
            if root_key in seen or not search_root.is_dir():
                continue
            seen.add(root_key)
            for candidate in search_root.rglob("Mono.Cecil.dll"):
                if candidate.is_file():
                    return candidate
        return None

    def _disable_doorstop_loader(self, game_dir: Path) -> None:
        config_path = game_dir / "doorstop_config.ini"
        if not config_path.is_file():
            return
        try:
            text = config_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = config_path.read_text(encoding="utf-8-sig")
        disabled = re.sub(r"(?im)^enabled\s*=\s*true\s*$", "enabled=false", text, count=1)
        if disabled != text:
            backup = config_path.with_suffix(config_path.suffix + ".jgt_managed_bak")
            if not backup.exists():
                shutil.copy2(config_path, backup)
            config_path.write_text(disabled, encoding="utf-8", newline="\n")

    def _find_csc(self) -> Optional[Path]:
        path = shutil.which("csc.exe") or shutil.which("csc")
        if path:
            return Path(path)
        for candidate in (
            Path("C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe"),
            Path("C:/Windows/Microsoft.NET/Framework/v4.0.30319/csc.exe"),
        ):
            if candidate.is_file():
                return candidate
        return None

    def _tmp_runtime_fallback_source(self) -> str:
        return importlib_resources.read_text(
            "jp_game_translator.resources",
            "JgtTmpFontFallback.cs",
            encoding="utf-8",
        )

    def _managed_assembly_patcher_source(self) -> str:
        return importlib_resources.read_text(
            "jp_game_translator.resources",
            "JgtManagedAssemblyPatcher.cs",
            encoding="utf-8",
        )

    def _display_alias_runtime_source(self) -> str:
        return importlib_resources.read_text(
            "jp_game_translator.resources",
            "JgtUnityDisplayAliases.cs",
            encoding="utf-8",
        )

    def _managed_string_tool_source(self) -> str:
        return importlib_resources.read_text(
            "jp_game_translator.resources",
            "JgtManagedStringTool.cs",
            encoding="utf-8",
        )

    def _doorstop_config_text(self, game_dir: Path, data_dir: Path, mono_runtime: Path) -> str:
        config_dir = game_dir / "MonoBleedingEdge" / "etc"
        return (
            "[UnityDoorstop]\n"
            "enabled=true\n"
            "targetAssembly=JgtTmpFontFallback\\JgtTmpFontFallback.dll\n"
            "redirectOutputLog=false\n"
            "\n"
            "[MonoBackend]\n"
            "runtimeLib=%s\n"
            "configDir=%s\n"
            "corlibDir=%s\n"
            "debugEnabled=false\n"
            "debugSuspend=false\n"
            "debugAddress=127.0.0.1:10000\n"
            % (
                self._relative_windows_path(mono_runtime, game_dir),
                self._relative_windows_path(config_dir, game_dir),
                self._relative_windows_path(data_dir / "Managed", game_dir),
            )
        )

    def _relative_windows_path(self, path: Path, root: Path) -> str:
        try:
            relative = path.relative_to(root)
        except ValueError:
            return str(path).replace("/", "\\")
        return str(relative).replace("/", "\\")

    def _required_tmp_font_chars(self, translated_texts: Sequence[str]) -> Set[str]:
        required: Set[str] = set()
        for text in translated_texts:
            required.update(CJK_FONT_RE.findall(text))
        return required

    def _find_unity_tmp_font_asset_files(self, game_dir: Path) -> List[Path]:
        candidates: List[Path] = []
        seen = set()
        for data_dir in self._find_data_dirs(game_dir):
            try:
                children = list(data_dir.iterdir())
            except OSError:
                continue
            for child in children:
                if not child.is_file() or child.suffix.lower() != ".assets":
                    continue
                try:
                    if child.stat().st_size < 64:
                        continue
                except OSError:
                    continue
                key = str(child.resolve())
                if key not in seen:
                    seen.add(key)
                    candidates.append(child)
        return sorted(candidates, key=lambda item: self._display_relative(item, game_dir).lower())

    def _select_tmp_source_font_object(self, environment: Any) -> Optional[Any]:
        readable_fonts: List[Tuple[int, Any]] = []
        fonts_with_data: List[Tuple[int, Any]] = []
        for obj in environment.objects:
            object_type = getattr(obj.type, "name", str(obj.type))
            if object_type != "Font":
                continue
            try:
                data = obj.read()
            except Exception:
                continue
            if not hasattr(data, "m_FontNames"):
                continue
            path_id = int(getattr(obj, "path_id", 0))
            readable_fonts.append((path_id, obj))
            if getattr(data, "m_FontData", None):
                fonts_with_data.append((path_id, obj))
        candidates = fonts_with_data or readable_fonts
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: item[0])[0][1]

    def _patch_unity_font_source_object(self, font_obj: Any) -> bool:
        try:
            data = font_obj.read()
        except Exception:
            return False

        changed = False
        font_names, font_data = self._tmp_font_source()
        fallback_names = list(font_names)
        if hasattr(data, "m_FontNames") and list(getattr(data, "m_FontNames") or []) != fallback_names:
            setattr(data, "m_FontNames", fallback_names)
            changed = True
        if hasattr(data, "m_FontData"):
            current_font_data = getattr(data, "m_FontData", None)
            if font_data is not None:
                current_size = len(current_font_data) if current_font_data else 0
                if current_size != len(font_data):
                    setattr(data, "m_FontData", list(font_data))
                    changed = True
            elif current_font_data:
                setattr(data, "m_FontData", [])
                changed = True
        if changed:
            data.save()
        return changed

    def _tmp_font_source(self) -> Tuple[Sequence[str], Optional[bytes]]:
        cached = getattr(self, "_tmp_font_source_cache", None)
        if cached is not None:
            return cached

        for font_path, font_names in TMP_FONT_FILE_CANDIDATES:
            try:
                if font_path.is_file():
                    data = font_path.read_bytes()
                    result = (list(font_names) + [name for name in TMP_FONT_FALLBACK_NAMES if name not in font_names], data)
                    setattr(self, "_tmp_font_source_cache", result)
                    return result
            except OSError:
                continue

        result = (list(TMP_FONT_FALLBACK_NAMES), None)
        setattr(self, "_tmp_font_source_cache", result)
        return result

    def _tmp_runtime_font_file(self) -> Optional[Path]:
        cached = getattr(self, "_tmp_runtime_font_file_cache", None)
        if cached is not None:
            return cached
        for font_path, _font_names in TMP_FONT_FILE_CANDIDATES:
            try:
                if font_path.is_file():
                    setattr(self, "_tmp_runtime_font_file_cache", font_path)
                    return font_path
            except OSError:
                continue
        return None

    def _parse_tmp_font_asset_patch_info(self, raw: bytes) -> Optional[TmpFontAssetPatchInfo]:
        try:
            reader = UnitySerializedReader(raw)
            reader.skip_pptr()
            reader.read_int32()
            reader.skip_pptr()
            _, name = reader.read_string()
            reader.read_string()
            reader.read_int32()
            reader.read_string()
            reader.read_string()
            reader.skip_float32()
            reader.skip_float32()
            reader.read_int32()
            for _index in range(15):
                reader.skip_float32()
            reader.skip_pptr()
            reader.read_string()
            reader.read_string()
            reader.read_string()
            for _index in range(9):
                reader.read_int32()
            _, character_sequence = reader.read_string()
            reader.read_string()
            reader.read_string()
            reader.read_int32()
            reader.skip_float32()
            reader.read_int32()
            reader.read_bool()
            source_font_file_offset = reader.offset
            reader.skip_pptr()
            reader.read_string()
            atlas_population_mode_offset, atlas_population_mode = reader.read_int32()
            internal_dynamic_os_offset, _ = reader.read_bool()
            if atlas_population_mode not in (0, 1, 2):
                return None
            return TmpFontAssetPatchInfo(
                name=name,
                character_sequence=character_sequence,
                source_font_file_offset=source_font_file_offset,
                atlas_population_mode_offset=atlas_population_mode_offset,
                atlas_population_mode=atlas_population_mode,
                internal_dynamic_os_offset=internal_dynamic_os_offset,
                multi_atlas_enabled_offset=self._tmp_multi_atlas_enabled_offset(reader),
            )
        except (UnicodeDecodeError, ValueError, struct.error):
            return None

    def _tmp_multi_atlas_enabled_offset(self, reader: UnitySerializedReader) -> Optional[int]:
        start = reader.offset
        try:
            _, glyph_count = reader.read_int32()
            if glyph_count < 0 or glyph_count > TMP_MAX_TABLE_ITEMS:
                return None
            reader.skip(glyph_count * TMP_GLYPH_SERIALIZED_SIZE)

            _, character_count = reader.read_int32()
            if character_count < 0 or character_count > TMP_MAX_TABLE_ITEMS:
                return None
            reader.skip(character_count * TMP_CHARACTER_SERIALIZED_SIZE)

            reader.skip_pptr()
            _, atlas_texture_count = reader.read_int32()
            if atlas_texture_count < 0 or atlas_texture_count > 1024:
                return None
            reader.skip(atlas_texture_count * 12)
            reader.read_int32()
            multi_atlas_enabled_offset, _ = reader.read_bool()
            return multi_atlas_enabled_offset
        except (ValueError, struct.error):
            reader.offset = start
            return None

    def _should_patch_tmp_font_asset(self, patch_info: TmpFontAssetPatchInfo, required_chars: Set[str]) -> bool:
        if "SDF" not in patch_info.name:
            return False
        if not required_chars:
            return False
        if not patch_info.character_sequence:
            return True
        available_chars = set(patch_info.character_sequence)
        return any(char not in available_chars for char in required_chars)

    def _patch_tmp_font_asset_raw(
        self,
        raw: bytes,
        patch_info: TmpFontAssetPatchInfo,
        source_font_path_id: int,
    ) -> bytes:
        if not source_font_path_id:
            return raw
        patched = bytearray(raw)
        struct.pack_into("<i", patched, patch_info.source_font_file_offset, 0)
        struct.pack_into("<q", patched, patch_info.source_font_file_offset + 4, source_font_path_id)
        struct.pack_into(
            "<i",
            patched,
            patch_info.atlas_population_mode_offset,
            TMP_DYNAMIC_ATLAS_POPULATION_MODE,
        )
        patched[patch_info.internal_dynamic_os_offset] = 0
        if patch_info.multi_atlas_enabled_offset is not None:
            patched[patch_info.multi_atlas_enabled_offset] = 1
        return bytes(patched)

    def _group_text_asset_entries(self, entries: Sequence[TextEntry]) -> Dict[Tuple[Optional[int], str], List[TextEntry]]:
        grouped: Dict[Tuple[Optional[int], str], List[TextEntry]] = {}
        for entry in entries:
            path_id = self._metadata_int(entry, "path_id")
            asset_name = str(entry.metadata.get("asset_name") or "")
            grouped.setdefault((path_id, asset_name), []).append(entry)
        return grouped

    def _find_text_asset_object(self, environment: Any, path_id: Optional[int], asset_name: str) -> Optional[Any]:
        name_matches: List[Any] = []
        for obj in environment.objects:
            object_type = getattr(obj.type, "name", str(obj.type))
            if object_type != "TextAsset":
                continue
            if path_id is not None and int(getattr(obj, "path_id", 0)) == path_id:
                return obj
            if not asset_name:
                continue
            try:
                data = obj.read()
            except Exception:
                continue
            current_name = str(getattr(data, "name", "") or getattr(data, "m_Name", "") or "")
            if current_name == asset_name:
                name_matches.append(obj)
        if len(name_matches) == 1:
            return name_matches[0]
        return None

    def _apply_text_asset_entries_to_document(
        self,
        document: UnityTextDocument,
        entries: Sequence[TextEntry],
    ) -> Tuple[str, int, int]:
        current_document = document
        applied = 0
        skipped = 0
        by_inner_format: Dict[str, List[TextEntry]] = {}
        for entry in entries:
            by_inner_format.setdefault(self._text_asset_inner_format(entry), []).append(entry)

        for inner_format, format_entries in by_inner_format.items():
            if inner_format == "json":
                text, format_applied, format_skipped = self._apply_json_entries_to_document(
                    current_document,
                    format_entries,
                )
            elif inner_format == "delimited":
                text, format_applied, format_skipped = self._apply_delimited_entries_to_document(
                    current_document,
                    format_entries,
                )
            elif inner_format == "text_line":
                text, format_applied, format_skipped = self._apply_text_line_entries_to_document(
                    current_document,
                    format_entries,
                )
            else:
                skipped += len(format_entries)
                continue
            current_document = UnityTextDocument(
                text=text,
                encoding=current_document.encoding,
                newline=current_document.newline,
            )
            applied += format_applied
            skipped += format_skipped

        return current_document.text, applied, skipped

    def _text_asset_inner_format(self, entry: TextEntry) -> str:
        inner_format = entry.metadata.get("inner_format")
        if inner_format:
            return str(inner_format)
        format_name = str(entry.metadata.get("format") or "text_line")
        if format_name.startswith(UNITYPY_TEXT_FORMAT_PREFIX):
            return format_name[len(UNITYPY_TEXT_FORMAT_PREFIX) :]
        return format_name

    def _set_text_asset_script(self, data: Any, text: str) -> bool:
        if hasattr(data, "m_Script"):
            setattr(data, "m_Script", text)
            return True
        if hasattr(data, "script"):
            setattr(data, "script", text)
            return True
        return False

    def _save_unitypy_environment_file(self, environment: Any, target_file: Path) -> None:
        changed_files = [
            (name, file_item)
            for name, file_item in environment.files.items()
            if getattr(file_item, "is_changed", False)
        ]
        if not changed_files:
            return

        target_name = target_file.name.lower()
        selected = None
        for name, file_item in changed_files:
            if ntpath.basename(str(name)).lower() == target_name:
                selected = file_item
                break
        if selected is None:
            selected = changed_files[0][1]

        try:
            data = selected.save(packer="original")
        except TypeError:
            data = selected.save()

        temp_file = target_file.with_name(target_file.name + ".jgt_tmp")
        try:
            with temp_file.open("wb") as handle:
                handle.write(data)
            try:
                temp_file.replace(target_file)
            except PermissionError:
                with temp_file.open("rb") as source, target_file.open("wb") as target:
                    shutil.copyfileobj(source, target)
        finally:
            if temp_file.exists():
                temp_file.unlink()

    def _unityfs_bundle_crc(self, path: Path) -> Optional[int]:
        try:
            from UnityPy.enums import ArchiveFlags, ArchiveFlagsOld  # type: ignore
            from UnityPy.helpers import CompressionHelper  # type: ignore
            from UnityPy.streams import EndianBinaryReader  # type: ignore
        except ImportError:
            return None

        try:
            reader = EndianBinaryReader(path.read_bytes())
            signature = reader.read_string_to_null()
            if signature != "UnityFS":
                return None
            bundle_version = reader.read_u_int()
            reader.read_string_to_null()
            engine_version = reader.read_string_to_null()
            reader.read_long()
            compressed_size = reader.read_u_int()
            uncompressed_size = reader.read_u_int()
            data_flags_value = reader.read_u_int()
            version = self._unity_version_tuple(engine_version)
            if (
                version < (2020,)
                or (version[0] == 2020 and version < (2020, 3, 34))
                or (version[0] == 2021 and version < (2021, 3, 2))
                or (version[0] == 2022 and version < (2022, 1, 1))
            ):
                data_flags = ArchiveFlagsOld(data_flags_value)
            else:
                data_flags = ArchiveFlags(data_flags_value)

            if bundle_version >= 7 or (version[0] == 2019 and version >= (2019, 4, 15)):
                reader.align_stream(16)
            start = reader.Position
            if data_flags & ArchiveFlags.BlocksInfoAtTheEnd:
                reader.Position = reader.Length - compressed_size
                blocks_info_bytes = reader.read_bytes(compressed_size)
                reader.Position = start
            else:
                blocks_info_bytes = reader.read_bytes(compressed_size)

            blocks_info_bytes = self._decompress_unity_bundle_bytes(
                CompressionHelper,
                blocks_info_bytes,
                uncompressed_size,
                int(data_flags),
            )
            if blocks_info_bytes is None:
                return None

            blocks_info_reader = EndianBinaryReader(blocks_info_bytes, offset=start)
            blocks_info_reader.read_bytes(16)
            block_count = blocks_info_reader.read_int()
            block_infos = [
                (
                    blocks_info_reader.read_u_int(),
                    blocks_info_reader.read_u_int(),
                    blocks_info_reader.read_u_short(),
                )
                for _ in range(block_count)
            ]
            node_count = blocks_info_reader.read_int()
            for _ in range(node_count):
                blocks_info_reader.read_long()
                blocks_info_reader.read_long()
                blocks_info_reader.read_u_int()
                blocks_info_reader.read_string_to_null()

            if isinstance(data_flags, ArchiveFlags) and data_flags & ArchiveFlags.BlockInfoNeedPaddingAtStart:
                reader.align_stream(16)

            chunks: List[bytes] = []
            for uncompressed_block_size, compressed_block_size, block_flags in block_infos:
                chunk = self._decompress_unity_bundle_bytes(
                    CompressionHelper,
                    reader.read_bytes(compressed_block_size),
                    uncompressed_block_size,
                    int(block_flags),
                )
                if chunk is None:
                    return None
                chunks.append(chunk)
            return zlib.crc32(b"".join(chunks)) & 0xFFFFFFFF
        except Exception:
            return None

    def _decompress_unity_bundle_bytes(
        self,
        compression_helper: Any,
        data: bytes,
        uncompressed_size: int,
        flags: int,
    ) -> Optional[bytes]:
        compression_type = flags & 0x3F
        if compression_type == 0:
            return data
        if compression_type == 1:
            return compression_helper.decompress_lzma(data, uncompressed_size)
        if compression_type in (2, 3):
            return compression_helper.decompress_lz4(data, uncompressed_size)
        return None

    def _unity_version_tuple(self, version: str) -> Tuple[int, ...]:
        match = re.match(r"(\d+)\.(\d+)\.(\d+)", version or "")
        if not match:
            return (0,)
        return tuple(int(part) for part in match.groups())

    def _patch_addressables_catalog_crc(self, bundle_path: Path, old_crc: int, new_crc: int) -> int:
        catalog_path = self._find_addressables_catalog(bundle_path)
        if catalog_path is None:
            return 0

        data = catalog_path.read_bytes()
        old_bytes = int(old_crc).to_bytes(4, "little", signed=False)
        new_bytes = int(new_crc).to_bytes(4, "little", signed=False)
        bundle_name = bundle_path.name.encode("utf-8")
        candidate_offsets: List[int] = []

        start = 0
        while True:
            position = data.find(bundle_name, start)
            if position < 0:
                break
            window_end = min(len(data), position + len(bundle_name) + 512)
            cursor = data.find(old_bytes, position, window_end)
            while cursor >= 0:
                candidate_offsets.append(cursor)
                cursor = data.find(old_bytes, cursor + 1, window_end)
            start = position + len(bundle_name)

        if not candidate_offsets:
            global_offset = data.find(old_bytes)
            if global_offset >= 0 and data.find(old_bytes, global_offset + 1) < 0:
                candidate_offsets.append(global_offset)

        unique_offsets = sorted(set(candidate_offsets))
        if not unique_offsets:
            return 0

        patched = bytearray(data)
        for offset in unique_offsets:
            patched[offset : offset + 4] = new_bytes
        catalog_path.write_bytes(bytes(patched))
        return len(unique_offsets)

    def _find_addressables_catalog(self, bundle_path: Path) -> Optional[Path]:
        for parent in bundle_path.parents:
            catalog_path = parent / "catalog.bin"
            if catalog_path.is_file():
                return catalog_path
        return None

    def _metadata_int(self, entry: TextEntry, key: str) -> Optional[int]:
        value = entry.metadata.get(key)
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _apply_json_entries(self, target_file: Path, entries: List[TextEntry]) -> Tuple[int, int]:
        document = self._read_text_document(target_file)
        if document is None:
            return 0, len(entries)
        text, applied, skipped = self._apply_json_entries_to_document(document, entries)
        if applied:
            with target_file.open("w", encoding=document.encoding, newline="\n") as handle:
                handle.write(text)
        return applied, skipped

    def _apply_json_entries_to_document(
        self,
        document: UnityTextDocument,
        entries: Sequence[TextEntry],
    ) -> Tuple[str, int, int]:
        try:
            parsed = json.loads(document.text)
        except ValueError:
            return document.text, 0, len(entries)

        applied = 0
        skipped = 0
        for entry in entries:
            path = entry.metadata.get("json_path")
            if not isinstance(path, list) or not entry.translation:
                skipped += 1
                continue
            try:
                self._set_path(parsed, path, entry.translation)
            except (KeyError, IndexError, TypeError):
                skipped += 1
                continue
            applied += 1

        output = io.StringIO()
        json.dump(parsed, output, ensure_ascii=False, indent=2)
        output.write("\n")
        return output.getvalue(), applied, skipped

    def _apply_delimited_entries(self, target_file: Path, entries: List[TextEntry]) -> Tuple[int, int]:
        document = self._read_text_document(target_file)
        if document is None:
            return 0, len(entries)
        text, applied, skipped = self._apply_delimited_entries_to_document(document, entries)
        if applied:
            with target_file.open("w", encoding=document.encoding, newline="") as handle:
                handle.write(text)
        return applied, skipped

    def _apply_delimited_entries_to_document(
        self,
        document: UnityTextDocument,
        entries: Sequence[TextEntry],
    ) -> Tuple[str, int, int]:
        delimiter = str(entries[0].metadata.get("delimiter") or ",")
        try:
            rows = list(csv.reader(io.StringIO(document.text), delimiter=delimiter))
        except csv.Error:
            return document.text, 0, len(entries)

        applied = 0
        skipped = 0
        for entry in entries:
            try:
                row = int(entry.metadata.get("row", -1))
                column = int(entry.metadata.get("column", -1))
            except (TypeError, ValueError):
                skipped += 1
                continue
            if row < 0 or row >= len(rows) or column < 0 or column >= len(rows[row]) or not entry.translation:
                skipped += 1
                continue
            rows[row][column] = entry.translation
            applied += 1

        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter=delimiter, lineterminator=document.newline)
        writer.writerows(rows)
        return output.getvalue(), applied, skipped

    def _apply_text_line_entries(self, target_file: Path, entries: List[TextEntry]) -> Tuple[int, int]:
        document = self._read_text_document(target_file)
        if document is None:
            return 0, len(entries)
        text, applied, skipped = self._apply_text_line_entries_to_document(document, entries)
        if applied:
            with target_file.open("w", encoding=document.encoding, newline="") as handle:
                handle.write(text)
        return applied, skipped

    def _apply_text_line_entries_to_document(
        self,
        document: UnityTextDocument,
        entries: Sequence[TextEntry],
    ) -> Tuple[str, int, int]:
        lines = document.text.splitlines(keepends=True)
        applied = 0
        skipped = 0
        by_line: Dict[int, List[TextEntry]] = {}
        for entry in entries:
            try:
                line_index = int(entry.metadata.get("line", -1))
            except (TypeError, ValueError):
                skipped += 1
                continue
            by_line.setdefault(line_index, []).append(entry)

        for line_index, line_entries in by_line.items():
            if line_index < 0 or line_index >= len(lines):
                skipped += len(line_entries)
                continue
            body, ending = self._split_line_ending(lines[line_index])
            for entry in sorted(line_entries, key=lambda item: int(item.metadata.get("span_start", -1)), reverse=True):
                try:
                    start = int(entry.metadata.get("span_start", -1))
                    end = int(entry.metadata.get("span_end", -1))
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                replacement = self._line_replacement(entry)
                if 0 <= start <= end <= len(body) and body[start:end] == entry.source:
                    body = body[:start] + replacement + body[end:]
                    applied += 1
                    continue
                fallback_index = body.find(entry.source)
                if fallback_index >= 0:
                    body = body[:fallback_index] + replacement + body[fallback_index + len(entry.source) :]
                    applied += 1
                    continue
                skipped += 1
            lines[line_index] = body + ending

        return "".join(lines), applied, skipped

    def _line_replacement(self, entry: TextEntry) -> str:
        value = entry.translation or entry.source
        if not entry.metadata.get("quoted"):
            return value
        quote = str(entry.metadata.get("quote") or '"')
        value = value.replace("\\", "\\\\")
        value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
        return value.replace(quote, "\\" + quote)

    def _split_line_ending(self, line: str) -> Tuple[str, str]:
        if line.endswith("\r\n"):
            return line[:-2], "\r\n"
        if line.endswith("\n"):
            return line[:-1], "\n"
        if line.endswith("\r"):
            return line[:-1], "\r"
        return line, ""

    def _walk_strings(
        self,
        node: Any,
        path: Sequence[JsonPathPart] = (),
    ) -> Iterator[Tuple[Tuple[JsonPathPart, ...], str]]:
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

    def _entry_id(self, relative_file: str, kind: str, location: str, value: str) -> str:
        digest = hashlib.sha1(("%s|%s|%s|%s" % (relative_file, kind, location, value)).encode("utf-8")).hexdigest()
        return digest[:16]

    def _display_relative(self, path: Path, root: Path) -> str:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return path.as_posix()
