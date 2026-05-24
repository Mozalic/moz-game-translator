from __future__ import annotations

import json
import os
import hashlib
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

from jp_game_translator.core.models import TextEntry

from .prompts import SYSTEM_PROMPT, TERM_SYSTEM_PROMPT, build_term_user_prompt, build_user_prompt, select_glossary_for_entries

_PRINT_LOCK = threading.Lock()


@dataclass
class ProviderConfig:
    name: str
    type: str
    base_url: str
    model: str
    api_key_env: str = ""
    api_key: str = ""
    temperature: float = 0.2
    json_mode: bool = False

    @classmethod
    def from_dict(cls, name: str, data: Mapping[str, Any]) -> "ProviderConfig":
        return cls(
            name=name,
            type=str(data.get("type", "openai_compatible")),
            base_url=str(data["base_url"]).rstrip("/"),
            model=str(data["model"]),
            api_key_env=str(data.get("api_key_env", "")),
            api_key=str(data.get("api_key", "")),
            temperature=float(data.get("temperature", 0.2)),
            json_mode=bool(data.get("json_mode", False)),
        )

    def resolve_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "") or _read_windows_environment_variable(self.api_key_env)
        return ""


@dataclass
class TranslationCallResult:
    translations: Dict[str, str]
    elapsed_seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    request_kind: str = ""
    provider_name: str = ""
    model: str = ""
    input_items: int = 0
    glossary_terms: int = 0
    system_prompt_chars: int = 0
    user_prompt_chars: int = 0
    request_chars: int = 0
    prompt_sha256: str = ""


class TranslationResponseError(ValueError):
    def __init__(self, message: str, result: TranslationCallResult):
        super().__init__(message)
        self.result = result


def load_provider_config(config_path: Path, provider_name: str) -> ProviderConfig:
    with config_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    providers = data.get("providers") or {}
    if provider_name not in providers:
        raise KeyError("provider not found in config: %s" % provider_name)
    return ProviderConfig.from_dict(provider_name, providers[provider_name])


class OpenAICompatibleProvider:
    def __init__(self, config: ProviderConfig):
        if config.type != "openai_compatible":
            raise ValueError("unsupported provider type: %s" % config.type)
        self.config = config

    def list_models(self) -> List[str]:
        response_data = self._request_json("/models", method="GET", timeout=60)
        raw_models = response_data.get("data", []) if isinstance(response_data, dict) else []
        models: List[str] = []
        if isinstance(raw_models, list):
            for item in raw_models:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    models.append(item["id"])
                elif isinstance(item, str):
                    models.append(item)
        return sorted(set(models))

    def test_connection(self) -> str:
        if not self.config.model:
            models = self.list_models()
            if models:
                return "OK: fetched %s models." % len(models)
            return "OK: endpoint responded, but no models were returned."

        request_body = {
            "model": self.config.model,
            "temperature": 0,
            "max_tokens": 8,
            "messages": [
                {"role": "user", "content": "Return exactly: OK"},
            ],
        }
        response_data = self._request_json("/chat/completions", method="POST", payload=request_body, timeout=60)
        choices = response_data.get("choices", []) if isinstance(response_data, dict) else []
        if choices:
            return "OK: chat completion succeeded with model '%s'." % self.config.model
        return "OK: endpoint responded for model '%s'." % self.config.model

    def translate_batch(
        self,
        entries: Iterable[TextEntry],
        glossary: Dict[str, str],
        target_language: str,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> Dict[str, str]:
        return self.translate_batch_with_stats(entries, glossary, target_language, system_prompt).translations

    def translate_batch_with_stats(
        self,
        entries: Iterable[TextEntry],
        glossary: Dict[str, str],
        target_language: str,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> TranslationCallResult:
        entries_list = list(entries)
        if not entries_list:
            return TranslationCallResult(translations={})

        system_content = system_prompt or SYSTEM_PROMPT
        batch_glossary = select_glossary_for_entries(entries_list, glossary)
        user_content = build_user_prompt(entries_list, batch_glossary, target_language)
        request_body = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
        }
        if self.config.json_mode:
            request_body["response_format"] = {"type": "json_object"}
        request_chars = _request_chars(request_body)
        _print_request_prompt(
            "text_translation",
            self.config.name,
            self.config.model,
            system_content,
            user_content,
            input_items=len(entries_list),
            glossary_terms=len(batch_glossary),
            request_chars=request_chars,
        )
        started = time.perf_counter()
        response_data = self._request_json("/chat/completions", method="POST", payload=request_body, timeout=120)
        elapsed = time.perf_counter() - started
        content = response_data["choices"][0]["message"]["content"]
        try:
            translations = _parse_translation_response(content)
        except ValueError as exc:
            failed_result = _result_with_usage(
                {},
                response_data,
                elapsed,
                request_kind="text_translation",
                provider_name=self.config.name,
                model=self.config.model,
                input_items=len(entries_list),
                glossary_terms=len(batch_glossary),
                system_prompt_chars=len(system_content),
                user_prompt_chars=len(user_content),
                request_chars=request_chars,
                prompt_sha256=_prompt_hash(system_content, user_content),
            )
            raise TranslationResponseError(str(exc), failed_result) from exc
        return _result_with_usage(
            translations,
            response_data,
            elapsed,
            request_kind="text_translation",
            provider_name=self.config.name,
            model=self.config.model,
            input_items=len(entries_list),
            glossary_terms=len(batch_glossary),
            system_prompt_chars=len(system_content),
            user_prompt_chars=len(user_content),
            request_chars=request_chars,
            prompt_sha256=_prompt_hash(system_content, user_content),
        )

    def translate_terms(
        self,
        terms: Iterable[str],
        target_language: str,
        system_prompt: str = TERM_SYSTEM_PROMPT,
    ) -> Dict[str, str]:
        return self.translate_terms_with_stats(terms, target_language, system_prompt).translations

    def translate_terms_with_stats(
        self,
        terms: Iterable[str],
        target_language: str,
        system_prompt: str = TERM_SYSTEM_PROMPT,
    ) -> TranslationCallResult:
        terms_list = list(terms)
        if not terms_list:
            return TranslationCallResult(translations={})
        system_content = system_prompt or TERM_SYSTEM_PROMPT
        user_content = build_term_user_prompt(terms_list, target_language)
        request_body = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
        }
        if self.config.json_mode:
            request_body["response_format"] = {"type": "json_object"}
        request_chars = _request_chars(request_body)
        _print_request_prompt(
            "term_translation",
            self.config.name,
            self.config.model,
            system_content,
            user_content,
            input_items=len(terms_list),
            glossary_terms=0,
            request_chars=request_chars,
        )
        started = time.perf_counter()
        response_data = self._request_json("/chat/completions", method="POST", payload=request_body, timeout=120)
        elapsed = time.perf_counter() - started
        content = response_data["choices"][0]["message"]["content"]
        try:
            translations = _parse_term_translation_response(content)
        except ValueError as exc:
            failed_result = _result_with_usage(
                {},
                response_data,
                elapsed,
                request_kind="term_translation",
                provider_name=self.config.name,
                model=self.config.model,
                input_items=len(terms_list),
                glossary_terms=0,
                system_prompt_chars=len(system_content),
                user_prompt_chars=len(user_content),
                request_chars=request_chars,
                prompt_sha256=_prompt_hash(system_content, user_content),
            )
            raise TranslationResponseError(str(exc), failed_result) from exc
        return _result_with_usage(
            translations,
            response_data,
            elapsed,
            request_kind="term_translation",
            provider_name=self.config.name,
            model=self.config.model,
            input_items=len(terms_list),
            glossary_terms=0,
            system_prompt_chars=len(system_content),
            user_prompt_chars=len(user_content),
            request_chars=request_chars,
            prompt_sha256=_prompt_hash(system_content, user_content),
        )

    def _request_json(
        self,
        path: str,
        method: str = "GET",
        payload: Any = None,
        timeout: int = 120,
    ) -> Any:
        api_key = self.config.resolve_api_key()
        if not api_key:
            raise RuntimeError(
                "missing API key for provider '%s'; set %s or put api_key in config"
                % (self.config.name, self.config.api_key_env or "api_key")
            )

        request_data = None
        if payload is not None:
            request_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + path,
            data=request_data,
            headers={
                "Authorization": "Bearer %s" % api_key,
                "Content-Type": "application/json",
            },
            method=method,
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError("provider HTTP error %s: %s" % (exc.code, body)) from exc


def _print_request_prompt(
    kind: str,
    provider_name: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    input_items: int = 0,
    glossary_terms: int = 0,
    request_chars: int = 0,
) -> None:
    mode = os.environ.get("JGT_PROMPT_LOG", "summary").strip().lower()
    if mode in ("0", "false", "off", "none", "quiet"):
        return
    with _PRINT_LOCK:
        if mode == "full":
            print("", flush=True)
            print("=== JGT PROMPT BEGIN ===", flush=True)
            print("kind: %s" % kind, flush=True)
            print("provider: %s" % provider_name, flush=True)
            print("model: %s" % model, flush=True)
            print("items: %s" % input_items, flush=True)
            print("glossary_terms: %s" % glossary_terms, flush=True)
            print("request_chars: %s" % request_chars, flush=True)
            print("prompt_sha256: %s" % _prompt_hash(system_prompt, user_prompt), flush=True)
            print("--- system prompt ---", flush=True)
            print(system_prompt, flush=True)
            print("--- user prompt ---", flush=True)
            print(user_prompt, flush=True)
            print("=== JGT PROMPT END ===", flush=True)
            return

        print(
            "JGT prompt: kind=%s provider=%s model=%s items=%s glossary_terms=%s system_chars=%s user_chars=%s request_chars=%s sha256=%s preview=%s"
            % (
                kind,
                provider_name,
                model,
                input_items,
                glossary_terms,
                len(system_prompt),
                len(user_prompt),
                request_chars,
                _prompt_hash(system_prompt, user_prompt)[:16],
                _prompt_preview(user_prompt),
            ),
            flush=True,
        )


def _result_with_usage(
    translations: Dict[str, str],
    response_data: Any,
    elapsed_seconds: float,
    request_kind: str = "",
    provider_name: str = "",
    model: str = "",
    input_items: int = 0,
    glossary_terms: int = 0,
    system_prompt_chars: int = 0,
    user_prompt_chars: int = 0,
    request_chars: int = 0,
    prompt_sha256: str = "",
) -> TranslationCallResult:
    usage = response_data.get("usage", {}) if isinstance(response_data, dict) else {}
    if not isinstance(usage, dict):
        usage = {}
    prompt_tokens = _usage_int(usage, "prompt_tokens")
    completion_tokens = _usage_int(usage, "completion_tokens")
    total_tokens = _usage_int(usage, "total_tokens")
    prompt_cache_hit_tokens = _usage_int(usage, "prompt_cache_hit_tokens")
    prompt_cache_miss_tokens = _usage_int(usage, "prompt_cache_miss_tokens")
    if total_tokens <= 0 and (prompt_tokens or completion_tokens):
        total_tokens = prompt_tokens + completion_tokens
    return TranslationCallResult(
        translations=translations,
        elapsed_seconds=elapsed_seconds,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_cache_hit_tokens=prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=prompt_cache_miss_tokens,
        request_kind=request_kind,
        provider_name=provider_name,
        model=model,
        input_items=input_items,
        glossary_terms=glossary_terms,
        system_prompt_chars=system_prompt_chars,
        user_prompt_chars=user_prompt_chars,
        request_chars=request_chars,
        prompt_sha256=prompt_sha256,
    )


def _usage_int(usage: Mapping[str, Any], key: str) -> int:
    try:
        return int(usage.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _request_chars(request_body: Mapping[str, Any]) -> int:
    return len(json.dumps(request_body, ensure_ascii=False, separators=(",", ":")))


def _prompt_hash(system_prompt: str, user_prompt: str) -> str:
    digest = hashlib.sha256()
    digest.update((system_prompt or "").encode("utf-8"))
    digest.update(b"\0")
    digest.update((user_prompt or "").encode("utf-8"))
    return digest.hexdigest()


def _prompt_preview(prompt: str, limit: int = 120) -> str:
    text = " ".join((prompt or "").split())
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _read_windows_environment_variable(name: str) -> str:
    if os.name != "nt" or not name:
        return ""
    try:
        import winreg
    except ImportError:
        return ""

    for root, subkey in (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ):
        try:
            with winreg.OpenKey(root, subkey) as handle:
                value, _value_type = winreg.QueryValueEx(handle, name)
        except OSError:
            continue
        if value:
            return str(value)
    return ""


def _parse_translation_response(content: str) -> Dict[str, str]:
    parsed = _load_response_json(content)
    if isinstance(parsed, dict) and "items" in parsed:
        parsed = parsed["items"]
    if not isinstance(parsed, list):
        raise ValueError("translation response must be a JSON array or an object with items")

    result: Dict[str, str] = {}
    for item in parsed:
        if isinstance(item, dict):
            entry_id = item.get("id")
            translation = item.get("translation")
        elif isinstance(item, list) and len(item) >= 2:
            entry_id = item[0]
            translation = item[1]
        else:
            continue
        if isinstance(entry_id, str) and isinstance(translation, str):
            result[entry_id] = translation
    return result


def _parse_term_translation_response(content: str) -> Dict[str, str]:
    parsed = _load_response_json(content)
    if isinstance(parsed, dict) and "items" in parsed:
        parsed = parsed["items"]
    if not isinstance(parsed, list):
        raise ValueError("term translation response must be a JSON array or an object with items")

    result: Dict[str, str] = {}
    for item in parsed:
        if isinstance(item, dict):
            source = item.get("source")
            target = item.get("target") or item.get("translation")
        elif isinstance(item, list) and len(item) >= 2:
            source = item[0]
            target = item[1]
        else:
            continue
        if isinstance(source, str) and isinstance(target, str):
            result[source] = target
    return result


def _load_response_json(content: str) -> Any:
    text = (content or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        extracted = _extract_json_payload(text)
        if extracted != text:
            return json.loads(extracted)
        raise


def _extract_json_payload(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    candidates = []
    object_start = stripped.find("{")
    object_end = stripped.rfind("}")
    if object_start >= 0 and object_end > object_start:
        candidates.append(stripped[object_start : object_end + 1])
    array_start = stripped.find("[")
    array_end = stripped.rfind("]")
    if array_start >= 0 and array_end > array_start:
        candidates.append(stripped[array_start : array_end + 1])

    for candidate in candidates:
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue
    return stripped
