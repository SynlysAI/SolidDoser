"""SolidDoser 自动运行界面（系统状态机入口）。"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QModelIndex, QObject, Qt, QRunnable, QThread, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from BusinessActions.SolidDoserSystem.system_actions import (
    system_request_abort,
    system_run_bottle_discharge,
    system_run_device_init,
    system_run_dose,
    system_run_go_datum,
    system_run_go_origin,
    system_run_initialize,
    system_run_servo_on_all,
)
from BusinessActions.SolidDoserSystem.system_context import SolidDoserSystemContext
from Common.DoseRecordsStore import DoseRecord, load_dose_records
from Common.MaterialDoseTotalsStore import (
    MaterialDoseTotal,
    load_material_dose_totals,
)
from Common.MotionSettingsStore import save_lift_work_mm
from Drivers.SartoriusBalance.balance_driver import get_balance_driver
from Drivers.SartoriusBalance.balance_protocol import BalanceReading
from Drivers.SolidDoserMotion import motion_config as motion_cfg
from UIInteraction.ParameterManagement.ParameterStorage import ParameterStorage
from UIInteraction.ParameterManagement.SolidDoserSystemModel import DoseItem, SystemRunState

ActionFn = Callable[[SolidDoserSystemContext], Tuple[bool, str]]

# 与调试页轴按钮一致的高度/字号；宽度兼顾「试剂瓶出料」
_AUTO_BTN_MIN_WIDTH = 96
_AUTO_BTN_SPACING = 6
_LIST_ROW_HEIGHT = 28
_DOSE_WEIGHT_COL_WIDTH = 88
# 中部两表等高：8 工位 + 表头
_MID_TABLE_HEIGHT = _LIST_ROW_HEIGHT * 8 + 30
_RECORDS_LOAD_LIMIT = 200

_STYLESHEET = """
SolidDoserAutoTabWidget {
    background: #eef2f7;
}
QFrame#Card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
}
QFrame#HeroCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 #ffffff, stop:1 #f8fafc);
    border: 1px solid #dbe3ef;
    border-radius: 10px;
}
QLabel#SectionTitle {
    font-size: 13px;
    font-weight: 600;
    color: #0f172a;
}
QLabel#Hint {
    font-size: 11px;
    color: #94a3b8;
}
QLabel#DoseHeroCaption {
    font-size: 13px;
    font-weight: 600;
    color: #64748b;
}
QLabel#DoseHero {
    font-size: 42px;
    font-weight: 800;
    color: #0f172a;
    letter-spacing: -1px;
}
QLabel#DoseHeroUnit {
    font-size: 18px;
    font-weight: 700;
    color: #334155;
    padding-bottom: 6px;
}
QLabel#DoseHeroMetaKey {
    font-size: 11px;
    color: #94a3b8;
}
QLabel#DoseHeroMetaVal {
    font-size: 15px;
    font-weight: 600;
    color: #1e293b;
}
QLabel#MetaKey {
    font-size: 11px;
    color: #94a3b8;
}
QLabel#MetaValue {
    font-size: 13px;
    font-weight: 600;
    color: #0f172a;
}
QLabel#BalanceWeight {
    font-size: 13px;
    font-weight: 600;
    color: #0f172a;
}
QLabel#LogValue {
    font-size: 12px;
    color: #64748b;
}
QLabel#Field {
    font-size: 12px;
    color: #64748b;
}
QFrame#MetaDivider {
    background: #e2e8f0;
    max-width: 1px;
    min-width: 1px;
}
QLineEdit {
    font-size: 12px;
    padding: 2px 6px;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    background: #ffffff;
    min-height: 28px;
    max-height: 28px;
}
QLineEdit:focus {
    border: 1px solid #3b82f6;
}
QLineEdit:disabled {
    background: #f8fafc;
    color: #94a3b8;
}
QDoubleSpinBox {
    font-size: 12px;
    min-height: 28px;
    max-height: 28px;
    padding: 0px 4px;
}
QTableWidget {
    font-size: 12px;
    gridline-color: #eef2f7;
    border: 1px solid #e2e8f0;
    border-radius: 4px;
    background: #ffffff;
    outline: none;
}
QTableWidget::item {
    padding: 0px 6px;
}
QTableWidget::item:selected {
    background: #e0edff;
    color: #0f172a;
}
QTableWidget QLineEdit {
    min-height: 0px;
    max-height: 26px;
    padding: 0px 4px;
    margin: 0px;
    border: 1px solid #3b82f6;
    border-radius: 2px;
    background: #ffffff;
}
QHeaderView::section {
    font-size: 12px;
    font-weight: 600;
    color: #64748b;
    background: #f8fafc;
    border: none;
    border-bottom: 1px solid #e2e8f0;
    border-right: 1px solid #eef2f7;
    padding: 0px 6px;
    min-height: 28px;
    max-height: 28px;
}
QPushButton {
    font-size: 12px;
    padding: 2px 10px;
    min-height: 32px;
    max-height: 32px;
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    background: #ffffff;
    color: #334155;
}
QPushButton:hover {
    background: #f1f5f9;
    border-color: #94a3b8;
}
QPushButton#PrimaryBtn {
    border-color: #60a5fa;
    color: #1d4ed8;
    background: #eff6ff;
    font-weight: 700;
}
QPushButton#PrimaryBtn:hover {
    background: #dbeafe;
}
QPushButton#PrimaryBtn:disabled {
    color: #93c5fd;
    background: #f8fbff;
    border-color: #bfdbfe;
}
QPushButton#DangerBtn {
    border-color: #f87171;
    color: #b91c1c;
    background: #fff1f2;
    font-weight: 700;
}
QPushButton#DangerBtn:hover {
    background: #fee2e2;
}
QPushButton#DangerBtn:disabled {
    color: #fca5a5;
    background: #fff7f7;
    border-color: #fecdd3;
}
QPushButton:disabled {
    color: #94a3b8;
    background: #f8fafc;
}
"""

class _CompactCellDelegate(QStyledItemDelegate):
    """加料清单单元格编辑器：几何贴合单元格，避免输入框高出一行。"""

    def createEditor(
        self,
        parent: QWidget,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QWidget:
        editor = QLineEdit(parent)
        editor.setFrame(False)
        return editor

    def updateEditorGeometry(
        self,
        editor: QWidget,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        editor.setGeometry(option.rect)


class _BalancePollBridge(QObject):
    polled = Signal(object, str)


class _BalancePollTask(QRunnable):
    """空闲时后台收天平 AUTO 流，避免阻塞界面。"""

    def __init__(self, bridge: _BalancePollBridge):
        super().__init__()
        self.setAutoDelete(True)
        self._bridge = bridge

    def run(self) -> None:
        try:
            driver = get_balance_driver()
            if not driver.session_open:
                ok, detail = driver.open_session()
                if not ok:
                    self._bridge.polled.emit(None, detail)
                    return
            reading, err = driver.read_next_stream(timeout_s=0.9)
            self._bridge.polled.emit(reading, err or "")
        except Exception as exc:  # noqa: BLE001
            self._bridge.polled.emit(None, str(exc))


class _SystemActionThread(QThread):
    # 勿覆盖 QThread.finished
    action_done = Signal(bool, str, str)

    def __init__(self, fn: ActionFn, ctx: SolidDoserSystemContext, action_name: str):
        super().__init__()
        self._fn = fn
        self._ctx = ctx
        self._action_name = action_name

    def run(self) -> None:
        try:
            ok, msg = self._fn(self._ctx)
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, str(exc)
        self.action_done.emit(ok, msg, self._action_name)


class SolidDoserAutoTabWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SolidDoserAutoTabWidget")
        self.setStyleSheet(_STYLESHEET)
        self._ctx: Optional[SolidDoserSystemContext] = None
        self._thread: Optional[_SystemActionThread] = None
        self._action_buttons: Dict[str, QPushButton] = {}
        self._buttons: List[QPushButton] = []
        self._dose_hero_label: Optional[QLabel] = None
        self._dose_hero_target_label: Optional[QLabel] = None
        self._dose_hero_barcode_label: Optional[QLabel] = None
        self._state_label: Optional[QLabel] = None
        self._balance_weight_label: Optional[QLabel] = None
        self._lift_work_spin: Optional[QDoubleSpinBox] = None
        self._lift_work_save_btn: Optional[QPushButton] = None
        self._flags_label: Optional[QLabel] = None
        self._progress_label: Optional[QLabel] = None
        self._log_label: Optional[QLabel] = None
        self._materials_title: Optional[QLabel] = None
        self._materials_table: Optional[QTableWidget] = None
        self._material_row_by_station: Dict[int, int] = {}
        self._dose_table: Optional[QTableWidget] = None
        self._dose_add_btn: Optional[QPushButton] = None
        self._dose_del_btn: Optional[QPushButton] = None
        self._records_table: Optional[QTableWidget] = None
        self._totals_table: Optional[QTableWidget] = None
        self._action_log_handler: Optional[Callable[[str], None]] = None
        self._balance_poll_busy = False
        self._balance_bridge = _BalancePollBridge(self)
        self._balance_bridge.polled.connect(self._on_balance_polled)
        self._balance_pool = QThreadPool(self)
        self._balance_pool.setMaxThreadCount(1)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(300)
        self._poll_timer.timeout.connect(self._refresh_status)
        self._build_ui()

    def set_action_log_handler(self, handler: Optional[Callable[[str], None]]) -> None:
        self._action_log_handler = handler

    def bind_parameter_storage(self, param_storage: ParameterStorage) -> None:
        self._ctx = SolidDoserSystemContext(param_storage)
        self._sync_lift_work_spin()
        self._load_dose_table(param_storage.solid_doser_system.dose_items)
        self._reload_dose_records()
        self._reload_material_totals()
        self._refresh_status()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_lift_work_spin()
        self._poll_timer.start()
        self._reload_dose_records()
        self._reload_material_totals()
        self._refresh_status()

    def hideEvent(self, event) -> None:
        # 有动作线程时保持轮询进度；否则停掉空闲天平拉取
        if self._thread is None:
            self._poll_timer.stop()
            self._balance_pool.waitForDone(500)
            self._balance_poll_busy = False
        super().hideEvent(event)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # —— 顶栏：左主视觉 | 右辅信息 ——
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(10)

        hero_card = QFrame()
        hero_card.setObjectName("HeroCard")
        hero_card.setMinimumHeight(118)
        hero_layout = QVBoxLayout(hero_card)
        hero_layout.setContentsMargins(18, 14, 18, 14)
        hero_layout.setSpacing(6)

        caption = QLabel("本次加样")
        caption.setObjectName("DoseHeroCaption")
        hero_layout.addWidget(caption)

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(8)
        self._dose_hero_label = QLabel("—")
        self._dose_hero_label.setObjectName("DoseHero")
        unit = QLabel("mg")
        unit.setObjectName("DoseHeroUnit")
        unit.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        value_row.addWidget(self._dose_hero_label, 0, Qt.AlignBottom)
        value_row.addWidget(unit, 0, Qt.AlignBottom)
        value_row.addStretch(1)
        hero_layout.addLayout(value_row)

        meta_pair = QHBoxLayout()
        meta_pair.setContentsMargins(0, 4, 0, 0)
        meta_pair.setSpacing(28)
        target_box = QVBoxLayout()
        target_box.setSpacing(2)
        t_key = QLabel("目标")
        t_key.setObjectName("DoseHeroMetaKey")
        self._dose_hero_target_label = QLabel("—")
        self._dose_hero_target_label.setObjectName("DoseHeroMetaVal")
        target_box.addWidget(t_key)
        target_box.addWidget(self._dose_hero_target_label)
        barcode_box = QVBoxLayout()
        barcode_box.setSpacing(2)
        b_key = QLabel("条码")
        b_key.setObjectName("DoseHeroMetaKey")
        self._dose_hero_barcode_label = QLabel("—")
        self._dose_hero_barcode_label.setObjectName("DoseHeroMetaVal")
        barcode_box.addWidget(b_key)
        barcode_box.addWidget(self._dose_hero_barcode_label)
        meta_pair.addLayout(target_box)
        meta_pair.addLayout(barcode_box)
        meta_pair.addStretch(1)
        hero_layout.addLayout(meta_pair)
        top_row.addWidget(hero_card, 5)

        meta_card = QFrame()
        meta_card.setObjectName("Card")
        meta_card.setMinimumHeight(118)
        meta_layout = QVBoxLayout(meta_card)
        meta_layout.setContentsMargins(14, 12, 14, 12)
        meta_layout.setSpacing(8)

        status_row = QHBoxLayout()
        status_row.setSpacing(18)
        state_col = QVBoxLayout()
        state_col.setSpacing(2)
        sk = QLabel("状态")
        sk.setObjectName("MetaKey")
        self._state_label = QLabel("空闲中")
        self._state_label.setObjectName("MetaValue")
        state_col.addWidget(sk)
        state_col.addWidget(self._state_label)
        bal_col = QVBoxLayout()
        bal_col.setSpacing(2)
        bk = QLabel("天平")
        bk.setObjectName("MetaKey")
        self._balance_weight_label = QLabel("—")
        self._balance_weight_label.setObjectName("BalanceWeight")
        bal_col.addWidget(bk)
        bal_col.addWidget(self._balance_weight_label)
        status_row.addLayout(state_col)
        status_row.addLayout(bal_col)
        status_row.addStretch(1)
        meta_layout.addLayout(status_row)

        lift_row = QHBoxLayout()
        lift_row.setSpacing(8)
        lift_lab = QLabel("升降工作位")
        lift_lab.setObjectName("Field")
        self._lift_work_spin = QDoubleSpinBox()
        self._lift_work_spin.setRange(
            motion_cfg.LIFT_WORK_MM_MIN, motion_cfg.LIFT_WORK_MM_MAX
        )
        self._lift_work_spin.setDecimals(2)
        self._lift_work_spin.setSingleStep(0.1)
        self._lift_work_spin.setValue(motion_cfg.get_lift_work_mm())
        self._lift_work_spin.setSuffix(" mm")
        self._lift_work_spin.setMinimumWidth(108)
        self._lift_work_save_btn = QPushButton("保存")
        self._lift_work_save_btn.setMinimumWidth(56)
        self._lift_work_save_btn.clicked.connect(self._on_save_lift_work)
        lift_hint = QLabel(
            f"{motion_cfg.LIFT_WORK_MM_MIN:g}～{motion_cfg.LIFT_WORK_MM_MAX:g}"
        )
        lift_hint.setObjectName("Hint")
        lift_row.addWidget(lift_lab)
        lift_row.addWidget(self._lift_work_spin)
        lift_row.addWidget(self._lift_work_save_btn)
        lift_row.addWidget(lift_hint)
        lift_row.addStretch(1)
        meta_layout.addLayout(lift_row)

        self._flags_label = QLabel("未使能 · 未回基准点 · 未回原点 · 未初始化")
        self._flags_label.setObjectName("LogValue")
        self._flags_label.setWordWrap(True)
        self._progress_label = QLabel("进度 —")
        self._progress_label.setObjectName("LogValue")
        self._progress_label.setWordWrap(True)
        self._log_label = QLabel("日志 —")
        self._log_label.setObjectName("LogValue")
        self._log_label.setWordWrap(True)
        meta_layout.addWidget(self._flags_label)
        meta_layout.addWidget(self._progress_label)
        meta_layout.addWidget(self._log_label)
        meta_layout.addStretch(1)
        top_row.addWidget(meta_card, 4)
        root.addLayout(top_row)

        # —— 中部：物料表 | 加料清单（等高） ——
        mid_row = QHBoxLayout()
        mid_row.setContentsMargins(0, 0, 0, 0)
        mid_row.setSpacing(10)

        materials_card = QFrame()
        materials_card.setObjectName("Card")
        materials_layout = QVBoxLayout(materials_card)
        materials_layout.setContentsMargins(12, 10, 12, 12)
        materials_layout.setSpacing(8)
        self._materials_title = QLabel("物料表")
        self._materials_title.setObjectName("SectionTitle")
        materials_layout.addWidget(self._materials_title)

        stations = list(motion_cfg.iter_indexing_stations())
        self._materials_table = QTableWidget(len(stations), 2)
        self._materials_table.setHorizontalHeaderLabels(["工位", "条码"])
        self._materials_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Fixed
        )
        self._materials_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        self._materials_table.setColumnWidth(0, 56)
        self._materials_table.horizontalHeader().setFixedHeight(_LIST_ROW_HEIGHT)
        self._materials_table.verticalHeader().setVisible(False)
        self._materials_table.verticalHeader().setDefaultSectionSize(_LIST_ROW_HEIGHT)
        self._materials_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._materials_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._materials_table.setFocusPolicy(Qt.NoFocus)
        self._materials_table.setFixedHeight(_MID_TABLE_HEIGHT)
        self._material_row_by_station = {}
        for row, station in enumerate(stations):
            st_item = QTableWidgetItem(str(station))
            st_item.setTextAlignment(Qt.AlignCenter)
            st_item.setFlags(st_item.flags() & ~Qt.ItemIsEditable)
            code_item = QTableWidgetItem("—")
            code_item.setFlags(code_item.flags() & ~Qt.ItemIsEditable)
            self._materials_table.setItem(row, 0, st_item)
            self._materials_table.setItem(row, 1, code_item)
            self._materials_table.setRowHeight(row, _LIST_ROW_HEIGHT)
            self._material_row_by_station[station] = row
        materials_layout.addWidget(self._materials_table)
        mid_row.addWidget(materials_card, 2)

        dose_card = QFrame()
        dose_card.setObjectName("Card")
        dose_layout = QVBoxLayout(dose_card)
        dose_layout.setContentsMargins(12, 10, 12, 12)
        dose_layout.setSpacing(8)

        dose_head = QHBoxLayout()
        dose_head.setContentsMargins(0, 0, 0, 0)
        dose_title = QLabel("加料清单")
        dose_title.setObjectName("SectionTitle")
        dose_unit = QLabel("单位 mg")
        dose_unit.setObjectName("Hint")
        dose_head.addWidget(dose_title)
        dose_head.addWidget(dose_unit)
        dose_head.addStretch(1)
        self._dose_add_btn = QPushButton("添加")
        self._dose_add_btn.setMinimumWidth(64)
        self._dose_add_btn.clicked.connect(self._on_dose_add_row)
        self._dose_del_btn = QPushButton("删除")
        self._dose_del_btn.setMinimumWidth(64)
        self._dose_del_btn.clicked.connect(self._on_dose_del_row)
        dose_head.addWidget(self._dose_add_btn)
        dose_head.addWidget(self._dose_del_btn)
        dose_layout.addLayout(dose_head)

        self._dose_table = QTableWidget(0, 3)
        self._dose_table.setHorizontalHeaderLabels(["物料条码", "目标", "实际"])
        self._dose_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._dose_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self._dose_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self._dose_table.setColumnWidth(1, _DOSE_WEIGHT_COL_WIDTH)
        self._dose_table.setColumnWidth(2, _DOSE_WEIGHT_COL_WIDTH)
        self._dose_table.horizontalHeader().setFixedHeight(_LIST_ROW_HEIGHT)
        self._dose_table.verticalHeader().setVisible(False)
        self._dose_table.verticalHeader().setDefaultSectionSize(_LIST_ROW_HEIGHT)
        self._dose_table.verticalHeader().setMinimumSectionSize(_LIST_ROW_HEIGHT)
        self._dose_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._dose_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._dose_table.setItemDelegate(_CompactCellDelegate(self._dose_table))
        self._dose_table.setFixedHeight(_MID_TABLE_HEIGHT)
        self._dose_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        dose_layout.addWidget(self._dose_table)
        self._append_dose_row("", "", "")
        mid_row.addWidget(dose_card, 3)
        root.addLayout(mid_row)

        # —— 累计加样 | 加样记录 ——
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(10)

        totals_card = QFrame()
        totals_card.setObjectName("Card")
        totals_layout = QVBoxLayout(totals_card)
        totals_layout.setContentsMargins(12, 10, 12, 12)
        totals_layout.setSpacing(8)
        totals_title = QLabel("物料累计加样")
        totals_title.setObjectName("SectionTitle")
        totals_layout.addWidget(totals_title)
        totals_hint = QLabel("按条码汇总实际加样量；物料初始化时清零")
        totals_hint.setObjectName("Hint")
        totals_layout.addWidget(totals_hint)
        self._totals_table = QTableWidget(0, 2)
        self._totals_table.setHorizontalHeaderLabels(["物料条码", "累计(mg)"])
        self._totals_table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self._totals_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._totals_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self._totals_table.setColumnWidth(1, 100)
        self._totals_table.horizontalHeader().setFixedHeight(_LIST_ROW_HEIGHT)
        self._totals_table.verticalHeader().setVisible(False)
        self._totals_table.verticalHeader().setDefaultSectionSize(_LIST_ROW_HEIGHT)
        self._totals_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._totals_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._totals_table.setFocusPolicy(Qt.NoFocus)
        self._totals_table.setMinimumHeight(_LIST_ROW_HEIGHT * 5 + 30)
        self._totals_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        totals_layout.addWidget(self._totals_table, 1)
        bottom_row.addWidget(totals_card, 2)

        records_card = QFrame()
        records_card.setObjectName("Card")
        records_layout = QVBoxLayout(records_card)
        records_layout.setContentsMargins(12, 10, 12, 12)
        records_layout.setSpacing(8)
        records_title = QLabel("加样记录")
        records_title.setObjectName("SectionTitle")
        records_layout.addWidget(records_title)
        self._records_table = QTableWidget(0, 4)
        self._records_table.setHorizontalHeaderLabels(
            ["时间", "加样条码", "目标(mg)", "实际(mg)"]
        )
        self._records_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self._records_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._records_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self._records_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeToContents
        )
        self._records_table.horizontalHeader().setFixedHeight(_LIST_ROW_HEIGHT)
        self._records_table.verticalHeader().setVisible(False)
        self._records_table.verticalHeader().setDefaultSectionSize(_LIST_ROW_HEIGHT)
        self._records_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._records_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._records_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._records_table.setMinimumHeight(_LIST_ROW_HEIGHT * 5 + 30)
        self._records_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        records_layout.addWidget(self._records_table, 1)
        bottom_row.addWidget(records_card, 3)
        root.addLayout(bottom_row, 1)

        # —— 底部操作 ——
        actions_card = QFrame()
        actions_card.setObjectName("Card")
        card_layout = QHBoxLayout(actions_card)
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(_AUTO_BTN_SPACING)

        actions = (
            ("设备初始化", system_run_device_init),
            ("使能", system_run_servo_on_all),
            ("回基准点", system_run_go_datum),
            ("回原点", system_run_go_origin),
            ("物料初始化", system_run_initialize),
            ("加料", system_run_dose),
            ("试剂瓶出料", system_run_bottle_discharge),
        )
        for text, fn in actions:
            btn = QPushButton(text)
            btn.setMinimumWidth(_AUTO_BTN_MIN_WIDTH)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            if text == "加料":
                btn.setObjectName("PrimaryBtn")
            btn.clicked.connect(
                lambda _c=False, f=fn, n=text: self._run_action(f, n)
            )
            self._action_buttons[text] = btn
            self._buttons.append(btn)
            card_layout.addWidget(btn, 1)

        btn_abort = QPushButton("停止")
        btn_abort.setObjectName("DangerBtn")
        btn_abort.setMinimumWidth(_AUTO_BTN_MIN_WIDTH)
        btn_abort.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn_abort.clicked.connect(self._on_abort)
        self._action_buttons["停止"] = btn_abort
        self._buttons.append(btn_abort)
        card_layout.addWidget(btn_abort, 1)
        root.addWidget(actions_card)
        self._set_controls_for_idle(True)

    def _refresh_status(self) -> None:
        if self._ctx is None:
            return
        st = self._ctx.system
        if self._state_label is not None:
            self._state_label.setText(st.state_label)
        self._refresh_dose_hero(st)
        self._refresh_balance_weight()
        if self._flags_label is not None:
            self._flags_label.setText(st.flags_summary())
        if self._progress_label is not None:
            if st.progress_total > 0:
                detail = f" · {st.progress_detail}" if st.progress_detail else ""
                self._progress_label.setText(
                    f"进度 {st.progress_current}/{st.progress_total}{detail}"
                )
            else:
                self._progress_label.setText("进度 —")
        if self._log_label is not None:
            prefix = "成功" if st.last_success else "失败"
            msg = st.last_message or "—"
            self._log_label.setText(f"日志 {prefix} · {msg}")
        self._refresh_materials_table(st)
        if not st.is_busy:
            self._reload_material_totals()
        if (not st.is_busy) and (not self._balance_poll_busy) and self.isVisible():
            self._balance_poll_busy = True
            self._balance_pool.start(_BalancePollTask(self._balance_bridge))

    def _refresh_dose_hero(self, st) -> None:
        if (
            self._dose_hero_label is None
            or self._dose_hero_target_label is None
            or self._dose_hero_barcode_label is None
        ):
            return
        dosing = st.run_state == SystemRunState.DOSING
        show_live = (
            dosing
            and (
                st.dose_live_actual_mg is not None
                or st.dose_live_target_mg is not None
            )
        ) or (
            not dosing
            and st.dose_live_target_mg is not None
            and st.dose_live_actual_mg is not None
        )
        if show_live:
            actual = (
                "—"
                if st.dose_live_actual_mg is None
                else f"{st.dose_live_actual_mg:g}"
            )
            target = (
                "—"
                if st.dose_live_target_mg is None
                else f"{st.dose_live_target_mg:g} mg"
            )
            barcode = st.dose_live_barcode or "—"
            self._dose_hero_label.setText(actual)
            self._dose_hero_target_label.setText(target)
            self._dose_hero_barcode_label.setText(barcode)
        else:
            self._dose_hero_label.setText("—")
            self._dose_hero_target_label.setText("—")
            self._dose_hero_barcode_label.setText("—")

    def _refresh_balance_weight(self) -> None:
        if self._balance_weight_label is None or self._ctx is None:
            return
        bal = self._ctx.balance_ctx.balance
        if bal.last_weight_display:
            text = bal.last_weight_display
        elif bal.last_weight_value is not None:
            unit = bal.last_weight_unit or "g"
            text = f"{bal.last_weight_value:g} {unit}"
        else:
            text = "—"
        self._balance_weight_label.setText(text)

    def _on_balance_polled(self, reading: object, err: str) -> None:
        self._balance_poll_busy = False
        if self._ctx is None:
            return
        if self._ctx.system.is_busy:
            return
        if not isinstance(reading, BalanceReading) or reading.value is None:
            return
        bal = self._ctx.balance_ctx.balance
        bal.connected = True
        bal.last_raw = reading.raw
        bal.last_weight_value = reading.value
        bal.last_weight_unit = reading.unit
        bal.last_weight_display = reading.display
        self._refresh_balance_weight()

    def _refresh_materials_table(self, st) -> None:
        stations = list(motion_cfg.iter_indexing_stations())
        has_map = bool(st.station_materials)
        if self._materials_title is not None:
            if st.materials_ok:
                filled = sum(
                    1
                    for s in stations
                    if (st.station_materials.get(s) or "").strip()
                )
                self._materials_title.setText(
                    f"物料表 · 已初始化（{filled}/{len(stations)}）"
                )
            elif has_map and st.is_busy:
                self._materials_title.setText("物料表 · 初始化中…")
            elif has_map:
                self._materials_title.setText("物料表 · 未完成 / 已作废")
            else:
                self._materials_title.setText("物料表")
        if self._materials_table is None:
            return
        for station, row in self._material_row_by_station.items():
            code = ""
            if has_map:
                code = (st.station_materials.get(station) or "").strip()
            item = self._materials_table.item(row, 1)
            if item is None:
                item = QTableWidgetItem()
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self._materials_table.setItem(row, 1, item)
            item.setText(code if code else "—")

    def _set_controls_for_idle(self, idle: bool) -> None:
        for name, btn in self._action_buttons.items():
            if name == "停止":
                btn.setEnabled(not idle)
            else:
                btn.setEnabled(idle)
        if self._dose_table is not None:
            self._dose_table.setEnabled(idle)
        if self._dose_add_btn is not None:
            self._dose_add_btn.setEnabled(idle)
        if self._dose_del_btn is not None:
            self._dose_del_btn.setEnabled(idle)
        if self._lift_work_spin is not None:
            self._lift_work_spin.setEnabled(idle)
        if self._lift_work_save_btn is not None:
            self._lift_work_save_btn.setEnabled(idle)

    def _sync_lift_work_spin(self) -> None:
        if self._lift_work_spin is None:
            return
        self._lift_work_spin.setValue(motion_cfg.get_lift_work_mm())

    def _on_save_lift_work(self) -> None:
        if self._lift_work_spin is None:
            return
        value = float(self._lift_work_spin.value())
        ok, msg = save_lift_work_mm(value)
        # 写回夹紧后的值
        self._sync_lift_work_spin()
        text = f"日志 {'成功' if ok else '失败'} · 升降工作位 · {msg}"
        if self._log_label is not None:
            self._log_label.setText(text)
        if self._action_log_handler is not None:
            self._action_log_handler(text)

    def _append_dose_row(
        self,
        material_id: str = "",
        weight_text: str = "",
        actual_text: str = "",
    ) -> None:
        if self._dose_table is None:
            return
        row = self._dose_table.rowCount()
        self._dose_table.insertRow(row)
        self._dose_table.setRowHeight(row, _LIST_ROW_HEIGHT)
        self._dose_table.setItem(row, 0, QTableWidgetItem(material_id))
        self._dose_table.setItem(row, 1, QTableWidgetItem(weight_text))
        actual_item = QTableWidgetItem(actual_text)
        actual_item.setFlags(actual_item.flags() & ~Qt.ItemIsEditable)
        self._dose_table.setItem(row, 2, actual_item)

    def _load_dose_table(self, items: List[DoseItem]) -> None:
        if self._dose_table is None:
            return
        self._dose_table.setRowCount(0)
        if not items:
            self._append_dose_row("", "", "")
            return
        for it in items:
            w = "" if float(it.weight_mg or 0.0) <= 0 else f"{it.weight_mg:g}"
            a = "" if it.actual_mg is None else f"{it.actual_mg:g}"
            self._append_dose_row((it.material_id or "").strip(), w, a)

    def _on_dose_add_row(self) -> None:
        self._append_dose_row("", "", "")

    def _on_dose_del_row(self) -> None:
        if self._dose_table is None:
            return
        row = self._dose_table.currentRow()
        if row < 0:
            return
        self._dose_table.removeRow(row)
        if self._dose_table.rowCount() == 0:
            self._append_dose_row("", "", "")

    def _sync_dose_params(self) -> None:
        if self._ctx is None or self._dose_table is None:
            return
        items: List[DoseItem] = []
        for row in range(self._dose_table.rowCount()):
            code_item = self._dose_table.item(row, 0)
            weight_item = self._dose_table.item(row, 1)
            actual_item = self._dose_table.item(row, 2)
            code = (code_item.text() if code_item is not None else "").strip()
            weight_text = (
                weight_item.text() if weight_item is not None else ""
            ).strip()
            actual_text = (
                actual_item.text() if actual_item is not None else ""
            ).strip()
            if not code and not weight_text:
                continue
            try:
                weight = float(weight_text) if weight_text else 0.0
            except ValueError:
                weight = 0.0
            actual: Optional[float] = None
            if actual_text:
                try:
                    actual = float(actual_text)
                except ValueError:
                    actual = None
            items.append(
                DoseItem(material_id=code, weight_mg=weight, actual_mg=actual)
            )
        self._ctx.system.dose_items = items

    def _run_action(self, fn: ActionFn, action_name: str) -> None:
        if self._ctx is None or self._thread is not None:
            return
        if self._ctx.system.is_busy:
            self._refresh_status()
            return
        self._sync_dose_params()
        if action_name == "加料":
            for it in self._ctx.system.dose_items:
                it.actual_mg = None
            self._load_dose_table(self._ctx.system.dose_items)
        self._set_controls_for_idle(False)
        self._thread = _SystemActionThread(fn, self._ctx, action_name)
        self._thread.action_done.connect(self._on_action_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        self._poll_timer.start()
        self._state_label.setText(f"启动{action_name}…")

    def _on_abort(self) -> None:
        if self._ctx is None:
            return
        system_request_abort(self._ctx)
        self._refresh_status()

    def _reload_dose_records(self) -> None:
        if self._records_table is None:
            return
        records = load_dose_records(limit=_RECORDS_LOAD_LIMIT)
        self._records_table.setRowCount(0)
        for rec in records:
            self._append_record_row(rec)

    def _reload_material_totals(self) -> None:
        if self._totals_table is None:
            return
        rows = load_material_dose_totals()
        self._totals_table.setRowCount(0)
        for tot in rows:
            self._append_total_row(tot)

    def _append_total_row(self, tot: MaterialDoseTotal) -> None:
        if self._totals_table is None:
            return
        row = self._totals_table.rowCount()
        self._totals_table.insertRow(row)
        self._totals_table.setRowHeight(row, _LIST_ROW_HEIGHT)
        for col, text in enumerate((tot.barcode, f"{tot.total_mg:g}")):
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignCenter)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._totals_table.setItem(row, col, item)

    def _append_record_row(self, rec: DoseRecord) -> None:
        if self._records_table is None:
            return
        row = self._records_table.rowCount()
        self._records_table.insertRow(row)
        self._records_table.setRowHeight(row, _LIST_ROW_HEIGHT)
        actual = "—" if rec.actual_mg is None else f"{rec.actual_mg:g}"
        values = (
            rec.dosed_at,
            rec.barcode,
            f"{rec.target_mg:g}",
            actual,
        )
        for col, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._records_table.setItem(row, col, item)

    def _on_action_finished(self, ok: bool, msg: str, action_name: str) -> None:
        self._thread = None
        if not self.isVisible():
            self._poll_timer.stop()
        self._set_controls_for_idle(True)
        if self._ctx is not None and action_name in ("加料", "物料初始化"):
            if action_name == "加料":
                self._load_dose_table(self._ctx.system.dose_items)
            self._reload_dose_records()
            self._reload_material_totals()
        self._refresh_status()
        prefix = "成功" if ok else "失败"
        text = f"日志 {prefix} · {action_name} · {msg}"
        if self._log_label is not None:
            self._log_label.setText(text)
        if self._action_log_handler is not None:
            self._action_log_handler(text)
