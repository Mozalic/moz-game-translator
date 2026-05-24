from __future__ import annotations

import importlib.resources as package_resources
import json
import re
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from jp_game_translator.adapters.registry import adapter_map, detect_best
from jp_game_translator.core.models import ProjectManifest, TextEntry
from jp_game_translator.core.project import (
    GLOSSARY_FILE,
    WorkspaceSummary,
    discover_workspaces,
    init_workspace,
    load_manifest,
    load_workspace_entries,
    save_workspace_entries,
)
from jp_game_translator.terminology.extractor import extract_term_candidates
from jp_game_translator.terminology.glossary import GlossaryTerm, load_glossary, save_glossary
from jp_game_translator.translation.pipeline import translate_glossary_terms, translate_workspace, translate_workspace_all
from jp_game_translator.translation.providers import OpenAICompatibleProvider, ProviderConfig
from jp_game_translator.translation.prompts import SYSTEM_PROMPT, TERM_SYSTEM_PROMPT
from jp_game_translator.translation.token_usage import (
    TokenUsageRun,
    latest_token_usage_run,
    token_usage_log_path,
)

try:
    from PySide6.QtCore import QRectF, Qt, QThread, QUrl, Signal
    from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QPainter, QPen
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSpinBox,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

    QT_IMPORT_ERROR: Optional[Exception] = None
except ImportError as exc:  # pragma: no cover - depends on optional GUI dependency
    QT_IMPORT_ERROR = exc


APP_STYLESHEET = """
QMainWindow, QWidget {
    background: #f5f7fa;
    color: #1f2933;
    font-family: "Microsoft YaHei UI", "Segoe UI", Arial;
    font-size: 13px;
}
QFrame#Surface {
    background: #ffffff;
    border: 1px solid #d8dee8;
    border-radius: 10px;
}
QLabel#Title {
    color: #17202a;
    font-size: 22px;
    font-weight: 700;
}
QLabel#Subtitle {
    color: #697586;
    font-size: 12px;
}
QLabel {
    background: transparent;
}
QLabel#SectionTitle {
    color: #26323f;
    font-size: 14px;
    font-weight: 700;
    background: transparent;
}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
    background: #ffffff;
    border: 1px solid #c9d2df;
    border-radius: 7px;
    padding: 7px;
    selection-background-color: #2f7d6d;
}
QPlainTextEdit {
    line-height: 1.35;
}
QPushButton {
    background: #ffffff;
    border: 1px solid #c5cfdc;
    border-radius: 8px;
    padding: 8px 10px;
    min-height: 22px;
}
QPushButton:hover {
    background: #eef6f3;
    border-color: #82b4a7;
}
QPushButton:pressed {
    background: #dceee8;
}
QPushButton[accent="true"] {
    background: #2f7d6d;
    border-color: #2f7d6d;
    color: #ffffff;
    font-weight: 700;
}
QPushButton[accent="true"]:hover {
    background: #286f61;
}
QPushButton[quiet="true"] {
    background: #f8fafc;
}
QCheckBox {
    background: transparent;
    spacing: 8px;
    padding: 7px 0;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
}
QTabWidget::pane {
    border: none;
    border-top: 1px solid #d8dee8;
    background: transparent;
    top: -1px;
}
QTabWidget#TopTabs::pane {
    border-top: none;
}
QTabBar::tab {
    background: #eef2f6;
    border: 1px solid #d8dee8;
    padding: 10px 18px;
    margin-right: 4px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}
QTabWidget#SettingsTabs QTabBar::tab {
    padding: 9px 18px;
    margin-top: 6px;
}
QTabBar::tab:selected {
    background: #ffffff;
    border-bottom-color: #ffffff;
    color: #2f7d6d;
    font-weight: 700;
}
QTableWidget {
    background: #ffffff;
    border: 1px solid #d8dee8;
    border-radius: 8px;
    gridline-color: #edf1f5;
    alternate-background-color: #f8fafc;
}
QHeaderView::section {
    background: #eef2f6;
    border: none;
    border-right: 1px solid #d8dee8;
    padding: 7px;
    font-weight: 700;
}
QProgressBar {
    border: 1px solid #d8dee8;
    border-radius: 7px;
    background: #ffffff;
    text-align: center;
}
QProgressBar::chunk {
    border-radius: 6px;
    background: #2f7d6d;
}
"""

PROGRESS_VIEW_REFRESH_INTERVAL_SECONDS = 15.0
TOKEN_USAGE_REFRESH_INTERVAL_SECONDS = 1.0


def _missing_qt_message() -> str:
    return (
        "PySide6 is not installed.\n"
        "Install it with:\n"
        "  python -m pip install -r requirements-gui.txt\n\n"
        "This project pins PySide6 6.6.2 on Python 3.8 because newer PySide6 releases require newer Python."
    )


if QT_IMPORT_ERROR is None:

    class TaskThread(QThread):
        succeeded = Signal(object)
        failed = Signal(str, str)
        progress = Signal(str)

        def __init__(
            self,
            task: Callable[..., object],
            parent: Optional[QWidget] = None,
            progress_enabled: bool = False,
            cancellation_enabled: bool = False,
        ):
            super().__init__(parent)
            self._task = task
            self._progress_enabled = progress_enabled
            self._cancellation_enabled = cancellation_enabled
            self._cancel_event = threading.Event()

        def run(self) -> None:
            try:
                if self._progress_enabled and self._cancellation_enabled:
                    self.succeeded.emit(self._task(self._report_progress, self.is_cancelled))
                elif self._progress_enabled:
                    self.succeeded.emit(self._task(self._report_progress))
                else:
                    self.succeeded.emit(self._task())
            except Exception as exc:  # pragma: no cover - exercised by GUI
                self.failed.emit(str(exc), traceback.format_exc())

        def _report_progress(self, message: str) -> None:
            text = str(message)
            print(text, flush=True)
            self.progress.emit(text)

        def cancel(self) -> None:
            self._cancel_event.set()

        def is_cancelled(self) -> bool:
            return self._cancel_event.is_set()


    def _short_token_count(value: int) -> str:
        value = int(value or 0)
        if value >= 1_000_000:
            return "%.1fM" % (value / 1_000_000)
        if value >= 10_000:
            return "%.0fK" % (value / 1_000)
        if value >= 1_000:
            return "%.1fK" % (value / 1_000)
        return str(value)


    class TokenUsageChart(QWidget):
        def __init__(self, parent: Optional[QWidget] = None) -> None:
            super().__init__(parent)
            self.records: List[Dict[str, object]] = []
            self.empty_text = "No token usage data yet."
            self.prompt_label = "Prompt"
            self.completion_label = "Completion"
            self.failed_label = "Failed"
            self.setMinimumHeight(260)

        def set_empty_text(self, text: str) -> None:
            self.empty_text = text
            self.update()

        def set_legend_labels(self, prompt: str, completion: str, failed: str) -> None:
            self.prompt_label = prompt
            self.completion_label = completion
            self.failed_label = failed
            self.update()

        def set_records(self, records: List[Dict[str, object]]) -> None:
            self.records = list(records)
            self.update()

        def paintEvent(self, _event: object) -> None:  # pragma: no cover - visual Qt paint path
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            outer = self.rect().adjusted(1, 1, -1, -1)
            painter.fillRect(outer, QColor("#ffffff"))
            painter.setPen(QPen(QColor("#d8dee8"), 1))
            painter.drawRoundedRect(outer, 8, 8)

            records = self._bucket_records(self.records)
            if not records:
                painter.setPen(QColor("#697586"))
                painter.drawText(QRectF(outer), Qt.AlignCenter, self.empty_text)
                painter.end()
                return

            plot = QRectF(outer).adjusted(58, 22, -18, -50)
            if plot.width() <= 10 or plot.height() <= 10:
                painter.end()
                return

            max_total = max(1, max(int(item["total"]) for item in records))
            painter.setPen(QPen(QColor("#edf1f5"), 1))
            for step in range(5):
                ratio = step / 4
                y = plot.bottom() - plot.height() * ratio
                painter.drawLine(QRectF(plot.left(), y, plot.width(), 1).topLeft(), QRectF(plot.left(), y, plot.width(), 1).topRight())
                label_value = int(max_total * ratio)
                painter.setPen(QColor("#697586"))
                painter.drawText(QRectF(4, y - 9, 48, 18), Qt.AlignRight | Qt.AlignVCenter, _short_token_count(label_value))
                painter.setPen(QPen(QColor("#edf1f5"), 1))

            painter.setPen(QPen(QColor("#c9d2df"), 1))
            painter.drawLine(plot.bottomLeft(), plot.bottomRight())
            painter.drawLine(plot.bottomLeft(), plot.topLeft())

            slot_width = plot.width() / max(len(records), 1)
            bar_width = max(2.0, min(24.0, slot_width * 0.62))
            prompt_color = QColor("#2f7d6d")
            completion_color = QColor("#3c6fb6")
            failed_color = QColor("#c2413b")

            for index, item in enumerate(records):
                x = plot.left() + index * slot_width + (slot_width - bar_width) / 2
                prompt_tokens = int(item["prompt"])
                completion_tokens = int(item["completion"])
                total_tokens = max(int(item["total"]), prompt_tokens + completion_tokens)
                prompt_height = plot.height() * prompt_tokens / max_total
                completion_height = plot.height() * completion_tokens / max_total
                if prompt_tokens <= 0 and completion_tokens <= 0 and total_tokens > 0:
                    prompt_height = plot.height() * total_tokens / max_total

                y = plot.bottom()
                if prompt_height > 0:
                    y -= prompt_height
                    painter.fillRect(QRectF(x, y, bar_width, prompt_height), prompt_color)
                if completion_height > 0:
                    y -= completion_height
                    painter.fillRect(QRectF(x, y, bar_width, completion_height), completion_color)
                if bool(item["failed"]):
                    painter.fillRect(QRectF(x, y - 3, bar_width, 3), failed_color)

            painter.setPen(QColor("#697586"))
            first_label = str(records[0]["label"])
            last_label = str(records[-1]["label"])
            painter.drawText(QRectF(plot.left(), plot.bottom() + 8, 100, 18), Qt.AlignLeft, first_label)
            painter.drawText(QRectF(plot.right() - 100, plot.bottom() + 8, 100, 18), Qt.AlignRight, last_label)

            legend_y = outer.bottom() - 26
            self._draw_legend(painter, 58, legend_y, prompt_color, self.prompt_label)
            self._draw_legend(painter, 152, legend_y, completion_color, self.completion_label)
            self._draw_legend(painter, 282, legend_y, failed_color, self.failed_label)
            painter.end()

        def _draw_legend(self, painter: QPainter, x: int, y: int, color: QColor, text: str) -> None:
            painter.fillRect(QRectF(x, y + 4, 12, 8), color)
            painter.setPen(QColor("#697586"))
            painter.drawText(QRectF(x + 18, y, 100, 18), Qt.AlignLeft | Qt.AlignVCenter, text)

        def _bucket_records(self, records: List[Dict[str, object]], limit: int = 80) -> List[Dict[str, object]]:
            sorted_records = sorted(records, key=lambda item: int(item.get("request_index") or 0))
            if len(sorted_records) <= limit:
                return [self._record_bucket([record]) for record in sorted_records]

            bucket_size = max(1, (len(sorted_records) + limit - 1) // limit)
            buckets: List[Dict[str, object]] = []
            for start in range(0, len(sorted_records), bucket_size):
                bucket = sorted_records[start : start + bucket_size]
                buckets.append(self._record_bucket(bucket))
            return buckets

        def _record_bucket(self, records: List[Dict[str, object]]) -> Dict[str, object]:
            first_index = int(records[0].get("request_index") or 0)
            last_index = int(records[-1].get("request_index") or first_index)
            label = str(first_index) if first_index == last_index else "%s-%s" % (first_index, last_index)
            prompt = sum(int(record.get("prompt_tokens") or 0) for record in records)
            completion = sum(int(record.get("completion_tokens") or 0) for record in records)
            total = sum(int(record.get("total_tokens") or 0) for record in records)
            if total <= 0:
                total = prompt + completion
            return {
                "label": label,
                "prompt": prompt,
                "completion": completion,
                "total": total,
                "failed": any(bool(record.get("failed")) for record in records),
                "request_index": first_index,
            }


    class ProviderConfigDialog(QDialog):
        def __init__(
            self,
            config_path: Path,
            tr: Callable[[str], str],
            parent: Optional[QWidget] = None,
        ) -> None:
            super().__init__(parent)
            self.config_path = config_path
            self.tr_text = tr
            self.config_data = self._load_config()
            self.current_provider: Optional[str] = None
            self.loading = False
            self.current_task: Optional[TaskThread] = None

            self.setWindowTitle(self.tr_text("provider_config"))
            self.resize(860, 560)
            self.setMinimumSize(760, 500)
            self._build_ui()
            self._refresh_provider_list()

        def _load_config(self) -> Dict[str, object]:
            if not self.config_path.exists():
                return {"providers": {}}
            try:
                with self.config_path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if not isinstance(data, dict):
                    return {"providers": {}}
                providers = data.get("providers")
                if not isinstance(providers, dict):
                    data["providers"] = {}
                return data
            except Exception:
                QMessageBox.warning(self, self.tr_text("provider_config"), self.tr_text("config_load_failed"))
                return {"providers": {}}

        def _build_ui(self) -> None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(12)

            body = QHBoxLayout()
            body.setSpacing(14)
            layout.addLayout(body, 1)

            left = QVBoxLayout()
            providers_label = QLabel(self.tr_text("providers"))
            providers_label.setObjectName("SectionTitle")
            self.provider_list = QListWidget()
            self.provider_list.currentItemChanged.connect(self._on_provider_selected)
            provider_buttons = QHBoxLayout()
            add_button = QPushButton(self.tr_text("add_provider"))
            delete_button = QPushButton(self.tr_text("delete_provider"))
            add_button.clicked.connect(self._add_provider)
            delete_button.clicked.connect(self._delete_provider)
            provider_buttons.addWidget(add_button)
            provider_buttons.addWidget(delete_button)
            left.addWidget(providers_label)
            left.addWidget(self.provider_list, 1)
            left.addLayout(provider_buttons)
            body.addLayout(left, 0)

            form_frame = QFrame()
            form_frame.setObjectName("Surface")
            form = QFormLayout(form_frame)
            form.setContentsMargins(14, 14, 14, 14)
            form.setSpacing(10)

            self.name_edit = QLineEdit()
            self.type_combo = QComboBox()
            self.type_combo.setEditable(True)
            self.type_combo.addItem("openai_compatible")
            self.base_url_edit = QLineEdit()
            self.api_key_env_edit = QLineEdit()
            self.api_key_edit = QLineEdit()
            self.api_key_edit.setEchoMode(QLineEdit.Password)
            self.model_combo = QComboBox()
            self.model_combo.setEditable(True)
            self.temperature_spin = QDoubleSpinBox()
            self.temperature_spin.setRange(0.0, 2.0)
            self.temperature_spin.setSingleStep(0.1)
            self.temperature_spin.setDecimals(2)
            self.temperature_spin.setValue(0.2)
            self.json_mode_check = QCheckBox()
            self.api_status_label = QLabel()
            self.api_status_label.setWordWrap(True)
            self.api_status_label.setObjectName("Subtitle")
            self.config_progress = QProgressBar()
            self.config_progress.setTextVisible(False)
            self.config_progress.hide()

            form.addRow(self.tr_text("provider_name"), self.name_edit)
            form.addRow(self.tr_text("type"), self.type_combo)
            form.addRow(self.tr_text("base_url"), self.base_url_edit)
            form.addRow(self.tr_text("api_key_env"), self.api_key_env_edit)
            form.addRow(self.tr_text("api_key"), self.api_key_edit)

            model_row = QHBoxLayout()
            fetch_models_button = QPushButton(self.tr_text("fetch_models"))
            test_api_button = QPushButton(self.tr_text("test_api"))
            fetch_models_button.clicked.connect(self._fetch_models)
            test_api_button.clicked.connect(self._test_api)
            model_row.addWidget(self.model_combo, 1)
            model_row.addWidget(fetch_models_button)
            model_row.addWidget(test_api_button)
            form.addRow(self.tr_text("model"), model_row)

            form.addRow(self.tr_text("temperature"), self.temperature_spin)
            form.addRow(self.tr_text("json_mode"), self.json_mode_check)
            form.addRow(self.tr_text("api_status"), self.api_status_label)
            form.addRow("", self.config_progress)
            body.addWidget(form_frame, 1)

            buttons = QDialogButtonBox()
            save_button = buttons.addButton(self.tr_text("save_config"), QDialogButtonBox.AcceptRole)
            cancel_button = buttons.addButton(self.tr_text("cancel"), QDialogButtonBox.RejectRole)
            save_button.clicked.connect(self._save_and_accept)
            cancel_button.clicked.connect(self.reject)
            layout.addWidget(buttons)

        def _providers(self) -> Dict[str, object]:
            providers = self.config_data.setdefault("providers", {})
            if not isinstance(providers, dict):
                providers = {}
                self.config_data["providers"] = providers
            return providers

        def _refresh_provider_list(self, select_name: Optional[str] = None) -> None:
            self.provider_list.blockSignals(True)
            self.provider_list.clear()
            providers = self._providers()
            for name in sorted(providers):
                item = QListWidgetItem(name)
                item.setData(Qt.UserRole, name)
                self.provider_list.addItem(item)
            self.provider_list.blockSignals(False)

            if self.provider_list.count() == 0:
                self._clear_form()
                return

            target = select_name or self.current_provider
            selected_row = 0
            for row in range(self.provider_list.count()):
                item = self.provider_list.item(row)
                if item and item.data(Qt.UserRole) == target:
                    selected_row = row
                    break
            self.provider_list.setCurrentRow(selected_row)
            self._load_provider(str(self.provider_list.currentItem().data(Qt.UserRole)))

        def _on_provider_selected(self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]) -> None:
            if self.loading:
                return
            if previous is not None:
                previous_name = str(previous.data(Qt.UserRole))
                try:
                    self._write_form_to_data(previous_name)
                except ValueError as exc:
                    QMessageBox.warning(self, self.tr_text("provider_config"), str(exc))
                    self.provider_list.blockSignals(True)
                    self.provider_list.setCurrentItem(previous)
                    self.provider_list.blockSignals(False)
                    return
            if current is not None:
                self._load_provider(str(current.data(Qt.UserRole)))

        def _load_provider(self, name: str) -> None:
            provider = self._providers().get(name, {})
            if not isinstance(provider, dict):
                provider = {}
            self.current_provider = name
            self.loading = True
            self.name_edit.setText(name)
            self.type_combo.setCurrentText(str(provider.get("type", "openai_compatible")))
            self.base_url_edit.setText(str(provider.get("base_url", "")))
            self.api_key_env_edit.setText(str(provider.get("api_key_env", "")))
            self.api_key_edit.setText(str(provider.get("api_key", "")))
            self.model_combo.clear()
            self._set_model_combo_text(str(provider.get("model", "")))
            self.temperature_spin.setValue(float(provider.get("temperature", 0.2)))
            self.json_mode_check.setChecked(bool(provider.get("json_mode", False)))
            self.loading = False

        def _clear_form(self) -> None:
            self.current_provider = None
            self.name_edit.clear()
            self.type_combo.setCurrentText("openai_compatible")
            self.base_url_edit.clear()
            self.api_key_env_edit.clear()
            self.api_key_edit.clear()
            self.model_combo.clearEditText()
            self.temperature_spin.setValue(0.2)
            self.json_mode_check.setChecked(False)
            self.api_status_label.clear()

        def _set_model_combo_text(self, value: str) -> None:
            if value and self.model_combo.findText(value) < 0:
                self.model_combo.addItem(value)
            self.model_combo.setCurrentText(value)

        def _provider_config_from_form(self) -> ProviderConfig:
            name = self.name_edit.text().strip() or "preview"
            base_url = self.base_url_edit.text().strip().rstrip("/")
            api_key = self.api_key_edit.text().strip()
            api_key_env = self.api_key_env_edit.text().strip()
            if not base_url:
                raise ValueError(self.tr_text("missing_endpoint"))
            if not api_key and not api_key_env:
                raise ValueError(self.tr_text("missing_auth"))
            return ProviderConfig(
                name=name,
                type=self.type_combo.currentText().strip() or "openai_compatible",
                base_url=base_url,
                model=self.model_combo.currentText().strip(),
                api_key_env=api_key_env,
                api_key=api_key,
                temperature=float(self.temperature_spin.value()),
                json_mode=bool(self.json_mode_check.isChecked()),
            )

        def _run_config_task(
            self,
            status_key: str,
            task: Callable[[], object],
            on_success: Callable[[object], None],
        ) -> None:
            if self.current_task is not None:
                return
            try:
                self.api_status_label.setText(self.tr_text(status_key))
                self.config_progress.show()
                self.config_progress.setRange(0, 0)
                worker = TaskThread(task, self)
                self.current_task = worker

                def success(result: object) -> None:
                    self.current_task = None
                    self.config_progress.hide()
                    on_success(result)

                def failure(message: str, details: str) -> None:
                    self.current_task = None
                    self.config_progress.hide()
                    self.api_status_label.setText(message)
                    QMessageBox.critical(self, self.tr_text("provider_config"), message)

                worker.succeeded.connect(success)
                worker.failed.connect(failure)
                worker.start()
            except Exception as exc:
                self.current_task = None
                self.config_progress.hide()
                self.api_status_label.setText(str(exc))
                QMessageBox.warning(self, self.tr_text("provider_config"), str(exc))

        def _fetch_models(self) -> None:
            def task() -> List[str]:
                provider = OpenAICompatibleProvider(self._provider_config_from_form())
                return provider.list_models()

            def success(result: object) -> None:
                models = list(result or [])
                current = self.model_combo.currentText().strip()
                self.model_combo.clear()
                self.model_combo.addItems(models)
                if current:
                    self._set_model_combo_text(current)
                elif models:
                    self.model_combo.setCurrentIndex(0)
                if models:
                    self.api_status_label.setText(self.tr_text("models_loaded").format(len(models)))
                else:
                    self.api_status_label.setText(self.tr_text("models_empty"))

            self._run_config_task("fetching_models", task, success)

        def _test_api(self) -> None:
            def task() -> str:
                provider = OpenAICompatibleProvider(self._provider_config_from_form())
                return provider.test_connection()

            def success(result: object) -> None:
                message = str(result)
                self.api_status_label.setText(message)
                QMessageBox.information(self, self.tr_text("api_test_success"), message)

            self._run_config_task("testing_api", task, success)

        def _write_form_to_data(self, old_name: Optional[str] = None) -> Optional[str]:
            name = self.name_edit.text().strip()
            if not name:
                return None
            providers = self._providers()
            old_name = old_name or self.current_provider or name
            provider = {
                "type": self.type_combo.currentText().strip() or "openai_compatible",
                "base_url": self.base_url_edit.text().strip().rstrip("/"),
                "api_key_env": self.api_key_env_edit.text().strip(),
                "model": self.model_combo.currentText().strip(),
                "temperature": float(self.temperature_spin.value()),
                "json_mode": bool(self.json_mode_check.isChecked()),
            }
            api_key = self.api_key_edit.text().strip()
            if api_key:
                provider["api_key"] = api_key

            if old_name and old_name != name and name in providers:
                raise ValueError(self.tr_text("provider_exists"))
            if old_name and old_name != name and old_name in providers:
                del providers[old_name]
            providers[name] = provider
            self.current_provider = name
            return name

        def _add_provider(self) -> None:
            name, accepted = QInputDialog.getText(self, self.tr_text("new_provider"), self.tr_text("provider_name"))
            if not accepted:
                return
            name = name.strip()
            if not name:
                QMessageBox.warning(self, self.tr_text("provider_config"), self.tr_text("invalid_provider"))
                return
            providers = self._providers()
            if name in providers:
                QMessageBox.warning(self, self.tr_text("provider_config"), self.tr_text("provider_exists"))
                return
            if self.current_provider:
                self._write_form_to_data(self.current_provider)
            providers[name] = {
                "type": "openai_compatible",
                "base_url": "",
                "api_key_env": "",
                "model": "",
                "temperature": 0.2,
                "json_mode": False,
            }
            self._refresh_provider_list(name)

        def _delete_provider(self) -> None:
            item = self.provider_list.currentItem()
            if item is None:
                return
            name = str(item.data(Qt.UserRole))
            reply = QMessageBox.question(
                self,
                self.tr_text("delete"),
                self.tr_text("confirm_delete").format(name),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            providers = self._providers()
            providers.pop(name, None)
            self.current_provider = None
            self._refresh_provider_list()

        def _save_and_accept(self) -> None:
            current_item = self.provider_list.currentItem()
            current_name = str(current_item.data(Qt.UserRole)) if current_item is not None else self.current_provider
            try:
                saved_name = self._write_form_to_data(current_name)
            except ValueError as exc:
                QMessageBox.warning(self, self.tr_text("provider_config"), str(exc))
                return
            if self.provider_list.count() > 0 and saved_name is None:
                QMessageBox.warning(self, self.tr_text("provider_config"), self.tr_text("invalid_provider"))
                return
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with self.config_path.open("w", encoding="utf-8") as handle:
                json.dump(self.config_data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            self.accept()

        def selected_provider(self) -> Optional[str]:
            return self.current_provider


    class QtTranslatorWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.language = "zh-Hans"
            self.translations = self._load_translations()
            self.entries: List[TextEntry] = []
            self.glossary: List[GlossaryTerm] = []
            self.workspace_summaries: List[WorkspaceSummary] = []
            self.tools: List[dict] = []
            self.current_task: Optional[TaskThread] = None
            self.current_task_cancellable = False
            self.busy = False
            self.gui_settings = self._load_gui_settings()
            self._last_entries_progress_refresh = 0.0
            self._last_glossary_progress_refresh = 0.0
            self._last_usage_progress_refresh = 0.0
            self._entries_progress_dirty = False
            self._glossary_progress_dirty = False

            self.text_bindings: List[Tuple[object, str, str]] = []
            self.tab_bindings: List[Tuple[QTabWidget, int, str]] = []
            self.header_bindings: List[Tuple[QTableWidget, List[str]]] = []

            self.setWindowTitle(self.tr("app_title"))
            self.resize(1360, 920)
            self.setMinimumSize(1120, 780)
            self._build_menu()
            self._build_ui()
            self._load_tools()
            self.refresh_workspace_list()
            self.apply_language()

        def _load_translations(self) -> Dict[str, Dict[str, str]]:
            with package_resources.open_text("jp_game_translator.resources", "gui_i18n.json", encoding="utf-8") as handle:
                return json.load(handle)

        def tr(self, key: str) -> str:
            language_table = self.translations.get(self.language, {})
            fallback = self.translations.get("en", {})
            return str(language_table.get(key, fallback.get(key, key)))

        def bind_text(self, widget: object, key: str, method: str = "setText") -> object:
            self.text_bindings.append((widget, key, method))
            getattr(widget, method)(self.tr(key))
            return widget

        def bind_tab(self, tabs: QTabWidget, index: int, key: str) -> None:
            self.tab_bindings.append((tabs, index, key))
            tabs.setTabText(index, self.tr(key))

        def bind_headers(self, table: QTableWidget, keys: List[str]) -> None:
            self.header_bindings.append((table, keys))
            table.setHorizontalHeaderLabels([self.tr(key) for key in keys])

        def apply_language(self) -> None:
            self.setWindowTitle(self.tr("app_title"))
            for widget, key, method in self.text_bindings:
                getattr(widget, method)(self.tr(key))
            for tabs, index, key in self.tab_bindings:
                tabs.setTabText(index, self.tr(key))
            for table, keys in self.header_bindings:
                table.setHorizontalHeaderLabels([self.tr(key) for key in keys])

            self.language_combo.blockSignals(True)
            current = self.language
            self.language_combo.clear()
            self.language_combo.addItem(self.tr("lang_zh_name"), "zh-Hans")
            self.language_combo.addItem(self.tr("lang_en_name"), "en")
            self.language_combo.setCurrentIndex(0 if current == "zh-Hans" else 1)
            self.language_combo.blockSignals(False)
            if not self.busy:
                self.status_label.setText(self.tr("ready"))
            self.refresh_entries()
            self.refresh_glossary()
            if hasattr(self, "workspace_combo"):
                self._populate_workspace_combo(self.workspace_edit.text().strip())
            if hasattr(self, "usage_chart"):
                self.refresh_token_usage(show_errors=False)

        def _build_menu(self) -> None:
            config_menu = self.menuBar().addMenu("")
            self.bind_text(config_menu, "config_menu", "setTitle")
            provider_config_action = QAction("", self)
            self.bind_text(provider_config_action, "provider_config")
            provider_config_action.triggered.connect(self.open_provider_config)
            config_menu.addAction(provider_config_action)

        def _build_ui(self) -> None:
            QApplication.instance().setStyle("Fusion")
            self.setStyleSheet(APP_STYLESHEET)

            central = QWidget()
            root = QVBoxLayout(central)
            root.setContentsMargins(18, 16, 18, 14)
            root.setSpacing(14)
            self.setCentralWidget(central)

            root.addWidget(self._build_header())

            root.addWidget(self._build_top_tabs(), 1)

            root.addWidget(self._build_status_bar())

        def _build_header(self) -> QWidget:
            header = QWidget()
            layout = QHBoxLayout(header)
            layout.setContentsMargins(2, 0, 2, 0)

            title_box = QVBoxLayout()
            title = QLabel()
            title.setObjectName("Title")
            self.bind_text(title, "app_title")
            subtitle = QLabel()
            subtitle.setObjectName("Subtitle")
            self.bind_text(subtitle, "theme_hint")
            title_box.addWidget(title)
            title_box.addWidget(subtitle)
            layout.addLayout(title_box, 1)

            language_label = QLabel()
            self.bind_text(language_label, "language")
            layout.addWidget(language_label)

            self.language_combo = QComboBox()
            self.language_combo.setFixedWidth(160)
            self.language_combo.currentIndexChanged.connect(self._on_language_changed)
            layout.addWidget(self.language_combo)
            return header

        def _build_sidebar(self) -> QWidget:
            scroll = QScrollArea()
            scroll.setFixedWidth(430)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

            sidebar = QWidget()
            sidebar.setFixedWidth(406)
            sidebar.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Maximum)
            layout = QVBoxLayout(sidebar)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(12)

            layout.addWidget(self._build_project_panel())
            layout.addWidget(self._build_action_panel())
            layout.addStretch(1)
            scroll.setWidget(sidebar)
            return scroll

        def _build_top_tabs(self) -> QWidget:
            self.top_tabs = QTabWidget()
            self.top_tabs.setObjectName("TopTabs")
            workflow_page = QWidget()
            workflow_layout = QHBoxLayout(workflow_page)
            workflow_layout.setContentsMargins(0, 0, 0, 0)
            workflow_layout.setSpacing(14)
            workflow_layout.addWidget(self._build_sidebar(), 0)
            workflow_layout.addWidget(self._build_tabs(), 1)

            settings_page = self._build_settings_tabs()
            usage_page = self._build_token_usage_tab()
            log_page = self._build_log_tab()

            self.top_tabs.addTab(workflow_page, "")
            self.top_tabs.addTab(settings_page, "")
            self.top_tabs.addTab(usage_page, "")
            self.top_tabs.addTab(log_page, "")
            self.bind_tab(self.top_tabs, 0, "workflow_main")
            self.bind_tab(self.top_tabs, 1, "settings_center")
            self.bind_tab(self.top_tabs, 2, "token_usage")
            self.bind_tab(self.top_tabs, 3, "log")
            return self.top_tabs

        def _surface(self, title_key: str) -> Tuple[QFrame, QVBoxLayout]:
            frame = QFrame()
            frame.setObjectName("Surface")
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(18, 16, 18, 18)
            layout.setSpacing(14)
            title = QLabel()
            title.setObjectName("SectionTitle")
            self.bind_text(title, title_key)
            layout.addWidget(title)
            return frame, layout

        def _flow_page(self, content: QWidget) -> QWidget:
            scroll = QScrollArea()
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

            page = QWidget()
            outer = QVBoxLayout(page)
            outer.setContentsMargins(28, 24, 28, 24)
            outer.setSpacing(16)
            content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            outer.addWidget(content)
            outer.addStretch(1)
            scroll.setWidget(page)
            return scroll

        def _path_row(self, layout: QGridLayout, row: int, label_key: str, browse_key: str) -> QLineEdit:
            label = QLabel()
            self.bind_text(label, label_key)
            edit = QLineEdit()
            edit.setMinimumHeight(34)
            edit.setMinimumWidth(0)
            edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button = QPushButton()
            button.setProperty("quiet", True)
            button.setFixedWidth(70)
            self.bind_text(button, "browse")
            if browse_key == "choose_game_dir":
                button.clicked.connect(lambda: self._browse_directory(edit, browse_key, is_game=True))
            else:
                button.clicked.connect(lambda: self._browse_directory(edit, browse_key, is_game=False))
            layout.addWidget(label, row, 0)
            layout.addWidget(edit, row, 1)
            layout.addWidget(button, row, 2)
            return edit

        def _build_project_panel(self) -> QWidget:
            frame, layout = self._surface("project")
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(8)
            grid.setColumnStretch(1, 1)
            grid.setColumnMinimumWidth(0, 72)
            grid.setColumnMinimumWidth(2, 70)
            layout.addLayout(grid)

            existing_label = QLabel()
            self.bind_text(existing_label, "existing_workspace")
            self.workspace_combo = QComboBox()
            self.workspace_combo.setMinimumHeight(34)
            self.workspace_combo.setMinimumWidth(0)
            self.workspace_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.workspace_combo.currentIndexChanged.connect(self._on_workspace_combo_changed)
            refresh_button = QPushButton()
            refresh_button.setProperty("quiet", True)
            refresh_button.setFixedWidth(70)
            self.bind_text(refresh_button, "refresh")
            refresh_button.clicked.connect(self.refresh_workspace_list)
            grid.addWidget(existing_label, 0, 0)
            grid.addWidget(self.workspace_combo, 0, 1)
            grid.addWidget(refresh_button, 0, 2)

            self.game_dir_edit = self._path_row(grid, 1, "game_dir", "choose_game_dir")
            self.workspace_edit = self._path_row(grid, 2, "workspace", "choose_workspace")
            self.output_dir_edit = self._path_row(grid, 3, "output_dir", "choose_output_dir")
            return frame

        def _build_action_panel(self) -> QWidget:
            frame, layout = self._surface("actions")
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(8)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            layout.addLayout(grid)

            actions = [
                ("read_text_terms", self.read_text_and_terms, True),
                ("translate_terms", self.translate_terms, True),
                ("translate_batch", self.translate_batch, True),
                ("translate_all", self.translate_all_entries, True),
                ("apply", self.apply_translation, True),
                ("load_workspace", self.load_workspace, False),
            ]
            for index, (key, handler, accent) in enumerate(actions):
                button = QPushButton()
                self.bind_text(button, key)
                button.setProperty("accent", accent)
                button.clicked.connect(handler)
                grid.addWidget(button, index // 2, index % 2)
            self.stop_button = QPushButton()
            self.stop_button.setProperty("quiet", True)
            self.bind_text(self.stop_button, "stop_translation")
            self.stop_button.clicked.connect(self.stop_current_task)
            self.stop_button.setEnabled(False)
            grid.addWidget(self.stop_button, 3, 0, 1, 2)
            return frame

        def _build_settings_panel(self) -> QWidget:
            frame, layout = self._surface("translation_settings")
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(12)
            grid.setColumnStretch(1, 1)
            grid.setColumnMinimumWidth(0, 96)
            layout.addLayout(grid)

            config_label = QLabel()
            self.bind_text(config_label, "config")
            self.config_edit = QLineEdit(
                str(self.gui_settings.get("provider_config_path") or self._default_provider_config_path())
            )
            self.config_edit.setMinimumWidth(0)
            self.config_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.config_edit.editingFinished.connect(self._on_config_path_changed)
            config_button = QPushButton()
            config_button.setProperty("quiet", True)
            config_button.setFixedWidth(64)
            self.bind_text(config_button, "browse")
            config_button.clicked.connect(self._browse_config)
            visual_config_button = QPushButton()
            visual_config_button.setProperty("quiet", True)
            visual_config_button.setFixedWidth(86)
            self.bind_text(visual_config_button, "open_config_editor")
            visual_config_button.clicked.connect(self.open_provider_config)
            grid.addWidget(config_label, 0, 0)
            grid.addWidget(self.config_edit, 0, 1)
            grid.addWidget(config_button, 0, 2)
            grid.addWidget(visual_config_button, 0, 3)

            provider_label = QLabel()
            self.bind_text(provider_label, "provider")
            self.provider_combo = QComboBox()
            self.provider_combo.setEditable(True)
            self.provider_combo.setMinimumWidth(0)
            self.provider_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
            if self.provider_combo.lineEdit() is not None:
                self.provider_combo.lineEdit().editingFinished.connect(self._save_gui_settings)
            self._refresh_provider_combo(str(self.gui_settings.get("selected_provider") or "openai"))
            grid.addWidget(provider_label, 1, 0)
            grid.addWidget(self.provider_combo, 1, 1, 1, 3)

            target_label = QLabel()
            self.bind_text(target_label, "target")
            self.target_combo = QComboBox()
            self.target_combo.setEditable(True)
            self.target_combo.setMinimumWidth(0)
            self.target_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.target_combo.addItems(["zh-Hans", "zh-Hant", "en", "ko"])
            grid.addWidget(target_label, 2, 0)
            grid.addWidget(self.target_combo, 2, 1, 1, 3)

            batch_label = QLabel()
            self.bind_text(batch_label, "batch")
            self.batch_limit_spin = QSpinBox()
            self.batch_limit_spin.setMinimumWidth(0)
            self.batch_limit_spin.setMaximumWidth(180)
            self.batch_limit_spin.setRange(1, 500)
            self.batch_limit_spin.setValue(60)
            grid.addWidget(batch_label, 3, 0)
            grid.addWidget(self.batch_limit_spin, 3, 1, 1, 3)

            concurrency_label = QLabel()
            self.bind_text(concurrency_label, "concurrency")
            self.concurrency_spin = QSpinBox()
            self.concurrency_spin.setMinimumWidth(0)
            self.concurrency_spin.setMaximumWidth(180)
            self.concurrency_spin.setRange(1, 32)
            self.concurrency_spin.setValue(int(self.gui_settings.get("translation_concurrency") or 2))
            self.concurrency_spin.valueChanged.connect(self._save_gui_settings)
            grid.addWidget(concurrency_label, 4, 0)
            grid.addWidget(self.concurrency_spin, 4, 1, 1, 3)

            self.retranslate_button = QCheckBox()
            self.retranslate_button.setChecked(bool(self.gui_settings.get("retranslate_existing") or False))
            self.bind_text(self.retranslate_button, "retranslate_existing")
            self.retranslate_button.toggled.connect(self._save_gui_settings)
            grid.addWidget(self.retranslate_button, 5, 1, 1, 3)

            self.overwrite_button = QPushButton()
            self.overwrite_button.setCheckable(True)
            self.overwrite_button.setProperty("quiet", True)
            self.overwrite_button.setMaximumWidth(360)
            self.bind_text(self.overwrite_button, "overwrite")
            grid.addWidget(self.overwrite_button, 6, 0, 1, 4)

            return frame

        def _build_terminology_settings_panel(self) -> QWidget:
            frame, layout = self._surface("term_extraction_config")
            term_grid = QGridLayout()
            term_grid.setContentsMargins(0, 0, 0, 0)
            term_grid.setColumnStretch(1, 1)
            term_grid.setHorizontalSpacing(14)
            term_grid.setVerticalSpacing(12)
            term_grid.setColumnMinimumWidth(0, 160)
            layout.addLayout(term_grid)
            min_label = QLabel()
            self.bind_text(min_label, "term_min_count")
            self.term_min_spin = QSpinBox()
            self.term_min_spin.setRange(1, 20)
            self.term_min_spin.setValue(2)
            self.term_min_spin.setMinimumWidth(0)
            self.term_min_spin.setMaximumWidth(180)
            self.term_min_spin.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            limit_label = QLabel()
            self.bind_text(limit_label, "candidate_limit")
            self.term_limit_spin = QSpinBox()
            self.term_limit_spin.setRange(10, 2000)
            self.term_limit_spin.setValue(200)
            self.term_limit_spin.setMinimumWidth(0)
            self.term_limit_spin.setMaximumWidth(180)
            self.term_limit_spin.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            term_grid.addWidget(min_label, 0, 0)
            term_grid.addWidget(self.term_min_spin, 0, 1)
            term_grid.addWidget(limit_label, 1, 0)
            term_grid.addWidget(self.term_limit_spin, 1, 1)
            return frame

        def _build_prompt_settings_panel(self) -> QWidget:
            frame, layout = self._surface("prompt_settings")
            self.translation_prompt_edit = QPlainTextEdit()
            self.term_prompt_edit = QPlainTextEdit()
            self.translation_prompt_edit.setMinimumHeight(220)
            self.term_prompt_edit.setMinimumHeight(180)
            self.translation_prompt_edit.setMinimumWidth(0)
            self.term_prompt_edit.setMinimumWidth(0)
            self.translation_prompt_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.term_prompt_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

            translation_label = QLabel()
            self.bind_text(translation_label, "translation_prompt")
            term_label = QLabel()
            self.bind_text(term_label, "term_prompt")
            layout.addWidget(translation_label)
            layout.addWidget(self.translation_prompt_edit, 1)
            layout.addWidget(term_label)
            layout.addWidget(self.term_prompt_edit, 1)

            buttons = QHBoxLayout()
            save_button = QPushButton()
            reset_button = QPushButton()
            save_button.setProperty("accent", True)
            reset_button.setProperty("quiet", True)
            self.bind_text(save_button, "save_prompts")
            self.bind_text(reset_button, "reset_prompts")
            save_button.clicked.connect(self.save_prompt_settings)
            reset_button.clicked.connect(self.reset_prompt_settings)
            buttons.addStretch(1)
            buttons.addWidget(reset_button)
            buttons.addWidget(save_button)
            layout.addLayout(buttons)
            self.load_prompt_settings()
            return frame

        def _build_settings_tabs(self) -> QWidget:
            tabs = QTabWidget()
            tabs.setObjectName("SettingsTabs")
            tabs.addTab(self._flow_page(self._build_settings_panel()), "")
            tabs.addTab(self._flow_page(self._build_terminology_settings_panel()), "")
            tabs.addTab(self._flow_page(self._build_prompt_settings_panel()), "")
            self.bind_tab(tabs, 0, "global_config")
            self.bind_tab(tabs, 1, "term_extraction_config")
            self.bind_tab(tabs, 2, "prompt_settings")
            return tabs

        def _build_tabs(self) -> QWidget:
            self.tabs = QTabWidget()
            self.entries_tab = self._build_entries_tab()
            self.glossary_tab = self._build_glossary_tab()
            self.tools_tab = self._build_tools_tab()

            self.tabs.addTab(self.entries_tab, "")
            self.tabs.addTab(self.glossary_tab, "")
            self.tabs.addTab(self.tools_tab, "")
            self.bind_tab(self.tabs, 0, "entries")
            self.bind_tab(self.tabs, 1, "glossary")
            self.bind_tab(self.tabs, 2, "open_source_tools")
            return self.tabs

        def _build_token_usage_tab(self) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(14, 14, 14, 14)
            layout.setSpacing(12)

            header_row = QHBoxLayout()
            header = QLabel()
            header.setObjectName("SectionTitle")
            self.bind_text(header, "token_usage")
            refresh_button = QPushButton()
            refresh_button.setProperty("quiet", True)
            self.bind_text(refresh_button, "refresh")
            refresh_button.clicked.connect(lambda: self.refresh_token_usage(True))
            header_row.addWidget(header, 1)
            header_row.addWidget(refresh_button)
            layout.addLayout(header_row)

            self.usage_run_label = QLabel()
            self.usage_run_label.setObjectName("Subtitle")
            self.usage_run_label.setWordWrap(True)
            self.usage_log_label = QLabel()
            self.usage_log_label.setObjectName("Subtitle")
            self.usage_log_label.setWordWrap(True)
            layout.addWidget(self.usage_run_label)
            layout.addWidget(self.usage_log_label)

            summary = QFrame()
            summary.setObjectName("Surface")
            summary_layout = QGridLayout(summary)
            summary_layout.setContentsMargins(14, 12, 14, 12)
            summary_layout.setHorizontalSpacing(18)
            summary_layout.setVerticalSpacing(8)
            self.usage_stat_labels: Dict[str, QLabel] = {}
            stats = [
                ("total", "token_usage_total"),
                ("prompt", "token_usage_prompt"),
                ("completion", "token_usage_completion"),
                ("requests", "token_usage_requests"),
                ("average", "token_usage_average"),
                ("cache", "token_usage_cache"),
                ("items", "token_usage_items"),
                ("state", "token_usage_state"),
            ]
            for index, (name, key) in enumerate(stats):
                self._add_usage_stat(summary_layout, index // 4, index % 4, name, key)
            layout.addWidget(summary)

            self.usage_chart = TokenUsageChart()
            layout.addWidget(self.usage_chart, 1)

            table_label = QLabel()
            table_label.setObjectName("SectionTitle")
            self.bind_text(table_label, "token_usage_latest_requests")
            layout.addWidget(table_label)

            self.usage_table = QTableWidget(0, 8)
            self.usage_table.setAlternatingRowColors(True)
            self.usage_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.usage_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.usage_table.verticalHeader().setVisible(False)
            self.usage_table.horizontalHeader().setStretchLastSection(True)
            self.usage_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
            self.usage_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
            self.bind_headers(
                self.usage_table,
                [
                    "token_usage_request",
                    "token_usage_total",
                    "token_usage_prompt",
                    "token_usage_completion",
                    "token_usage_cache_hit",
                    "token_usage_elapsed",
                    "token_usage_items",
                    "token_usage_changed",
                ],
            )
            layout.addWidget(self.usage_table, 1)
            self._clear_token_usage()
            return page

        def _add_usage_stat(
            self,
            layout: QGridLayout,
            row: int,
            column: int,
            name: str,
            key: str,
        ) -> None:
            box = QVBoxLayout()
            label = QLabel()
            label.setObjectName("Subtitle")
            self.bind_text(label, key)
            value = QLabel("0")
            value.setObjectName("SectionTitle")
            value.setWordWrap(True)
            box.addWidget(label)
            box.addWidget(value)
            layout.addLayout(box, row, column)
            self.usage_stat_labels[name] = value

        def _build_log_tab(self) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(14, 14, 14, 14)
            layout.setSpacing(10)
            header = QLabel()
            header.setObjectName("SectionTitle")
            self.bind_text(header, "log")
            self.log_edit = QPlainTextEdit()
            self.log_edit.setReadOnly(True)
            self.log_edit.setMinimumHeight(360)
            layout.addWidget(header)
            layout.addWidget(self.log_edit, 1)
            return page

        def _build_entries_tab(self) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(14, 14, 14, 14)
            layout.setSpacing(10)

            top = QHBoxLayout()
            search_label = QLabel()
            self.bind_text(search_label, "search")
            self.entry_search_edit = QLineEdit()
            self.entry_search_edit.textChanged.connect(self.refresh_entries)
            save_button = QPushButton()
            self.bind_text(save_button, "save")
            save_button.clicked.connect(self.save_current_entry)
            refresh_button = QPushButton()
            self.bind_text(refresh_button, "refresh")
            refresh_button.clicked.connect(self.load_workspace)
            top.addWidget(search_label)
            top.addWidget(self.entry_search_edit, 1)
            top.addWidget(save_button)
            top.addWidget(refresh_button)
            layout.addLayout(top)

            splitter = QSplitter(Qt.Horizontal)
            layout.addWidget(splitter, 1)

            self.entries_table = QTableWidget(0, 4)
            self.entries_table.setAlternatingRowColors(True)
            self.entries_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.entries_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.entries_table.verticalHeader().setVisible(False)
            self.entries_table.horizontalHeader().setStretchLastSection(True)
            self.entries_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            self.bind_headers(self.entries_table, ["status", "source", "translation", "file"])
            self.entries_table.itemSelectionChanged.connect(self._on_entry_selected)
            splitter.addWidget(self.entries_table)

            detail = QFrame()
            detail.setObjectName("Surface")
            detail_layout = QVBoxLayout(detail)
            detail_layout.setContentsMargins(14, 12, 14, 14)
            detail_label = QLabel()
            detail_label.setObjectName("SectionTitle")
            self.bind_text(detail_label, "entry_detail")
            detail_layout.addWidget(detail_label)

            source_label = QLabel()
            self.bind_text(source_label, "source_text")
            self.entry_source_edit = QPlainTextEdit()
            self.entry_source_edit.setReadOnly(True)
            self.entry_translation_edit = QPlainTextEdit()
            translation_label = QLabel()
            self.bind_text(translation_label, "translated_text")
            self.entry_status_combo = QComboBox()
            self.entry_status_combo.addItems(["new", "translated", "reviewed", "locked", "rejected"])
            detail_layout.addWidget(source_label)
            detail_layout.addWidget(self.entry_source_edit, 1)
            detail_layout.addWidget(translation_label)
            detail_layout.addWidget(self.entry_translation_edit, 1)
            detail_layout.addWidget(self.entry_status_combo)
            splitter.addWidget(detail)
            splitter.setSizes([760, 360])
            return page

        def _build_glossary_tab(self) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(14, 14, 14, 14)
            layout.setSpacing(10)

            top = QHBoxLayout()
            search_label = QLabel()
            self.bind_text(search_label, "search")
            self.term_search_edit = QLineEdit()
            self.term_search_edit.textChanged.connect(self.refresh_glossary)
            save_button = QPushButton()
            self.bind_text(save_button, "save")
            save_button.clicked.connect(self.save_current_term)
            save_all_button = QPushButton()
            self.bind_text(save_all_button, "save_all")
            save_all_button.clicked.connect(self.save_glossary_file)
            top.addWidget(search_label)
            top.addWidget(self.term_search_edit, 1)
            top.addWidget(save_button)
            top.addWidget(save_all_button)
            layout.addLayout(top)

            batch = QHBoxLayout()
            batch.setSpacing(8)
            batch_label = QLabel()
            self.bind_text(batch_label, "batch_terms")
            approve_selected = QPushButton()
            approve_all_translated = QPushButton()
            reject_selected = QPushButton()
            pending_selected = QPushButton()
            clear_selected = QPushButton()
            approve_all_translated.setProperty("accent", True)
            for button, key, handler in (
                (approve_selected, "approve_selected", self.approve_selected_terms),
                (approve_all_translated, "approve_all_translated", self.approve_all_translated_terms),
                (reject_selected, "reject_selected", self.reject_selected_terms),
                (pending_selected, "pending_selected", self.pending_selected_terms),
                (clear_selected, "clear_selected_terms", self.clear_selected_term_targets),
            ):
                self.bind_text(button, key)
                button.clicked.connect(handler)
            batch.addWidget(batch_label)
            batch.addWidget(approve_selected)
            batch.addWidget(approve_all_translated)
            batch.addWidget(reject_selected)
            batch.addWidget(pending_selected)
            batch.addWidget(clear_selected)
            batch.addStretch(1)
            layout.addLayout(batch)

            splitter = QSplitter(Qt.Horizontal)
            layout.addWidget(splitter, 1)

            self.glossary_table = QTableWidget(0, 4)
            self.glossary_table.setAlternatingRowColors(True)
            self.glossary_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.glossary_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.glossary_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.glossary_table.verticalHeader().setVisible(False)
            self.glossary_table.horizontalHeader().setStretchLastSection(True)
            self.glossary_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            self.bind_headers(self.glossary_table, ["term_source", "term_target", "status", "count"])
            self.glossary_table.itemSelectionChanged.connect(self._on_term_selected)
            splitter.addWidget(self.glossary_table)

            detail = QFrame()
            detail.setObjectName("Surface")
            detail_layout = QVBoxLayout(detail)
            detail_layout.setContentsMargins(14, 12, 14, 14)
            detail_label = QLabel()
            detail_label.setObjectName("SectionTitle")
            self.bind_text(detail_label, "term_detail")
            self.term_source_edit = QLineEdit()
            self.term_source_edit.setReadOnly(True)
            self.term_target_edit = QLineEdit()
            self.term_status_combo = QComboBox()
            self.term_status_combo.addItems(["pending", "approved", "rejected"])
            self.term_note_edit = QPlainTextEdit()

            for key, widget in (
                ("term_source", self.term_source_edit),
                ("term_target", self.term_target_edit),
                ("status", self.term_status_combo),
                ("note", self.term_note_edit),
            ):
                label = QLabel()
                self.bind_text(label, key)
                detail_layout.addWidget(label)
                detail_layout.addWidget(widget)

            buttons = QHBoxLayout()
            approve = QPushButton()
            reject = QPushButton()
            pending = QPushButton()
            self.bind_text(approve, "approve")
            self.bind_text(reject, "reject")
            self.bind_text(pending, "pending")
            approve.clicked.connect(lambda: self._set_term_status("approved"))
            reject.clicked.connect(lambda: self._set_term_status("rejected"))
            pending.clicked.connect(lambda: self._set_term_status("pending"))
            buttons.addWidget(approve)
            buttons.addWidget(reject)
            buttons.addWidget(pending)
            detail_layout.insertWidget(0, detail_label)
            detail_layout.addLayout(buttons)
            splitter.addWidget(detail)
            splitter.setSizes([720, 360])
            return page

        def _build_tools_tab(self) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(14, 14, 14, 14)
            layout.setSpacing(10)
            self.tools_table = QTableWidget(0, 4)
            self.tools_table.setAlternatingRowColors(True)
            self.tools_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.tools_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.tools_table.verticalHeader().setVisible(False)
            self.tools_table.horizontalHeader().setStretchLastSection(True)
            self.tools_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            self.bind_headers(self.tools_table, ["tool", "engines", "role", "license"])
            open_button = QPushButton()
            open_button.setProperty("accent", True)
            self.bind_text(open_button, "open_homepage")
            open_button.clicked.connect(self.open_selected_tool)
            layout.addWidget(self.tools_table, 1)
            layout.addWidget(open_button, 0, Qt.AlignRight)
            return page

        def _build_status_bar(self) -> QWidget:
            bar = QWidget()
            layout = QHBoxLayout(bar)
            layout.setContentsMargins(2, 0, 2, 0)
            self.status_label = QLabel()
            self.bind_text(self.status_label, "ready")
            self.progress = QProgressBar()
            self.progress.setFixedWidth(190)
            self.progress.setTextVisible(False)
            self.progress.hide()
            layout.addWidget(self.status_label, 1)
            layout.addWidget(self.progress)
            return bar

        def _on_language_changed(self) -> None:
            data = self.language_combo.currentData()
            if data and data != self.language:
                self.language = data
                self.apply_language()

        def _browse_directory(self, target: QLineEdit, title_key: str, is_game: bool = False) -> None:
            initial = target.text().strip() if target.text().strip() else str(Path.cwd())
            value = QFileDialog.getExistingDirectory(self, self.tr(title_key), initial)
            if not value:
                return
            target.setText(value)
            if is_game:
                game_path = Path(value)
                if not self.workspace_edit.text().strip():
                    self.workspace_edit.setText(str(Path.cwd() / "work" / game_path.name))
                if not self.output_dir_edit.text().strip():
                    self.output_dir_edit.setText(str(game_path.with_name(game_path.name + "_zh")))

        def _workspace_root(self) -> Path:
            return Path.cwd() / "work"

        def _default_provider_config_path(self) -> Path:
            return Path("configs") / "providers.example.json"

        def _gui_settings_path(self) -> Path:
            return Path.cwd() / "configs" / "gui_settings.json"

        def _load_gui_settings(self) -> Dict[str, object]:
            path = self._gui_settings_path()
            if not path.exists():
                return {}
            try:
                with path.open("r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                return loaded if isinstance(loaded, dict) else {}
            except Exception:
                return {}

        def _save_gui_settings(self, *_args: object) -> None:
            if not hasattr(self, "config_edit") or not hasattr(self, "provider_combo"):
                return
            data = dict(self.gui_settings)
            data["provider_config_path"] = self.config_edit.text().strip() or str(self._default_provider_config_path())
            data["selected_provider"] = self.provider_combo.currentText().strip()
            if hasattr(self, "concurrency_spin"):
                data["translation_concurrency"] = int(self.concurrency_spin.value())
            if hasattr(self, "retranslate_button"):
                data["retranslate_existing"] = bool(self.retranslate_button.isChecked())
            path = self._gui_settings_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            self.gui_settings = data

        def _provider_names_from_config(self) -> List[str]:
            if not hasattr(self, "config_edit"):
                return []
            config_text = self.config_edit.text().strip() or str(self._default_provider_config_path())
            config_path = Path(config_text)
            try:
                with config_path.open("r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
            except Exception:
                return []
            if not isinstance(loaded, dict):
                return []
            providers = loaded.get("providers")
            if not isinstance(providers, dict):
                return []
            return sorted(str(name) for name in providers.keys())

        def _refresh_provider_combo(self, selected: str = "") -> None:
            if not hasattr(self, "provider_combo"):
                return
            desired = selected or self.provider_combo.currentText().strip() or str(
                self.gui_settings.get("selected_provider") or "openai"
            )
            names = self._provider_names_from_config()
            self.provider_combo.blockSignals(True)
            self.provider_combo.clear()
            self.provider_combo.addItems(names)
            if desired:
                index = self.provider_combo.findText(desired, Qt.MatchFixedString)
                if index >= 0:
                    self.provider_combo.setCurrentIndex(index)
                else:
                    self.provider_combo.setEditText(desired)
            elif names:
                self.provider_combo.setCurrentIndex(0)
            self.provider_combo.blockSignals(False)

        def provider_name(self) -> str:
            if hasattr(self, "provider_combo"):
                return self.provider_combo.currentText().strip()
            return ""

        def _on_provider_changed(self, *_args: object) -> None:
            self._save_gui_settings()

        def _on_config_path_changed(self) -> None:
            self._refresh_provider_combo()
            self._save_gui_settings()

        def _prompt_config_path(self) -> Path:
            return Path.cwd() / "configs" / "prompts.json"

        def load_prompt_settings(self) -> None:
            path = self._prompt_config_path()
            data = {}
            if path.exists():
                try:
                    with path.open("r", encoding="utf-8") as handle:
                        loaded = json.load(handle)
                    if isinstance(loaded, dict):
                        data = loaded
                except Exception:
                    data = {}
            if hasattr(self, "translation_prompt_edit"):
                self.translation_prompt_edit.setPlainText(str(data.get("translation_prompt", SYSTEM_PROMPT)))
            if hasattr(self, "term_prompt_edit"):
                self.term_prompt_edit.setPlainText(str(data.get("term_prompt", TERM_SYSTEM_PROMPT)))

        def save_prompt_settings(self) -> None:
            path = self._prompt_config_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "translation_prompt": self.translation_prompt(),
                "term_prompt": self.term_prompt(),
            }
            with path.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            self.log(self.tr("prompts_saved").format(path))

        def reset_prompt_settings(self) -> None:
            if hasattr(self, "translation_prompt_edit"):
                self.translation_prompt_edit.setPlainText(SYSTEM_PROMPT)
            if hasattr(self, "term_prompt_edit"):
                self.term_prompt_edit.setPlainText(TERM_SYSTEM_PROMPT)

        def translation_prompt(self) -> str:
            if hasattr(self, "translation_prompt_edit"):
                value = self.translation_prompt_edit.toPlainText().strip()
                if value:
                    return value
            return SYSTEM_PROMPT

        def term_prompt(self) -> str:
            if hasattr(self, "term_prompt_edit"):
                value = self.term_prompt_edit.toPlainText().strip()
                if value:
                    return value
            return TERM_SYSTEM_PROMPT

        def refresh_workspace_list(self) -> None:
            current = self.workspace_edit.text().strip() if hasattr(self, "workspace_edit") else ""
            self.workspace_summaries = discover_workspaces(self._workspace_root())
            self._populate_workspace_combo(current)

        def _populate_workspace_combo(self, selected_path: str = "") -> None:
            if not hasattr(self, "workspace_combo"):
                return
            self.workspace_combo.blockSignals(True)
            self.workspace_combo.clear()
            placeholder = self.tr("select_workspace") if self.workspace_summaries else self.tr("workspace_list_empty")
            self.workspace_combo.addItem(placeholder, "")

            selected_index = 0
            selected_norm = self._normalize_path_text(selected_path)
            for summary in self.workspace_summaries:
                workspace_text = str(summary.path)
                self.workspace_combo.addItem(self._workspace_label(summary), workspace_text)
                if selected_norm and self._normalize_path_text(workspace_text) == selected_norm:
                    selected_index = self.workspace_combo.count() - 1

            self.workspace_combo.setCurrentIndex(selected_index)
            self.workspace_combo.blockSignals(False)

        def _workspace_label(self, summary: WorkspaceSummary) -> str:
            manifest = summary.manifest
            name = summary.path.name
            engine = manifest.engine_name or manifest.adapter_id
            return "%s  |  %s  |  %s" % (name, engine, manifest.entry_count)

        def _normalize_path_text(self, value: str) -> str:
            if not value:
                return ""
            try:
                return str(Path(value).resolve()).lower()
            except Exception:
                return str(Path(value)).lower()

        def _on_workspace_combo_changed(self) -> None:
            if not hasattr(self, "workspace_combo"):
                return
            workspace_text = self.workspace_combo.currentData()
            if not workspace_text:
                return
            self.open_workspace(Path(str(workspace_text)))

        def open_workspace(self, workspace: Path) -> None:
            self.workspace_edit.setText(str(workspace))
            try:
                manifest = load_manifest(workspace)
                if manifest.source_game_dir:
                    self.game_dir_edit.setText(manifest.source_game_dir)
                    self._set_default_output_dir(manifest.source_game_dir)
            except Exception:
                pass
            self.load_workspace(show_errors=True)

        def _set_default_output_dir(self, game_dir: str) -> None:
            if self.output_dir_edit.text().strip():
                return
            try:
                game_path = Path(game_dir)
                self.output_dir_edit.setText(str(game_path.with_name(game_path.name + "_zh")))
            except Exception:
                pass

        def _browse_config(self) -> None:
            value, _selected = QFileDialog.getOpenFileName(
                self,
                self.tr("choose_provider_config"),
                self.config_edit.text().strip() or str(Path.cwd()),
                "JSON (*.json);;All files (*.*)",
            )
            if value:
                self.config_edit.setText(value)
                self._on_config_path_changed()

        def open_provider_config(self) -> None:
            config_text = self.config_edit.text().strip() or str(self._default_provider_config_path())
            config_path = Path(config_text)
            dialog = ProviderConfigDialog(config_path, self.tr, self)
            if dialog.exec() != QDialog.Accepted:
                return
            self.config_edit.setText(str(config_path))
            selected_provider = dialog.selected_provider()
            if selected_provider:
                self._refresh_provider_combo(selected_provider)
            else:
                self._refresh_provider_combo()
            self._save_gui_settings()
            self.log(self.tr("saved_config").format(config_path))

        def _require_path(self, edit: QLineEdit, key: str) -> Path:
            value = edit.text().strip()
            if not value:
                raise ValueError(self.tr("please_choose").format(self.tr(key)))
            return Path(value)

        def _workspace_path(self) -> Path:
            return self._require_path(self.workspace_edit, "workspace")

        def refresh_token_usage(self, show_errors: bool = True) -> None:
            if not hasattr(self, "usage_chart"):
                return
            try:
                workspace = self._workspace_path()
            except Exception as exc:
                self._clear_token_usage(str(exc))
                return
            try:
                run = latest_token_usage_run(workspace)
                log_path = token_usage_log_path(workspace)
                if run is None:
                    self._clear_token_usage(self.tr("token_usage_no_data"), log_path)
                    return
                self._set_token_usage_run(run, log_path)
            except Exception as exc:
                self._clear_token_usage(str(exc))
                if show_errors:
                    QMessageBox.warning(self, self.tr("token_usage"), str(exc))

        def _clear_token_usage(self, message: str = "", log_path: Optional[Path] = None) -> None:
            if not hasattr(self, "usage_chart"):
                return
            text = message or self.tr("token_usage_no_data")
            self.usage_run_label.setText(text)
            if log_path is not None:
                self.usage_log_label.setText("%s: %s" % (self.tr("token_usage_log_file"), log_path))
            else:
                self.usage_log_label.setText("")
            self.usage_chart.set_legend_labels(
                self.tr("token_usage_prompt"),
                self.tr("token_usage_completion"),
                self.tr("token_usage_failed"),
            )
            self.usage_chart.set_empty_text(text)
            self.usage_chart.set_records([])
            if hasattr(self, "usage_table"):
                self.usage_table.setRowCount(0)
            for label in getattr(self, "usage_stat_labels", {}).values():
                label.setText("0")

        def _set_token_usage_run(self, run: TokenUsageRun, log_path: Path) -> None:
            scope = self.tr("entries") if run.scope == "entries" else self.tr("glossary") if run.scope == "terms" else run.scope
            provider_text = run.provider or "-"
            model_text = run.model or "-"
            self.usage_run_label.setText(
                "%s: %s | %s: %s | %s"
                % (self.tr("token_usage_current_run"), run.run_id, provider_text, model_text, scope)
            )
            self.usage_log_label.setText("%s: %s" % (self.tr("token_usage_log_file"), log_path))
            self.usage_stat_labels["total"].setText(self._format_token_count(run.total_tokens))
            self.usage_stat_labels["prompt"].setText(self._format_token_count(run.prompt_tokens))
            self.usage_stat_labels["completion"].setText(self._format_token_count(run.completion_tokens))
            request_goal = run.request_count if run.request_count else "?"
            self.usage_stat_labels["requests"].setText("%s / %s" % (run.completed_requests, request_goal))
            self.usage_stat_labels["average"].setText(self._format_token_count(int(run.average_tokens_per_request)))
            self.usage_stat_labels["cache"].setText(
                "%s / %s (%s)"
                % (
                    self._format_token_count(run.prompt_cache_hit_tokens),
                    self._format_token_count(run.cache_total_tokens),
                    self._format_percent(run.prompt_cache_hit_rate),
                )
            )
            self.usage_stat_labels["items"].setText("%s / %s" % (run.changed, run.item_count))
            state_text = self.tr("token_usage_state_%s" % run.state)
            if state_text == "token_usage_state_%s" % run.state:
                state_text = run.state or "-"
            if run.failed_requests:
                state_text = "%s, %s %s" % (state_text, run.failed_requests, self.tr("token_usage_failed"))
            self.usage_stat_labels["state"].setText(state_text)
            self.usage_chart.set_legend_labels(
                self.tr("token_usage_prompt"),
                self.tr("token_usage_completion"),
                self.tr("token_usage_failed"),
            )
            self.usage_chart.set_empty_text(self.tr("token_usage_no_data"))
            self.usage_chart.set_records(run.requests)
            self._populate_token_usage_table(run)

        def _populate_token_usage_table(self, run: TokenUsageRun) -> None:
            records = sorted(run.requests, key=lambda item: int(item.get("request_index") or 0))
            visible_records = records[-80:]
            self.usage_table.setRowCount(0)
            for record in visible_records:
                row = self.usage_table.rowCount()
                self.usage_table.insertRow(row)
                request_index = int(record.get("request_index") or 0)
                request_count = int(record.get("request_count") or 0)
                cache_hit = int(record.get("prompt_cache_hit_tokens") or 0)
                values = [
                    "%s/%s" % (request_index, request_count or "?"),
                    self._format_token_count(int(record.get("total_tokens") or 0)),
                    self._format_token_count(int(record.get("prompt_tokens") or 0)),
                    self._format_token_count(int(record.get("completion_tokens") or 0)),
                    self._format_token_count(cache_hit),
                    self._format_seconds(float(record.get("elapsed_seconds") or 0.0)),
                    str(int(record.get("item_count") or record.get("input_items") or 0)),
                    str(int(record.get("changed") or 0)),
                ]
                if bool(record.get("failed")):
                    values[-1] = self.tr("failed_suffix").strip() or "failed"
                for column, value in enumerate(values):
                    self.usage_table.setItem(row, column, QTableWidgetItem(value))

        def _format_token_count(self, value: int) -> str:
            return "{:,}".format(int(value or 0))

        def _format_percent(self, value: float) -> str:
            return "%.1f%%" % (max(0.0, float(value or 0.0)) * 100)

        def _format_seconds(self, seconds: float) -> str:
            return "%.2fs" % max(0.0, float(seconds or 0.0))

        def log(self, message: str) -> None:
            if not message:
                return
            self.log_edit.appendPlainText(message)

        def _set_busy(self, busy: bool, message: str) -> None:
            self.busy = busy
            self.status_label.setText(message)
            if hasattr(self, "stop_button"):
                self.stop_button.setEnabled(busy and self.current_task_cancellable)
            self.progress.setVisible(busy)
            if busy:
                self.progress.setRange(0, 0)
                self.progress.setFormat("")
                self.progress.setTextVisible(False)
            else:
                self.progress.setRange(0, 1)
                self.progress.setValue(0)
                self.progress.setFormat("")
                self.progress.setTextVisible(False)

        def _run_task(
            self,
            title: str,
            task: Callable[..., object],
            on_success: Callable[[object], None],
            progress_enabled: bool = False,
            cancellable: bool = False,
        ) -> None:
            if self.busy:
                QMessageBox.warning(self, self.tr("busy"), self.tr("busy_message"))
                return
            self.log(title + self.tr("started_suffix"))
            self.current_task_cancellable = cancellable
            if progress_enabled:
                now = time.monotonic()
                self._last_entries_progress_refresh = now
                self._last_glossary_progress_refresh = now
                self._last_usage_progress_refresh = 0.0
                self._entries_progress_dirty = False
                self._glossary_progress_dirty = False
            worker = TaskThread(
                task,
                self,
                progress_enabled=progress_enabled,
                cancellation_enabled=cancellable,
            )
            self.current_task = worker
            self._set_busy(True, title + self.tr("running_suffix"))

            def success(result: object) -> None:
                self.current_task = None
                self.current_task_cancellable = False
                self._set_busy(False, title + self.tr("done_suffix"))
                if isinstance(result, str):
                    self.log(result)
                self.refresh_token_usage(show_errors=False)
                on_success(result)

            def failure(message: str, details: str) -> None:
                self.current_task = None
                self.current_task_cancellable = False
                self._set_busy(False, title + self.tr("failed_suffix"))
                self.log(details)
                self.refresh_token_usage(show_errors=False)
                QMessageBox.critical(self, title + self.tr("failed_suffix"), message)

            def progress(message: str) -> None:
                self.status_label.setText(message)
                self.log(message)
                self._update_progress_bar_from_message(message)
                self._refresh_views_after_progress(message)

            worker.succeeded.connect(success)
            worker.failed.connect(failure)
            worker.progress.connect(progress)
            worker.start()

        def _update_progress_bar_from_message(self, message: str) -> None:
            progress_counts = self._progress_counts_from_message(message)
            if progress_counts is None:
                return
            completed, total = progress_counts
            if total <= 0:
                self.progress.setRange(0, 1)
                self.progress.setValue(1)
                self.progress.setFormat("0/0")
                self.progress.setTextVisible(True)
                return
            completed = max(0, min(completed, total))
            self.progress.setRange(0, total)
            self.progress.setValue(completed)
            self.progress.setFormat("%v/%m")
            self.progress.setTextVisible(True)

        def _progress_counts_from_message(self, message: str) -> Optional[Tuple[int, int]]:
            pending_match = re.search(r"\bpending=(\d+)\b", message)
            if pending_match:
                return 0, int(pending_match.group(1))
            for pattern in (
                r"\bcompleted=(\d+)/(\d+)\b",
                r"\btotal=(\d+)/(\d+)\b",
                r"\btranslated=(\d+)/(\d+)\b",
            ):
                match = re.search(pattern, message)
                if match:
                    return int(match.group(1)), int(match.group(2))
            return None

        def _refresh_views_after_progress(self, message: str) -> None:
            if message.startswith("[entries]") or message.startswith("[terms]"):
                self._refresh_token_usage_after_progress()
            if message.startswith("[entries]") and " done:" in message:
                self._refresh_entries_after_progress()
            elif message.startswith("[terms]") and " done:" in message:
                self._refresh_glossary_after_progress()

        def _refresh_token_usage_after_progress(self) -> None:
            now = time.monotonic()
            if now - self._last_usage_progress_refresh < TOKEN_USAGE_REFRESH_INTERVAL_SECONDS:
                return
            self._last_usage_progress_refresh = now
            self.refresh_token_usage(show_errors=False)

        def _refresh_entries_after_progress(self) -> None:
            self._entries_progress_dirty = True
            if not self._should_refresh_progress_view("entries"):
                return
            try:
                self.entries = load_workspace_entries(self._workspace_path())
                self.refresh_entries()
                self._entries_progress_dirty = False
                self._last_entries_progress_refresh = time.monotonic()
            except Exception as exc:
                self.log("Failed to refresh entries after progress update: %s" % exc)

        def _refresh_glossary_after_progress(self) -> None:
            self._glossary_progress_dirty = True
            if not self._should_refresh_progress_view("glossary"):
                return
            try:
                self.glossary = load_glossary(self._workspace_path() / GLOSSARY_FILE)
                self.refresh_glossary()
                self._glossary_progress_dirty = False
                self._last_glossary_progress_refresh = time.monotonic()
            except Exception as exc:
                self.log("Failed to refresh glossary after progress update: %s" % exc)

        def _should_refresh_progress_view(self, scope: str) -> bool:
            now = time.monotonic()
            if scope == "entries":
                if now - self._last_entries_progress_refresh < PROGRESS_VIEW_REFRESH_INTERVAL_SECONDS:
                    return False
                if hasattr(self, "tabs") and hasattr(self, "entries_tab") and self.tabs.currentWidget() is not self.entries_tab:
                    return False
                return True
            if now - self._last_glossary_progress_refresh < PROGRESS_VIEW_REFRESH_INTERVAL_SECONDS:
                return False
            if hasattr(self, "tabs") and hasattr(self, "glossary_tab") and self.tabs.currentWidget() is not self.glossary_tab:
                return False
            return True

        def stop_current_task(self) -> None:
            if not self.current_task or not self.current_task_cancellable:
                return
            self.current_task.cancel()
            message = self.tr("stopping_translation")
            print(message, flush=True)
            self.status_label.setText(message)
            self.log(message)

        def detect_game(self) -> None:
            def task() -> str:
                result = detect_best(self._require_path(self.game_dir_edit, "game_dir"))
                if result is None:
                    return "No supported engine detected."
                return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)

            self._run_task(
                self.tr("detect"),
                task,
                lambda result: QMessageBox.information(self, self.tr("detect_result"), str(result)),
            )

        def extract_text(self) -> None:
            def task() -> str:
                game_dir = self._require_path(self.game_dir_edit, "game_dir")
                workspace = self._workspace_path()
                result = detect_best(game_dir)
                if result is None:
                    raise RuntimeError("No supported adapter detected.")
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
                return "Extracted %s entries into %s" % (len(bundle.entries), workspace)

            self._run_task(self.tr("extract"), task, lambda _result: self._after_workspace_created())

        def read_text_and_terms(self) -> None:
            def task() -> str:
                game_dir = self._require_path(self.game_dir_edit, "game_dir")
                workspace = self._workspace_path()
                result = detect_best(game_dir)
                if result is None:
                    raise RuntimeError("No supported adapter detected.")
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
                term_count = self._generate_terms_for_workspace(workspace)
                return self.tr("text_and_terms_ready").format(len(bundle.entries), term_count)

            self._run_task(self.tr("read_text_terms"), task, lambda _result: self._after_workspace_created())

        def _after_workspace_created(self) -> None:
            self.refresh_workspace_list()
            self.load_workspace(show_errors=False)

        def load_workspace(self, show_errors: bool = True) -> None:
            try:
                workspace = self._workspace_path()
                self.entries = load_workspace_entries(workspace)
                self.glossary = load_glossary(workspace / GLOSSARY_FILE)
                try:
                    manifest = load_manifest(workspace)
                    if manifest.source_game_dir:
                        self.game_dir_edit.setText(manifest.source_game_dir)
                        self._set_default_output_dir(manifest.source_game_dir)
                    self.log("%s (%s)" % (self.tr("workspace_loaded"), manifest.adapter_id))
                except FileNotFoundError:
                    self.log(self.tr("workspace_loaded"))
                self.refresh_entries()
                self.refresh_glossary()
                self.refresh_token_usage(show_errors=False)
                self._entries_progress_dirty = False
                self._glossary_progress_dirty = False
                now = time.monotonic()
                self._last_entries_progress_refresh = now
                self._last_glossary_progress_refresh = now
                self._populate_workspace_combo(str(workspace))
                self.status_label.setText(self.tr("workspace_loaded"))
            except Exception as exc:
                if show_errors:
                    QMessageBox.critical(self, self.tr("load_failed"), str(exc))

        def generate_terms(self) -> None:
            def task() -> str:
                count = self._generate_terms_for_workspace(self._workspace_path())
                return "Wrote %s terminology candidates." % count

            self._run_task(self.tr("build_terms"), task, lambda _result: self.load_workspace(show_errors=False))

        def _generate_terms_for_workspace(self, workspace: Path) -> int:
            entries = load_workspace_entries(workspace)
            candidates = extract_term_candidates(
                entries,
                min_count=int(self.term_min_spin.value()),
                limit=int(self.term_limit_spin.value()),
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
            return len(terms)

        def translate_terms(self) -> None:
            def task(report: Callable[[str], None], cancelled: Callable[[], bool]) -> str:
                changed = translate_glossary_terms(
                    workspace=self._workspace_path(),
                    config_path=self._require_path(self.config_edit, "config"),
                    provider_name=self.provider_name(),
                    target_language=self.target_combo.currentText().strip() or "zh-Hans",
                    system_prompt=self.term_prompt(),
                    logger=report,
                    concurrency=int(self.concurrency_spin.value()),
                    cancelled=cancelled,
                )
                return self.tr("terms_translated").format(changed)

            self._run_task(
                self.tr("translate_terms"),
                task,
                lambda _result: self.load_workspace(show_errors=False),
                progress_enabled=True,
                cancellable=True,
            )

        def translate_batch(self) -> None:
            def task(report: Callable[[str], None], cancelled: Callable[[], bool]) -> str:
                changed = translate_workspace(
                    workspace=self._workspace_path(),
                    config_path=self._require_path(self.config_edit, "config"),
                    provider_name=self.provider_name(),
                    target_language=self.target_combo.currentText().strip() or "zh-Hans",
                    limit=int(self.batch_limit_spin.value()),
                    system_prompt=self.translation_prompt(),
                    logger=report,
                    cancelled=cancelled,
                )
                return self.tr("entries_translated").format(changed)

            self._run_task(
                self.tr("translate_batch"),
                task,
                lambda _result: self.load_workspace(show_errors=False),
                progress_enabled=True,
                cancellable=True,
            )

        def translate_all_entries(self) -> None:
            def task(report: Callable[[str], None], cancelled: Callable[[], bool]) -> str:
                changed = translate_workspace_all(
                    workspace=self._workspace_path(),
                    config_path=self._require_path(self.config_edit, "config"),
                    provider_name=self.provider_name(),
                    target_language=self.target_combo.currentText().strip() or "zh-Hans",
                    batch_size=int(self.batch_limit_spin.value()),
                    system_prompt=self.translation_prompt(),
                    logger=report,
                    concurrency=int(self.concurrency_spin.value()),
                    include_translated=bool(self.retranslate_button.isChecked()),
                    cancelled=cancelled,
                )
                return self.tr("all_entries_translated").format(changed)

            self._run_task(
                self.tr("translate_all"),
                task,
                lambda _result: self.load_workspace(show_errors=False),
                progress_enabled=True,
                cancellable=True,
            )

        def apply_translation(self) -> None:
            def task(report: Callable[[str], None]) -> str:
                workspace = self._workspace_path()
                manifest = load_manifest(workspace)
                adapters = adapter_map()
                if manifest.adapter_id not in adapters:
                    raise RuntimeError("Adapter not available: %s" % manifest.adapter_id)
                entries = load_workspace_entries(workspace)
                adapters[manifest.adapter_id].apply(
                    self._require_path(self.game_dir_edit, "game_dir"),
                    workspace,
                    self._require_path(self.output_dir_edit, "output_dir"),
                    entries,
                    overwrite=self.overwrite_button.isChecked(),
                    progress=report,
                )
                translated = len([entry for entry in entries if entry.translation and entry.status != "rejected"])
                return "Applied %s translated entries." % translated

            self._run_task(
                self.tr("apply"),
                task,
                lambda result: QMessageBox.information(self, self.tr("done"), str(result)),
                progress_enabled=True,
            )

        def refresh_entries(self) -> None:
            if not hasattr(self, "entries_table"):
                return
            query = self.entry_search_edit.text().strip().lower() if hasattr(self, "entry_search_edit") else ""
            current_entry_id = None
            current_row = self.entries_table.currentRow()
            if current_row >= 0:
                current_item = self.entries_table.item(current_row, 0)
                if current_item is not None:
                    current_entry_id = str(current_item.data(Qt.UserRole) or "")
            scroll_value = self.entries_table.verticalScrollBar().value()
            rows = []
            selected_row = -1
            for entry in self.entries:
                haystack = "\n".join([entry.source, entry.translation or "", entry.file, entry.status]).lower()
                if query and query not in haystack:
                    continue
                if entry.id == current_entry_id:
                    selected_row = len(rows)
                rows.append(entry)

            self.entries_table.setUpdatesEnabled(False)
            self.entries_table.blockSignals(True)
            try:
                self.entries_table.setRowCount(len(rows))
                for row, entry in enumerate(rows):
                    values = [self._display_status(entry.status), entry.source, entry.translation or "", entry.file]
                    for column, value in enumerate(values):
                        item = QTableWidgetItem(self._one_line(value))
                        if column == 0:
                            item.setData(Qt.UserRole, entry.id)
                        self.entries_table.setItem(row, column, item)
                if selected_row >= 0:
                    self.entries_table.setCurrentCell(selected_row, 0)
                    self.entries_table.selectRow(selected_row)
                else:
                    self.entries_table.clearSelection()
            finally:
                self.entries_table.blockSignals(False)
                self.entries_table.setUpdatesEnabled(True)

            self.entries_table.verticalScrollBar().setValue(
                min(scroll_value, self.entries_table.verticalScrollBar().maximum())
            )
            if selected_row >= 0:
                self._on_entry_selected()

        def refresh_glossary(self) -> None:
            if not hasattr(self, "glossary_table"):
                return
            query = self.term_search_edit.text().strip().lower() if hasattr(self, "term_search_edit") else ""
            current_source = None
            current_row = self.glossary_table.currentRow()
            if current_row >= 0:
                current_item = self.glossary_table.item(current_row, 0)
                if current_item is not None:
                    current_source = str(current_item.data(Qt.UserRole) or "")
            scroll_value = self.glossary_table.verticalScrollBar().value()
            rows = []
            selected_row = -1
            for term in self.glossary:
                haystack = "\n".join([term.source, term.target, term.status, term.note]).lower()
                if query and query not in haystack:
                    continue
                if term.source == current_source:
                    selected_row = len(rows)
                rows.append(term)

            self.glossary_table.setUpdatesEnabled(False)
            self.glossary_table.blockSignals(True)
            try:
                self.glossary_table.setRowCount(len(rows))
                for row, term in enumerate(rows):
                    values = [term.source, term.target, self._display_status(term.status), str(term.count)]
                    for column, value in enumerate(values):
                        item = QTableWidgetItem(self._one_line(value))
                        if column == 0:
                            item.setData(Qt.UserRole, term.source)
                        self.glossary_table.setItem(row, column, item)
                if selected_row >= 0:
                    self.glossary_table.setCurrentCell(selected_row, 0)
                    self.glossary_table.selectRow(selected_row)
                else:
                    self.glossary_table.clearSelection()
            finally:
                self.glossary_table.blockSignals(False)
                self.glossary_table.setUpdatesEnabled(True)

            self.glossary_table.verticalScrollBar().setValue(
                min(scroll_value, self.glossary_table.verticalScrollBar().maximum())
            )
            if selected_row >= 0:
                self._on_term_selected()

        def _on_entry_selected(self) -> None:
            row = self.entries_table.currentRow()
            if row < 0:
                return
            id_item = self.entries_table.item(row, 0)
            if id_item is None:
                return
            entry = self._entry_by_id(str(id_item.data(Qt.UserRole)))
            if entry is None:
                return
            self.entry_source_edit.setPlainText(entry.source)
            self.entry_translation_edit.setPlainText(entry.translation or "")
            self.entry_status_combo.setCurrentText(entry.status)

        def _on_term_selected(self) -> None:
            row = self.glossary_table.currentRow()
            if row < 0:
                return
            source_item = self.glossary_table.item(row, 0)
            if source_item is None:
                return
            term = self._term_by_source(str(source_item.data(Qt.UserRole)))
            if term is None:
                return
            self.term_source_edit.setText(term.source)
            self.term_target_edit.setText(term.target)
            self.term_status_combo.setCurrentText(term.status)
            self.term_note_edit.setPlainText(term.note)

        def save_current_entry(self) -> None:
            row = self.entries_table.currentRow()
            if row < 0:
                return
            id_item = self.entries_table.item(row, 0)
            if id_item is None:
                return
            entry = self._entry_by_id(str(id_item.data(Qt.UserRole)))
            if entry is None:
                return
            entry.translation = self.entry_translation_edit.toPlainText().rstrip("\n")
            entry.status = self.entry_status_combo.currentText()
            save_workspace_entries(self._workspace_path(), self.entries)
            self.refresh_entries()
            self.log(self.tr("saved_entry").format(entry.id))

        def save_current_term(self) -> None:
            row = self.glossary_table.currentRow()
            if row < 0:
                return
            source_item = self.glossary_table.item(row, 0)
            if source_item is None:
                return
            term = self._term_by_source(str(source_item.data(Qt.UserRole)))
            if term is None:
                return
            term.target = self.term_target_edit.text().strip()
            term.status = self.term_status_combo.currentText()
            term.note = self.term_note_edit.toPlainText().rstrip("\n")
            self.save_glossary_file()

        def save_glossary_file(self) -> None:
            path = self._workspace_path() / GLOSSARY_FILE
            save_glossary(path, self.glossary)
            self.refresh_glossary()
            self.log(self.tr("saved_glossary").format(path))

        def _set_term_status(self, status: str) -> None:
            self.term_status_combo.setCurrentText(status)
            self.save_current_term()

        def _selected_term_sources(self) -> List[str]:
            if not hasattr(self, "glossary_table"):
                return []
            rows = sorted({index.row() for index in self.glossary_table.selectedIndexes()})
            if not rows and self.glossary_table.currentRow() >= 0:
                rows = [self.glossary_table.currentRow()]

            sources: List[str] = []
            for row in rows:
                source_item = self.glossary_table.item(row, 0)
                if source_item is None:
                    continue
                source = str(source_item.data(Qt.UserRole) or "")
                if source and source not in sources:
                    sources.append(source)
            return sources

        def _batch_update_selected_terms(
            self,
            status: str,
            clear_target: bool = False,
            require_target: bool = False,
        ) -> None:
            sources = self._selected_term_sources()
            if not sources:
                self.log(self.tr("no_terms_selected"))
                return

            changed = 0
            skipped = 0
            selected = set(sources)
            for term in self.glossary:
                if term.source not in selected:
                    continue
                if require_target and not term.target.strip():
                    skipped += 1
                    continue
                term.status = status
                if clear_target:
                    term.target = ""
                changed += 1

            self.save_glossary_file()
            self.log(self.tr("batch_terms_done").format(changed, skipped))

        def approve_selected_terms(self) -> None:
            self._batch_update_selected_terms("approved", require_target=True)

        def reject_selected_terms(self) -> None:
            self._batch_update_selected_terms("rejected", clear_target=True)

        def pending_selected_terms(self) -> None:
            self._batch_update_selected_terms("pending")

        def clear_selected_term_targets(self) -> None:
            self._batch_update_selected_terms("pending", clear_target=True)

        def approve_all_translated_terms(self) -> None:
            changed = 0
            for term in self.glossary:
                if not term.target.strip():
                    continue
                if term.status in ("approved", "rejected"):
                    continue
                term.status = "approved"
                changed += 1

            self.save_glossary_file()
            self.log(self.tr("batch_terms_all_approved").format(changed))

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

        def _load_tools(self) -> None:
            try:
                with package_resources.open_text("jp_game_translator.resources", "tool_catalog.json", encoding="utf-8") as handle:
                    data = json.load(handle)
                self.tools = list(data.get("tools", []))
                self.tools_table.setRowCount(0)
                for tool in self.tools:
                    row = self.tools_table.rowCount()
                    self.tools_table.insertRow(row)
                    values = [
                        tool.get("name", ""),
                        ", ".join(tool.get("engines", [])),
                        tool.get("role", ""),
                        tool.get("license", ""),
                    ]
                    for column, value in enumerate(values):
                        item = QTableWidgetItem(str(value))
                        if column == 0:
                            item.setData(Qt.UserRole, tool.get("url", ""))
                        self.tools_table.setItem(row, column, item)
            except Exception as exc:
                self.log(self.tr("tool_catalog_failed") + str(exc))

        def open_selected_tool(self) -> None:
            row = self.tools_table.currentRow()
            if row < 0:
                return
            item = self.tools_table.item(row, 0)
            if item is None:
                return
            url = item.data(Qt.UserRole)
            if url:
                QDesktopServices.openUrl(QUrl(str(url)))

        def _display_status(self, status: str) -> str:
            return self.tr(status) if status in self.translations.get("en", {}) else status

        def _one_line(self, value: str, limit: int = 140) -> str:
            text = (value or "").replace("\r", "\\r").replace("\n", "\\n")
            if len(text) > limit:
                return text[: limit - 1] + "..."
            return text


def main() -> int:
    if QT_IMPORT_ERROR is not None:
        print(_missing_qt_message(), file=sys.stderr)
        return 10
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    window = QtTranslatorWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
