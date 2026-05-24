from __future__ import annotations

import importlib.resources as package_resources
import json
import queue
import threading
import traceback
import webbrowser
from pathlib import Path
from tkinter import BOTH, END, HORIZONTAL, LEFT, RIGHT, VERTICAL, W, X, filedialog, messagebox
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional

from jp_game_translator.adapters.registry import adapter_map, detect_best
from jp_game_translator.core.models import ProjectManifest, TextEntry
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


class TranslatorGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("日文游戏自动翻译器")
        self.geometry("1180x760")
        self.minsize(980, 620)

        self.entries: List[TextEntry] = []
        self.glossary: List[GlossaryTerm] = []
        self.current_entry_id: Optional[str] = None
        self.current_term_source: Optional[str] = None
        self.worker_queue: "queue.Queue[tuple]" = queue.Queue()
        self.busy = False

        self.game_dir_var = tk.StringVar()
        self.workspace_var = tk.StringVar()
        self.output_dir_var = tk.StringVar()
        self.config_var = tk.StringVar(value=str(Path("configs") / "providers.example.json"))
        self.provider_var = tk.StringVar(value="openai")
        self.target_var = tk.StringVar(value="zh-Hans")
        self.batch_limit_var = tk.IntVar(value=20)
        self.term_min_count_var = tk.IntVar(value=2)
        self.term_limit_var = tk.IntVar(value=200)
        self.overwrite_var = tk.BooleanVar(value=False)
        self.entry_search_var = tk.StringVar()
        self.term_search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪")

        self._build_styles()
        self._build_layout()
        self._load_tools()
        self.after(100, self._poll_worker_queue)

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TButton", padding=(10, 5))
        style.configure("Primary.TButton", padding=(12, 6))
        style.configure("Path.TEntry", padding=4)

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=BOTH, expand=True)

        self._build_path_panel(root)

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=BOTH, expand=True, pady=(10, 8))

        workflow_tab = ttk.Frame(self.notebook, padding=10)
        entries_tab = ttk.Frame(self.notebook, padding=10)
        glossary_tab = ttk.Frame(self.notebook, padding=10)
        tools_tab = ttk.Frame(self.notebook, padding=10)

        self.notebook.add(workflow_tab, text="工作流")
        self.notebook.add(entries_tab, text="文案")
        self.notebook.add(glossary_tab, text="术语")
        self.notebook.add(tools_tab, text="开源工具")

        self._build_workflow_tab(workflow_tab)
        self._build_entries_tab(entries_tab)
        self._build_glossary_tab(glossary_tab)
        self._build_tools_tab(tools_tab)
        self._build_status_bar(root)

    def _build_path_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="项目路径", padding=10)
        panel.pack(fill=X)
        panel.columnconfigure(1, weight=1)

        self._path_row(panel, 0, "游戏目录", self.game_dir_var, self._browse_game_dir)
        self._path_row(panel, 1, "工作区", self.workspace_var, self._browse_workspace)
        self._path_row(panel, 2, "输出目录", self.output_dir_var, self._browse_output_dir)

    def _path_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        command: Callable[[], None],
    ) -> None:
        ttk.Label(parent, text=label, width=10, anchor=W).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=variable, style="Path.TEntry").grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=3)
        ttk.Button(parent, text="浏览", command=command).grid(row=row, column=2, sticky="e", pady=3)

    def _build_workflow_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(2, weight=1)

        actions = ttk.LabelFrame(parent, text="操作", padding=10)
        actions.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        for index in range(3):
            actions.columnconfigure(index, weight=1)

        self.detect_button = ttk.Button(actions, text="检测引擎", command=self.detect_game, style="Primary.TButton")
        self.detect_button.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        self.extract_button = ttk.Button(actions, text="抽取文案", command=self.extract_text, style="Primary.TButton")
        self.extract_button.grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        self.load_button = ttk.Button(actions, text="载入工作区", command=self.load_workspace, style="Primary.TButton")
        self.load_button.grid(row=0, column=2, sticky="ew", padx=4, pady=4)
        self.terms_button = ttk.Button(actions, text="生成术语", command=self.generate_terms)
        self.terms_button.grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        self.translate_button = ttk.Button(actions, text="翻译下一批", command=self.translate_batch)
        self.translate_button.grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        self.apply_button = ttk.Button(actions, text="回填输出", command=self.apply_translation)
        self.apply_button.grid(row=1, column=2, sticky="ew", padx=4, pady=4)

        settings = ttk.LabelFrame(parent, text="翻译设置", padding=10)
        settings.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="配置文件").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(settings, textvariable=self.config_var).grid(row=0, column=1, sticky="ew", padx=8, pady=3)
        ttk.Button(settings, text="浏览", command=self._browse_config).grid(row=0, column=2, pady=3)
        ttk.Label(settings, text="Provider").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(settings, textvariable=self.provider_var, width=20).grid(row=1, column=1, sticky="ew", padx=8, pady=3)
        ttk.Label(settings, text="目标语言").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(settings, textvariable=self.target_var, width=20).grid(row=2, column=1, sticky="ew", padx=8, pady=3)
        ttk.Label(settings, text="批量条数").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Spinbox(settings, from_=1, to=200, textvariable=self.batch_limit_var, width=10).grid(row=3, column=1, sticky="w", padx=8, pady=3)
        ttk.Checkbutton(settings, text="覆盖已存在输出目录", variable=self.overwrite_var).grid(row=4, column=1, sticky="w", padx=8, pady=3)

        terms = ttk.LabelFrame(parent, text="术语设置", padding=10)
        terms.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 8))
        ttk.Label(terms, text="最少出现次数").pack(side=LEFT)
        ttk.Spinbox(terms, from_=1, to=20, textvariable=self.term_min_count_var, width=8).pack(side=LEFT, padx=(8, 18))
        ttk.Label(terms, text="候选上限").pack(side=LEFT)
        ttk.Spinbox(terms, from_=10, to=2000, textvariable=self.term_limit_var, width=8).pack(side=LEFT, padx=(8, 18))

        log_panel = ttk.LabelFrame(parent, text="日志", padding=8)
        log_panel.grid(row=2, column=0, columnspan=2, sticky="nsew")
        log_panel.rowconfigure(0, weight=1)
        log_panel.columnconfigure(0, weight=1)
        self.log_text = tk.Text(log_panel, height=12, wrap="word", state="disabled")
        log_scroll = ttk.Scrollbar(log_panel, orient=VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll.grid(row=0, column=1, sticky="ns")

    def _build_entries_tab(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        top = ttk.Frame(parent)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="搜索").grid(row=0, column=0, sticky="w")
        search = ttk.Entry(top, textvariable=self.entry_search_var)
        search.grid(row=0, column=1, sticky="ew", padx=8)
        search.bind("<KeyRelease>", lambda _event: self.refresh_entries())
        ttk.Button(top, text="保存当前文案", command=self.save_current_entry).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(top, text="刷新", command=self.load_workspace).grid(row=0, column=3)

        body = ttk.PanedWindow(parent, orient=HORIZONTAL)
        body.grid(row=1, column=0, sticky="nsew")

        list_frame = ttk.Frame(body)
        detail_frame = ttk.Frame(body)
        body.add(list_frame, weight=3)
        body.add(detail_frame, weight=2)

        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        columns = ("status", "source", "translation", "file")
        self.entries_tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse")
        self.entries_tree.heading("status", text="状态")
        self.entries_tree.heading("source", text="原文")
        self.entries_tree.heading("translation", text="译文")
        self.entries_tree.heading("file", text="文件")
        self.entries_tree.column("status", width=82, stretch=False)
        self.entries_tree.column("source", width=320)
        self.entries_tree.column("translation", width=320)
        self.entries_tree.column("file", width=180)
        entry_scroll = ttk.Scrollbar(list_frame, orient=VERTICAL, command=self.entries_tree.yview)
        self.entries_tree.configure(yscrollcommand=entry_scroll.set)
        self.entries_tree.grid(row=0, column=0, sticky="nsew")
        entry_scroll.grid(row=0, column=1, sticky="ns")
        self.entries_tree.bind("<<TreeviewSelect>>", self._on_entry_selected)

        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(1, weight=1)
        detail_frame.rowconfigure(3, weight=1)
        ttk.Label(detail_frame, text="原文").grid(row=0, column=0, sticky="w")
        self.source_text = tk.Text(detail_frame, height=8, wrap="word", state="disabled")
        self.source_text.grid(row=1, column=0, sticky="nsew", pady=(2, 8))
        ttk.Label(detail_frame, text="译文").grid(row=2, column=0, sticky="w")
        self.translation_text = tk.Text(detail_frame, height=8, wrap="word")
        self.translation_text.grid(row=3, column=0, sticky="nsew", pady=(2, 8))
        status_row = ttk.Frame(detail_frame)
        status_row.grid(row=4, column=0, sticky="ew")
        ttk.Label(status_row, text="状态").pack(side=LEFT)
        self.entry_status_var = tk.StringVar(value="new")
        ttk.Combobox(
            status_row,
            textvariable=self.entry_status_var,
            values=("new", "translated", "reviewed", "locked", "rejected"),
            width=14,
            state="readonly",
        ).pack(side=LEFT, padx=8)

    def _build_glossary_tab(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        top = ttk.Frame(parent)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="搜索").grid(row=0, column=0, sticky="w")
        search = ttk.Entry(top, textvariable=self.term_search_var)
        search.grid(row=0, column=1, sticky="ew", padx=8)
        search.bind("<KeyRelease>", lambda _event: self.refresh_glossary())
        ttk.Button(top, text="保存术语", command=self.save_current_term).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(top, text="全部保存", command=self.save_glossary).grid(row=0, column=3)

        body = ttk.PanedWindow(parent, orient=HORIZONTAL)
        body.grid(row=1, column=0, sticky="nsew")
        list_frame = ttk.Frame(body)
        edit_frame = ttk.Frame(body)
        body.add(list_frame, weight=3)
        body.add(edit_frame, weight=2)

        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        columns = ("source", "target", "status", "count")
        self.glossary_tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse")
        for column, title, width in (
            ("source", "原术语", 220),
            ("target", "译名", 220),
            ("status", "状态", 90),
            ("count", "次数", 70),
        ):
            self.glossary_tree.heading(column, text=title)
            self.glossary_tree.column(column, width=width, stretch=column in ("source", "target"))
        term_scroll = ttk.Scrollbar(list_frame, orient=VERTICAL, command=self.glossary_tree.yview)
        self.glossary_tree.configure(yscrollcommand=term_scroll.set)
        self.glossary_tree.grid(row=0, column=0, sticky="nsew")
        term_scroll.grid(row=0, column=1, sticky="ns")
        self.glossary_tree.bind("<<TreeviewSelect>>", self._on_term_selected)

        edit_frame.columnconfigure(1, weight=1)
        ttk.Label(edit_frame, text="原术语").grid(row=0, column=0, sticky="w", pady=4)
        self.term_source_var = tk.StringVar()
        ttk.Entry(edit_frame, textvariable=self.term_source_var, state="readonly").grid(row=0, column=1, sticky="ew", padx=8, pady=4)
        ttk.Label(edit_frame, text="译名").grid(row=1, column=0, sticky="w", pady=4)
        self.term_target_var = tk.StringVar()
        ttk.Entry(edit_frame, textvariable=self.term_target_var).grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        ttk.Label(edit_frame, text="状态").grid(row=2, column=0, sticky="w", pady=4)
        self.term_status_var = tk.StringVar(value="pending")
        ttk.Combobox(
            edit_frame,
            textvariable=self.term_status_var,
            values=("pending", "approved", "rejected"),
            state="readonly",
            width=14,
        ).grid(row=2, column=1, sticky="w", padx=8, pady=4)
        ttk.Label(edit_frame, text="备注/例句").grid(row=3, column=0, sticky="nw", pady=4)
        self.term_note_text = tk.Text(edit_frame, height=10, wrap="word")
        self.term_note_text.grid(row=3, column=1, sticky="nsew", padx=8, pady=4)
        edit_frame.rowconfigure(3, weight=1)
        button_row = ttk.Frame(edit_frame)
        button_row.grid(row=4, column=1, sticky="ew", padx=8, pady=8)
        ttk.Button(button_row, text="批准", command=lambda: self._set_term_status("approved")).pack(side=LEFT, padx=(0, 8))
        ttk.Button(button_row, text="拒绝", command=lambda: self._set_term_status("rejected")).pack(side=LEFT, padx=(0, 8))
        ttk.Button(button_row, text="待定", command=lambda: self._set_term_status("pending")).pack(side=LEFT)

    def _build_tools_tab(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        columns = ("name", "engines", "role", "license")
        self.tools_tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        for column, title, width in (
            ("name", "工具", 160),
            ("engines", "适用范围", 380),
            ("role", "角色", 190),
            ("license", "许可证", 120),
        ):
            self.tools_tree.heading(column, text=title)
            self.tools_tree.column(column, width=width, stretch=column == "engines")
        self.tools_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(parent, orient=VERTICAL, command=self.tools_tree.yview)
        self.tools_tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")
        ttk.Button(parent, text="打开项目主页", command=self.open_selected_tool).grid(row=1, column=0, sticky="e", pady=(8, 0))

    def _build_status_bar(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.pack(fill=X)
        self.progress = ttk.Progressbar(bar, mode="indeterminate")
        self.progress.pack(side=RIGHT, fill=X, expand=False, padx=(8, 0))
        ttk.Label(bar, textvariable=self.status_var).pack(side=LEFT)

    def _browse_game_dir(self) -> None:
        value = filedialog.askdirectory(title="选择游戏目录")
        if not value:
            return
        self.game_dir_var.set(value)
        game_path = Path(value)
        if not self.workspace_var.get():
            self.workspace_var.set(str(Path.cwd() / "work" / game_path.name))
        if not self.output_dir_var.get():
            self.output_dir_var.set(str(game_path.with_name(game_path.name + "_zh")))

    def _browse_workspace(self) -> None:
        value = filedialog.askdirectory(title="选择工作区")
        if value:
            self.workspace_var.set(value)

    def _browse_output_dir(self) -> None:
        value = filedialog.askdirectory(title="选择输出目录")
        if value:
            self.output_dir_var.set(value)

    def _browse_config(self) -> None:
        value = filedialog.askopenfilename(
            title="选择 Provider 配置",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
        if value:
            self.config_var.set(value)

    def detect_game(self) -> None:
        def work() -> str:
            result = detect_best(self._require_path(self.game_dir_var, "游戏目录"))
            if result is None:
                return "未识别到当前已支持的游戏引擎。"
            return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)

        self._run_background("检测引擎", work, self._show_info_result)

    def extract_text(self) -> None:
        def work() -> str:
            game_dir = self._require_path(self.game_dir_var, "游戏目录")
            workspace = self._require_path(self.workspace_var, "工作区")
            result = detect_best(game_dir)
            if result is None:
                raise RuntimeError("未识别到当前已支持的游戏引擎")
            adapter = adapter_map()[result.adapter_id]
            bundle = adapter.extract(game_dir, workspace)
            manifest = ProjectManifest(
                adapter_id=bundle.adapter_id,
                engine_name=bundle.engine_name,
                source_game_dir=str(game_dir.resolve()),
                entry_count=len(bundle.entries),
                notes=bundle.notes,
            )
            init_workspace(workspace, manifest, bundle.entries)
            return "已抽取 %s 条文案到 %s" % (len(bundle.entries), workspace)

        self._run_background("抽取文案", work, self._after_workspace_changed)

    def load_workspace(self) -> None:
        workspace = self._safe_workspace()
        if workspace is None:
            return
        try:
            self.entries = load_workspace_entries(workspace)
            self.glossary = load_glossary(workspace / GLOSSARY_FILE)
            try:
                manifest = load_manifest(workspace)
                self.log("载入工作区：%s，%s 条文案，适配器 %s" % (workspace, len(self.entries), manifest.adapter_id))
            except FileNotFoundError:
                self.log("载入工作区：%s，%s 条文案" % (workspace, len(self.entries)))
            self.refresh_entries()
            self.refresh_glossary()
            self.status_var.set("已载入工作区")
        except Exception as exc:
            messagebox.showerror("载入失败", str(exc))

    def generate_terms(self) -> None:
        def work() -> str:
            workspace = self._require_path(self.workspace_var, "工作区")
            entries = load_workspace_entries(workspace)
            candidates = extract_term_candidates(
                entries,
                min_count=int(self.term_min_count_var.get()),
                limit=int(self.term_limit_var.get()),
            )
            existing = {term.source: term for term in load_glossary(workspace / GLOSSARY_FILE)}
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
            save_glossary(workspace / GLOSSARY_FILE, terms)
            return "已生成 %s 个术语候选" % len(terms)

        self._run_background("生成术语", work, self._after_workspace_changed)

    def translate_batch(self) -> None:
        def work() -> str:
            changed = translate_workspace(
                workspace=self._require_path(self.workspace_var, "工作区"),
                config_path=self._require_path(self.config_var, "配置文件"),
                provider_name=self.provider_var.get().strip(),
                target_language=self.target_var.get().strip() or "zh-Hans",
                limit=int(self.batch_limit_var.get()),
            )
            return "已翻译 %s 条文案" % changed

        self._run_background("翻译下一批", work, self._after_workspace_changed)

    def apply_translation(self) -> None:
        def work() -> str:
            workspace = self._require_path(self.workspace_var, "工作区")
            manifest = load_manifest(workspace)
            adapters = adapter_map()
            if manifest.adapter_id not in adapters:
                raise RuntimeError("当前 GUI 未加载适配器：%s" % manifest.adapter_id)
            entries = load_workspace_entries(workspace)
            adapters[manifest.adapter_id].apply(
                self._require_path(self.game_dir_var, "游戏目录"),
                workspace,
                self._require_path(self.output_dir_var, "输出目录"),
                entries,
                overwrite=bool(self.overwrite_var.get()),
            )
            translated = len([entry for entry in entries if entry.translation and entry.status != "rejected"])
            return "已回填 %s 条译文到 %s" % (translated, self.output_dir_var.get())

        self._run_background("回填输出", work, self._show_info_result)

    def refresh_entries(self) -> None:
        self.entries_tree.delete(*self.entries_tree.get_children())
        query = self.entry_search_var.get().strip().lower()
        for entry in self.entries:
            haystack = "\n".join([entry.source, entry.translation or "", entry.file, entry.status]).lower()
            if query and query not in haystack:
                continue
            self.entries_tree.insert(
                "",
                END,
                iid=entry.id,
                values=(
                    entry.status,
                    self._one_line(entry.source),
                    self._one_line(entry.translation or ""),
                    entry.file,
                ),
            )

    def refresh_glossary(self) -> None:
        self.glossary_tree.delete(*self.glossary_tree.get_children())
        query = self.term_search_var.get().strip().lower()
        for term in self.glossary:
            haystack = "\n".join([term.source, term.target, term.status, term.note]).lower()
            if query and query not in haystack:
                continue
            self.glossary_tree.insert(
                "",
                END,
                iid=term.source,
                values=(term.source, term.target, term.status, term.count),
            )

    def save_current_entry(self) -> None:
        if not self.current_entry_id:
            return
        entry = self._entry_by_id(self.current_entry_id)
        if entry is None:
            return
        entry.translation = self.translation_text.get("1.0", END).rstrip("\n")
        entry.status = self.entry_status_var.get()
        workspace = self._safe_workspace()
        if workspace is None:
            return
        save_workspace_entries(workspace, self.entries)
        self.refresh_entries()
        self.log("已保存文案：%s" % entry.id)

    def save_current_term(self) -> None:
        if not self.current_term_source:
            return
        term = self._term_by_source(self.current_term_source)
        if term is None:
            return
        term.target = self.term_target_var.get().strip()
        term.status = self.term_status_var.get()
        term.note = self.term_note_text.get("1.0", END).rstrip("\n")
        self.save_glossary()

    def save_glossary(self) -> None:
        workspace = self._safe_workspace()
        if workspace is None:
            return
        save_glossary(workspace / GLOSSARY_FILE, self.glossary)
        self.refresh_glossary()
        self.log("已保存术语表：%s" % (workspace / GLOSSARY_FILE))

    def open_selected_tool(self) -> None:
        selected = self.tools_tree.selection()
        if not selected:
            return
        url = self.tools_tree.item(selected[0], "tags")[0]
        webbrowser.open(url)

    def _load_tools(self) -> None:
        try:
            with package_resources.open_text("jp_game_translator.resources", "tool_catalog.json", encoding="utf-8") as handle:
                data = json.load(handle)
            for tool in data.get("tools", []):
                self.tools_tree.insert(
                    "",
                    END,
                    values=(
                        tool.get("name", ""),
                        ", ".join(tool.get("engines", [])),
                        tool.get("role", ""),
                        tool.get("license", ""),
                    ),
                    tags=(tool.get("url", ""),),
                )
        except Exception as exc:
            self.log("开源工具清单载入失败：%s" % exc)

    def _on_entry_selected(self, _event) -> None:
        selected = self.entries_tree.selection()
        if not selected:
            return
        entry = self._entry_by_id(selected[0])
        if entry is None:
            return
        self.current_entry_id = entry.id
        self.source_text.configure(state="normal")
        self.source_text.delete("1.0", END)
        self.source_text.insert("1.0", entry.source)
        self.source_text.configure(state="disabled")
        self.translation_text.delete("1.0", END)
        self.translation_text.insert("1.0", entry.translation or "")
        self.entry_status_var.set(entry.status)

    def _on_term_selected(self, _event) -> None:
        selected = self.glossary_tree.selection()
        if not selected:
            return
        term = self._term_by_source(selected[0])
        if term is None:
            return
        self.current_term_source = term.source
        self.term_source_var.set(term.source)
        self.term_target_var.set(term.target)
        self.term_status_var.set(term.status)
        self.term_note_text.delete("1.0", END)
        self.term_note_text.insert("1.0", term.note)

    def _set_term_status(self, status: str) -> None:
        self.term_status_var.set(status)
        self.save_current_term()

    def _entry_by_id(self, entry_id: str) -> Optional[TextEntry]:
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        return None

    def _term_by_source(self, source: str) -> Optional[GlossaryTerm]:
        for term in self.glossary:
            if term.source == source:
                return term
        return None

    def _after_workspace_changed(self, message: str) -> None:
        self.log(message)
        self.load_workspace()
        messagebox.showinfo("完成", message)

    def _show_info_result(self, message: str) -> None:
        self.log(message)
        messagebox.showinfo("结果", message)

    def _run_background(self, title: str, work: Callable[[], str], on_success: Callable[[str], None]) -> None:
        if self.busy:
            messagebox.showwarning("任务进行中", "当前已有任务在运行。")
            return
        self.busy = True
        self.status_var.set("%s中..." % title)
        self.progress.start(12)
        self.log("%s开始" % title)

        def runner() -> None:
            try:
                result = work()
                self.worker_queue.put(("success", title, result, on_success))
            except Exception as exc:
                self.worker_queue.put(("error", title, str(exc), traceback.format_exc()))

        threading.Thread(target=runner, daemon=True).start()

    def _poll_worker_queue(self) -> None:
        try:
            while True:
                item = self.worker_queue.get_nowait()
                kind = item[0]
                if kind == "success":
                    _kind, title, result, on_success = item
                    self.busy = False
                    self.progress.stop()
                    self.status_var.set("%s完成" % title)
                    on_success(result)
                elif kind == "error":
                    _kind, title, message, details = item
                    self.busy = False
                    self.progress.stop()
                    self.status_var.set("%s失败" % title)
                    self.log(details)
                    messagebox.showerror("%s失败" % title, message)
        except queue.Empty:
            pass
        self.after(100, self._poll_worker_queue)

    def _safe_workspace(self) -> Optional[Path]:
        value = self.workspace_var.get().strip()
        if not value:
            messagebox.showwarning("缺少路径", "请先选择工作区。")
            return None
        return Path(value)

    def _require_path(self, variable: tk.StringVar, label: str) -> Path:
        value = variable.get().strip()
        if not value:
            raise ValueError("请先选择%s" % label)
        return Path(value)

    def _one_line(self, value: str, limit: int = 120) -> str:
        text = value.replace("\r", "\\r").replace("\n", "\\n")
        if len(text) > limit:
            return text[: limit - 1] + "..."
        return text

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert(END, message + "\n")
        self.log_text.see(END)
        self.log_text.configure(state="disabled")


def main() -> int:
    app = TranslatorGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
