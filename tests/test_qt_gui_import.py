from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class QtGuiImportTest(unittest.TestCase):
    def test_qt_gui_module_imports_without_optional_dependency(self) -> None:
        import jp_game_translator.qt_gui as qt_gui

        self.assertTrue(callable(qt_gui.main))


if __name__ == "__main__":
    unittest.main()
