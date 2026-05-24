from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from jp_game_translator.core.models import DetectionResult

from .base import GameAdapter
from .pal_softpal import PalSoftpalAdapter
from .rpg_maker_mv_mz import RpgMakerMvMzAdapter


def get_adapters() -> List[GameAdapter]:
    return [
        RpgMakerMvMzAdapter(),
        PalSoftpalAdapter(),
    ]


def adapter_map() -> Dict[str, GameAdapter]:
    return {adapter.adapter_id: adapter for adapter in get_adapters()}


def detect_best(game_dir: Path) -> Optional[DetectionResult]:
    results: List[DetectionResult] = []
    for adapter in get_adapters():
        result = adapter.detect(game_dir)
        if result is not None:
            results.append(result)
    if not results:
        return None
    results.sort(key=lambda item: item.confidence, reverse=True)
    return results[0]
