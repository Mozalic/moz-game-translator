from __future__ import annotations

import json
import time
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Mapping, Any

from jp_game_translator.core.models import TextEntry
from jp_game_translator.core.project import GLOSSARY_FILE, load_workspace_entries, save_workspace_entries
from jp_game_translator.terminology.glossary import approved_glossary_map, load_glossary, save_glossary

from .providers import OpenAICompatibleProvider, TranslationCallResult, load_provider_config
from .prompts import SYSTEM_PROMPT, TERM_SYSTEM_PROMPT
from .token_usage import TOKEN_USAGE_LOG_FILE

TERM_TRANSLATION_BATCH_SIZE = 80
TERM_TRANSLATION_MAX_CHARS = 4000
ENTRY_TRANSLATION_BATCH_SIZE = 60
ENTRY_TRANSLATION_MAX_CHARS = 6000


@dataclass
class _RequestTotals:
    requests: int = 0
    elapsed_seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0

    def add(self, result: TranslationCallResult) -> None:
        self.requests += 1
        self.elapsed_seconds += result.elapsed_seconds
        self.prompt_tokens += result.prompt_tokens
        self.completion_tokens += result.completion_tokens
        self.total_tokens += result.total_tokens
        self.prompt_cache_hit_tokens += result.prompt_cache_hit_tokens
        self.prompt_cache_miss_tokens += result.prompt_cache_miss_tokens


@dataclass
class _BatchOutcome:
    result: TranslationCallResult
    changed: int = 0
    failed: bool = False
    error: str = ""


@dataclass
class _EntryBatch:
    entries: List[TextEntry]
    entry_count: int
    duplicate_count: int
    entry_ids_by_representative_id: Dict[str, List[str]]
    source_chars: int


def translate_workspace(
    workspace: Path,
    config_path: Path,
    provider_name: str,
    target_language: str,
    limit: int = ENTRY_TRANSLATION_BATCH_SIZE,
    system_prompt: str = SYSTEM_PROMPT,
    include_translated: bool = False,
    logger: Optional[Callable[[str], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> int:
    logger = logger or _print_progress
    if _is_cancelled(cancelled):
        logger("[entries] stop requested before request; skipped.")
        return 0
    entries = load_workspace_entries(workspace)
    batches = _select_entry_batches(
        entries,
        max(1, int(limit or ENTRY_TRANSLATION_BATCH_SIZE)),
        include_translated=include_translated,
        max_batches=1,
        max_chars=ENTRY_TRANSLATION_MAX_CHARS,
    )
    if not batches:
        logger("[entries] no pending entries.")
        return 0
    batch = batches[0]

    glossary = approved_glossary_map(load_glossary(workspace / GLOSSARY_FILE))
    provider_config = load_provider_config(config_path, provider_name)
    provider = OpenAICompatibleProvider(provider_config)
    if _is_cancelled(cancelled):
        logger("[entries] stop requested before API call; skipped.")
        return 0
    result = _translate_entry_batch(provider, batch.entries, glossary, target_language, system_prompt)
    translations = result.translations

    by_id: Dict[str, TextEntry] = {entry.id: entry for entry in entries}
    changed = _apply_entry_translations(by_id, translations, batch.entry_ids_by_representative_id)
    save_workspace_entries(workspace, entries)
    usage_log_path = workspace / TOKEN_USAGE_LOG_FILE
    _append_usage_record(
        usage_log_path,
        _usage_record(
            run_id=_make_run_id("entries"),
            scope="entries",
            event="request",
            provider_name=provider_name,
            request_index=1,
            request_count=1,
            item_count=batch.entry_count,
            changed=changed,
            result=result,
            extra={
                "unique_items": len(batch.entries),
                "duplicate_items": batch.duplicate_count,
                "source_chars": batch.source_chars,
            },
        ),
    )
    logger(
        "[entries] request done: translated=%s/%s; unique=%s; duplicates=%s; api=%s; %s."
        % (
            changed,
            batch.entry_count,
            len(batch.entries),
            batch.duplicate_count,
            _format_seconds(result.elapsed_seconds),
            _format_usage(result),
        )
    )
    return changed


def translate_workspace_all(
    workspace: Path,
    config_path: Path,
    provider_name: str,
    target_language: str,
    batch_size: int = ENTRY_TRANSLATION_BATCH_SIZE,
    system_prompt: str = SYSTEM_PROMPT,
    max_batches: int = 10000,
    logger: Optional[Callable[[str], None]] = None,
    concurrency: int = 1,
    include_translated: bool = False,
    cancelled: Optional[Callable[[], bool]] = None,
) -> int:
    logger = logger or _print_progress
    batch_size = max(1, int(batch_size or ENTRY_TRANSLATION_BATCH_SIZE))
    concurrency = max(1, int(concurrency or 1))
    entries = load_workspace_entries(workspace)
    batches = _select_entry_batches(
        entries,
        batch_size,
        include_translated=include_translated,
        max_batches=max_batches,
        max_chars=ENTRY_TRANSLATION_MAX_CHARS,
    )
    total_entries = sum(batch.entry_count for batch in batches)
    total_unique_entries = sum(len(batch.entries) for batch in batches)
    total_duplicate_entries = sum(batch.duplicate_count for batch in batches)
    if total_entries <= 0:
        logger("[entries] no pending entries.")
        return 0

    logger(
        "[entries] pending=%s, unique=%s, duplicates=%s, request_batch_size=%s, max_chars=%s, concurrency=%s, requests=%s, include_translated=%s, provider=%s"
        % (
            total_entries,
            total_unique_entries,
            total_duplicate_entries,
            batch_size,
            ENTRY_TRANSLATION_MAX_CHARS,
            concurrency,
            len(batches),
            include_translated,
            provider_name,
        )
    )

    glossary = approved_glossary_map(load_glossary(workspace / GLOSSARY_FILE))
    provider_config = load_provider_config(config_path, provider_name)
    by_id: Dict[str, TextEntry] = {entry.id: entry for entry in entries}
    total_changed = 0
    completed_entries = 0
    failed_requests = 0
    totals = _RequestTotals()
    write_lock = threading.Lock()
    usage_log_lock = threading.Lock()
    usage_log_path = workspace / TOKEN_USAGE_LOG_FILE
    run_id = _make_run_id("entries")
    started = time.perf_counter()
    logger("[entries] token usage log: %s" % usage_log_path)
    _append_usage_record(
        usage_log_path,
        _run_record(
            run_id=run_id,
            scope="entries",
            event="start",
            provider_name=provider_name,
            request_count=len(batches),
            item_count=total_entries,
            extra={
                "batch_size": batch_size,
                "max_chars": ENTRY_TRANSLATION_MAX_CHARS,
                "concurrency": concurrency,
                "include_translated": include_translated,
                "unique_items": total_unique_entries,
                "duplicate_items": total_duplicate_entries,
            },
        ),
        usage_log_lock,
    )

    def persist_result(result: TranslationCallResult, entry_ids_by_representative_id: Dict[str, List[str]]) -> int:
        with write_lock:
            changed = _apply_entry_translations(by_id, result.translations, entry_ids_by_representative_id)
            save_workspace_entries(workspace, entries)
        return changed

    def run_batch(batch_index: int, batch_entries: _EntryBatch) -> Tuple[int, _EntryBatch, _BatchOutcome]:
        try:
            def persist_batch_result(result: TranslationCallResult) -> int:
                return persist_result(result, batch_entries.entry_ids_by_representative_id)

            outcome = _translate_entry_batch_resilient(
                provider_config,
                batch_entries.entries,
                glossary,
                target_language,
                system_prompt,
                logger,
                "entries",
                batch_index,
                persist_batch_result,
                cancelled,
            )
        except Exception as exc:
            outcome = _BatchOutcome(result=TranslationCallResult(translations={}), failed=True, error=str(exc))
        return batch_index, batch_entries, outcome

    completed_requests = 0
    stopped = False
    stop_new_submissions = False
    batch_iterator = iter(enumerate(batches, start=1))

    with ThreadPoolExecutor(max_workers=min(concurrency, len(batches))) as executor:
        futures: Dict[Future, Tuple[int, _EntryBatch]] = {}

        def submit_next() -> bool:
            if stop_new_submissions or _is_cancelled(cancelled):
                return False
            try:
                next_index, next_batch = next(batch_iterator)
            except StopIteration:
                return False
            logger(
                "[entries] request %s/%s queued: %s entries, %s unique, %s duplicates, %s source chars."
                % (
                    next_index,
                    len(batches),
                    next_batch.entry_count,
                    len(next_batch.entries),
                    next_batch.duplicate_count,
                    next_batch.source_chars,
                )
            )
            futures[executor.submit(run_batch, next_index, next_batch)] = (next_index, next_batch)
            return True

        for _slot in range(min(concurrency, len(batches))):
            submit_next()

        while futures:
            done, _pending = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                index, batch = futures.pop(future)
                batch_index, batch_entries, outcome = future.result()
                completed_requests += 1
                result = outcome.result
                batch_changed = outcome.changed
                if outcome.failed:
                    failed_requests += 1
                    logger("[entries] request %s/%s failed: %s; skipped." % (batch_index, len(batches), outcome.error))
                    if _is_fatal_provider_error(outcome.error):
                        stop_new_submissions = True
                        stopped = True
                        logger(
                            "[entries] fatal provider error; stopping new requests after in-flight requests finish: %s"
                            % outcome.error
                        )
                total_changed += batch_changed
                completed_entries += batch_entries.entry_count
                totals.add(result)
                _append_usage_record(
                    usage_log_path,
                    _usage_record(
                        run_id=run_id,
                        scope="entries",
                        event="request",
                        provider_name=provider_name,
                        request_index=batch_index,
                        request_count=len(batches),
                        item_count=batch_entries.entry_count,
                        changed=batch_changed,
                        result=result,
                        failed=outcome.failed,
                        error=outcome.error,
                        extra={
                            "unique_items": len(batch_entries.entries),
                            "duplicate_items": batch_entries.duplicate_count,
                            "source_chars": batch_entries.source_chars,
                        },
                    ),
                    usage_log_lock,
                )

                remaining_requests = len(batches) - completed_requests
                remaining_entries = max(total_entries - completed_entries, 0)
                eta_seconds = _estimate_eta(time.perf_counter() - started, completed_requests, remaining_requests)
                logger(
                    "[entries] request %s/%s done: persisted=%s/%s; completed=%s/%s; remaining=%s; api=%s; %s; eta=%s."
                    % (
                        batch_index,
                        len(batches),
                        batch_changed,
                        batch.entry_count,
                        completed_entries,
                        total_entries,
                        remaining_entries,
                        _format_seconds(result.elapsed_seconds),
                        _format_usage(result),
                        _format_duration(eta_seconds),
                    )
                )
                if _is_cancelled(cancelled):
                    stopped = True
                if not stopped and not stop_new_submissions:
                    submit_next()

    wall_seconds = time.perf_counter() - started
    finish_state = "stopped" if stopped else "finished"
    logger(
        "[entries] %s: translated=%s/%s; requests=%s; api_total=%s; wall=%s; %s."
        % (
            finish_state,
            total_changed,
            total_entries,
            totals.requests,
            _format_seconds(totals.elapsed_seconds),
            _format_seconds(wall_seconds),
            _format_usage(totals),
        )
    )
    _append_usage_record(
        usage_log_path,
        _run_record(
            run_id=run_id,
            scope="entries",
            event="summary",
            provider_name=provider_name,
            request_count=totals.requests,
            item_count=total_entries,
            changed=total_changed,
            elapsed_seconds=totals.elapsed_seconds,
            wall_seconds=wall_seconds,
            totals=totals,
            extra={"state": finish_state, "failed_requests": failed_requests},
        ),
        usage_log_lock,
    )
    if failed_requests:
        logger("[entries] skipped failed requests: %s." % failed_requests)
    return total_changed


def translate_glossary_terms(
    workspace: Path,
    config_path: Path,
    provider_name: str,
    target_language: str,
    limit: int = TERM_TRANSLATION_BATCH_SIZE,
    system_prompt: str = TERM_SYSTEM_PROMPT,
    max_chars: int = TERM_TRANSLATION_MAX_CHARS,
    logger: Optional[Callable[[str], None]] = None,
    concurrency: int = 1,
    cancelled: Optional[Callable[[], bool]] = None,
) -> int:
    logger = logger or _print_progress
    concurrency = max(1, int(concurrency or 1))
    terms = load_glossary(workspace / GLOSSARY_FILE)
    pending = []
    for term in terms:
        if term.status == "rejected":
            continue
        if term.target:
            continue
        if not term.source:
            continue
        pending.append(term.source)

    if not pending:
        logger("[terms] no pending glossary terms.")
        return 0

    provider_config = load_provider_config(config_path, provider_name)
    request_size = max(1, int(limit or TERM_TRANSLATION_BATCH_SIZE))
    request_chars = max(500, int(max_chars or TERM_TRANSLATION_MAX_CHARS))
    batches = _chunk_terms(pending, request_size, request_chars)
    logger(
        "[terms] pending=%s, request_batch_size=%s, max_chars=%s, concurrency=%s, requests=%s, provider=%s"
        % (len(pending), request_size, request_chars, concurrency, len(batches), provider_name)
    )

    changed = 0
    completed_terms = 0
    failed_requests = 0
    totals = _RequestTotals()
    term_by_source = {term.source: term for term in terms}
    usage_log_lock = threading.Lock()
    usage_log_path = workspace / TOKEN_USAGE_LOG_FILE
    run_id = _make_run_id("terms")
    started = time.perf_counter()
    logger("[terms] token usage log: %s" % usage_log_path)
    _append_usage_record(
        usage_log_path,
        _run_record(
            run_id=run_id,
            scope="terms",
            event="start",
            provider_name=provider_name,
            request_count=len(batches),
            item_count=len(pending),
            extra={
                "batch_size": request_size,
                "max_chars": request_chars,
                "concurrency": concurrency,
            },
        ),
        usage_log_lock,
    )

    def run_batch(batch_index: int, batch_terms: List[str]) -> Tuple[int, List[str], _BatchOutcome]:
        try:
            result = _translate_term_batch_resilient(
                provider_config,
                batch_terms,
                target_language,
                system_prompt,
                logger,
                "terms",
                batch_index,
                cancelled,
            )
            outcome = _BatchOutcome(result=result)
        except Exception as exc:
            outcome = _BatchOutcome(result=TranslationCallResult(translations={}), failed=True, error=str(exc))
        return batch_index, batch_terms, outcome

    completed_requests = 0
    stopped = False
    stop_new_submissions = False
    batch_iterator = iter(enumerate(batches, start=1))

    with ThreadPoolExecutor(max_workers=min(concurrency, len(batches))) as executor:
        futures: Dict[Future, Tuple[int, List[str]]] = {}

        def submit_next() -> bool:
            if stop_new_submissions or _is_cancelled(cancelled):
                return False
            try:
                next_index, next_batch = next(batch_iterator)
            except StopIteration:
                return False
            logger("[terms] request %s/%s queued: %s terms." % (next_index, len(batches), len(next_batch)))
            futures[executor.submit(run_batch, next_index, next_batch)] = (next_index, next_batch)
            return True

        for _slot in range(min(concurrency, len(batches))):
            submit_next()

        while futures:
            done, _pending = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                index, batch = futures.pop(future)
                batch_index, batch_terms, outcome = future.result()
                completed_requests += 1
                result = outcome.result
                translations = result.translations
                totals.add(result)

                if outcome.failed:
                    failed_requests += 1
                    logger("[terms] request %s/%s failed: %s; skipped." % (batch_index, len(batches), outcome.error))
                    if _is_fatal_provider_error(outcome.error):
                        stop_new_submissions = True
                        stopped = True
                        logger(
                            "[terms] fatal provider error; stopping new requests after in-flight requests finish: %s"
                            % outcome.error
                        )

                batch_changed = 0
                for source, target in translations.items():
                    term = term_by_source.get(source)
                    if term is None or not target:
                        continue
                    if not term.target:
                        batch_changed += 1
                    term.target = target
                    term.status = "pending"

                changed += batch_changed
                completed_terms += len(batch_terms)
                save_glossary(workspace / GLOSSARY_FILE, terms)
                _append_usage_record(
                    usage_log_path,
                    _usage_record(
                        run_id=run_id,
                        scope="terms",
                        event="request",
                        provider_name=provider_name,
                        request_index=batch_index,
                        request_count=len(batches),
                        item_count=len(batch_terms),
                        changed=batch_changed,
                        result=result,
                        failed=outcome.failed,
                        error=outcome.error,
                    ),
                    usage_log_lock,
                )
                remaining_requests = len(batches) - completed_requests
                remaining_terms = max(len(pending) - completed_terms, 0)
                eta_seconds = _estimate_eta(time.perf_counter() - started, completed_requests, remaining_requests)
                logger(
                    "[terms] request %s/%s done: translated=%s/%s; total=%s/%s; remaining=%s; api=%s; %s; eta=%s."
                    % (
                        batch_index,
                        len(batches),
                        batch_changed,
                        len(batch_terms),
                        completed_terms,
                        len(pending),
                        remaining_terms,
                        _format_seconds(result.elapsed_seconds),
                        _format_usage(result),
                        _format_duration(eta_seconds),
                    )
                )
                if _is_cancelled(cancelled):
                    stopped = True
                if not stopped and not stop_new_submissions:
                    submit_next()

    finish_state = "stopped" if stopped else "finished"
    logger(
        "[terms] %s: translated=%s/%s; requests=%s; api_total=%s; %s."
        % (finish_state, changed, len(pending), totals.requests, _format_seconds(totals.elapsed_seconds), _format_usage(totals))
    )
    _append_usage_record(
        usage_log_path,
        _run_record(
            run_id=run_id,
            scope="terms",
            event="summary",
            provider_name=provider_name,
            request_count=totals.requests,
            item_count=len(pending),
            changed=changed,
            elapsed_seconds=totals.elapsed_seconds,
            totals=totals,
            extra={"state": finish_state, "failed_requests": failed_requests},
        ),
        usage_log_lock,
    )
    if failed_requests:
        logger("[terms] skipped failed requests: %s." % failed_requests)
    return changed


def _select_batch(entries: List[TextEntry], limit: int, include_translated: bool = False) -> List[TextEntry]:
    batch: List[TextEntry] = []
    for entry in entries:
        if not _should_translate_entry(entry, include_translated):
            continue
        batch.append(entry)
        if len(batch) >= limit:
            break
    return batch


def _is_cancelled(cancelled: Optional[Callable[[], bool]]) -> bool:
    if cancelled is None:
        return False
    try:
        return bool(cancelled())
    except Exception:
        return False


def _is_fatal_provider_error(message: str) -> bool:
    text = (message or "").lower()
    fatal_markers = (
        "provider http error 400",
        "provider http error 401",
        "provider http error 402",
        "provider http error 403",
        "provider http error 404",
        "provider http error 413",
        "provider http error 422",
        "provider http error 429",
        "missing api key",
        "invalid api key",
        "incorrect api key",
        "unauthorized",
        "forbidden",
        "insufficient balance",
        "insufficient quota",
        "insufficient_quota",
        "quota exceeded",
        "billing",
        "rate limit",
        "too many requests",
    )
    return any(marker in text for marker in fatal_markers)


def _pending_entries_count(entries: List[TextEntry]) -> int:
    return len(_select_batch(entries, len(entries) or 1))


def _select_batches(
    entries: List[TextEntry],
    batch_size: int,
    include_translated: bool = False,
    max_batches: int = 10000,
) -> List[List[TextEntry]]:
    selected = [entry for entry in entries if _should_translate_entry(entry, include_translated)]
    batches = []
    for start in range(0, len(selected), batch_size):
        if len(batches) >= max_batches:
            break
        batches.append(selected[start : start + batch_size])
    return batches


def _select_entry_batches(
    entries: List[TextEntry],
    batch_size: int,
    include_translated: bool = False,
    max_batches: int = 10000,
    max_chars: int = ENTRY_TRANSLATION_MAX_CHARS,
) -> List[_EntryBatch]:
    selected = [entry for entry in entries if _should_translate_entry(entry, include_translated)]
    groups = _dedupe_entry_groups(selected)
    batches: List[_EntryBatch] = []
    current: List[Tuple[TextEntry, List[str], int]] = []
    current_chars = 0
    max_unique = max(1, int(batch_size or ENTRY_TRANSLATION_BATCH_SIZE))
    char_budget = max(500, int(max_chars or ENTRY_TRANSLATION_MAX_CHARS))

    for representative, entry_ids, source_chars in groups:
        if current and (len(current) >= max_unique or current_chars + source_chars > char_budget):
            batches.append(_build_entry_batch_from_groups(current))
            if len(batches) >= max_batches:
                return batches
            current = []
            current_chars = 0
        current.append((representative, entry_ids, source_chars))
        current_chars += source_chars

    if current and len(batches) < max_batches:
        batches.append(_build_entry_batch_from_groups(current))
    return batches


def _dedupe_entry_groups(entries: List[TextEntry]) -> List[Tuple[TextEntry, List[str], int]]:
    groups: List[Tuple[TextEntry, List[str], int]] = []
    group_index_by_key: Dict[str, int] = {}
    for entry in entries:
        key = _entry_dedupe_key(entry)
        group_index = group_index_by_key.get(key)
        if group_index is None:
            group_index_by_key[key] = len(groups)
            groups.append((entry, [entry.id], _entry_source_chars(entry)))
        else:
            groups[group_index][1].append(entry.id)
    return groups


def _build_entry_batch(entries: List[TextEntry]) -> _EntryBatch:
    return _build_entry_batch_from_groups(_dedupe_entry_groups(entries))


def _build_entry_batch_from_groups(groups: List[Tuple[TextEntry, List[str], int]]) -> _EntryBatch:
    representatives = [representative for representative, _entry_ids, _source_chars in groups]
    entry_ids_by_representative_id = {
        representative.id: list(entry_ids) for representative, entry_ids, _source_chars in groups
    }
    entry_count = sum(len(entry_ids) for _representative, entry_ids, _source_chars in groups)
    source_chars = sum(source_chars for _representative, _entry_ids, source_chars in groups)
    return _EntryBatch(
        entries=representatives,
        entry_count=entry_count,
        duplicate_count=max(entry_count - len(representatives), 0),
        entry_ids_by_representative_id=entry_ids_by_representative_id,
        source_chars=source_chars,
    )


def _entry_dedupe_key(entry: TextEntry) -> str:
    if entry.source:
        return entry.source
    return "__entry__:%s" % entry.id


def _entry_source_chars(entry: TextEntry) -> int:
    return len(entry.source or "") + len(entry.context or "") + len(entry.speaker or "")


def _should_translate_entry(entry: TextEntry, include_translated: bool = False) -> bool:
    if entry.status in ("rejected", "locked"):
        return False
    if entry.translation and not include_translated:
        return False
    return True


def _apply_entry_translations(
    entries_by_id: Dict[str, TextEntry],
    translations: Dict[str, str],
    entry_ids_by_representative_id: Optional[Dict[str, List[str]]] = None,
) -> int:
    changed = 0
    for entry_id, translation in translations.items():
        target_ids = [entry_id]
        if entry_ids_by_representative_id is not None:
            target_ids = entry_ids_by_representative_id.get(entry_id, [entry_id])
        for target_id in target_ids:
            entry = entries_by_id.get(target_id)
            if entry is None:
                continue
            entry.translation = translation
            entry.status = "translated"
            changed += 1
    return changed


def _translate_entry_batch(
    provider: OpenAICompatibleProvider,
    batch: List[TextEntry],
    glossary: Dict[str, str],
    target_language: str,
    system_prompt: str,
) -> TranslationCallResult:
    if hasattr(provider, "translate_batch_with_stats"):
        return provider.translate_batch_with_stats(batch, glossary, target_language, system_prompt=system_prompt)
    translations = provider.translate_batch(batch, glossary, target_language, system_prompt=system_prompt)
    return TranslationCallResult(translations=translations)


def _translate_entry_batch_resilient(
    provider_config: object,
    batch: List[TextEntry],
    glossary: Dict[str, str],
    target_language: str,
    system_prompt: str,
    logger: Callable[[str], None],
    label: str,
    batch_index: int,
    persist_result: Callable[[TranslationCallResult], int],
    cancelled: Optional[Callable[[], bool]] = None,
) -> _BatchOutcome:
    if not batch or _is_cancelled(cancelled):
        return _BatchOutcome(result=TranslationCallResult(translations={}))
    provider = OpenAICompatibleProvider(provider_config)
    try:
        result = _translate_entry_batch(provider, batch, glossary, target_language, system_prompt)
        changed = persist_result(result)
        return _BatchOutcome(result=result, changed=changed)
    except ValueError as exc:
        failed_result = getattr(exc, "result", TranslationCallResult(translations={}))
        if len(batch) <= 1:
            logger("[%s] request %s failed on single entry %s: %s; skipped." % (label, batch_index, batch[0].id, exc))
            return _BatchOutcome(result=failed_result, failed=True, error=str(exc))
        midpoint = max(1, len(batch) // 2)
        logger(
            "[%s] request %s returned invalid JSON for %s entries: %s; splitting into %s + %s."
            % (label, batch_index, len(batch), exc, midpoint, len(batch) - midpoint)
        )
        first = _translate_entry_batch_resilient(
            provider_config,
            batch[:midpoint],
            glossary,
            target_language,
            system_prompt,
            logger,
            label,
            batch_index,
            persist_result,
            cancelled,
        )
        second = _translate_entry_batch_resilient(
            provider_config,
            batch[midpoint:],
            glossary,
            target_language,
            system_prompt,
            logger,
            label,
            batch_index,
            persist_result,
            cancelled,
        )
        return _merge_outcomes([_BatchOutcome(result=failed_result), first, second])


def _translate_term_batch(
    provider: OpenAICompatibleProvider,
    batch: List[str],
    target_language: str,
    system_prompt: str,
) -> TranslationCallResult:
    if hasattr(provider, "translate_terms_with_stats"):
        return provider.translate_terms_with_stats(batch, target_language, system_prompt=system_prompt)
    translations = provider.translate_terms(batch, target_language, system_prompt=system_prompt)
    return TranslationCallResult(translations=translations)


def _translate_term_batch_resilient(
    provider_config: object,
    batch: List[str],
    target_language: str,
    system_prompt: str,
    logger: Callable[[str], None],
    label: str,
    batch_index: int,
    cancelled: Optional[Callable[[], bool]] = None,
) -> TranslationCallResult:
    if not batch or _is_cancelled(cancelled):
        return TranslationCallResult(translations={})
    provider = OpenAICompatibleProvider(provider_config)
    try:
        return _translate_term_batch(provider, batch, target_language, system_prompt)
    except ValueError as exc:
        failed_result = getattr(exc, "result", TranslationCallResult(translations={}))
        if len(batch) <= 1:
            logger("[%s] request %s failed on single term %s: %s; skipped." % (label, batch_index, batch[0], exc))
            return failed_result
        midpoint = max(1, len(batch) // 2)
        logger(
            "[%s] request %s returned invalid JSON for %s terms: %s; splitting into %s + %s."
            % (label, batch_index, len(batch), exc, midpoint, len(batch) - midpoint)
        )
        first = _translate_term_batch_resilient(
            provider_config,
            batch[:midpoint],
            target_language,
            system_prompt,
            logger,
            label,
            batch_index,
            cancelled,
        )
        second = _translate_term_batch_resilient(
            provider_config,
            batch[midpoint:],
            target_language,
            system_prompt,
            logger,
            label,
            batch_index,
            cancelled,
        )
        return _merge_results([failed_result, first, second])


def _merge_results(results: List[TranslationCallResult]) -> TranslationCallResult:
    merged = TranslationCallResult(translations={})
    for result in results:
        merged.translations.update(result.translations)
        merged.elapsed_seconds += result.elapsed_seconds
        merged.prompt_tokens += result.prompt_tokens
        merged.completion_tokens += result.completion_tokens
        merged.total_tokens += result.total_tokens
        merged.prompt_cache_hit_tokens += result.prompt_cache_hit_tokens
        merged.prompt_cache_miss_tokens += result.prompt_cache_miss_tokens
        merged.input_items += result.input_items
        merged.glossary_terms += result.glossary_terms
        merged.system_prompt_chars += result.system_prompt_chars
        merged.user_prompt_chars += result.user_prompt_chars
        merged.request_chars += result.request_chars
        if not merged.request_kind:
            merged.request_kind = result.request_kind
        if not merged.provider_name:
            merged.provider_name = result.provider_name
        if not merged.model:
            merged.model = result.model
        if not merged.prompt_sha256:
            merged.prompt_sha256 = result.prompt_sha256
    return merged


def _merge_outcomes(outcomes: List[_BatchOutcome]) -> _BatchOutcome:
    result = _merge_results([outcome.result for outcome in outcomes])
    return _BatchOutcome(
        result=result,
        changed=sum(outcome.changed for outcome in outcomes),
        failed=any(outcome.failed for outcome in outcomes),
        error="; ".join(outcome.error for outcome in outcomes if outcome.error),
    )


def _estimate_eta(elapsed_seconds: float, completed_requests: int, remaining_requests: int) -> float:
    if completed_requests <= 0 or remaining_requests <= 0:
        return 0.0
    requests_per_second = completed_requests / max(elapsed_seconds, 0.001)
    return remaining_requests / max(requests_per_second, 0.001)


def _format_seconds(seconds: float) -> str:
    return "%.2fs" % max(seconds, 0.0)


def _format_duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    minutes, remainder = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return "%sh%02sm%02ss" % (hours, minutes, remainder)
    if minutes:
        return "%sm%02ss" % (minutes, remainder)
    return "%ss" % remainder


def _format_usage(result: object) -> str:
    prompt_tokens = int(getattr(result, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(result, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(result, "total_tokens", 0) or 0)
    cache_hit_tokens = int(getattr(result, "prompt_cache_hit_tokens", 0) or 0)
    cache_miss_tokens = int(getattr(result, "prompt_cache_miss_tokens", 0) or 0)
    if total_tokens <= 0 and (prompt_tokens or completion_tokens):
        total_tokens = prompt_tokens + completion_tokens
    if total_tokens <= 0 and not (cache_hit_tokens or cache_miss_tokens):
        return "tokens=n/a"
    cache_total = cache_hit_tokens + cache_miss_tokens
    cache_text = ""
    if cache_total:
        cache_text = ", cache_hit=%s, cache_miss=%s, cache_hit_rate=%s%%" % (
            cache_hit_tokens,
            cache_miss_tokens,
            int(cache_hit_tokens * 100 / max(cache_total, 1)),
        )
    return "tokens=%s (prompt=%s, completion=%s%s)" % (total_tokens, prompt_tokens, completion_tokens, cache_text)


def _make_run_id(scope: str) -> str:
    return "%s-%s" % (scope, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append_usage_record(log_path: Path, record: Mapping[str, Any], lock: Optional[threading.Lock] = None) -> None:
    def write() -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")

    if lock is None:
        write()
        return
    with lock:
        write()


def _usage_record(
    run_id: str,
    scope: str,
    event: str,
    provider_name: str,
    request_index: int,
    request_count: int,
    item_count: int,
    changed: int,
    result: TranslationCallResult,
    failed: bool = False,
    error: str = "",
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    record = _base_usage_record(
        run_id=run_id,
        scope=scope,
        event=event,
        provider_name=provider_name,
        result=result,
    )
    record.update(
        {
            "request_index": request_index,
            "request_count": request_count,
            "item_count": item_count,
            "changed": changed,
            "failed": bool(failed),
            "error": error,
        }
    )
    if extra:
        record.update(dict(extra))
    return record


def _run_record(
    run_id: str,
    scope: str,
    event: str,
    provider_name: str,
    request_count: int,
    item_count: int,
    changed: int = 0,
    elapsed_seconds: float = 0.0,
    wall_seconds: float = 0.0,
    totals: Optional[_RequestTotals] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    result = totals or _RequestTotals()
    record = _base_usage_record(
        run_id=run_id,
        scope=scope,
        event=event,
        provider_name=provider_name,
        result=result,
    )
    record.update(
        {
            "request_count": request_count,
            "item_count": item_count,
            "changed": changed,
            "elapsed_seconds": round(elapsed_seconds, 4),
            "wall_seconds": round(wall_seconds, 4),
        }
    )
    if extra:
        record.update(dict(extra))
    return record


def _base_usage_record(
    run_id: str,
    scope: str,
    event: str,
    provider_name: str,
    result: object,
) -> Dict[str, Any]:
    prompt_tokens = int(getattr(result, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(result, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(result, "total_tokens", 0) or 0)
    cache_hit_tokens = int(getattr(result, "prompt_cache_hit_tokens", 0) or 0)
    cache_miss_tokens = int(getattr(result, "prompt_cache_miss_tokens", 0) or 0)
    cache_total = cache_hit_tokens + cache_miss_tokens
    return {
        "timestamp": _utc_now(),
        "run_id": run_id,
        "scope": scope,
        "event": event,
        "provider": getattr(result, "provider_name", "") or provider_name,
        "model": getattr(result, "model", ""),
        "elapsed_seconds": round(float(getattr(result, "elapsed_seconds", 0.0) or 0.0), 4),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "prompt_cache_hit_tokens": cache_hit_tokens,
        "prompt_cache_miss_tokens": cache_miss_tokens,
        "prompt_cache_hit_rate": round(cache_hit_tokens / cache_total, 4) if cache_total else 0.0,
        "input_items": int(getattr(result, "input_items", 0) or 0),
        "glossary_terms": int(getattr(result, "glossary_terms", 0) or 0),
        "system_prompt_chars": int(getattr(result, "system_prompt_chars", 0) or 0),
        "user_prompt_chars": int(getattr(result, "user_prompt_chars", 0) or 0),
        "request_chars": int(getattr(result, "request_chars", 0) or 0),
        "prompt_sha256": getattr(result, "prompt_sha256", ""),
    }


def _chunk_terms(terms: List[str], limit: int, max_chars: int) -> List[List[str]]:
    batches: List[List[str]] = []
    batch: List[str] = []
    batch_chars = 0
    for term in terms:
        term_chars = len(term)
        if batch and (len(batch) >= limit or batch_chars + term_chars > max_chars):
            batches.append(batch)
            batch = []
            batch_chars = 0
        batch.append(term)
        batch_chars += term_chars
    if batch:
        batches.append(batch)
    return batches


def _print_progress(message: str) -> None:
    print(message, flush=True)
