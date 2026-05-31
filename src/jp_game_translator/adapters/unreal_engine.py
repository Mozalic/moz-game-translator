from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from jp_game_translator.core.models import DetectionResult, ExtractionBundle, TextEntry

from .base import GameAdapter


@dataclass(frozen=True)
class UnrealProjectLayout:
    project_dir: Path
    pak_dir: Optional[Path]
    shipping_exe: Optional[Path]


class UnrealEngineAdapter(GameAdapter):
    adapter_id = "unreal_engine"
    engine_name = "Unreal Engine"

    def detect(self, game_dir: Path) -> Optional[DetectionResult]:
        layouts = self._find_project_layouts(game_dir)
        engine_dir = game_dir / "Engine"
        manifests = sorted(game_dir.glob("Manifest_*Files_*.txt")) if game_dir.is_dir() else []

        if not layouts and not engine_dir.is_dir() and not manifests:
            return None

        reasons: List[str] = []
        confidence = 0.0
        if engine_dir.is_dir():
            confidence += 0.25
            reasons.append("found Engine directory")
        if manifests:
            confidence += 0.12
            reasons.append("found Unreal packaged build manifests")
        if layouts:
            confidence += 0.25
            reasons.append(
                "found Unreal project content directory: %s"
                % ", ".join(self._relative(path.project_dir, game_dir) for path in layouts[:3])
            )

        has_pak = False
        has_iostore = False
        has_shipping_exe = False
        for layout in layouts:
            if layout.pak_dir is not None:
                if any(layout.pak_dir.glob("*.pak")):
                    has_pak = True
                if any(layout.pak_dir.glob("*.ucas")) and any(layout.pak_dir.glob("*.utoc")):
                    has_iostore = True
            if layout.shipping_exe is not None:
                has_shipping_exe = True

        if has_pak:
            confidence += 0.18
            reasons.append("found .pak content archives")
        if has_iostore:
            confidence += 0.18
            reasons.append("found IoStore .ucas/.utoc archives")
        if has_shipping_exe:
            confidence += 0.12
            reasons.append("found Win64 Shipping executable")

        if confidence < 0.35:
            return None

        engine_name = "Unreal Engine"
        if has_iostore:
            engine_name = "Unreal Engine (IoStore)"
        return DetectionResult(
            adapter_id=self.adapter_id,
            engine_name=engine_name,
            confidence=min(confidence, 0.99),
            reasons=reasons,
        )

    def extract(self, game_dir: Path, workspace: Path) -> ExtractionBundle:
        layouts = self._find_project_layouts(game_dir)
        notes: List[str] = []
        if layouts:
            project_names = ", ".join(layout.project_dir.name for layout in layouts)
            notes.append("Detected Unreal project directory: %s." % project_names)
        if self._has_iostore(layouts):
            notes.append(
                "This build uses Unreal IoStore archives (.ucas/.utoc). Text extraction requires an Unreal "
                "package/asset workflow before this tool can normalize entries."
            )
        elif any(layout.pak_dir is not None for layout in layouts):
            notes.append(
                "This build uses Unreal .pak archives. Text extraction requires unpacking or mounting the archives first."
            )
        notes.append(
            "Recommended next adapter layer: call an external Unreal extractor for UE5.6 IoStore, then parse "
            "StringTable/DataTable/Widget Blueprint FText fields from .uasset/.uexp and rebuild a patch pak."
        )
        return ExtractionBundle(
            adapter_id=self.adapter_id,
            engine_name=self.engine_name,
            entries=[],
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
    ) -> None:
        progress = progress or (lambda _message: None)
        if output_dir.exists():
            if not overwrite:
                raise FileExistsError("output directory already exists: %s" % output_dir)
            if output_dir.resolve() == game_dir.resolve():
                raise ValueError("output directory must be different from source game directory")
            shutil.rmtree(str(output_dir))
        usable_entries = [entry for entry in entries if entry.adapter_id == self.adapter_id and entry.translation]
        if usable_entries:
            raise NotImplementedError(
                "Unreal Engine write-back is not implemented yet; build a patch pak/IoStore workflow first."
            )
        progress("[apply] copy start: source=%s, output=%s." % (game_dir, output_dir))
        shutil.copytree(str(game_dir), str(output_dir))
        progress("[apply] finished: no Unreal text entries were available to apply; output=%s." % output_dir)

    def _find_project_layouts(self, game_dir: Path) -> List[UnrealProjectLayout]:
        layouts: List[UnrealProjectLayout] = []
        if not game_dir.is_dir():
            return layouts
        for child in sorted(game_dir.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir() or child.name == "Engine":
                continue
            content_dir = child / "Content"
            binaries_dir = child / "Binaries" / "Win64"
            pak_dir = content_dir / "Paks"
            if not content_dir.is_dir() and not binaries_dir.is_dir():
                continue
            if not pak_dir.is_dir() and not binaries_dir.is_dir():
                continue
            shipping_exe = self._find_shipping_exe(binaries_dir)
            layouts.append(
                UnrealProjectLayout(
                    project_dir=child,
                    pak_dir=pak_dir if pak_dir.is_dir() else None,
                    shipping_exe=shipping_exe,
                )
            )
        return layouts

    def _find_shipping_exe(self, binaries_dir: Path) -> Optional[Path]:
        if not binaries_dir.is_dir():
            return None
        candidates = sorted(binaries_dir.glob("*-Win64-Shipping.exe"))
        return candidates[0] if candidates else None

    def _has_iostore(self, layouts: Iterable[UnrealProjectLayout]) -> bool:
        for layout in layouts:
            if layout.pak_dir is None:
                continue
            if any(layout.pak_dir.glob("*.ucas")) and any(layout.pak_dir.glob("*.utoc")):
                return True
        return False

    def _relative(self, path: Path, root: Path) -> str:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return path.as_posix()
