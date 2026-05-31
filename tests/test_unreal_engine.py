from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jp_game_translator.adapters.registry import detect_best
from jp_game_translator.adapters.unreal_engine import UnrealEngineAdapter


class UnrealEngineAdapterTest(unittest.TestCase):
    def test_detects_packaged_iostore_build(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game_dir = root / "game"
            pak_dir = game_dir / "SampleProject" / "Content" / "Paks"
            binaries_dir = game_dir / "SampleProject" / "Binaries" / "Win64"
            pak_dir.mkdir(parents=True)
            binaries_dir.mkdir(parents=True)
            (game_dir / "Engine").mkdir()
            (game_dir / "Manifest_UFSFiles_Win64.txt").write_text(
                "SampleProject/Content/Paks/SampleProject-Windows.pak\t2026-01-01T00:00:00.000Z\n",
                encoding="utf-8",
            )
            (binaries_dir / "SampleProject-Win64-Shipping.exe").write_bytes(b"MZ")
            (pak_dir / "SampleProject-Windows.pak").write_bytes(b"pak")
            (pak_dir / "SampleProject-Windows.ucas").write_bytes(b"ucas")
            (pak_dir / "SampleProject-Windows.utoc").write_bytes(b"utoc")

            adapter = UnrealEngineAdapter()
            detection = adapter.detect(game_dir)

            self.assertIsNotNone(detection)
            assert detection is not None
            self.assertEqual(detection.adapter_id, adapter.adapter_id)
            self.assertEqual(detection.engine_name, "Unreal Engine (IoStore)")
            self.assertGreaterEqual(detection.confidence, 0.9)
            self.assertEqual(detect_best(game_dir).adapter_id, adapter.adapter_id)

            bundle = adapter.extract(game_dir, root / "work")
            self.assertEqual(bundle.entries, [])
            self.assertTrue(any("IoStore" in note for note in bundle.notes))
            self.assertTrue(any("patch pak" in note for note in bundle.notes))


if __name__ == "__main__":
    unittest.main()
