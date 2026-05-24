from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 1


@dataclass
class DetectionResult:
    adapter_id: str
    engine_name: str
    confidence: float
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "engine_name": self.engine_name,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
        }


@dataclass
class TextEntry:
    id: str
    source: str
    context: str
    file: str
    adapter_id: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    translation: Optional[str] = None
    speaker: Optional[str] = None
    status: str = "new"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "translation": self.translation,
            "context": self.context,
            "file": self.file,
            "adapter_id": self.adapter_id,
            "speaker": self.speaker,
            "status": self.status,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TextEntry":
        return cls(
            id=data["id"],
            source=data["source"],
            translation=data.get("translation"),
            context=data.get("context", ""),
            file=data.get("file", ""),
            adapter_id=data.get("adapter_id", ""),
            speaker=data.get("speaker"),
            status=data.get("status", "new"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class ExtractionBundle:
    adapter_id: str
    engine_name: str
    entries: List[TextEntry]
    notes: List[str] = field(default_factory=list)


@dataclass
class ProjectManifest:
    adapter_id: str
    engine_name: str
    source_game_dir: str
    entry_count: int
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat(timespec="seconds") + "Z")
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "adapter_id": self.adapter_id,
            "engine_name": self.engine_name,
            "source_game_dir": self.source_game_dir,
            "entry_count": self.entry_count,
            "created_at": self.created_at,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectManifest":
        return cls(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            adapter_id=data["adapter_id"],
            engine_name=data.get("engine_name", data["adapter_id"]),
            source_game_dir=data.get("source_game_dir", ""),
            entry_count=int(data.get("entry_count", 0)),
            created_at=data.get("created_at", datetime.utcnow().isoformat(timespec="seconds") + "Z"),
            notes=list(data.get("notes") or []),
        )
