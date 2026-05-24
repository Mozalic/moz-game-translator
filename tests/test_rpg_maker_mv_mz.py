from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jp_game_translator.adapters.rpg_maker_mv_mz import RpgMakerMvMzAdapter
from jp_game_translator.core.models import ProjectManifest, TextEntry
from jp_game_translator.core.project import init_workspace, load_workspace_entries, save_workspace_entries


class RpgMakerMvMzAdapterTest(unittest.TestCase):
    def test_extract_and_apply_json_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game_dir = root / "game"
            data_dir = game_dir / "www" / "data"
            data_dir.mkdir(parents=True)

            self._write_json(data_dir / "System.json", {"gameTitle": "テストの冒険"})
            self._write_json(
                data_dir / "States.json",
                [
                    None,
                    {
                        "id": 1,
                        "name": "魔力範囲Dアップ",
                        "note": "<elementDamageBonus:魔力:50\n魔力範囲:50>",
                    },
                ],
            )
            self._write_json(
                data_dir / "Map001.json",
                {
                    "events": [
                        None,
                        {
                            "name": "村人",
                            "pages": [
                                {
                                    "list": [
                                        {"code": 401, "parameters": ["こんにちは、勇者さん！"]},
                                        {"code": 102, "parameters": [["はい", "いいえ"]]},
                                    ]
                                }
                            ],
                        },
                    ]
                },
            )

            adapter = RpgMakerMvMzAdapter()
            detection = adapter.detect(game_dir)
            self.assertIsNotNone(detection)
            bundle = adapter.extract(game_dir, root / "work")
            sources = {entry.source for entry in bundle.entries}
            self.assertIn("テストの冒険", sources)
            self.assertIn("こんにちは、勇者さん！", sources)
            self.assertIn("村人", sources)
            self.assertIn("魔力範囲Dアップ", sources)
            self.assertNotIn("<elementDamageBonus:魔力:50\n魔力範囲:50>", sources)

            workspace = root / "work"
            init_workspace(
                workspace,
                ProjectManifest(
                    adapter_id=bundle.adapter_id,
                    engine_name=bundle.engine_name,
                    source_game_dir=str(game_dir),
                    entry_count=len(bundle.entries),
                ),
                bundle.entries,
            )
            entries = load_workspace_entries(workspace)
            for entry in entries:
                if entry.source == "こんにちは、勇者さん！":
                    entry.translation = "你好，勇者！"
                    entry.status = "translated"
                elif entry.source == "テストの冒険":
                    entry.translation = "测试冒险"
                    entry.status = "translated"
                elif entry.source == "魔力範囲Dアップ":
                    entry.translation = "魔力范围伤害提升"
                    entry.status = "translated"
            save_workspace_entries(workspace, entries)

            output_dir = root / "game_zh"
            progress_logs = []
            adapter.apply(game_dir, workspace, output_dir, load_workspace_entries(workspace), progress=progress_logs.append)

            translated_map = self._read_json(output_dir / "www" / "data" / "Map001.json")
            translated_system = self._read_json(output_dir / "www" / "data" / "System.json")
            translated_states = self._read_json(output_dir / "www" / "data" / "States.json")
            self.assertEqual(translated_system["gameTitle"], "测试冒险")
            self.assertEqual(translated_states[1]["name"], "魔力范围伤害提升")
            self.assertEqual(translated_states[1]["note"], "<elementDamageBonus:魔力:50\n魔力範囲:50>")
            self.assertEqual(translated_map["events"][1]["pages"][0]["list"][0]["parameters"][0], "你好，勇者！")
            self.assertEqual(translated_map["events"][1]["name"], "村人")
            self.assertTrue(any("[apply] pending=3" in message for message in progress_logs))
            self.assertTrue(any("completed=3/3" in message for message in progress_logs))
            self.assertTrue(any("[apply] finished: applied=3/3" in message for message in progress_logs))

    def test_system_internal_tables_are_not_extracted_or_applied(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game_dir = root / "game"
            data_dir = game_dir / "data"
            data_dir.mkdir(parents=True)
            self._write_json(
                data_dir / "System.json",
                {
                    "gameTitle": "テストの冒険",
                    "elements": ["", "魔力範囲"],
                    "skillTypes": ["", "術式"],
                    "variables": ["", "内部変数"],
                },
            )

            adapter = RpgMakerMvMzAdapter()
            bundle = adapter.extract(game_dir, root / "work")
            sources = {entry.source for entry in bundle.entries}
            self.assertIn("テストの冒険", sources)
            self.assertNotIn("魔力範囲", sources)
            self.assertNotIn("術式", sources)
            self.assertNotIn("内部変数", sources)

            workspace = root / "work"
            init_workspace(
                workspace,
                ProjectManifest(
                    adapter_id=bundle.adapter_id,
                    engine_name=bundle.engine_name,
                    source_game_dir=str(game_dir),
                    entry_count=len(bundle.entries),
                ),
                bundle.entries,
            )
            entries = load_workspace_entries(workspace)
            for entry in entries:
                entry.translation = "测试冒险"
                entry.status = "translated"
            entries.append(
                TextEntry(
                    id="protected-element",
                    source="魔力範囲",
                    translation="魔力范围",
                    context="data/System.json/elements/1",
                    file="data/System.json",
                    adapter_id=adapter.adapter_id,
                    metadata={"json_path": ["elements", 1], "json_pointer": "/elements/1"},
                    status="translated",
                )
            )

            output_dir = root / "game_zh"
            adapter.apply(game_dir, workspace, output_dir, entries)
            translated_system = self._read_json(output_dir / "data" / "System.json")
            self.assertEqual(translated_system["gameTitle"], "测试冒险")
            self.assertEqual(translated_system["elements"][1], "魔力範囲")

    def _write_json(self, path: Path, data) -> None:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)

    def _read_json(self, path: Path):
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)


if __name__ == "__main__":
    unittest.main()
