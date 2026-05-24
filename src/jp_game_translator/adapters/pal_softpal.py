from __future__ import annotations

import hashlib
import re
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from jp_game_translator.core.models import DetectionResult, ExtractionBundle, TextEntry

from .base import GameAdapter

PAC_V2_MAGIC = b"PAC "
PAC_V2_TABLE_OFFSET = 0x804
PAC_V2_NAME_SIZE = 32
PAC_V2_ENTRY_SIZE = PAC_V2_NAME_SIZE + 8
SOFTPAL_CODE_OFFSET = 0x0C
POINT_MAGIC = b"$POINT_LIST_****"
TEXT_CIPHER_NONE = "none"
TEXT_CIPHER_SOFTPAL = "softpal"
SOFTPAL_TEXT_CIPHER_XOR = 0x084DF873 ^ 0xFF987DEE

JAPANESE_OR_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
HALFWIDTH_KATAKANA_RE = re.compile(r"[\uff61-\uff9f]")
PRIVATE_USE_RE = re.compile(r"[\ue000-\uf8ff]")

OPERAND_TYPES: Dict[int, str] = {
    0x0001: "pp",
    0x0002: "pp",
    0x0003: "pp",
    0x0004: "pp",
    0x0005: "pp",
    0x0006: "pp",
    0x0007: "pp",
    0x0008: "pp",
    0x0009: "l",
    0x000A: "lp",
    0x000B: "l",
    0x000C: "pp",
    0x000D: "pp",
    0x000E: "pp",
    0x000F: "pp",
    0x0010: "pp",
    0x0011: "pp",
    0x0012: "pp",
    0x0013: "pp",
    0x0014: "i",
    0x0017: "ii",
    0x001A: "pp",
    0x001B: "pp",
    0x001C: "pp",
    0x001D: "i",
    0x001E: "p",
    0x001F: "p",
    0x0020: "p",
    0x0021: "p",
}

OP_MOV = 0x0001
OP_CALL = 0x000B
OP_SYSCALL = 0x0017
OP_RET = 0x0018
OP_PUSH = 0x001F
OP_ENTER = 0x0020
OP_SELECT_ADD_CHOICE = 0x003A
MESSAGE_OPCODES = {0x0088, 0x0095, 0x0096, 0x0097, 0x0098, 0x0099}
MESSAGE_SYSCALLS = {0x20002, 0x2000F, 0x20010, 0x20011, 0x20012, 0x20013}


@dataclass
class PacEntry:
    path: str
    size: int
    offset: int
    data: bytes


@dataclass
class PacArchive:
    path: Path
    entries: List[PacEntry]
    raw: bytes


@dataclass
class ScriptSet:
    script_set_id: str
    container: str
    script_path: str
    text_path: str
    point_path: str
    code: bytes
    text: bytes
    point: bytes


@dataclass(frozen=True)
class Operand:
    offset: int
    raw_value: int

    @property
    def type(self) -> int:
        return (self.raw_value >> 28) & 0x0F

    @property
    def value(self) -> int:
        value = self.raw_value & 0x0FFFFFFF
        if value & 0x08000000:
            value -= 0x10000000
        return value


@dataclass
class Instruction:
    offset: int
    opcode: int
    operands: List[Operand]


@dataclass
class TextOperand:
    operand_offset: int
    text_address: int
    kind: str


@dataclass
class TextDecodeSelection:
    cipher: str
    text: bytes
    decoded_items: List[Tuple[int, TextOperand, str, str]]
    quality: int


@dataclass
class UserMessageFunction:
    num_args: int
    name_arg_index: int
    message_arg_index: int


class PalSoftpalAdapter(GameAdapter):
    adapter_id = "pal_softpal"
    engine_name = "PAL / Softpal AVG"

    def detect(self, game_dir: Path) -> Optional[DetectionResult]:
        pal_dlls = sorted(
            path
            for path in list(game_dir.glob("Pal*.dll")) + list((game_dir / "dll").glob("Pal*.dll"))
            if path.is_file()
        )
        pac_files = sorted(path for path in game_dir.glob("*.pac") if path.is_file())
        pac_v2 = [path for path in pac_files if self._looks_like_pac_v2(path)]
        script_archives = []
        for pac_file in pac_v2:
            try:
                archive = self._read_pac_v2(pac_file)
            except Exception:
                continue
            names = {entry.path.upper() for entry in archive.entries}
            if {"SCRIPT.SRC", "TEXT.DAT", "POINT.DAT"}.issubset(names):
                script_archives.append(pac_file.name)

        loose_script_sets = list(self._find_loose_script_sets(game_dir))

        if not pal_dlls and not pac_v2 and not script_archives and not loose_script_sets:
            return None

        confidence = 0.45
        reasons: List[str] = []
        if pal_dlls:
            confidence += 0.25
            reasons.append("found PAL runtime: %s" % ", ".join(path.relative_to(game_dir).as_posix() for path in pal_dlls))
        if pac_v2:
            confidence += 0.15
            reasons.append("found Softpal PAC v2 archives: %s" % len(pac_v2))
        if script_archives:
            confidence += 0.12
            reasons.append("found Softpal script set in archives: %s" % ", ".join(script_archives))
        if loose_script_sets:
            confidence += 0.08
            reasons.append("found loose SCRIPT.SRC/TEXT.DAT/POINT.DAT set")

        return DetectionResult(
            adapter_id=self.adapter_id,
            engine_name=self.engine_name,
            confidence=min(confidence, 0.99),
            reasons=reasons,
        )

    def extract(self, game_dir: Path, workspace: Path) -> ExtractionBundle:
        entries: List[TextEntry] = []
        notes: List[str] = []
        script_sets = self._find_script_sets(game_dir)
        encrypted_or_unsupported = 0

        for script_set in script_sets:
            try:
                operands = SoftpalDisassembler(script_set.code, script_set.point).find_text_operands()
            except Exception as exc:
                notes.append("Skipped %s: failed to disassemble SCRIPT.SRC (%s)." % (script_set.container, exc))
                continue

            selection = self._select_text_decode(script_set.text, operands)
            decoded_items = selection.decoded_items

            decoded_count = len(decoded_items)
            skipped_count = len(operands) - decoded_count
            if operands and not self._is_readable_text_table(decoded_count, len(operands)):
                encrypted_or_unsupported += 1
                notes.append(
                    "Softpal script set %s: decoded %s/%s text operands; skipped as unsupported."
                    % (script_set.container, decoded_count, len(operands))
                )
                continue

            for index, operand, source, source_encoding in decoded_items:
                status = "new" if JAPANESE_OR_CJK_RE.search(source) else "locked"
                entry_id = self._entry_id(script_set.script_set_id, operand.operand_offset, source)
                entries.append(
                    TextEntry(
                        id=entry_id,
                        source=source,
                        context="%s:%s:%08X" % (script_set.container, operand.kind, operand.operand_offset),
                        file=script_set.container,
                        adapter_id=self.adapter_id,
                        metadata={
                            "script_set_id": script_set.script_set_id,
                            "container": script_set.container,
                            "script_path": script_set.script_path,
                            "text_path": script_set.text_path,
                            "point_path": script_set.point_path,
                            "operand_index": index,
                            "operand_count": len(operands),
                            "operand_offset": operand.operand_offset,
                            "text_address": operand.text_address,
                            "kind": operand.kind,
                            "source_encoding": source_encoding,
                            "text_cipher": selection.cipher,
                        },
                        status=status,
                    )
                )

            if decoded_count == 0 and skipped_count:
                encrypted_or_unsupported += 1
            notes.append(
                "Softpal script set %s: decoded %s/%s text operands%s."
                % (
                    script_set.container,
                    decoded_count,
                    len(operands),
                    " using %s TEXT.DAT cipher" % selection.cipher if selection.cipher != TEXT_CIPHER_NONE else "",
                )
            )

        if not script_sets:
            notes.append("No SCRIPT.SRC/TEXT.DAT/POINT.DAT set was found.")
        if encrypted_or_unsupported:
            notes.append(
                "Some TEXT.DAT files look encrypted or use an unsupported text cipher; "
                "PAC/script detection still works, but those strings were not extracted."
            )

        return ExtractionBundle(
            adapter_id=self.adapter_id,
            engine_name=self.engine_name,
            entries=entries,
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

        usable_entries = [
            entry
            for entry in entries
            if entry.adapter_id == self.adapter_id and entry.status != "rejected" and entry.metadata.get("script_set_id")
        ]
        grouped: Dict[str, List[TextEntry]] = {}
        for entry in usable_entries:
            grouped.setdefault(str(entry.metadata["script_set_id"]), []).append(entry)

        total_entries = len(usable_entries)
        progress(
            "[apply] pending=%s, script_sets=%s, adapter=%s, output=%s"
            % (total_entries, len(grouped), self.adapter_id, output_dir)
        )
        progress("[apply] copy start: source=%s, output=%s." % (game_dir, output_dir))
        shutil.copytree(str(game_dir), str(output_dir))
        progress("[apply] copy done: completed=0/%s; script_sets=%s." % (total_entries, len(grouped)))

        script_sets = {script_set.script_set_id: script_set for script_set in self._find_script_sets(game_dir)}
        completed_entries = 0
        applied_entries = 0
        skipped_entries = 0

        for set_index, script_set_id in enumerate(sorted(grouped), start=1):
            script_set = script_sets.get(script_set_id)
            set_entries = sorted(grouped[script_set_id], key=lambda item: int(item.metadata.get("operand_index", 0)))
            if script_set is None:
                skipped_entries += len(set_entries)
                completed_entries += len(set_entries)
                progress(
                    "[apply] script set %s/%s skipped: source files missing; completed=%s/%s; skipped=%s; set=%s."
                    % (set_index, len(grouped), completed_entries, total_entries, skipped_entries, script_set_id)
                )
                continue

            code = bytearray(script_set.code)
            text_cipher = self._entry_text_cipher(set_entries)
            text = bytearray(self._decode_text_payload(script_set.text, text_cipher))
            if text and text_cipher == TEXT_CIPHER_NONE:
                text[0] = ord("_")

            set_applied = 0
            for entry in set_entries:
                operand_offset = int(entry.metadata.get("operand_offset", -1))
                if operand_offset < 0 or operand_offset + 4 > len(code):
                    skipped_entries += 1
                    continue
                patch_text = entry.translation if entry.translation and entry.status != "locked" else entry.source
                encoded, target_encoding = self._encode_text(patch_text)
                new_address = len(text)
                text.extend(b"\x00\x00\x00\x00")
                text.extend(encoded)
                text.append(0)
                struct.pack_into("<i", code, operand_offset, new_address)
                entry.metadata["target_encoding"] = target_encoding
                set_applied += 1

            overlay_dir = output_dir / "data"
            overlay_dir.mkdir(parents=True, exist_ok=True)
            (overlay_dir / Path(script_set.script_path).name).write_bytes(bytes(code))
            (overlay_dir / Path(script_set.text_path).name).write_bytes(
                self._encode_text_payload(bytes(text), text_cipher)
            )
            (overlay_dir / Path(script_set.point_path).name).write_bytes(script_set.point)

            completed_entries += len(set_entries)
            applied_entries += set_applied
            progress(
                "[apply] script set %s/%s done: applied=%s/%s; completed=%s/%s; skipped=%s; text_cipher=%s; overlay=%s."
                % (
                    set_index,
                    len(grouped),
                    set_applied,
                    len(set_entries),
                    completed_entries,
                    total_entries,
                    skipped_entries,
                    text_cipher,
                    overlay_dir,
                )
            )

        progress(
            "[apply] finished: applied=%s/%s; completed=%s/%s; skipped=%s; output=%s."
            % (applied_entries, total_entries, completed_entries, total_entries, skipped_entries, output_dir)
        )

    def _find_script_sets(self, game_dir: Path) -> List[ScriptSet]:
        script_sets = list(self._find_loose_script_sets(game_dir))
        if script_sets:
            return script_sets
        for pac_file in sorted(game_dir.glob("*.pac")):
            if not self._looks_like_pac_v2(pac_file):
                continue
            try:
                archive = self._read_pac_v2(pac_file)
            except Exception:
                continue
            by_name = {entry.path.upper(): entry for entry in archive.entries}
            if not {"SCRIPT.SRC", "TEXT.DAT", "POINT.DAT"}.issubset(by_name):
                continue
            container = pac_file.relative_to(game_dir).as_posix()
            script_sets.append(
                ScriptSet(
                    script_set_id=self._script_set_id(container, "SCRIPT.SRC", "TEXT.DAT", "POINT.DAT"),
                    container=container,
                    script_path=by_name["SCRIPT.SRC"].path,
                    text_path=by_name["TEXT.DAT"].path,
                    point_path=by_name["POINT.DAT"].path,
                    code=by_name["SCRIPT.SRC"].data,
                    text=by_name["TEXT.DAT"].data,
                    point=by_name["POINT.DAT"].data,
                )
            )
        return script_sets

    def _find_loose_script_sets(self, game_dir: Path) -> Iterator[ScriptSet]:
        candidates = [game_dir, game_dir / "data", game_dir / "script"]
        seen = set()
        for directory in candidates:
            if not directory.is_dir():
                continue
            script = self._case_insensitive_child(directory, "SCRIPT.SRC")
            text = self._case_insensitive_child(directory, "TEXT.DAT")
            point = self._case_insensitive_child(directory, "POINT.DAT")
            if script is None or text is None or point is None:
                continue
            key = str(directory.resolve())
            if key in seen:
                continue
            seen.add(key)
            container = directory.relative_to(game_dir).as_posix() if directory != game_dir else "."
            yield ScriptSet(
                script_set_id=self._script_set_id(container, script.name, text.name, point.name),
                container=container,
                script_path=script.relative_to(game_dir).as_posix(),
                text_path=text.relative_to(game_dir).as_posix(),
                point_path=point.relative_to(game_dir).as_posix(),
                code=script.read_bytes(),
                text=text.read_bytes(),
                point=point.read_bytes(),
            )

    def _case_insensitive_child(self, directory: Path, name: str) -> Optional[Path]:
        target = name.upper()
        for child in directory.iterdir():
            if child.is_file() and child.name.upper() == target:
                return child
        return None

    def _looks_like_pac_v2(self, path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                return handle.read(4) == PAC_V2_MAGIC
        except OSError:
            return False

    def _read_pac_v2(self, path: Path) -> PacArchive:
        raw = path.read_bytes()
        if raw[:4] != PAC_V2_MAGIC:
            raise ValueError("not a Softpal PAC v2 archive")
        if len(raw) < PAC_V2_TABLE_OFFSET:
            raise ValueError("truncated PAC header")
        file_count = struct.unpack_from("<I", raw, 8)[0]
        entries: List[PacEntry] = []
        for index in range(file_count):
            pos = PAC_V2_TABLE_OFFSET + index * PAC_V2_ENTRY_SIZE
            if pos + PAC_V2_ENTRY_SIZE > len(raw):
                raise ValueError("truncated PAC table")
            name_bytes = raw[pos : pos + PAC_V2_NAME_SIZE].split(b"\x00", 1)[0]
            name = name_bytes.decode("ascii", errors="replace").replace("_", "/")
            size, offset = struct.unpack_from("<II", raw, pos + PAC_V2_NAME_SIZE)
            if offset + size > len(raw):
                raise ValueError("PAC entry points outside archive: %s" % name)
            entries.append(PacEntry(path=name, size=size, offset=offset, data=raw[offset : offset + size]))
        return PacArchive(path=path, entries=entries, raw=raw)

    def _select_text_decode(self, text_data: bytes, operands: List[TextOperand]) -> TextDecodeSelection:
        candidates = [TextDecodeSelection(TEXT_CIPHER_NONE, text_data, [], 0)]
        decrypted = self._decrypt_softpal_text_dat(text_data)
        if decrypted != text_data:
            candidates.append(TextDecodeSelection(TEXT_CIPHER_SOFTPAL, decrypted, [], 0))

        for candidate in candidates:
            candidate.decoded_items, candidate.quality = self._decode_text_items(candidate.text, operands)

        return max(
            candidates,
            key=lambda item: (
                len(item.decoded_items),
                item.quality,
                1 if item.cipher == TEXT_CIPHER_NONE else 0,
            ),
        )

    def _decode_text_items(
        self,
        text_data: bytes,
        operands: List[TextOperand],
    ) -> Tuple[List[Tuple[int, TextOperand, str, str]], int]:
        decoded_items: List[Tuple[int, TextOperand, str, str]] = []
        quality = 0
        for index, operand in enumerate(operands):
            decoded = self._read_text(text_data, operand.text_address)
            if decoded is None:
                continue
            source, source_encoding = decoded
            if not self._is_plausible_text(source):
                continue
            decoded_items.append((index, operand, source, source_encoding))
            quality += self._text_quality(source)
        return decoded_items, quality

    def _entry_text_cipher(self, entries: List[TextEntry]) -> str:
        counts: Dict[str, int] = {}
        for entry in entries:
            cipher = str(entry.metadata.get("text_cipher") or TEXT_CIPHER_NONE)
            counts[cipher] = counts.get(cipher, 0) + 1
        if not counts:
            return TEXT_CIPHER_NONE
        return max(counts.items(), key=lambda item: item[1])[0]

    def _decode_text_payload(self, text_data: bytes, cipher: str) -> bytes:
        if cipher == TEXT_CIPHER_SOFTPAL:
            return self._decrypt_softpal_text_dat(text_data)
        return text_data

    def _encode_text_payload(self, text_data: bytes, cipher: str) -> bytes:
        if cipher == TEXT_CIPHER_SOFTPAL:
            return self._encrypt_softpal_text_dat(text_data)
        return text_data

    def _decrypt_softpal_text_dat(self, text_data: bytes) -> bytes:
        data = bytearray(text_data)
        pos = 0x10
        shift = 4
        while pos + 4 <= len(data):
            block = bytearray(data[pos : pos + 4])
            block[0] = self._rotate_left(block[0], shift)
            value = struct.unpack("<I", block)[0] ^ SOFTPAL_TEXT_CIPHER_XOR
            data[pos : pos + 4] = struct.pack("<I", value)
            pos += 4
            shift = (shift + 1) % 8
        return bytes(data)

    def _encrypt_softpal_text_dat(self, text_data: bytes) -> bytes:
        data = bytearray(text_data)
        pos = 0x10
        shift = 4
        while pos + 4 <= len(data):
            value = struct.unpack("<I", data[pos : pos + 4])[0] ^ SOFTPAL_TEXT_CIPHER_XOR
            block = bytearray(struct.pack("<I", value))
            block[0] = self._rotate_right(block[0], shift)
            data[pos : pos + 4] = block
            pos += 4
            shift = (shift + 1) % 8
        return bytes(data)

    def _rotate_left(self, value: int, shift: int) -> int:
        shift %= 8
        return ((value << shift) & 0xFF) | (value >> (8 - shift))

    def _rotate_right(self, value: int, shift: int) -> int:
        shift %= 8
        return ((value >> shift) | ((value << (8 - shift)) & 0xFF)) & 0xFF

    def _read_text(self, text_data: bytes, address: int) -> Optional[Tuple[str, str]]:
        start = address + 4
        if address < 0 or start >= len(text_data):
            return None
        end = text_data.find(b"\x00", start)
        if end < 0 or end - start > 5000:
            return None
        raw = text_data[start:end]
        if not raw:
            return ("", "cp932")
        candidates: List[Tuple[str, str]] = []
        for encoding in ("cp932", "gbk"):
            try:
                value = raw.decode(encoding)
            except UnicodeDecodeError:
                continue
            value = value.replace("<br>", "\n")
            candidates.append((value, encoding))
        if not candidates:
            return None
        return max(candidates, key=lambda item: self._text_quality(item[0]))

    def _is_plausible_text(self, value: str) -> bool:
        if len(value) > 1000:
            return False
        if "\ufffd" in value:
            return False
        if PRIVATE_USE_RE.search(value):
            return False
        controls = [char for char in value if ord(char) < 32 and char not in "\r\n\t"]
        if controls:
            return False
        return bool(value.strip())

    def _text_quality(self, value: str) -> int:
        score = 0
        for char in value:
            codepoint = ord(char)
            if "\u3040" <= char <= "\u30ff":
                score += 4
            elif "\u3400" <= char <= "\u9fff":
                score += 3
            elif char in "\r\n\t":
                score += 0
            elif 0x20 <= codepoint <= 0x7E:
                score += 1
            else:
                score += 1
        score -= len(HALFWIDTH_KATAKANA_RE.findall(value)) * 5
        score -= len(PRIVATE_USE_RE.findall(value)) * 10
        return score

    def _is_readable_text_table(self, decoded_count: int, operand_count: int) -> bool:
        if operand_count == 0:
            return True
        if decoded_count == 0:
            return False
        if operand_count < 20:
            return True
        return decoded_count / operand_count >= 0.5

    def _encode_text(self, value: str) -> Tuple[bytes, str]:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
        for encoding in ("gbk", "cp932"):
            try:
                return normalized.encode(encoding), encoding
            except UnicodeEncodeError:
                continue
        raise UnicodeEncodeError("gbk/cp932", normalized, 0, len(normalized), "text contains unsupported characters")

    def _entry_id(self, script_set_id: str, operand_offset: int, source: str) -> str:
        digest = hashlib.sha1(("%s|%s|%s" % (script_set_id, operand_offset, source)).encode("utf-8")).hexdigest()
        return digest[:16]

    def _script_set_id(self, container: str, script: str, text: str, point: str) -> str:
        digest = hashlib.sha1(("%s|%s|%s|%s" % (container, script, text, point)).encode("utf-8")).hexdigest()
        return digest[:16]


class SoftpalDisassembler:
    def __init__(self, code: bytes, point: bytes):
        self.code = code
        self.point = point
        self.label_offsets = self._read_point_dat(point)
        self.user_message_funcs: Dict[int, UserMessageFunction] = {}

    def find_text_operands(self) -> List[TextOperand]:
        self._find_user_message_functions()
        return self._collect_text_operands()

    def _read_point_dat(self, point: bytes) -> List[int]:
        if not point.startswith(POINT_MAGIC):
            raise ValueError("invalid POINT.DAT magic")
        labels = [SOFTPAL_CODE_OFFSET + struct.unpack_from("<i", point, pos)[0] for pos in range(16, len(point), 4)]
        labels.reverse()
        return labels

    def _instructions(self) -> Iterator[Instruction]:
        if self.code[:4] != b"Sv20":
            raise ValueError("invalid SCRIPT.SRC magic")
        pos = SOFTPAL_CODE_OFFSET
        while pos < len(self.code):
            instruction, pos = self._read_instruction(pos)
            yield instruction

    def _read_instruction(self, pos: int) -> Tuple[Instruction, int]:
        if pos + 4 > len(self.code):
            raise ValueError("truncated instruction at 0x%X" % pos)
        offset = pos
        word = struct.unpack_from("<i", self.code, pos)[0]
        pos += 4
        if (word >> 16) != 1:
            raise ValueError("invalid opcode marker at 0x%X" % offset)
        opcode = word & 0xFFFF
        operands: List[Operand] = []
        for _kind in OPERAND_TYPES.get(opcode, ""):
            if pos + 4 > len(self.code):
                raise ValueError("truncated operand at 0x%X" % pos)
            operands.append(Operand(pos, struct.unpack_from("<i", self.code, pos)[0]))
            pos += 4
        return Instruction(offset=offset, opcode=opcode, operands=operands), pos

    def _find_user_message_functions(self) -> None:
        current_func_offset = -1
        current_func_num_args = -1
        variables: Dict[int, Operand] = {}
        stack: List[Operand] = []

        for instruction in self._instructions():
            if self._is_message_instruction(instruction):
                if current_func_offset >= 0 and len(stack) >= 4:
                    _number = stack.pop()
                    name = stack.pop()
                    message = stack.pop()
                    if name.type == 8 and message.type == 8:
                        self.user_message_funcs[current_func_offset] = UserMessageFunction(
                            current_func_num_args,
                            name.value - 1,
                            message.value - 1,
                        )
                        current_func_offset = -1
                        current_func_num_args = -1
                variables.clear()
                stack.clear()
                continue

            if instruction.opcode == OP_ENTER and instruction.operands:
                current_func_offset = instruction.offset
                current_func_num_args = instruction.operands[0].value
                variables.clear()
                stack.clear()
            elif instruction.opcode == OP_MOV and len(instruction.operands) >= 2 and instruction.operands[0].type == 4:
                variables[instruction.operands[0].value] = instruction.operands[1]
            elif instruction.opcode == OP_PUSH and instruction.operands:
                stack.append(self._resolve_operand(instruction.operands[0], variables))
            elif instruction.opcode == OP_RET:
                current_func_offset = -1
                current_func_num_args = -1
                variables.clear()
                stack.clear()
            else:
                variables.clear()
                stack.clear()

    def _collect_text_operands(self) -> List[TextOperand]:
        variables: Dict[int, Operand] = {}
        stack: List[Operand] = []
        text_operands: List[TextOperand] = []

        for instruction in self._instructions():
            if self._is_message_instruction(instruction):
                self._handle_message_instruction(stack, variables, text_operands)
            elif instruction.opcode == OP_MOV and len(instruction.operands) >= 2:
                if instruction.operands[0].type == 4:
                    variables[instruction.operands[0].value] = instruction.operands[1]
            elif instruction.opcode == OP_PUSH and instruction.operands:
                stack.append(self._resolve_operand(instruction.operands[0], variables))
            elif instruction.opcode == OP_CALL:
                self._handle_call_instruction(instruction, stack, variables, text_operands)
            elif instruction.opcode == OP_SYSCALL:
                if instruction.operands and instruction.operands[0].raw_value == 0x60002:
                    self._handle_select_choice_instruction(stack, variables, text_operands)
                else:
                    stack.clear()
            elif instruction.opcode == OP_SELECT_ADD_CHOICE:
                self._handle_select_choice_instruction(stack, variables, text_operands)
            else:
                variables.clear()
                stack.clear()

        return text_operands

    def _resolve_operand(self, operand: Operand, variables: Dict[int, Operand]) -> Operand:
        if operand.type == 4 and operand.value in variables:
            return variables[operand.value]
        return operand

    def _handle_message_instruction(
        self,
        stack: List[Operand],
        variables: Dict[int, Operand],
        text_operands: List[TextOperand],
    ) -> None:
        try:
            if len(stack) < 4:
                return
            _number = stack.pop()
            name = stack.pop()
            message = stack.pop()
            if name.type != 0 or message.type != 0:
                return
            if name.value >= 0:
                text_operands.append(TextOperand(name.offset, name.value, "name"))
            if message.value >= 0:
                text_operands.append(TextOperand(message.offset, message.value, "message"))
        finally:
            variables.clear()
            stack.clear()

    def _handle_call_instruction(
        self,
        instruction: Instruction,
        stack: List[Operand],
        variables: Dict[int, Operand],
        text_operands: List[TextOperand],
    ) -> None:
        try:
            if not instruction.operands or instruction.operands[0].type != 0:
                return
            target_index = instruction.operands[0].value - 1
            if target_index < 0 or target_index >= len(self.label_offsets):
                return
            message_func = self.user_message_funcs.get(self.label_offsets[target_index])
            if message_func is None or len(stack) < message_func.num_args:
                return
            args = [stack.pop() for _index in range(message_func.num_args)]
            args.reverse()
            if message_func.name_arg_index >= len(args) or message_func.message_arg_index >= len(args):
                return
            name = args[message_func.name_arg_index]
            message = args[message_func.message_arg_index]
            if message.type == 0 and message.value >= 0:
                if name.type == 0 and name.value >= 0:
                    text_operands.append(TextOperand(name.offset, name.value, "name"))
                text_operands.append(TextOperand(message.offset, message.value, "message"))
        finally:
            variables.clear()
            stack.clear()

    def _handle_select_choice_instruction(
        self,
        stack: List[Operand],
        variables: Dict[int, Operand],
        text_operands: List[TextOperand],
    ) -> None:
        try:
            if stack:
                choice = stack.pop()
                if choice.type == 0 and choice.value >= 0:
                    text_operands.append(TextOperand(choice.offset, choice.value, "choice"))
        finally:
            variables.clear()
            stack.clear()

    def _is_message_instruction(self, instruction: Instruction) -> bool:
        if instruction.opcode in MESSAGE_OPCODES:
            return True
        if instruction.opcode == OP_SYSCALL and instruction.operands:
            return instruction.operands[0].raw_value in MESSAGE_SYSCALLS
        return False
