"""PyQt5 + qfluentwidgets 图形界面（Fluent Design 风格）。

界面组成：
- 左侧导航：处理结果 / 统计分析 / 历史记录
- 「处理结果」：设置卡片（目录、匹配维度复选框、处理选项）+ 结果表格（含日志列）+ 运行日志
- 「统计分析」：统计卡片 + 状态分布柱状图 + 历史趋势图
- 「历史记录」：历次运行列表 + 单次运行明细
"""

from __future__ import annotations

import csv
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QColor, QDesktopServices, QFont, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    DoubleSpinBox,
    FluentIcon,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TableWidget,
    Theme,
    ToolButton,
    isDarkTheme,
    setTheme,
    setThemeColor,
)

from . import APP_NAME, __version__
from .history import build_run_record, clear_history, load_history, save_run
from .worker import (
    STATUS_LABELS,
    FixWorker,
    FileResult,
    RunOptions,
    ST_FAILED,
    ST_NO_COVER,
    ST_SKIPPED,
    ST_SUCCESS,
)

ACCENT = "#31C27C"  # QQ 音乐绿
STATUS_COLORS = {
    ST_SUCCESS: "#1a7f37",
    ST_NO_COVER: "#b36b00",
    ST_SKIPPED: "#6e7781",
    ST_FAILED: "#c62828",
}
STATUS_BG = {
    ST_SUCCESS: "#e6f4ea",
    ST_NO_COVER: "#fff4e0",
    ST_SKIPPED: "#f0f1f3",
    ST_FAILED: "#fdecea",
}


# ---------------------------------------------------------------------- #
# 支持拖拽目录的输入框
# ---------------------------------------------------------------------- #
class DirLineEdit(LineEdit):
    """可拖入文件夹路径的输入框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if urls:
            self.setText(urls[0].toLocalFile())


# ---------------------------------------------------------------------- #
# 简易柱状图控件（QPainter 绘制，无额外依赖，适配明暗主题）
# ---------------------------------------------------------------------- #
class BarChart(QWidget):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.title = title
        self.items: List[tuple] = []  # (label, value, color)
        self.setMinimumHeight(170)

    def set_data(self, items: List[tuple]) -> None:
        self.items = items
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        dark = isDarkTheme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        margin_left, margin_bottom, margin_top = 46, 30, 26
        text_color = QColor("#d8dee9") if dark else QColor("#30353d")
        sub_color = QColor("#9aa0a8") if dark else QColor("#6e7781")
        grid_color = QColor("#3a3f47") if dark else QColor("#e2e5e9")

        if self.title:
            font = QFont()
            font.setBold(True)
            font.setPointSize(10)
            painter.setFont(font)
            painter.setPen(text_color)
            painter.drawText(8, 0, w - 16, 20, Qt.AlignLeft, self.title)
            margin_top = 28

        if not self.items:
            painter.setPen(sub_color)
            painter.drawText(0, h // 2 - 10, w, 20, Qt.AlignCenter, "暂无数据")
            return

        max_v = max(v for _, v, _ in self.items) or 1
        plot_w = w - margin_left - 8
        plot_h = h - margin_top - margin_bottom
        n = len(self.items)
        slot = plot_w / n
        bar_w = min(slot * 0.55, 72)

        painter.setPen(QPen(grid_color, 1))
        for i in range(5):
            y = margin_top + plot_h * i / 4
            painter.drawLine(margin_left, int(y), w - 8, int(y))

        for i, (label, value, color) in enumerate(self.items):
            bar_h = plot_h * value / max_v
            x = margin_left + slot * i + (slot - bar_w) / 2
            y = margin_top + plot_h - bar_h
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(int(x), int(y), int(bar_w), int(bar_h), 4, 4)
            painter.setPen(text_color)
            painter.drawText(int(x), int(y) - 16, int(bar_w), 14, Qt.AlignCenter, str(value))
            painter.setPen(sub_color)
            painter.drawText(int(x - 6), margin_top + plot_h + 6, int(bar_w + 12), 18,
                             Qt.AlignCenter, label)


# ---------------------------------------------------------------------- #
# 统计卡片
# ---------------------------------------------------------------------- #
class StatCard(CardWidget):
    def __init__(self, label: str, color: str = "#30353d", parent=None):
        super().__init__(parent)
        self.setBorderRadius(10)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.value_label = StrongBodyLabel("0")
        f = QFont()
        f.setPointSize(20)
        f.setBold(True)
        self.value_label.setFont(f)
        self.value_label.setStyleSheet(f"color:{color};")
        self.value_label.setAlignment(Qt.AlignCenter)
        self.name_label = CaptionLabel(label)
        self.name_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.value_label)
        lay.addWidget(self.name_label)

    def set_value(self, value) -> None:
        self.value_label.setText(str(value))


# ---------------------------------------------------------------------- #
# 页面：处理结果
# ---------------------------------------------------------------------- #
class HomePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._result_rows: List[Dict[str, Any]] = []
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ---------------- 设置卡片 ---------------- #
        settings = CardWidget(self)
        settings.setBorderRadius(10)
        grid = QGridLayout(settings)
        grid.setContentsMargins(20, 16, 20, 16)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        grid.addWidget(StrongBodyLabel("源目录"), 0, 0)
        self.src_edit = DirLineEdit()
        self.src_edit.setPlaceholderText("选择存放 mp3 / flac / m4a / ogg 的目录")
        grid.addWidget(self.src_edit, 0, 1)
        btn_src = ToolButton(FluentIcon.FOLDER)
        btn_src.clicked.connect(lambda: self._pick_dir(self.src_edit))
        grid.addWidget(btn_src, 0, 2)

        grid.addWidget(StrongBodyLabel("目标目录"), 1, 0)
        self.dst_edit = DirLineEdit()
        self.dst_edit.setPlaceholderText("输出目录（不存在则自动创建）；留空则使用源目录")
        grid.addWidget(self.dst_edit, 1, 1)
        btn_dst = ToolButton(FluentIcon.FOLDER_ADD)
        btn_dst.clicked.connect(lambda: self._pick_dir(self.dst_edit))
        grid.addWidget(btn_dst, 1, 2)

        dim_box = QWidget()
        dim_lay = QHBoxLayout(dim_box)
        dim_lay.setContentsMargins(0, 4, 0, 0)
        dim_lay.addWidget(SubtitleLabel("匹配维度"))
        dim_lay.addSpacing(12)
        self.chk_match_title = CheckBox("歌名")
        self.chk_match_artist = CheckBox("歌手")
        self.chk_match_album = CheckBox("专辑")
        for c in (self.chk_match_title, self.chk_match_artist, self.chk_match_album):
            c.setChecked(True)
            dim_lay.addWidget(c)
        dim_lay.addWidget(CaptionLabel("（勾选用于检索与比对的字段）"))
        dim_lay.addStretch(1)
        grid.addWidget(dim_box, 2, 0, 1, 3)

        opt_row1 = QWidget()
        o1 = QHBoxLayout(opt_row1)
        o1.setContentsMargins(0, 0, 0, 0)
        self.chk_skip = CheckBox("跳过已有封面")
        self.chk_skip.setChecked(True)
        self.chk_fallback = CheckBox("无元信息时用文件名推断")
        self.chk_fallback.setChecked(True)
        self.chk_copy_fail = CheckBox("封面失败也复制文件")
        self.chk_copy_fail.setChecked(True)
        for c in (self.chk_skip, self.chk_fallback, self.chk_copy_fail):
            o1.addWidget(c)
        o1.addStretch(1)
        grid.addWidget(opt_row1, 3, 0, 1, 3)

        opt_row2 = QWidget()
        o2 = QHBoxLayout(opt_row2)
        o2.setContentsMargins(0, 0, 0, 0)
        o2.addWidget(CaptionLabel("封面尺寸"))
        self.cmb_size = ComboBox()
        self.cmb_size.addItem("500 × 500", userData=500)
        self.cmb_size.addItem("1000 × 1000", userData=1000)
        o2.addWidget(self.cmb_size)
        o2.addSpacing(18)
        o2.addWidget(CaptionLabel("请求间隔(秒)"))
        self.spin_interval = DoubleSpinBox()
        self.spin_interval.setRange(0.0, 10.0)
        self.spin_interval.setSingleStep(0.1)
        self.spin_interval.setValue(0.35)
        o2.addWidget(self.spin_interval)
        o2.addStretch(1)
        grid.addWidget(opt_row2, 4, 0, 1, 3)

        act_row = QWidget()
        a = QHBoxLayout(act_row)
        a.setContentsMargins(0, 4, 0, 0)
        self.btn_start = PrimaryPushButton("开始处理")
        self.btn_start.setMinimumWidth(130)
        self.btn_stop = PushButton("停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet(
            "PushButton { color: white; background: #e5484d; border-radius: 5px; }"
            "PushButton:hover { background: #cf3a3f; }"
            "PushButton:disabled { color: #b3b8c0; background: #f0f1f3; }"
        )
        self.progress = ProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFixedHeight(6)
        self.status_label = BodyLabel("就绪")
        self.status_label.setStyleSheet("color:#9aa0a8;")
        a.addWidget(self.btn_start)
        a.addWidget(self.btn_stop)
        a.addSpacing(8)
        a.addWidget(self.progress, 1)
        a.addSpacing(8)
        a.addWidget(self.status_label)
        grid.addWidget(act_row, 5, 0, 1, 3)

        root.addWidget(settings)

        # ---------------- 结果卡片 ---------------- #
        result_card = CardWidget(self)
        result_card.setBorderRadius(10)
        rlay = QVBoxLayout(result_card)
        rlay.setContentsMargins(16, 12, 16, 12)

        head = QHBoxLayout()
        self.count_label = StrongBodyLabel("处理结果（0 条）")
        head.addWidget(self.count_label)
        head.addStretch(1)
        btn_export = PushButton("导出 CSV")
        btn_export.setIcon(FluentIcon.SAVE_AS.icon())
        btn_export.clicked.connect(self._export_csv)
        head.addWidget(btn_export)
        rlay.addLayout(head)

        self.table = TableWidget(self)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setWordWrap(False)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["#", "文件", "状态", "匹配封面", "耗时(秒)", "日志"]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(self.table.NoEditTriggers)
        self.table.setSelectionBehavior(self.table.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_menu)
        rlay.addWidget(self.table, 1)
        root.addWidget(result_card, 1)

        # ---------------- 日志卡片 ---------------- #
        log_card = CardWidget(self)
        log_card.setBorderRadius(10)
        llay = QVBoxLayout(log_card)
        llay.setContentsMargins(16, 12, 16, 12)
        llay.addWidget(StrongBodyLabel("运行日志"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setStyleSheet(
            "QPlainTextEdit { background:#1e2228; color:#d8dee9;"
            " border:1px solid #2a2f37; border-radius:6px;"
            " font-family: Menlo, Consolas, monospace; }"
        )
        llay.addWidget(self.log_view)
        log_card.setFixedHeight(180)
        root.addWidget(log_card)

    def _pick_dir(self, edit: LineEdit) -> None:
        start = edit.text() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "选择目录", start)
        if path:
            edit.setText(path)

    # ---------------- 结果行 ---------------- #
    def reset_run(self) -> None:
        self.table.setRowCount(0)
        self._result_rows = []
        self.count_label.setText("处理结果（0 条）")
        self.progress.setValue(0)
        self.status_label.setText("就绪")

    def append_result(self, result: FileResult) -> None:
        self._result_rows.append({
            "file": result.rel_path,
            "status": result.status,
            "cover": result.cover_name,
            "duration": round(result.duration, 2),
            "log": result.log,
        })
        row = self.table.rowCount()
        self.table.insertRow(row)
        cells = [
            (str(row + 1), None),
            (result.rel_path, None),
            (STATUS_LABELS.get(result.status, result.status), STATUS_COLORS.get(result.status)),
            (result.cover_name or "—", None),
            (f"{result.duration:.2f}", None),
            (result.log or "—", None),
        ]
        for col, (text, color) in enumerate(cells):
            item = QTableWidgetItem(text)
            if color:
                item.setForeground(QColor(color))
            item.setToolTip(text)
            self.table.setItem(row, col, item)
        bg = STATUS_BG.get(result.status)
        if bg:
            for col in range(self.table.columnCount()):
                self.table.item(row, col).setBackground(QColor(bg))
        self.count_label.setText(f"处理结果（{len(self._result_rows)} 条）")

    def append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ---------------- 导出 CSV ---------------- #
    def _export_csv(self) -> None:
        if not self._result_rows:
            InfoBar.info("提示", "当前没有可导出的结果。", parent=self.window())
            return
        default = f"qq_cover_fixer_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "导出 CSV", default, "CSV 文件 (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh)
                writer.writerow(["文件", "状态", "匹配封面", "耗时(秒)", "日志"])
                for r in self._result_rows:
                    writer.writerow([
                        r["file"],
                        STATUS_LABELS.get(r["status"], r["status"]),
                        r["cover"],
                        r["duration"],
                        r["log"],
                    ])
            InfoBar.success("导出成功", f"已导出：{path}", parent=self.window())
        except Exception as exc:  # noqa: BLE001
            InfoBar.error("导出失败", str(exc), parent=self.window())

    # ---------------- 右键菜单 ---------------- #
    def _show_table_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        item = self.table.item(row, 1)
        if not item:
            return
        rel = item.text()
        src_root = self.src_edit.text().strip()
        menu = QMenu(self)
        act_open = menu.addAction("打开文件")
        act_reveal = menu.addAction("在文件夹中显示")
        act_copy = menu.addAction("复制路径")
        act = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if act is None:
            return
        src = str(Path(src_root) / rel) if src_root else rel
        if act == act_copy:
            QApplication.clipboard().setText(src)
        elif act == act_open:
            QDesktopServices.openUrl(QUrl.fromLocalFile(src))
        elif act == act_reveal:
            _reveal_in_folder(src)

    @property
    def options(self) -> Optional[RunOptions]:
        src = self.src_edit.text().strip()
        if not src:
            InfoBar.warning("提示", "请先选择源目录。", parent=self.window())
            return None
        if not Path(src).is_dir():
            InfoBar.warning("提示", f"源目录不存在：{src}", parent=self.window())
            return None
        dst = self.dst_edit.text().strip() or src
        return RunOptions(
            source_dir=src,
            target_dir=dst,
            skip_with_cover=self.chk_skip.isChecked(),
            use_filename_fallback=self.chk_fallback.isChecked(),
            copy_on_fail=self.chk_copy_fail.isChecked(),
            match_title=self.chk_match_title.isChecked(),
            match_artist=self.chk_match_artist.isChecked(),
            match_album=self.chk_match_album.isChecked(),
            cover_size=self.cmb_size.currentData(),
            request_interval=self.spin_interval.value(),
        )

    @property
    def result_rows(self) -> List[Dict[str, Any]]:
        return self._result_rows


# ---------------------------------------------------------------------- #
# 页面：统计分析
# ---------------------------------------------------------------------- #
class StatsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        cards = QHBoxLayout()
        self.card_total = StatCard("总文件数", "#30353d")
        self.card_success = StatCard("成功", STATUS_COLORS[ST_SUCCESS])
        self.card_no_cover = StatCard("已复制·无封面", STATUS_COLORS[ST_NO_COVER])
        self.card_skipped = StatCard("跳过·已有封面", STATUS_COLORS[ST_SKIPPED])
        self.card_failed = StatCard("失败", STATUS_COLORS[ST_FAILED])
        self.card_rate = StatCard("成功率", ACCENT)
        for c in (self.card_total, self.card_success, self.card_no_cover,
                  self.card_skipped, self.card_failed, self.card_rate):
            cards.addWidget(c, 1)
        root.addLayout(cards)

        charts = QHBoxLayout()
        chart_box1 = CardWidget(self)
        chart_box1.setBorderRadius(10)
        c1 = QVBoxLayout(chart_box1)
        c1.setContentsMargins(16, 12, 16, 12)
        self.chart_status = BarChart("本次运行状态分布")
        c1.addWidget(self.chart_status)

        chart_box2 = CardWidget(self)
        chart_box2.setBorderRadius(10)
        c2 = QVBoxLayout(chart_box2)
        c2.setContentsMargins(16, 12, 16, 12)
        self.chart_trend = BarChart("历史趋势（最近 10 次 · 成功数）")
        c2.addWidget(self.chart_trend)

        charts.addWidget(chart_box1, 1)
        charts.addWidget(chart_box2, 1)
        root.addLayout(charts, 1)

        info_box = CardWidget(self)
        info_box.setBorderRadius(10)
        info = QGridLayout(info_box)
        info.setContentsMargins(20, 14, 20, 14)
        info.setHorizontalSpacing(14)
        self.info_source = BodyLabel("—")
        self.info_target = BodyLabel("—")
        self.info_dims = BodyLabel("—")
        self.info_time = BodyLabel("—")
        for row, (name, w) in enumerate([
            ("源目录", self.info_source),
            ("目标目录", self.info_target),
            ("匹配维度", self.info_dims),
            ("开始时间", self.info_time),
        ]):
            info.addWidget(CaptionLabel(name), row, 0)
            info.addWidget(w, row, 1)
        root.addWidget(info_box)

    def update_stats(self, stats: Dict[str, int]) -> None:
        total = sum(stats.values())
        self.card_total.set_value(total)
        self.card_success.set_value(stats[ST_SUCCESS])
        self.card_no_cover.set_value(stats[ST_NO_COVER])
        self.card_skipped.set_value(stats[ST_SKIPPED])
        self.card_failed.set_value(stats[ST_FAILED])
        rate = (stats[ST_SUCCESS] / total * 100) if total else 0
        self.card_rate.set_value(f"{rate:.1f}%")
        self.chart_status.set_data([
            ("成功", stats[ST_SUCCESS], STATUS_COLORS[ST_SUCCESS]),
            ("无封面", stats[ST_NO_COVER], STATUS_COLORS[ST_NO_COVER]),
            ("跳过", stats[ST_SKIPPED], STATUS_COLORS[ST_SKIPPED]),
            ("失败", stats[ST_FAILED], STATUS_COLORS[ST_FAILED]),
        ])

    def update_trend(self) -> None:
        runs = load_history()[:10]
        runs.reverse()
        items = []
        for r in runs:
            label = (r.get("timestamp") or "")[5:16]
            items.append((label, r.get("success", 0), ACCENT))
        self.chart_trend.set_data(items)


# ---------------------------------------------------------------------- #
# 页面：历史记录
# ---------------------------------------------------------------------- #
class HistoryPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(SubtitleLabel("历次运行"))
        head.addStretch(1)
        btn_refresh = PushButton("刷新")
        btn_refresh.setIcon(FluentIcon.SYNC.icon())
        btn_refresh.clicked.connect(self.refresh)
        btn_open = PushButton("打开历史文件")
        btn_open.setIcon(FluentIcon.DOCUMENT.icon())
        btn_open.clicked.connect(self._open_history_file)
        btn_clear = PushButton("清空历史")
        btn_clear.setStyleSheet(
            "PushButton { color: white; background: #e5484d; border-radius: 5px; }"
            "PushButton:hover { background: #cf3a3f; }"
        )
        btn_clear.clicked.connect(self._clear_history)
        head.addWidget(btn_refresh)
        head.addWidget(btn_open)
        head.addWidget(btn_clear)
        root.addLayout(head)

        splitter = QSplitter(Qt.Vertical)
        self.history_table = TableWidget(self)
        self.history_table.setBorderVisible(True)
        self.history_table.setBorderRadius(8)
        self.history_table.setWordWrap(False)
        self.history_table.setColumnCount(9)
        self.history_table.setHorizontalHeaderLabels(
            ["时间", "源目录", "目标目录", "总数", "成功", "无封面", "跳过", "失败", "耗时(s)"]
        )
        hh = self.history_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        for i in range(3, 9):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.history_table.setSelectionBehavior(self.history_table.SelectRows)
        self.history_table.setEditTriggers(self.history_table.NoEditTriggers)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.itemSelectionChanged.connect(self._show_detail)
        splitter.addWidget(self.history_table)

        detail_card = CardWidget(self)
        detail_card.setBorderRadius(10)
        dlay = QVBoxLayout(detail_card)
        dlay.setContentsMargins(16, 12, 16, 12)
        dlay.addWidget(StrongBodyLabel("选中运行的明细"))
        self.detail_table = TableWidget(self)
        self.detail_table.setBorderVisible(True)
        self.detail_table.setBorderRadius(8)
        self.detail_table.setWordWrap(False)
        self.detail_table.setColumnCount(3)
        self.detail_table.setHorizontalHeaderLabels(["文件", "状态", "日志"])
        dh = self.detail_table.horizontalHeader()
        dh.setSectionResizeMode(0, QHeaderView.Stretch)
        dh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        dh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.detail_table.setEditTriggers(self.detail_table.NoEditTriggers)
        self.detail_table.verticalHeader().setVisible(False)
        dlay.addWidget(self.detail_table, 1)
        splitter.addWidget(detail_card)
        splitter.setSizes([260, 240])
        root.addWidget(splitter, 1)

        self.refresh()

    def refresh(self) -> None:
        runs = load_history()
        self.history_table.setRowCount(0)
        for run in runs:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            values = [
                run.get("timestamp", ""),
                run.get("source", ""),
                run.get("target", ""),
                run.get("total", 0),
                run.get("success", 0),
                run.get("no_cover", 0),
                run.get("skipped", 0),
                run.get("failed", 0),
                f"{run.get('elapsed', 0):.1f}",
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                item.setToolTip(str(text))
                if col == 4 and int(run.get("success", 0)) == 0 \
                        and int(run.get("total", 0)) > 0:
                    item.setForeground(QColor(STATUS_COLORS[ST_FAILED]))
                self.history_table.setItem(row, col, item)

    def _show_detail(self) -> None:
        rows = self.history_table.selectionModel().selectedRows()
        if not rows:
            return
        run = load_history()[rows[0].row()]
        files = run.get("files", [])
        self.detail_table.setRowCount(0)
        for f in files:
            row = self.detail_table.rowCount()
            self.detail_table.insertRow(row)
            status = f.get("status", "")
            label = STATUS_LABELS.get(status, status)
            items = [
                QTableWidgetItem(f.get("file", "")),
                QTableWidgetItem(label),
                QTableWidgetItem(f.get("log", "") or "—"),
            ]
            color = STATUS_COLORS.get(status)
            if color:
                items[1].setForeground(QColor(color))
            for col, it in enumerate(items):
                it.setToolTip(it.text())
                self.detail_table.setItem(row, col, it)

    def _open_history_file(self) -> None:
        from .history import HISTORY_FILE
        if HISTORY_FILE.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(HISTORY_FILE)))
        else:
            InfoBar.info("提示", "暂无历史记录文件。", parent=self.window())

    def _clear_history(self) -> None:
        box = MessageBox("清空历史", "确定要清空全部历史记录吗？", self.window())
        if box.exec():
            clear_history()
            self.refresh()
            self.detail_table.setRowCount(0)
            InfoBar.success("已清空", "历史记录已清空。", parent=self.window())


# ---------------------------------------------------------------------- #
# 主窗口
# ---------------------------------------------------------------------- #
class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.resize(1180, 820)
        self.worker: Optional[FixWorker] = None
        self._stats = {ST_SUCCESS: 0, ST_NO_COVER: 0, ST_SKIPPED: 0, ST_FAILED: 0}
        self._total_files = 0
        self._run_started_ts = 0.0
        self._last_source = ""
        self._last_target = ""

        self.home = HomePage(self)
        self.stats = StatsPage(self)
        self.history = HistoryPage(self)
        self.home.setObjectName("homePage")
        self.stats.setObjectName("statsPage")
        self.history.setObjectName("historyPage")

        self.addSubInterface(self.home, FluentIcon.MUSIC, "处理结果")
        self.addSubInterface(self.stats, FluentIcon.PIE_SINGLE, "统计分析")
        self.addSubInterface(self.history, FluentIcon.HISTORY, "历史记录")

        self.home.btn_start.clicked.connect(self._on_start)
        self.home.btn_stop.clicked.connect(self._on_stop)
        self.stats.update_trend()

    # ---------------- 启动 / 停止 ---------------- #
    def _on_start(self) -> None:
        options = self.home.options
        if options is None:
            return
        if not (options.match_title or options.match_artist or options.match_album):
            InfoBar.warning("提示", "请至少勾选一个匹配维度（歌名/歌手/专辑）。", self)
            return

        self._reset_run(options)

        self.worker = FixWorker(options)
        self.worker.file_done.connect(self._on_file_done)
        self.worker.progress.connect(self._on_progress)
        self.worker.log.connect(self.home.append_log)
        self.worker.run_finished.connect(self._on_run_finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.finished.connect(self._on_worker_finished)

        self.home.btn_start.setEnabled(False)
        self.home.btn_stop.setEnabled(True)
        self.home.status_label.setText("正在处理…")
        self.home.append_log("=" * 70)
        self.home.append_log(f"开始处理：{options.source_dir} → {options.target_dir}")
        self.worker.start()

    def _on_stop(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.home.append_log("已发送停止请求…")

    def _reset_run(self, options: RunOptions) -> None:
        self._stats = {ST_SUCCESS: 0, ST_NO_COVER: 0, ST_SKIPPED: 0, ST_FAILED: 0}
        self._total_files = 0
        self._run_started_ts = time.time()
        self._last_source = options.source_dir
        self._last_target = options.target_dir
        self.home.reset_run()
        self.stats.update_stats(self._stats)

        dims = []
        if options.match_title:
            dims.append("歌名")
        if options.match_artist:
            dims.append("歌手")
        if options.match_album:
            dims.append("专辑")
        self.stats.info_source.setText(options.source_dir)
        self.stats.info_target.setText(options.target_dir)
        self.stats.info_dims.setText("、".join(dims))
        self.stats.info_time.setText(
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._run_started_ts))
        )

    # ---------------- 信号槽 ---------------- #
    def _on_worker_finished(self) -> None:
        """线程结束后清空引用，避免访问已删除对象。"""
        self.worker = None

    def _on_file_done(self, result: FileResult) -> None:
        self.home.append_result(result)
        self._stats[result.status] += 1
        self.stats.update_stats(self._stats)

    def _on_progress(self, done: int, total: int) -> None:
        self._total_files = total
        self.home.progress.setRange(0, total)
        self.home.progress.setValue(done)
        self.home.status_label.setText(f"处理中 {done} / {total}")

    def _on_run_finished(self, summary: dict) -> None:
        elapsed = summary.get("elapsed", 0.0)
        stopped = summary.get("stopped", False)
        self.home.btn_start.setEnabled(True)
        self.home.btn_stop.setEnabled(False)
        self.home.status_label.setText("已停止" if stopped else "处理完成")
        self.stats.update_stats(self._stats)

        run = build_run_record(
            source_dir=self._last_source,
            target_dir=self._last_target,
            files=self.home.result_rows,
            stats=self._stats,
            elapsed=elapsed,
            stopped=stopped,
        )
        save_run(run)
        self.history.refresh()
        self.stats.update_trend()

        total = self._total_files or len(self.home.result_rows)
        msg = (
            f"共 {total} 个文件：成功 {self._stats[ST_SUCCESS]}，"
            f"无封面 {self._stats[ST_NO_COVER]}，跳过 {self._stats[ST_SKIPPED]}，"
            f"失败 {self._stats[ST_FAILED]}，耗时 {elapsed:.1f}s"
        )
        self.home.append_log(msg)
        if stopped:
            InfoBar.warning("已停止", msg, parent=self, position=InfoBarPosition.TOP)
        elif self._stats[ST_FAILED] == 0 and self._stats[ST_SUCCESS] > 0:
            InfoBar.success("处理完成", msg, parent=self, position=InfoBarPosition.TOP)
        else:
            InfoBar.info("处理完成", msg, parent=self, position=InfoBarPosition.TOP)

    # ---------------- 退出 ---------------- #
    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker is not None and self.worker.isRunning():
            box = MessageBox("退出", "正在处理中，确定要退出吗？", self)
            if not box.exec():
                event.ignore()
                return
            self.worker.stop()
            self.worker.wait(3000)
        event.accept()


def _reveal_in_folder(path: str) -> None:
    """在系统文件管理器中定位文件（macOS / Windows / Linux）。"""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        elif os.name == "nt":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))
    except Exception:  # noqa: BLE001
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))


def run_app() -> int:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    setTheme(Theme.AUTO)
    setThemeColor(QColor(ACCENT))
    win = MainWindow()
    win.show()
    return app.exec_()
