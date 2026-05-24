from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jp_game_translator.core.models import ProjectManifest
from jp_game_translator.core.project import discover_workspaces, save_manifest


class WorkspaceDiscoveryTest(unittest.TestCase):
    def test_discovers_workspaces_with_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "work" / "sample"
            save_manifest(
                workspace,
                ProjectManifest(
                    adapter_id="test",
                    engine_name="Test Engine",
                    source_game_dir="C:/Games/Sample",
                    entry_count=12,
                ),
            )

            summaries = discover_workspaces(root / "work")

            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0].path, workspace)
            self.assertEqual(summaries[0].manifest.source_game_dir, "C:/Games/Sample")


if __name__ == "__main__":
    unittest.main()
