from __future__ import annotations

import argparse
import importlib.resources as package_resources
import json
import sys
from pathlib import Path
from typing import List, Optional

from jp_game_translator.adapters.registry import adapter_map, detect_best
from jp_game_translator.core.models import ProjectManifest
from jp_game_translator.core.project import (
    GLOSSARY_FILE,
    init_workspace,
    load_manifest,
    load_workspace_entries,
    save_workspace_entries,
)
from jp_game_translator.terminology.extractor import extract_term_candidates
from jp_game_translator.terminology.glossary import GlossaryTerm, load_glossary, save_glossary
from jp_game_translator.translation.pipeline import translate_workspace


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except Exception as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jgt", description="Japanese game translation workflow toolkit.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect_parser = subparsers.add_parser("detect", help="Detect supported game engine.")
    detect_parser.add_argument("game_dir", type=Path)
    detect_parser.set_defaults(func=cmd_detect)

    tools_parser = subparsers.add_parser("tools", help="List recommended open-source parser/patcher tools.")
    tools_parser.add_argument("--json", action="store_true", help="Print raw JSON metadata.")
    tools_parser.set_defaults(func=cmd_tools)

    extract_parser = subparsers.add_parser("extract", help="Extract Japanese text into a workspace.")
    extract_parser.add_argument("game_dir", type=Path)
    extract_parser.add_argument("--workspace", type=Path, required=True)
    extract_parser.set_defaults(func=cmd_extract)

    terms_parser = subparsers.add_parser("terms", help="Extract terminology candidates into glossary.tsv.")
    terms_parser.add_argument("workspace", type=Path)
    terms_parser.add_argument("--min-count", type=int, default=2)
    terms_parser.add_argument("--limit", type=int, default=200)
    terms_parser.set_defaults(func=cmd_terms)

    review_parser = subparsers.add_parser("review-terms", help="Interactively approve terminology translations.")
    review_parser.add_argument("workspace", type=Path)
    review_parser.add_argument("--limit", type=int, default=100)
    review_parser.set_defaults(func=cmd_review_terms)

    translate_parser = subparsers.add_parser("translate", help="Translate the next batch with a configured LLM provider.")
    translate_parser.add_argument("workspace", type=Path)
    translate_parser.add_argument("--config", type=Path, required=True)
    translate_parser.add_argument("--provider", required=True)
    translate_parser.add_argument("--target", default="zh-Hans")
    translate_parser.add_argument("--limit", type=int, default=60)
    translate_parser.set_defaults(func=cmd_translate)

    apply_parser = subparsers.add_parser("apply", help="Apply translated entries into a copied game directory.")
    apply_parser.add_argument("game_dir", type=Path)
    apply_parser.add_argument("workspace", type=Path)
    apply_parser.add_argument("output_dir", type=Path)
    apply_parser.add_argument("--overwrite", action="store_true")
    apply_parser.set_defaults(func=cmd_apply)

    return parser


def cmd_detect(args: argparse.Namespace) -> int:
    result = detect_best(args.game_dir)
    if result is None:
        print("No supported engine detected.")
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    with package_resources.open_text("jp_game_translator.resources", "tool_catalog.json", encoding="utf-8") as handle:
        data = json.load(handle)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    for tool in data.get("tools", []):
        engines = ", ".join(tool.get("engines", []))
        print("%s" % tool.get("name", ""))
        print("  URL: %s" % tool.get("url", ""))
        print("  Engines: %s" % engines)
        print("  Role: %s" % tool.get("role", ""))
        print("  Integration: %s" % tool.get("integration", ""))
        print("")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    result = detect_best(args.game_dir)
    if result is None:
        raise RuntimeError("no supported adapter detected for %s" % args.game_dir)
    adapters = adapter_map()
    adapter = adapters[result.adapter_id]
    bundle = adapter.extract(args.game_dir, args.workspace)
    manifest = ProjectManifest(
        adapter_id=bundle.adapter_id,
        engine_name=bundle.engine_name,
        source_game_dir=str(args.game_dir.resolve()),
        entry_count=len(bundle.entries),
        notes=bundle.notes,
    )
    init_workspace(args.workspace, manifest, bundle.entries)
    print("Extracted %s entries into %s" % (len(bundle.entries), args.workspace))
    return 0


def cmd_terms(args: argparse.Namespace) -> int:
    entries = load_workspace_entries(args.workspace)
    candidates = extract_term_candidates(entries, min_count=args.min_count, limit=args.limit)

    existing = {term.source: term for term in load_glossary(args.workspace / GLOSSARY_FILE)}
    for candidate in candidates:
        if candidate.source not in existing:
            existing[candidate.source] = GlossaryTerm(
                source=candidate.source,
                count=candidate.count,
                note=" | ".join(candidate.examples[:2]),
            )
        else:
            existing[candidate.source].count = candidate.count

    terms = sorted(existing.values(), key=lambda item: (-item.count, item.source))
    save_glossary(args.workspace / GLOSSARY_FILE, terms)
    print("Wrote %s terminology candidates to %s" % (len(terms), args.workspace / GLOSSARY_FILE))
    return 0


def cmd_review_terms(args: argparse.Namespace) -> int:
    glossary_path = args.workspace / GLOSSARY_FILE
    terms = load_glossary(glossary_path)
    reviewed = 0

    for term in terms:
        if reviewed >= args.limit:
            break
        if term.status == "approved":
            continue
        print("")
        print("Term: %s  (count=%s)" % (term.source, term.count))
        if term.note:
            print("Example: %s" % term.note[:240])
        value = input("Translation [empty=skip, '-'=reject]: ").strip()
        if not value:
            continue
        if value == "-":
            term.status = "rejected"
            term.target = ""
        else:
            term.target = value
            term.status = "approved"
        reviewed += 1

    save_glossary(glossary_path, terms)
    print("Reviewed %s terms." % reviewed)
    return 0


def cmd_translate(args: argparse.Namespace) -> int:
    changed = translate_workspace(
        workspace=args.workspace,
        config_path=args.config,
        provider_name=args.provider,
        target_language=args.target,
        limit=args.limit,
    )
    print("Translated %s entries." % changed)
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.workspace)
    adapters = adapter_map()
    if manifest.adapter_id not in adapters:
        raise RuntimeError("adapter not available: %s" % manifest.adapter_id)
    entries = load_workspace_entries(args.workspace)
    adapters[manifest.adapter_id].apply(
        args.game_dir,
        args.workspace,
        args.output_dir,
        entries,
        overwrite=args.overwrite,
    )
    translated = len([entry for entry in entries if entry.translation and entry.status != "rejected"])
    print("Applied %s translated entries into %s" % (translated, args.output_dir))
    return 0
