from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from .jsonl import load_entries, save_entries
from .models import ProjectManifest, TextEntry


MANIFEST_FILE = "manifest.json"
ENTRIES_FILE = "entries.jsonl"
GLOSSARY_FILE = "glossary.tsv"


@dataclass
class WorkspaceSummary:
    path: Path
    manifest: ProjectManifest


def init_workspace(workspace: Path, manifest: ProjectManifest, entries: Iterable[TextEntry]) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    save_manifest(workspace, manifest)
    save_entries(workspace / ENTRIES_FILE, entries)


def save_manifest(workspace: Path, manifest: ProjectManifest) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    with (workspace / MANIFEST_FILE).open("w", encoding="utf-8") as handle:
        json.dump(manifest.to_dict(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def load_manifest(workspace: Path) -> ProjectManifest:
    with (workspace / MANIFEST_FILE).open("r", encoding="utf-8") as handle:
        return ProjectManifest.from_dict(json.load(handle))


def load_workspace_entries(workspace: Path) -> List[TextEntry]:
    return load_entries(workspace / ENTRIES_FILE)


def save_workspace_entries(workspace: Path, entries: Iterable[TextEntry]) -> None:
    save_entries(workspace / ENTRIES_FILE, entries)


def discover_workspaces(root: Path) -> List[WorkspaceSummary]:
    if not root.exists():
        return []

    manifest_paths: List[Path] = []
    if (root / MANIFEST_FILE).is_file():
        manifest_paths.append(root / MANIFEST_FILE)
    if root.is_dir():
        manifest_paths.extend(root.rglob(MANIFEST_FILE))

    summaries = []
    seen = set()
    for manifest_path in manifest_paths:
        workspace = manifest_path.parent
        key = str(workspace.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            summaries.append(WorkspaceSummary(path=workspace, manifest=load_manifest(workspace)))
        except Exception:
            continue

    summaries.sort(key=lambda item: (item.manifest.created_at, item.path.name), reverse=True)
    return summaries
