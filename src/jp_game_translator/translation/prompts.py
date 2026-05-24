from __future__ import annotations

import json
from typing import Dict, Iterable, List

from jp_game_translator.core.models import TextEntry


SYSTEM_PROMPT = """你是专业的日文游戏本地化译者，负责将日文游戏文本翻译为简体中文。
要求：
1. 保持角色语气、场景氛围和游戏 UI 语境。
2. 严格保留控制符、变量、转义序列、颜色代码、图标代码、换行标记和占位符，例如 \\V[1]、\\N[2]、%s、{name}、[ruby]。
3. 不要翻译文件名、脚本命令、变量名、标签名。
4. 对术语表中的词必须使用指定译名，保持前后一致。
5. 输出必须是 JSON 对象，格式为 {"items":[{"id":"...","translation":"..."}]}，不要输出额外解释。"""

TERM_SYSTEM_PROMPT = """你是专业的日文游戏本地化术语翻译助手，负责将游戏中的专属名词翻译为简体中文。
要求：
1. 优先识别角色名、地名、组织名、技能名、道具名、系统名。
2. 译名要短、稳定，适合在 UI 和对白中反复出现。
3. 不要解释，不要补充出处，不要改变原术语。
4. 如果术语明显是人名或地名，保持命名风格统一。
5. 输出必须是 JSON 对象，格式为 {"items":[{"source":"...","target":"..."}]}。"""


def select_glossary_for_entries(entries: Iterable[TextEntry], glossary: Dict[str, str]) -> Dict[str, str]:
    entries_list = list(entries)
    if not glossary or not entries_list:
        return {}

    haystack_parts: List[str] = []
    for entry in entries_list:
        if entry.source:
            haystack_parts.append(entry.source)
        if entry.context:
            haystack_parts.append(entry.context)
        if entry.speaker:
            haystack_parts.append(entry.speaker)
    haystack = "\n".join(haystack_parts)
    if not haystack:
        return {}

    selected: Dict[str, str] = {}
    for source, target in glossary.items():
        if not source or not target:
            continue
        if source in haystack:
            selected[source] = target
    return selected


def build_user_prompt(entries: Iterable[TextEntry], glossary: Dict[str, str], target_language: str) -> str:
    payload: List[dict] = []
    entries_list = list(entries)
    for entry in entries_list:
        item = {
            "id": entry.id,
            "source": entry.source,
        }
        if entry.context:
            item["context"] = entry.context
        if entry.speaker:
            item["speaker"] = entry.speaker
        payload.append(item)

    prompt = {
        "target_language": target_language,
        "glossary": select_glossary_for_entries(entries_list, glossary),
        "items": payload,
    }
    return json.dumps(prompt, ensure_ascii=False, separators=(",", ":"))


def build_term_user_prompt(terms: Iterable[str], target_language: str) -> str:
    prompt = {
        "target_language": target_language,
        "items": [term for term in terms if term],
    }
    return json.dumps(prompt, ensure_ascii=False, separators=(",", ":"))
