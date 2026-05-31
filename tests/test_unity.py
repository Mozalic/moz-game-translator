from __future__ import annotations

import csv
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jp_game_translator.adapters.registry import detect_best
from jp_game_translator.adapters.unity import DEFAULT_UNITY_FONT_STRATEGY, UNITY_DISPLAY_ALIAS_FORMAT, UnityAdapter
from jp_game_translator.core.models import TextEntry


class UnityAdapterTest(unittest.TestCase):
    def test_detect_extract_and_apply_loose_unity_text_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game_dir = root / "game"
            data_dir = game_dir / "Sample_Data"
            localization_dir = data_dir / "StreamingAssets" / "Localization"
            managed_dir = data_dir / "Managed"
            localization_dir.mkdir(parents=True)
            managed_dir.mkdir(parents=True)

            (game_dir / "Sample.exe").write_bytes(b"exe")
            (game_dir / "UnityPlayer.dll").write_bytes(b"unity")
            (data_dir / "globalgamemanagers").write_bytes(b"unity")
            (data_dir / "resources.assets").write_bytes(b"assets")
            (managed_dir / "UnityEngine.CoreModule.dll").write_bytes(b"runtime")
            self._write_json(
                localization_dir / "strings.json",
                {
                    "title": "\u30c6\u30b9\u30c8\u306e\u5192\u967a",
                    "menu": {"start": "\u306f\u3058\u3081\u308b"},
                    "debug": "English only",
                },
            )
            (localization_dir / "dialogue.csv").write_text(
                "id,text\nhello,\u3053\u3093\u306b\u3061\u306f\nbye,\u3055\u3088\u3046\u306a\u3089\n",
                encoding="utf-8",
            )
            (localization_dir / "script.lang").write_text(
                "speaker=\u6751\u4eba\n"
                "message=\"\u3053\u3093\u306b\u3061\u306f\u3001\u52c7\u8005\uff01\"\n"
                "- \u3055\u3088\u3046\u306a\u3089\n",
                encoding="utf-8",
            )

            adapter = UnityAdapter()
            detection = adapter.detect(game_dir)
            self.assertIsNotNone(detection)
            self.assertGreaterEqual(detection.confidence, 0.9)
            self.assertEqual(detect_best(game_dir).adapter_id, adapter.adapter_id)

            bundle = adapter.extract(game_dir, root / "work")
            by_source = {entry.source: entry for entry in bundle.entries}
            self.assertIn("\u30c6\u30b9\u30c8\u306e\u5192\u967a", by_source)
            self.assertIn("\u306f\u3058\u3081\u308b", by_source)
            self.assertIn("\u3053\u3093\u306b\u3061\u306f", by_source)
            self.assertIn("\u6751\u4eba", by_source)
            self.assertIn("\u3053\u3093\u306b\u3061\u306f\u3001\u52c7\u8005\uff01", by_source)
            self.assertTrue(any("loose text extraction completed" in note for note in bundle.notes))

            translations = {
                "\u30c6\u30b9\u30c8\u306e\u5192\u967a": "\u6d4b\u8bd5\u5192\u9669",
                "\u306f\u3058\u3081\u308b": "\u5f00\u59cb",
                "\u3053\u3093\u306b\u3061\u306f": "\u4f60\u597d",
                "\u6751\u4eba": "\u6751\u6c11",
                "\u3053\u3093\u306b\u3061\u306f\u3001\u52c7\u8005\uff01": "\u4f60\u597d\uff0c\u52c7\u8005\uff01",
                "\u3055\u3088\u3046\u306a\u3089": "\u518d\u89c1",
            }
            for entry in bundle.entries:
                if entry.source in translations:
                    entry.translation = translations[entry.source]
                    entry.status = "translated"

            output_dir = root / "game_zh"
            progress_logs: list[str] = []
            adapter.apply(game_dir, root / "work", output_dir, bundle.entries, progress=progress_logs.append)

            translated_json = self._read_json(output_dir / "Sample_Data" / "StreamingAssets" / "Localization" / "strings.json")
            self.assertEqual(translated_json["title"], "\u6d4b\u8bd5\u5192\u9669")
            self.assertEqual(translated_json["menu"]["start"], "\u5f00\u59cb")

            with (output_dir / "Sample_Data" / "StreamingAssets" / "Localization" / "dialogue.csv").open(
                "r",
                encoding="utf-8",
                newline="",
            ) as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[1][1], "\u4f60\u597d")
            self.assertEqual(rows[2][1], "\u518d\u89c1")

            translated_script = (
                output_dir / "Sample_Data" / "StreamingAssets" / "Localization" / "script.lang"
            ).read_text(encoding="utf-8")
            self.assertIn("speaker=\u6751\u6c11", translated_script)
            self.assertIn("message=\"\u4f60\u597d\uff0c\u52c7\u8005\uff01\"", translated_script)
            self.assertIn("- \u518d\u89c1", translated_script)
            self.assertTrue(any("[apply] pending=7" in message for message in progress_logs))
            self.assertTrue(any("[apply] finished: applied=7/7" in message for message in progress_logs))

    def test_extract_text_asset_csv_entries(self) -> None:
        adapter = UnityAdapter()
        entries = adapter._extract_text_asset_entries(
            relative_container="Game_Data/StreamingAssets/aa/StandaloneWindows64/masterdata.bundle",
            asset_name="CardMaster",
            path_id=123,
            document=adapter._text_asset_document(
                "\ufeffID,cardName,effectText\n"
                "1001,\u30b9\u30e9\u30c3\u30b7\u30e5,\u30c0\u30e1\u30fc\u30b85\n"
                "# \u3053\u308c\u306f\u30b3\u30e1\u30f3\u30c8\n"
                "1002,Hard Slash,\u30c0\u30e1\u30fc\u30b88{n}\u885d\u64831\n"
            ),
        )

        sources = {entry.source for entry in entries}
        self.assertIn("\u30b9\u30e9\u30c3\u30b7\u30e5", sources)
        self.assertIn("\u30c0\u30e1\u30fc\u30b85", sources)
        self.assertIn("\u30c0\u30e1\u30fc\u30b88{n}\u885d\u64831", sources)
        self.assertNotIn("\u3053\u308c\u306f\u30b3\u30e1\u30f3\u30c8", sources)
        self.assertTrue(all(entry.file == "Game_Data/StreamingAssets/aa/StandaloneWindows64/masterdata.bundle" for entry in entries))
        self.assertTrue(all(entry.metadata["format"] == "unity_text_asset_delimited" for entry in entries))
        self.assertTrue(all(entry.metadata["asset_name"] == "CardMaster" for entry in entries))
        self.assertTrue(all(entry.metadata["path_id"] == 123 for entry in entries))

    def test_extract_and_apply_serialized_raw_utf8_strings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game_dir = root / "game"
            data_dir = game_dir / "Sample_Data"
            data_dir.mkdir(parents=True)
            (game_dir / "Sample.exe").write_bytes(b"exe")
            (game_dir / "UnityPlayer.dll").write_bytes(b"unity")
            (data_dir / "globalgamemanagers").write_bytes(b"unity")
            level0 = data_dir / "level0"
            level0.write_bytes(
                b"\x00" * 16
                + self._unity_raw_string("\u30c7\u30fc\u30bf\u306a\u3057")
                + b"\x00" * 8
                + self._unity_raw_string("H\u30b9\u30c6\u30fc\u30bf\u30b9")
            )

            adapter = UnityAdapter()
            bundle = adapter.extract(game_dir, root / "work")
            by_source = {entry.source: entry for entry in bundle.entries}
            self.assertIn("\u30c7\u30fc\u30bf\u306a\u3057", by_source)
            self.assertIn("H\u30b9\u30c6\u30fc\u30bf\u30b9", by_source)
            self.assertEqual(
                by_source["\u30c7\u30fc\u30bf\u306a\u3057"].metadata["format"],
                "unity_serialized_raw_string",
            )

            by_source["\u30c7\u30fc\u30bf\u306a\u3057"].translation = "\u65e0\u6570\u636e"
            by_source["\u30c7\u30fc\u30bf\u306a\u3057"].status = "translated"
            by_source["H\u30b9\u30c6\u30fc\u30bf\u30b9"].translation = "H\u72b6\u6001"
            by_source["H\u30b9\u30c6\u30fc\u30bf\u30b9"].status = "translated"

            output_dir = root / "game_zh"
            adapter.apply(
                game_dir,
                root / "work",
                output_dir,
                bundle.entries,
                font_strategy="none",
            )

            patched = (output_dir / "Sample_Data" / "level0").read_bytes()
            self.assertEqual(len(patched), level0.stat().st_size)
            self.assertIn("\u65e0\u6570\u636e".encode("utf-8"), patched)
            self.assertIn("H\u72b6\u6001".encode("utf-8"), patched)
            self.assertNotIn("\u30c7\u30fc\u30bf\u306a\u3057".encode("utf-8"), patched)

    def test_apply_serialized_raw_utf8_skips_longer_translation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target_file = Path(temp_dir) / "level0"
            data = b"\x00" * 16 + self._unity_raw_string("\u30c7\u30fc\u30bf\u306a\u3057")
            target_file.write_bytes(data)
            entry = TextEntry(
                id="raw",
                source="\u30c7\u30fc\u30bf\u306a\u3057",
                translation="\u3068\u3066\u3082\u3068\u3066\u3082\u9577\u3044\u7ffb\u8a33",
                status="translated",
                context="level0#raw_utf8:16",
                file="level0",
                adapter_id="unity",
                metadata={
                    "format": "unity_serialized_raw_string",
                    "raw_patch_policy": "safe",
                    "raw_string_offset": 16,
                    "raw_text_offset": 20,
                    "raw_byte_length": len("\u30c7\u30fc\u30bf\u306a\u3057".encode("utf-8")),
                },
            )

            applied, skipped = UnityAdapter()._apply_unity_serialized_raw_string_entries(target_file, [entry])

            self.assertEqual((applied, skipped), (0, 1))
            self.assertEqual(target_file.read_bytes(), data)

    def test_apply_serialized_raw_utf8_skips_candidate_action_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target_file = Path(temp_dir) / "level0"
            data = b"\x00" * 16 + self._unity_raw_string("\u4f11\u3080")
            target_file.write_bytes(data)
            entry = TextEntry(
                id="raw",
                source="\u4f11\u3080",
                translation="\u4f11\u606f",
                status="translated",
                context="level0#raw_utf8:16",
                file="level0",
                adapter_id="unity",
                metadata={
                    "format": "unity_serialized_raw_string",
                    "raw_patch_policy": "candidate",
                    "raw_string_offset": 16,
                    "raw_text_offset": 20,
                    "raw_byte_length": len("\u4f11\u3080".encode("utf-8")),
                },
            )

            applied, skipped = UnityAdapter()._apply_unity_serialized_raw_string_entries(target_file, [entry])

            self.assertEqual((applied, skipped), (0, 1))
            self.assertEqual(target_file.read_bytes(), data)

    def test_extract_text_asset_text_lines_skips_comment_lines(self) -> None:
        adapter = UnityAdapter()
        entries = adapter._extract_text_asset_entries(
            relative_container="Game_Data/StreamingAssets/aa/StandaloneWindows64/masterdata.bundle",
            asset_name="DungeonNode_Second",
            path_id=456,
            document=adapter._text_asset_document(
                "# 0=\u30ad\u30e3\u30f3\u30d7 1=\u6226\u95d8 2=\u30a4\u30d9\u30f3\u30c8\n"
                "label=\u6751\u4eba\n"
                "# \u30b3\u30e1\u30f3\u30c8\u884c\n"
                "message=\u3053\u3093\u306b\u3061\u306f\n"
            ),
        )

        sources = {entry.source for entry in entries}
        self.assertIn("\u6751\u4eba", sources)
        self.assertIn("\u3053\u3093\u306b\u3061\u306f", sources)
        self.assertNotIn("\u30ad\u30e3\u30f3\u30d7 1=\u6226\u95d8 2=\u30a4\u30d9\u30f3\u30c8", sources)
        self.assertNotIn("# \u30b3\u30e1\u30f3\u30c8\u884c", sources)
        self.assertTrue(all(entry.metadata["format"] == "unity_text_asset_text_line" for entry in entries))

    def test_extract_event_text_asset_skips_control_keys(self) -> None:
        adapter = UnityAdapter()
        entries = adapter._extract_text_asset_entries(
            relative_container="Game_Data/StreamingAssets/aa/StandaloneWindows64/masterdata.bundle",
            asset_name="EVENT",
            path_id=456,
            document=adapter._text_asset_document(
                "id,text,face,position,message\n"
                "Text,\u30a8\u30ea\u30fc,1N\u7b11\u9854,C,\u304a\u306f\u3088\u3046\n"
                "choicescommand,\u4f11\u3080,\u8a13\u7df4,Ev_Rest,Ev_Training\n"
                "SetFlag,\u4f11\u61a9\u6a5f\u80fd\u89e3\u653e\n"
                "FlagCheckEvJump,\u4eba\u3055\u3089\u3044\u6483\u7834,Ev_Clear\n"
            ),
        )

        patch_sources = {entry.source for entry in entries if entry.metadata.get("format") != UNITY_DISPLAY_ALIAS_FORMAT}
        alias_sources = {entry.source for entry in entries if entry.metadata.get("format") == UNITY_DISPLAY_ALIAS_FORMAT}
        self.assertIn("\u304a\u306f\u3088\u3046", patch_sources)
        self.assertIn("\u4f11\u3080", patch_sources)
        self.assertIn("\u8a13\u7df4", patch_sources)
        self.assertNotIn("\u30a8\u30ea\u30fc", patch_sources)
        self.assertNotIn("1N\u7b11\u9854", patch_sources)
        self.assertNotIn("\u4f11\u61a9\u6a5f\u80fd\u89e3\u653e", patch_sources)
        self.assertNotIn("\u4eba\u3055\u3089\u3044\u6483\u7834", patch_sources)
        self.assertIn("\u30a8\u30ea\u30fc", alias_sources)
        self.assertTrue(
            all(entry.metadata.get("event_patch_policy") == "display" for entry in entries if entry.source in patch_sources)
        )

    def test_extract_character_expression_asset_is_control_only(self) -> None:
        adapter = UnityAdapter()
        entries = adapter._extract_text_asset_entries(
            relative_container="Game_Data/StreamingAssets/aa/StandaloneWindows64/masterdata.bundle",
            asset_name="CharacterExpression",
            path_id=456,
            document=adapter._text_asset_document(
                "\ufeff\u540d\u524d,\u8868\u60c5ID,body,face,faceRed,samen,hand,other\n"
                "\u30a8\u30ea\u30fc,1N\u7b11\u9854,Assets/Sprite/Elie/body.png,Assets/Sprite/Elie/face.png,,,,\n"
                "\u30a8\u30ea\u30fc,CG01\u89e6\u308b\u524d\u3048\u3063\u3068,Assets/Sprite/CG/CG01.png,,,,,\n"
            ),
        )

        self.assertEqual(entries, [])

    def test_apply_unity_text_asset_entries_rewrites_bundle_file(self) -> None:
        adapter = UnityAdapter()
        document = adapter._text_asset_document(
            "ID,key,text\n"
            "1,hello,\u3053\u3093\u306b\u3061\u306f\n"
            "2,bye,\u3055\u3088\u3046\u306a\u3089\n"
        )
        entries = adapter._extract_text_asset_entries(
            relative_container="Game_Data/StreamingAssets/aa/StandaloneWindows64/masterdata.bundle",
            asset_name="Greeting",
            path_id=789,
            document=document,
        )
        translated_entries = []
        for entry in entries:
            if entry.source == "\u3053\u3093\u306b\u3061\u306f":
                entry.translation = "\u4f60\u597d"
                entry.status = "translated"
                translated_entries.append(entry)

        with tempfile.TemporaryDirectory() as temp_dir:
            target_file = Path(temp_dir) / "masterdata.bundle"
            target_file.write_bytes(b"original bundle")

            fake_data = _FakeTextAssetData(
                name="Greeting",
                script="ID,key,text\n1,hello,\u3053\u3093\u306b\u3061\u306f\n2,bye,\u3055\u3088\u3046\u306a\u3089\n",
            )
            fake_file = _FakeUnityFile(fake_data)
            fake_environment = _FakeUnityEnvironment(target_file, fake_data, fake_file)
            previous_unitypy = sys.modules.get("UnityPy")
            sys.modules["UnityPy"] = _FakeUnityPy(fake_environment)
            try:
                applied, skipped = adapter._apply_unity_text_asset_entries(target_file, translated_entries)
            finally:
                if previous_unitypy is None:
                    sys.modules.pop("UnityPy", None)
                else:
                    sys.modules["UnityPy"] = previous_unitypy

            self.assertEqual(applied, 1)
            self.assertEqual(skipped, 0)
            self.assertEqual(fake_file.packer, "original")
            self.assertIn("\u4f60\u597d", fake_data.m_Script)
            self.assertIn("\u4f60\u597d", target_file.read_text(encoding="utf-8"))

    def test_patch_addressables_catalog_crc_near_bundle_name(self) -> None:
        adapter = UnityAdapter()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            aa_dir = root / "Game_Data" / "StreamingAssets" / "aa"
            bundle_dir = aa_dir / "StandaloneWindows64"
            bundle_dir.mkdir(parents=True)
            bundle_path = bundle_dir / "masterdata_assets_all_hash.bundle"
            bundle_path.write_bytes(b"bundle")
            old_crc = 0xE2750CBB
            new_crc = 0xF91E7B7D
            catalog_path = aa_dir / "catalog.bin"
            catalog_path.write_bytes(
                b"other" + old_crc.to_bytes(4, "little") + b"\x00" * 8
                + bundle_path.name.encode("utf-8")
                + b"\x00" * 16
                + old_crc.to_bytes(4, "little")
                + b"tail"
            )

            patched = adapter._patch_addressables_catalog_crc(bundle_path, old_crc, new_crc)
            data = catalog_path.read_bytes()

            self.assertEqual(patched, 1)
            self.assertIn(b"other" + old_crc.to_bytes(4, "little"), data)
            self.assertIn(bundle_path.name.encode("utf-8") + b"\x00" * 16 + new_crc.to_bytes(4, "little"), data)

    def test_patch_tmp_font_asset_raw_enables_dynamic_source_and_multi_atlas(self) -> None:
        adapter = UnityAdapter()
        raw = self._tmp_font_asset_raw(character_sequence="\u65e5")
        patch_info = adapter._parse_tmp_font_asset_patch_info(raw)

        self.assertIsNotNone(patch_info)
        assert patch_info is not None
        self.assertTrue(adapter._should_patch_tmp_font_asset(patch_info, {"\u6d4b"}))
        self.assertFalse(adapter._should_patch_tmp_font_asset(patch_info, {"\u65e5"}))

        patched = adapter._patch_tmp_font_asset_raw(raw, patch_info, source_font_path_id=321)

        self.assertEqual(struct.unpack_from("<i", patched, patch_info.source_font_file_offset)[0], 0)
        self.assertEqual(struct.unpack_from("<q", patched, patch_info.source_font_file_offset + 4)[0], 321)
        self.assertEqual(
            struct.unpack_from("<i", patched, patch_info.atlas_population_mode_offset)[0],
            1,
        )
        self.assertEqual(patched[patch_info.internal_dynamic_os_offset], 0)
        self.assertIsNotNone(patch_info.multi_atlas_enabled_offset)
        assert patch_info.multi_atlas_enabled_offset is not None
        self.assertEqual(patched[patch_info.multi_atlas_enabled_offset], 1)

    def test_default_unity_font_strategy_is_auto(self) -> None:
        adapter = UnityAdapter()

        self.assertEqual(DEFAULT_UNITY_FONT_STRATEGY, "auto")
        self.assertEqual(adapter._normalize_font_strategy(""), "auto")

    def test_auto_font_strategy_prefers_runtime_fallback(self) -> None:
        adapter = UnityAdapter()
        calls: list[str] = []

        def install_runtime(output_dir, translated_texts, progress):
            calls.append("runtime")
            return True

        def patch_assets(output_dir, translated_texts, progress):
            calls.append("asset")
            return (1, 1, 1)

        adapter._install_tmp_runtime_fallback = install_runtime  # type: ignore[method-assign]
        adapter._patch_tmp_font_assets_for_translations = patch_assets  # type: ignore[method-assign]

        adapter._apply_font_strategy(Path("out"), ["\u6d4b\u8bd5"], lambda _message: None, "auto")

        self.assertEqual(calls, ["runtime"])

    def test_auto_font_strategy_falls_back_to_asset_patch(self) -> None:
        adapter = UnityAdapter()
        calls: list[str] = []
        messages: list[str] = []

        def install_runtime(output_dir, translated_texts, progress):
            calls.append("runtime")
            return False

        def patch_assets(output_dir, translated_texts, progress):
            calls.append("asset")
            return (1, 1, 1)

        adapter._install_tmp_runtime_fallback = install_runtime  # type: ignore[method-assign]
        adapter._patch_tmp_font_assets_for_translations = patch_assets  # type: ignore[method-assign]

        adapter._apply_font_strategy(Path("out"), ["\u6d4b\u8bd5"], messages.append, "auto")

        self.assertEqual(calls, ["runtime", "asset"])
        self.assertTrue(any("runtime fallback unavailable" in message for message in messages))

    def test_collect_display_aliases_prefers_alias_entries_and_glossary_legacy_keys(self) -> None:
        adapter = UnityAdapter()
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "glossary.tsv").write_text(
                "\u30a8\u30ea\u30fc\t\u827e\u8389\tapproved\t\t3\n"
                "\u4f11\u61a9\u6a5f\u80fd\u89e3\u653e\t\u4f11\u606f\u529f\u80fd\u89e3\u653e\tapproved\t\t1\n",
                encoding="utf-8",
            )
            entries = [
                TextEntry(
                    id="alias",
                    source="\u30df\u30e5\u30fc\u30b8\u30fc",
                    translation="\u7f2a\u897f",
                    context="EVENT:1:2",
                    file="bundle",
                    adapter_id=adapter.adapter_id,
                    metadata={"format": UNITY_DISPLAY_ALIAS_FORMAT, "runtime_key": "\u30df\u30e5\u30fc\u30b8\u30fc"},
                    status="translated",
                ),
                TextEntry(
                    id="legacy-1",
                    source="\u30a8\u30ea\u30fc",
                    context="EVENT:2:2",
                    file="bundle",
                    adapter_id=adapter.adapter_id,
                    metadata={"asset_name": "EVENT", "column": 1, "event_patch_policy": "control"},
                    status="rejected",
                ),
                TextEntry(
                    id="legacy-2",
                    source="\u30a8\u30ea\u30fc",
                    context="EVENT:3:2",
                    file="bundle",
                    adapter_id=adapter.adapter_id,
                    metadata={"asset_name": "EVENT", "column": 1, "event_patch_policy": "control"},
                    status="rejected",
                ),
                TextEntry(
                    id="flag",
                    source="\u4f11\u61a9\u6a5f\u80fd\u89e3\u653e",
                    context="EVENT:4:2",
                    file="bundle",
                    adapter_id=adapter.adapter_id,
                    metadata={"asset_name": "EVENT", "column": 1, "event_patch_policy": "control"},
                    status="rejected",
                ),
            ]

            aliases = adapter._collect_display_aliases(entries, workspace)

        self.assertEqual(aliases["\u30df\u30e5\u30fc\u30b8\u30fc"], "\u7f2a\u897f")
        self.assertEqual(aliases["\u30a8\u30ea\u30fc"], "\u827e\u8389")
        self.assertNotIn("\u4f11\u61a9\u6a5f\u80fd\u89e3\u653e", aliases)

    def test_extract_managed_ldstr_entries_from_dump_tool(self) -> None:
        adapter = UnityAdapter()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game_dir = root / "game"
            managed_dir = game_dir / "Sample_Data" / "Managed"
            managed_dir.mkdir(parents=True)
            assembly_path = managed_dir / "Assembly-CSharp.dll"
            assembly_path.write_bytes(b"managed")

            def prepare_tool(_game_dir: Path, tool_dir: Path):
                tool_dir.mkdir(parents=True, exist_ok=True)
                source = adapter._encode_base64_text("\u30aa\u30fc\u30c8\u30bb\u30fc\u30d6\u30b9\u30ed\u30c3\u30c8")
                context = adapter._encode_base64_text("LoadPanelController::OnClickSlot")
                tool = self._write_fake_managed_tool(
                    tool_dir,
                    "if command == 'dump':\n"
                    "    Path(args[2]).write_text('123\\t4\\t%s\\t%s\\n', encoding='utf-8')\n"
                    "    raise SystemExit(0)\n" % (source, context),
                )
                return tool, []

            adapter._prepare_managed_string_tool = prepare_tool  # type: ignore[method-assign]

            entries, notes = adapter._extract_managed_ldstr_entries(game_dir, root / "work")

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].source, "\u30aa\u30fc\u30c8\u30bb\u30fc\u30d6\u30b9\u30ed\u30c3\u30c8")
            self.assertEqual(entries[0].file, "Sample_Data/Managed/Assembly-CSharp.dll")
            self.assertEqual(entries[0].metadata["format"], "unity_managed_ldstr")
            self.assertEqual(entries[0].metadata["method_token"], 123)
            self.assertTrue(any("Managed ldstr extraction completed" in note for note in notes))

    def test_apply_managed_ldstr_entries_invokes_patch_tool(self) -> None:
        adapter = UnityAdapter()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game_dir = root / "game"
            managed_dir = game_dir / "Sample_Data" / "Managed"
            managed_dir.mkdir(parents=True)
            assembly_path = managed_dir / "Assembly-CSharp.dll"
            assembly_path.write_bytes(b"managed")

            def prepare_tool(_game_dir: Path, tool_dir: Path):
                tool_dir.mkdir(parents=True, exist_ok=True)
                tool = self._write_fake_managed_tool(
                    tool_dir,
                    "if command == 'patch':\n"
                    "    print('patched=1 skipped=0')\n"
                    "    raise SystemExit(0)\n",
                )
                return tool, []

            adapter._prepare_managed_string_tool = prepare_tool  # type: ignore[method-assign]
            entry = TextEntry(
                id="managed-test",
                source="\u5e2b\u5320\u306e\u5bb6",
                translation="\u5e08\u5320\u7684\u5bb6",
                context="Assembly-CSharp.dll#SaveSlotUI::.cctor",
                file="Sample_Data/Managed/Assembly-CSharp.dll",
                adapter_id=adapter.adapter_id,
                metadata={"format": "unity_managed_ldstr", "method_token": 456, "instruction_index": 7},
                status="translated",
            )

            applied, skipped = adapter._apply_managed_ldstr_entries(assembly_path, [entry])

            self.assertEqual((applied, skipped), (1, 0))
            self.assertTrue((managed_dir / "Assembly-CSharp.dll.jgt_strings_bak").is_file())
            translations = (game_dir / "JgtManagedStringTool" / "Assembly-CSharp.translations.tsv").read_text(
                encoding="utf-8"
            )
            self.assertIn(adapter._encode_base64_text("\u5e08\u5320\u7684\u5bb6"), translations)

    def test_build_offline_resource_index_includes_loose_and_assetripper_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game_dir = root / "game"
            data_dir = game_dir / "Sample_Data"
            localization_dir = data_dir / "StreamingAssets" / "Localization"
            export_dir = root / "assetripper_export"
            export_asset_dir = export_dir / "Assets" / "Data"
            localization_dir.mkdir(parents=True)
            export_asset_dir.mkdir(parents=True)

            (game_dir / "Sample.exe").write_bytes(b"exe")
            (game_dir / "UnityPlayer.dll").write_bytes(b"unity")
            (data_dir / "globalgamemanagers").write_bytes(b"unity")
            self._write_json(
                localization_dir / "strings.json",
                {
                    "title": "\u30d7\u30ea\u30c6\u30a3\u30a2",
                    "start": "\u306f\u3058\u3081\u304b\u3089",
                    "debug": "English only",
                },
            )
            (export_asset_dir / "Card.asset").write_text(
                "%YAML 1.1\n"
                "--- !u!114 &11400000\n"
                "MonoBehaviour:\n"
                "  m_Name: \u706b\u7130\u5263\n"
                "  description: '\u706b\u30c0\u30e1\u30fc\u30b85'\n",
                encoding="utf-8",
            )

            adapter = UnityAdapter()
            index = adapter.build_offline_resource_index(
                game_dir,
                assetripper_export_dir=export_dir,
                include_unitypy=False,
            )

            by_source = {}
            for item in index.items:
                by_source.setdefault(item.source, []).append(item)

            self.assertIn("loose", by_source)
            self.assertIn("assetripper", by_source)
            self.assertTrue(any(item.path.endswith("strings.json") for item in by_source["loose"]))
            self.assertTrue(any(item.path.endswith("Card.asset") for item in by_source["assetripper"]))
            self.assertTrue(any("\u30d7\u30ea\u30c6\u30a3\u30a2" in item.sample for item in by_source["loose"]))
            self.assertTrue(any("\u706b\u7130\u5263" in item.sample for item in by_source["assetripper"]))
            self.assertGreaterEqual(index.counts()["japanese_strings"], 4)

    def test_build_offline_resource_index_includes_unitypy_text_asset_objects(self) -> None:
        adapter = UnityAdapter()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game_dir = root / "game"
            data_dir = game_dir / "Sample_Data"
            bundle_dir = data_dir / "StreamingAssets" / "aa" / "StandaloneWindows64"
            bundle_dir.mkdir(parents=True)
            (game_dir / "Sample.exe").write_bytes(b"exe")
            (game_dir / "UnityPlayer.dll").write_bytes(b"unity")
            (data_dir / "globalgamemanagers").write_bytes(b"unity")
            bundle_path = bundle_dir / "masterdata.bundle"
            bundle_path.write_bytes(b"bundle" * 16)

            fake_data = _FakeTextAssetData(
                name="CardMaster",
                script="ID,cardName,effectText\n1,\u65ac\u6483,\u30c0\u30e1\u30fc\u30b85\n",
            )
            fake_file = _FakeUnityFile(fake_data)
            fake_environment = _FakeUnityEnvironment(bundle_path, fake_data, fake_file)
            previous_unitypy = sys.modules.get("UnityPy")
            sys.modules["UnityPy"] = _FakeUnityPy(fake_environment)
            try:
                index = adapter.build_offline_resource_index(game_dir, include_unitypy=True)
            finally:
                if previous_unitypy is None:
                    sys.modules.pop("UnityPy", None)
                else:
                    sys.modules["UnityPy"] = previous_unitypy

            item = next(item for item in index.items if item.object_type == "TextAsset")
            self.assertEqual(item.source, "unitypy")
            self.assertEqual(item.kind, "asset_object")
            self.assertEqual(item.asset_name, "CardMaster")
            self.assertEqual(item.path_id, 789)
            self.assertEqual(item.text_entry_count, 2)
            self.assertIn("\u65ac\u6483", item.sample)

    def _write_json(self, path: Path, data) -> None:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)

    def _read_json(self, path: Path):
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write_fake_managed_tool(self, tool_dir: Path, body: str) -> Path:
        if os.name == "nt":
            tool = tool_dir / "fake_managed_tool.cmd"
            tool.write_text(
                "@echo off\n"
                "\"%s\" \"%%~f0.py\" %%*\n"
                "exit /b %%ERRORLEVEL%%\n" % sys.executable,
                encoding="utf-8",
            )
            script = tool_dir / "fake_managed_tool.cmd.py"
        else:
            tool = tool_dir / "fake_managed_tool"
            script = tool

        script.write_text(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            "import sys\n"
            "args = sys.argv[1:]\n"
            "command = args[0] if args else ''\n"
            + body
            + "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        if os.name != "nt":
            tool.chmod(0o755)
        return tool

    def _unity_raw_string(self, value: str) -> bytes:
        raw = value.encode("utf-8")
        data = bytearray(struct.pack("<i", len(raw)))
        data.extend(raw)
        while len(data) % 4:
            data.append(0)
        return bytes(data)

    def _tmp_font_asset_raw(self, character_sequence: str) -> bytes:
        data = bytearray()

        def align() -> None:
            while len(data) % 4:
                data.append(0)

        def i32(value: int) -> None:
            data.extend(struct.pack("<i", value))

        def i64(value: int) -> None:
            data.extend(struct.pack("<q", value))

        def f32(value: float) -> None:
            data.extend(struct.pack("<f", value))

        def boolean(value: bool) -> None:
            data.append(1 if value else 0)
            align()

        def pptr(file_id: int = 0, path_id: int = 0) -> None:
            i32(file_id)
            i64(path_id)

        def string(value: str) -> None:
            raw = value.encode("utf-8")
            i32(len(raw))
            data.extend(raw)
            align()

        pptr()
        i32(1)
        pptr(1, 1184)
        string("Sample SDF")
        string("1.1.0")
        i32(0)
        string("Sample")
        string("Regular")
        f32(40.0)
        f32(1.0)
        i32(1000)
        for _index in range(15):
            f32(0.0)
        pptr(0, 2)
        string("0" * 32)
        string("")
        string("0" * 32)
        for value in (0, 0, 40, 5, 0, 0, 1024, 1024, 7):
            i32(value)
        string(character_sequence)
        string("")
        string("")
        i32(0)
        f32(0.0)
        i32(4165)
        boolean(False)
        pptr()
        string("")
        i32(0)
        boolean(True)
        i32(0)
        i32(0)
        pptr(0, 99)
        i32(0)
        i32(0)
        boolean(False)
        boolean(False)
        boolean(False)
        i32(1024)
        i32(1024)
        i32(5)
        i32(4165)
        return bytes(data)


class _FakeUnityPy:
    def __init__(self, environment) -> None:
        self._environment = environment

    def load(self, path: str):
        return self._environment


class _FakeUnityEnvironment:
    def __init__(self, target_file: Path, data, file_item) -> None:
        self.objects = [_FakeUnityObject(data)]
        self.files = {str(target_file): file_item}


class _FakeUnityObject:
    def __init__(self, data) -> None:
        self.type = _FakeUnityType()
        self.path_id = 789
        self._data = data

    def read(self):
        return self._data


class _FakeUnityType:
    name = "TextAsset"


class _FakeTextAssetData:
    def __init__(self, name: str, script: str) -> None:
        self.m_Name = name
        self.m_Script = script
        self.file_item = None

    def save(self) -> None:
        self.file_item.is_changed = True


class _FakeUnityFile:
    def __init__(self, data: _FakeTextAssetData) -> None:
        self.is_changed = False
        self.packer = None
        self._data = data
        data.file_item = self

    def save(self, packer=None):
        self.packer = packer
        return self._data.m_Script.encode("utf-8")


if __name__ == "__main__":
    unittest.main()
