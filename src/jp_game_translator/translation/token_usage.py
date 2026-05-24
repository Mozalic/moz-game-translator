from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


TOKEN_USAGE_LOG_FILE = "logs/token_usage.jsonl"


@dataclass
class TokenUsageRun:
    run_id: str
    scope: str = ""
    provider: str = ""
    model: str = ""
    started_at: str = ""
    updated_at: str = ""
    state: str = "running"
    request_count: int = 0
    completed_requests: int = 0
    item_count: int = 0
    changed: int = 0
    failed_requests: int = 0
    elapsed_seconds: float = 0.0
    wall_seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    records: List[Dict[str, Any]] = field(default_factory=list)
    requests: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def cache_total_tokens(self) -> int:
        return self.prompt_cache_hit_tokens + self.prompt_cache_miss_tokens

    @property
    def prompt_cache_hit_rate(self) -> float:
        total = self.cache_total_tokens
        if total <= 0:
            return 0.0
        return self.prompt_cache_hit_tokens / total

    @property
    def average_tokens_per_request(self) -> float:
        if self.completed_requests <= 0:
            return 0.0
        return self.total_tokens / self.completed_requests

    @property
    def is_finished(self) -> bool:
        return self.state in ("finished", "stopped", "failed")


def token_usage_log_path(workspace: Path) -> Path:
    return workspace / TOKEN_USAGE_LOG_FILE


def load_token_usage_runs(log_path: Path) -> List[TokenUsageRun]:
    runs_by_id: Dict[str, TokenUsageRun] = {}
    order: List[str] = []
    for record in _iter_usage_records(log_path):
        run_id = str(record.get("run_id") or "").strip()
        if not run_id:
            continue
        run = runs_by_id.get(run_id)
        if run is None:
            run = TokenUsageRun(run_id=run_id)
            runs_by_id[run_id] = run
            order.append(run_id)
        _apply_record(run, record)

    runs = [runs_by_id[run_id] for run_id in order]
    runs.sort(key=lambda run: run.updated_at or run.started_at)
    return runs


def latest_token_usage_run(workspace: Path) -> Optional[TokenUsageRun]:
    runs = load_token_usage_runs(token_usage_log_path(workspace))
    if not runs:
        return None
    return runs[-1]


def _iter_usage_records(log_path: Path) -> Iterable[Dict[str, Any]]:
    if not log_path.exists():
        return
    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def _apply_record(run: TokenUsageRun, record: Mapping[str, Any]) -> None:
    timestamp = str(record.get("timestamp") or "")
    if timestamp:
        if not run.started_at:
            run.started_at = timestamp
        run.updated_at = timestamp
    run.scope = str(record.get("scope") or run.scope)
    run.provider = str(record.get("provider") or run.provider)
    run.model = str(record.get("model") or run.model)
    run.request_count = max(run.request_count, _int_value(record.get("request_count")))
    run.item_count = max(run.item_count, _int_value(record.get("item_count")))
    run.records.append(dict(record))

    event = str(record.get("event") or "")
    if event == "start":
        run.state = "running"
        return

    if event == "request":
        run.requests.append(dict(record))
        run.completed_requests += 1
        run.changed += _int_value(record.get("changed"))
        run.elapsed_seconds += _float_value(record.get("elapsed_seconds"))
        run.prompt_tokens += _int_value(record.get("prompt_tokens"))
        run.completion_tokens += _int_value(record.get("completion_tokens"))
        run.total_tokens += _record_total_tokens(record)
        run.prompt_cache_hit_tokens += _int_value(record.get("prompt_cache_hit_tokens"))
        run.prompt_cache_miss_tokens += _int_value(record.get("prompt_cache_miss_tokens"))
        if bool(record.get("failed")):
            run.failed_requests += 1
        if run.request_count and run.completed_requests >= run.request_count:
            run.state = "finished"
        return

    if event == "summary":
        state = str(record.get("state") or "finished")
        run.state = state
        run.completed_requests = _int_value(record.get("request_count")) or run.completed_requests
        run.item_count = _int_value(record.get("item_count")) or run.item_count
        run.changed = _int_value(record.get("changed"))
        run.elapsed_seconds = _float_value(record.get("elapsed_seconds")) or run.elapsed_seconds
        run.wall_seconds = _float_value(record.get("wall_seconds"))
        run.prompt_tokens = _int_value(record.get("prompt_tokens"))
        run.completion_tokens = _int_value(record.get("completion_tokens"))
        run.total_tokens = _record_total_tokens(record)
        run.prompt_cache_hit_tokens = _int_value(record.get("prompt_cache_hit_tokens"))
        run.prompt_cache_miss_tokens = _int_value(record.get("prompt_cache_miss_tokens"))
        run.failed_requests = _int_value(record.get("failed_requests"))


def _record_total_tokens(record: Mapping[str, Any]) -> int:
    total = _int_value(record.get("total_tokens"))
    if total > 0:
        return total
    return _int_value(record.get("prompt_tokens")) + _int_value(record.get("completion_tokens"))


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
