from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Iterable, Optional

from jp_game_translator.core.models import DetectionResult, ExtractionBundle, TextEntry


class GameAdapter(ABC):
    adapter_id: str
    engine_name: str

    @abstractmethod
    def detect(self, game_dir: Path) -> Optional[DetectionResult]:
        """Return a detection result when this adapter can handle the game."""

    @abstractmethod
    def extract(self, game_dir: Path, workspace: Path) -> ExtractionBundle:
        """Extract source text into normalized entries."""

    @abstractmethod
    def apply(
        self,
        game_dir: Path,
        workspace: Path,
        output_dir: Path,
        entries: Iterable[TextEntry],
        overwrite: bool = False,
        progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Create a translated game copy by applying translated entries."""
