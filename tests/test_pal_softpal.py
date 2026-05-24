from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jp_game_translator.adapters.pal_softpal import PalSoftpalAdapter


class PalSoftpalAdapterTest(unittest.TestCase):
    def test_detect_extract_and_apply_softpal_pac_v2(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game_dir = root / "game"
            dll_dir = game_dir / "dll"
            dll_dir.mkdir(parents=True)
            (dll_dir / "Pal.dll").write_bytes(b"runtime")

            script = self._build_script_src(message_addr=0x10, name_addr=0x40)
            text = self._build_text_dat([(0x10, "こんにちは"), (0x40, "祈理")])
            point = b"$POINT_LIST_****"
            self._write_pac_v2(game_dir / "data.pac", {"SCRIPT.SRC": script, "TEXT.DAT": text, "POINT.DAT": point})

            adapter = PalSoftpalAdapter()
            detection = adapter.detect(game_dir)
            self.assertIsNotNone(detection)
            self.assertGreaterEqual(detection.confidence, 0.9)

            bundle = adapter.extract(game_dir, root / "work")
            by_source = {entry.source: entry for entry in bundle.entries}
            self.assertIn("こんにちは", by_source)
            self.assertIn("祈理", by_source)
            self.assertTrue(any("decoded 2/2" in note for note in bundle.notes))

            message_entry = by_source["こんにちは"]
            message_entry.translation = "你好"
            message_entry.status = "translated"
            progress_logs: list[str] = []
            output_dir = root / "game_zh"
            adapter.apply(game_dir, root / "work", output_dir, bundle.entries, progress=progress_logs.append)

            patched_script = (output_dir / "data" / "SCRIPT.SRC").read_bytes()
            patched_text = (output_dir / "data" / "TEXT.DAT").read_bytes()
            self.assertEqual(patched_text[:1], b"_")
            self.assertTrue((output_dir / "data" / "POINT.DAT").exists())

            translated_address = struct.unpack_from("<i", patched_script, int(message_entry.metadata["operand_offset"]))[0]
            translated = self._read_text_at(patched_text, translated_address, "gbk")
            self.assertEqual(translated, "你好")

            patched_bundle = adapter.extract(output_dir, root / "work2")
            patched_sources = {entry.source for entry in patched_bundle.entries}
            self.assertIn("你好", patched_sources)
            self.assertIn("祈理", patched_sources)
            self.assertNotIn("こんにちは", patched_sources)
            self.assertTrue(any("[apply] pending=2" in message for message in progress_logs))
            self.assertTrue(any("completed=2/2" in message for message in progress_logs))
            self.assertTrue(any("[apply] finished: applied=2/2" in message for message in progress_logs))

    def test_extract_and_apply_encrypted_softpal_text_dat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game_dir = root / "game"
            dll_dir = game_dir / "dll"
            dll_dir.mkdir(parents=True)
            (dll_dir / "Pal.dll").write_bytes(b"runtime")

            adapter = PalSoftpalAdapter()
            script = self._build_script_src(message_addr=0x10, name_addr=0x40)
            plain_text = self._build_text_dat(
                [(0x10, "\u3053\u3093\u306b\u3061\u306f"), (0x40, "\u795d\u8a5e")],
                marker=b"$TEXT_LIST__",
            )
            encrypted_text = adapter._encrypt_softpal_text_dat(plain_text)
            self.assertEqual(adapter._encrypt_softpal_text_dat(adapter._decrypt_softpal_text_dat(encrypted_text)), encrypted_text)
            point = b"$POINT_LIST_****"
            self._write_pac_v2(
                game_dir / "data.pac",
                {"SCRIPT.SRC": script, "TEXT.DAT": encrypted_text, "POINT.DAT": point},
            )

            bundle = adapter.extract(game_dir, root / "work")
            by_source = {entry.source: entry for entry in bundle.entries}
            self.assertIn("\u3053\u3093\u306b\u3061\u306f", by_source)
            self.assertIn("\u795d\u8a5e", by_source)
            self.assertTrue(all(entry.metadata.get("text_cipher") == "softpal" for entry in bundle.entries))
            self.assertTrue(any("using softpal TEXT.DAT cipher" in note for note in bundle.notes))

            message_entry = by_source["\u3053\u3093\u306b\u3061\u306f"]
            message_entry.translation = "\u4f60\u597d"
            message_entry.status = "translated"
            output_dir = root / "game_zh"
            adapter.apply(game_dir, root / "work", output_dir, bundle.entries)

            patched_script = (output_dir / "data" / "SCRIPT.SRC").read_bytes()
            patched_text = (output_dir / "data" / "TEXT.DAT").read_bytes()
            self.assertEqual(patched_text[:1], b"$")

            decrypted_text = adapter._decrypt_softpal_text_dat(patched_text)
            translated_address = struct.unpack_from("<i", patched_script, int(message_entry.metadata["operand_offset"]))[0]
            translated = self._read_text_at(decrypted_text, translated_address, "gbk")
            self.assertEqual(translated, "\u4f60\u597d")

            patched_bundle = adapter.extract(output_dir, root / "work2")
            patched_sources = {entry.source for entry in patched_bundle.entries}
            self.assertIn("\u4f60\u597d", patched_sources)
            self.assertNotIn("\u3053\u3093\u306b\u3061\u306f", patched_sources)

    def _build_script_src(self, message_addr: int, name_addr: int) -> bytes:
        code = bytearray(b"Sv20" + b"\x00" * 8)
        code.extend(self._instruction(0x001F, 0))
        code.extend(self._instruction(0x001F, message_addr))
        code.extend(self._instruction(0x001F, name_addr))
        code.extend(self._instruction(0x001F, 0))
        code.extend(self._instruction(0x0088))
        return bytes(code)

    def _instruction(self, opcode: int, *operands: int) -> bytes:
        data = bytearray(struct.pack("<i", (1 << 16) | opcode))
        for operand in operands:
            data.extend(struct.pack("<i", operand))
        return bytes(data)

    def _build_text_dat(self, items: list[tuple[int, str]], marker: bytes = b"_TEXT_LIST__") -> bytes:
        data = bytearray(marker + struct.pack("<I", len(items)))
        for address, value in items:
            if len(data) > address:
                raise ValueError("overlapping test text entry")
            data.extend(b"\x00" * (address - len(data)))
            data.extend(b"\x00\x00\x00\x00")
            data.extend(value.encode("cp932"))
            data.append(0)
        return bytes(data)

    def _read_text_at(self, text_data: bytes, address: int, encoding: str) -> str:
        start = address + 4
        end = text_data.index(b"\x00", start)
        return text_data[start:end].decode(encoding)

    def _write_pac_v2(self, path: Path, files: dict[str, bytes]) -> None:
        names = list(files)
        table_size = len(names) * 40
        data_offset = 0x804 + table_size
        header = bytearray(0x804 + table_size)
        header[:4] = b"PAC "
        struct.pack_into("<I", header, 8, len(names))

        payload = bytearray()
        for index, name in enumerate(names):
            raw_name = name.replace("/", "_").encode("ascii")
            pos = 0x804 + index * 40
            header[pos : pos + len(raw_name)] = raw_name
            data = files[name]
            offset = data_offset + len(payload)
            struct.pack_into("<II", header, pos + 32, len(data), offset)
            payload.extend(data)

        path.write_bytes(bytes(header) + bytes(payload))


if __name__ == "__main__":
    unittest.main()
