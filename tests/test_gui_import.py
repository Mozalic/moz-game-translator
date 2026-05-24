from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class GuiImportTest(unittest.TestCase):
    def test_gui_module_imports(self) -> None:
        import jp_game_translator.gui as gui

        self.assertTrue(callable(gui.main))


if __name__ == "__main__":
    unittest.main()
