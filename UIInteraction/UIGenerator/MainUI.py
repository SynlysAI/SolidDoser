from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QFont
from typing import Optional

from Common.ActionLogger import get_action_logger

_APP_STYLE = """
QMainWindow {
    background: #f8fafc;
}
QTabWidget::pane {
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    background: #f8fafc;
    top: -1px;
}
QTabBar::tab {
                        font-size: 14px;
    padding: 8px 20px;
    margin-right: 4px;
    border: 1px solid #e2e8f0;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    background: #eef2f7;
    color: #475569;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #0f172a;
    font-weight: 600;
}
QPushButton#PrimaryAction {
    font-size: 12px;
    padding: 2px 10px;
    min-height: 30px;
    max-height: 30px;
    border: 1px solid #2563eb;
    border-radius: 5px;
    background: #2563eb;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#PrimaryAction:hover {
    background: #1d4ed8;
}
QPushButton#ProcessBtn {
    font-size: 12px;
    padding: 2px 10px;
    min-height: 30px;
    max-height: 30px;
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    background: #ffffff;
    color: #334155;
}
QPushButton#ProcessBtn:hover {
    background: #f1f5f9;
    border-color: #94a3b8;
}
QLabel#SectionTitle {
    font-size: 15px;
    font-weight: 600;
    color: #1e293b;
}
QFrame#Divider {
    color: #e2e8f0;
}
                    QProgressBar {
    border: 1px solid #cbd5e1;
    border-radius: 6px;
                        text-align: center;
    background: #ffffff;
    min-height: 22px;
    font-size: 12px;
                    }
                    QProgressBar::chunk {
    background: #2563eb;
                        border-radius: 5px;
}
QTableWidget {
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    gridline-color: #eef2f7;
    background: #ffffff;
    font-size: 12px;
}
QTableWidget::item {
    padding: 6px;
}
QTableWidget::item:selected {
    background: #dbeafe;
    color: #1e3a8a;
}
QHeaderView::section {
    background: #f8fafc;
    color: #64748b;
    font-weight: 600;
    font-size: 12px;
    padding: 8px;
    border: none;
    border-bottom: 1px solid #e2e8f0;
}
QLabel#ActionLog {
    font-size: 12px;
    color: #334155;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 4px;
    padding: 6px 10px;
}
"""


class MainUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.solid_doser_auto_tab_widget = None
        self.solid_doser_motion_debug_tab_widget = None
        self.label_log: Optional[QLabel] = None
        self.setMinimumSize(1100, 720)
        self.resize(1180, 800)
        self.init_ui()

    def closeEvent(self, event: QCloseEvent):
        debug = self.solid_doser_motion_debug_tab_widget
        if debug is not None:
            debug.shutdown_di_poll()
        super().closeEvent(event)
        get_action_logger().persist()

    def set_action_log(self, text: str) -> None:
        """三个页签共用的底部日志（自动 / 调试 / 流程导入）。"""
        if self.label_log is None:
            return
        msg = (text or "").strip() or "—"
        if not msg.startswith("日志"):
            msg = f"日志：{msg}"
        self.label_log.setText(msg)

    def init_ui(self):
        self.setStyleSheet(_APP_STYLE)
        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(8)
        tabs = QTabWidget()

        # 自动 → 调试 → 流程导入
        tab_titles = ["自动", "调试", "流程导入"]
        for idx, title in enumerate(tab_titles):
            tab = QWidget()
            layout = QVBoxLayout(tab)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(10)
            if idx == 0:
                from UIInteraction.UIGenerator.SolidDoserAutoTabWidget import (
                    SolidDoserAutoTabWidget,
                )

                self.solid_doser_auto_tab_widget = SolidDoserAutoTabWidget()
                layout.addWidget(self.solid_doser_auto_tab_widget)
            elif idx == 1:
                from UIInteraction.UIGenerator.SolidDoserMotionDebugTabWidget import (
                    SolidDoserMotionDebugTabWidget,
                )

                self.solid_doser_motion_debug_tab_widget = SolidDoserMotionDebugTabWidget()
                layout.addWidget(self.solid_doser_motion_debug_tab_widget)
            elif idx == 2:
                self._build_process_tab(layout)
            tabs.addTab(tab, title)

        root_layout.addWidget(tabs, 1)

        self.label_log = QLabel("日志：—")
        self.label_log.setObjectName("ActionLog")
        self.label_log.setWordWrap(True)
        self.label_log.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root_layout.addWidget(self.label_log)

        self.setCentralWidget(central)

    def _build_process_tab(self, layout: QVBoxLayout) -> None:
        title = QLabel("工艺流程")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        self.btn_import_process = QPushButton("导入工艺文件")
        self.btn_import_process.setObjectName("ProcessBtn")
        self.btn_execute_process = QPushButton("执行工艺流程")
        self.btn_execute_process.setObjectName("PrimaryAction")
        button_layout.addWidget(self.btn_import_process)
        button_layout.addWidget(self.btn_execute_process)
        button_layout.addStretch(1)
        layout.addLayout(button_layout)

        separator = QFrame()
        separator.setObjectName("Divider")
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameStyle(QFrame.HLine | QFrame.Sunken)
        layout.addWidget(separator)

        self.table_process = QTableWidget(0, 2)
        self.table_process.setHorizontalHeaderLabels(["函数名", "参数"])
        self.table_process.setColumnWidth(0, 180)
        self.table_process.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_process.verticalHeader().setDefaultSectionSize(32)
        self.table_process.verticalHeader().setVisible(False)
        self.table_process.setFont(QFont("Microsoft YaHei UI", 10))
        self.table_process.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_process.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_process.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table_process.setAlternatingRowColors(True)
        layout.addWidget(self.table_process, 1)

        progress_layout = QHBoxLayout()
        progress_layout.setSpacing(12)
        self.label_progress = QLabel("执行进度: 0/0")
        self.label_progress.setStyleSheet("font-size: 12px; color: #475569;")
        self.progress_bar = QProgressBar()
        self.label_time = QLabel("总耗时: 00:00:00")
        self.label_time.setStyleSheet("font-size: 12px; color: #64748b;")
        progress_layout.addWidget(self.label_progress)
        progress_layout.addWidget(self.progress_bar, 1)
        progress_layout.addWidget(self.label_time)
        layout.addLayout(progress_layout)

    def bind_solid_doser_motion_debug(self, param_storage) -> None:
        if self.solid_doser_motion_debug_tab_widget is not None:
            self.solid_doser_motion_debug_tab_widget.bind_parameter_storage(param_storage)
            self.solid_doser_motion_debug_tab_widget.set_action_log_handler(
                self.set_action_log
            )

    def bind_solid_doser(self, param_storage) -> None:
        """绑定 SolidDoser 自动页 + 调试页到同一 ParameterStorage。"""
        self.bind_solid_doser_motion_debug(param_storage)
        if self.solid_doser_auto_tab_widget is not None:
            self.solid_doser_auto_tab_widget.bind_parameter_storage(param_storage)
            self.solid_doser_auto_tab_widget.set_action_log_handler(self.set_action_log)
