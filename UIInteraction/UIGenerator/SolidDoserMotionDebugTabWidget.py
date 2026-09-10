"""SolidDoser 设备调试界面（运动轴 / DO / 扫码枪 / 天平）。"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, QTimer, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from BusinessActions.KeyenceScanner.scanner_actions import (
    scanner_clear_result,
    scanner_test_connection,
    scanner_trigger_read,
)
from BusinessActions.KeyenceScanner.scanner_context import ScannerContext
from BusinessActions.SartoriusBalance.balance_actions import (
    balance_clear_result,
    balance_read_weight,
    balance_tare,
    balance_test_connection,
    balance_zero,
)
from BusinessActions.SartoriusBalance.balance_context import BalanceContext
from BusinessActions.SolidDoserMotion.motion_actions import (
    indexing_disc_go_to_station,
    motion_axis_go_datum,
    motion_axis_move_abs,
    motion_axis_servo_on,
    motion_axis_stop,
    motion_do_set,
    motion_horizontal_go_origin,
    motion_indexing_go_origin,
    motion_lift_go_origin,
    motion_powder_go_origin,
    motion_refresh_all,
)
from BusinessActions.SolidDoserMotion.motion_context import SolidDoserMotionContext
from Drivers.KeyenceScanner import scanner_config as scanner_cfg
from Drivers.SartoriusBalance import balance_config as balance_cfg
from Drivers.SerialServer import ut_6801a_config as serial_cfg
from Drivers.SolidDoserMotion import motion_config as motion_cfg
from Drivers.SolidDoserMotion.motion_config import AxisMap, DiInputMap, DoOutputMap
from Drivers.SolidDoserMotion.indexing_disc import get_indexing_disc
from Drivers.SolidDoserMotion.motion_driver import (
    apply_status_to_state,
    get_motion_driver,
)
from UIInteraction.ParameterManagement.ParameterStorage import ParameterStorage

ActionFn = Callable[[SolidDoserMotionContext], Tuple[bool, str]]
ScannerActionFn = Callable[[ScannerContext], Tuple[bool, str]]
BalanceActionFn = Callable[[BalanceContext], Tuple[bool, str]]

_STIR_AXIS_KEY = "stirring"
_INDEXING_AXIS_KEY = motion_cfg.INDEXING_AXIS_KEY
_HORIZONTAL_AXIS_KEY = motion_cfg.HORIZONTAL_AXIS_KEY
_LIFT_AXIS_KEY = motion_cfg.LIFT_AXIS_KEY
_POWDER_AXIS_KEY = motion_cfg.POWDER_AXIS_KEY
_FIELD_LABEL_WIDTH = 28
_TARGET_INPUT_MIN_WIDTH = 56
_VELOCITY_INPUT_MIN_WIDTH = 52
_AXIS_STATUS_MIN_WIDTH = 120
# 按钮列：使能 / 回基准点 / 回原点 / 定位 / 工位下拉 / 分度 / 停止
# 目标、速度插在「回原点」与「定位」之间
_AXIS_BTN_COLS = 7
_AXIS_BTN_MIN_WIDTH = 76  # 保证「回基准点」四字完整显示
_AXIS_BTN_SPACING = 4

_STYLESHEET = """
SolidDoserMotionDebugTabWidget {
    background: #f1f5f9;
}
QFrame#Card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
}
QFrame#AxisRow {
    background: #ffffff;
    border: none;
    border-bottom: 1px solid #f1f5f9;
}
QLabel#Title {
    font-size: 14px;
    font-weight: 600;
    color: #0f172a;
}
QLabel#Hint {
    font-size: 11px;
    color: #64748b;
}
QLabel#Field {
    font-size: 11px;
    color: #64748b;
}
QLabel#Name {
    font-size: 13px;
    font-weight: 600;
    color: #0f172a;
}
QLabel#Status {
    font-size: 11px;
    color: #475569;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 4px;
    padding: 4px 8px;
}
QLabel#Result {
    font-size: 12px;
    color: #334155;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 4px;
    padding: 6px 8px;
}
QLineEdit {
    font-size: 12px;
    padding: 2px 6px;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    background: #ffffff;
    min-height: 30px;
    max-height: 30px;
}
QLineEdit:focus {
    border: 1px solid #3b82f6;
}
QComboBox {
    font-size: 12px;
    padding: 2px 6px;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    background: #ffffff;
    min-height: 30px;
    max-height: 30px;
}
QComboBox:focus {
    border: 1px solid #3b82f6;
}
QComboBox::drop-down {
    border: none;
    width: 18px;
}
QPushButton {
    font-size: 12px;
    padding: 2px 10px;
    min-height: 30px;
    max-height: 30px;
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    background: #ffffff;
    color: #334155;
}
QPushButton:hover {
    background: #f1f5f9;
    border-color: #94a3b8;
}
QPushButton:pressed {
    background: #e2e8f0;
}
QPushButton#PrimaryBtn {
    background: #2563eb;
    border-color: #2563eb;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#PrimaryBtn:hover {
    background: #1d4ed8;
}
QPushButton#DangerBtn {
    background: #fff1f2;
    border-color: #fecdd3;
    color: #be123c;
}
QPushButton#DangerBtn:hover {
    background: #ffe4e6;
}
QPushButton#AxisBtn {
    padding: 2px 2px;
}
QPushButton#PrimaryBtn,
QPushButton#DangerBtn {
    padding: 2px 4px;
}
"""


class _MotionActionThread(QThread):
    # 勿覆盖 QThread.finished，否则易导致原生闪退
    action_done = Signal(bool, str, str)

    def __init__(self, fn: ActionFn, ctx: SolidDoserMotionContext, action_name: str):
        super().__init__()
        self._fn = fn
        self._ctx = ctx
        self._action_name = action_name

    def run(self) -> None:
        try:
            ok, msg = self._fn(self._ctx)
            self.action_done.emit(ok, msg, self._action_name)
        except Exception as e:
            self.action_done.emit(False, str(e), self._action_name)


class _DiPollBridge(QObject):
    """DI 轮询结果桥回主线程（配合 QThreadPool，避免每 400ms 新建 QThread）。"""

    polled = Signal(object, str)


class _DiPollTask(QRunnable):
    def __init__(self, bridge: _DiPollBridge):
        super().__init__()
        self.setAutoDelete(True)
        self._bridge = bridge

    def run(self) -> None:
        try:
            status, err = get_motion_driver().read_status()
            self._bridge.polled.emit(status, err)
        except Exception as exc:
            self._bridge.polled.emit(None, str(exc))


class _AxisActionThread(QThread):
    action_done = Signal(bool, str, str)

    def __init__(
        self,
        fn: Callable[[SolidDoserMotionContext, str], Tuple[bool, str]],
        ctx: SolidDoserMotionContext,
        axis_key: str,
        action_name: str,
    ):
        super().__init__()
        self._fn = fn
        self._ctx = ctx
        self._axis_key = axis_key
        self._action_name = action_name

    def run(self) -> None:
        try:
            ok, msg = self._fn(self._ctx, self._axis_key)
            self.action_done.emit(ok, msg, self._action_name)
        except Exception as e:
            self.action_done.emit(False, str(e), self._action_name)


class _ScannerActionThread(QThread):
    action_done = Signal(bool, str, str)

    def __init__(self, fn: ScannerActionFn, ctx: ScannerContext, action_name: str):
        super().__init__()
        self._fn = fn
        self._ctx = ctx
        self._action_name = action_name

    def run(self) -> None:
        try:
            ok, msg = self._fn(self._ctx)
            self.action_done.emit(ok, msg, self._action_name)
        except Exception as e:
            self.action_done.emit(False, str(e), self._action_name)


class _BalanceActionThread(QThread):
    action_done = Signal(bool, str, str)

    def __init__(self, fn: BalanceActionFn, ctx: BalanceContext, action_name: str):
        super().__init__()
        self._fn = fn
        self._ctx = ctx
        self._action_name = action_name

    def run(self) -> None:
        try:
            ok, msg = self._fn(self._ctx)
            self.action_done.emit(ok, msg, self._action_name)
        except Exception as e:
            self.action_done.emit(False, str(e), self._action_name)


def _make_btn(text: str, *, primary: bool = False, danger: bool = False) -> QPushButton:
    btn = QPushButton(text)
    if primary:
        btn.setObjectName("PrimaryBtn")
    elif danger:
        btn.setObjectName("DangerBtn")
    btn.setMinimumWidth(64)
    btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return btn


def _make_axis_btn(text: str, *, primary: bool = False, danger: bool = False) -> QPushButton:
    """运动轴按钮：同行等比拉伸，各行同列对齐。"""
    btn = _make_btn(text, primary=primary, danger=danger)
    if not primary and not danger:
        btn.setObjectName("AxisBtn")
    btn.setMinimumWidth(_AXIS_BTN_MIN_WIDTH)
    btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return btn


def _add_axis_btn_slot(layout: QHBoxLayout, widget: Optional[QWidget]) -> None:
    if widget is None:
        placeholder = QWidget()
        placeholder.setMinimumWidth(_AXIS_BTN_MIN_WIDTH)
        placeholder.setFixedHeight(30)
        placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(placeholder, 1)
        return
    widget.setMinimumWidth(_AXIS_BTN_MIN_WIDTH)
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    layout.addWidget(widget, 1)


class SolidDoserMotionDebugTabWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SolidDoserMotionDebugTabWidget")
        self.setStyleSheet(_STYLESHEET)
        self._ctx: Optional[SolidDoserMotionContext] = None
        self._scanner_ctx: Optional[ScannerContext] = None
        self._balance_ctx: Optional[BalanceContext] = None
        self._thread: Optional[QThread] = None
        self._stop_thread: Optional[QThread] = None
        self._di_busy = False
        self._di_bridge = _DiPollBridge(self)
        self._di_bridge.polled.connect(self._on_di_polled)
        self._di_pool = QThreadPool(self)
        self._di_pool.setMaxThreadCount(1)
        self._di_timer = QTimer(self)
        self._di_timer.setInterval(400)
        self._di_timer.timeout.connect(self._poll_di)
        # 自动页使能等会更新共用 ParameterStorage；调试页定时重绘，
        # 实时轴位置由 _poll_di → read_status 从 PLC 拉取。
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(300)
        self._status_timer.timeout.connect(self._refresh_status)
        self._buttons: List[QPushButton] = []
        self._stop_buttons: List[QPushButton] = []
        self._indexing_station_combo: Optional[QComboBox] = None
        self._status_label: Optional[QLabel] = None
        self._log_label: Optional[QLabel] = None
        self._axis_status_labels: Dict[str, QLabel] = {}
        self._do_status_labels: Dict[str, QLabel] = {}
        self._do_on_buttons: Dict[str, QPushButton] = {}
        self._do_off_buttons: Dict[str, QPushButton] = {}
        self._di_status_labels: Dict[str, QLabel] = {}
        self._target_inputs: Dict[str, QLineEdit] = {}
        self._velocity_inputs: Dict[str, QLineEdit] = {}
        self._scanner_status_label: Optional[QLabel] = None
        self._scanner_result_label: Optional[QLabel] = None
        self._balance_status_label: Optional[QLabel] = None
        self._balance_result_label: Optional[QLabel] = None
        self._param_storage: Optional[ParameterStorage] = None
        self._di_closing = False
        self._action_log_handler: Optional[Callable[[str], None]] = None
        self._build_ui()

    def set_action_log_handler(self, handler: Optional[Callable[[str], None]]) -> None:
        self._action_log_handler = handler

    def bind_parameter_storage(self, param_storage: ParameterStorage) -> None:
        self._param_storage = param_storage
        self._ctx = SolidDoserMotionContext(param_storage)
        self._scanner_ctx = ScannerContext(param_storage)
        self._balance_ctx = BalanceContext(param_storage)
        self._load_motion_params_from_state()
        self._refresh_status()
        self._refresh_scanner_status()
        self._refresh_balance_status()
        self._poll_di()
        if self.isVisible():
            self._di_timer.start()

    def hideEvent(self, event) -> None:
        self._di_timer.stop()
        self._status_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._ctx is not None and not self._di_closing:
            # 从自动页切回时，先同步共用状态到界面（使能/连接等）
            self._refresh_status()
            self._refresh_scanner_status()
            self._refresh_balance_status()
            self._status_timer.start()
            self._poll_di()
            self._di_timer.start()

    def closeEvent(self, event) -> None:
        self.shutdown_di_poll()
        super().closeEvent(event)

    def shutdown_di_poll(self) -> None:
        """主窗口关闭时调用，避免轮询/动作线程未结束导致进程异常退出。"""
        self._di_closing = True
        self._status_timer.stop()
        self._stop_di_poll(wait=True)
        for attr in ("_thread", "_stop_thread"):
            thread = getattr(self, attr, None)
            if thread is None:
                continue
            setattr(self, attr, None)
            if thread.isRunning():
                thread.wait(3000)

    def _stop_di_poll(self, *, wait: bool = False) -> None:
        self._di_timer.stop()
        if not wait:
            return
        self._di_pool.waitForDone(2000)
        self._di_busy = False

    def _arm_worker(self, thread: QThread, slot) -> None:
        """启动动作线程：用 action_done 回传结果，用原生 finished 回收对象。"""
        thread.action_done.connect(slot)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _system_busy(self) -> bool:
        return bool(self._param_storage and self._param_storage.is_system_busy)

    def _reject_if_system_busy(self) -> bool:
        if not self._system_busy():
            return False
        self._append_log(False, "系统自动流程运行中，调试运动已锁定。", "调试")
        return True

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("设备调试")
        title.setObjectName("Title")
        header.addWidget(title)
        hint = QLabel(
            f"PLC {motion_cfg.PLC_HOST}:{motion_cfg.PLC_PORT}  ·  "
            f"扫码 {scanner_cfg.SCANNER_HOST}:{scanner_cfg.SCANNER_PORT}  ·  "
            f"天平 {serial_cfg.SERIAL_SERVER_HOST}:{serial_cfg.SERIAL_SERVER_PORT}"
        )
        hint.setObjectName("Hint")
        header.addWidget(hint, 1)
        btn_refresh = _make_btn("刷新", primary=True)
        btn_refresh.setFixedWidth(72)
        btn_refresh.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        btn_refresh.clicked.connect(
            lambda: self._run_global_action(motion_refresh_all, "刷新全部状态")
        )
        self._buttons.append(btn_refresh)
        header.addWidget(btn_refresh)
        root.addLayout(header)

        self._status_label = QLabel("PLC：未绑定")
        self._status_label.setObjectName("Status")
        root.addWidget(self._status_label)

        root.addWidget(self._build_motion_card())
        root.addWidget(self._build_do_card())
        root.addWidget(self._build_di_card())

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addWidget(self._build_scanner_card(), 1)
        bottom.addWidget(self._build_balance_card(), 1)
        root.addLayout(bottom)
        # 操作日志在主窗口底部共用栏显示（三页可见）
        root.addStretch(1)

    def _build_motion_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(0)

        head = QLabel("运动轴（AM600）")
        head.setObjectName("Title")
        layout.addWidget(head)

        for axis in motion_cfg.AXES:
            layout.addWidget(self._build_axis_row(axis))
        return card

    def _build_axis_row(self, axis: AxisMap) -> QWidget:
        row = QFrame()
        row.setObjectName("AxisRow")
        row.setMinimumHeight(40)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 6, 4, 6)
        layout.setSpacing(6)

        name = QLabel(axis.label)
        name.setObjectName("Name")
        name.setFixedWidth(72)
        layout.addWidget(name)

        is_stir_axis = axis.key == _STIR_AXIS_KEY
        is_indexing_axis = axis.key == _INDEXING_AXIS_KEY
        is_horizontal_axis = axis.key == _HORIZONTAL_AXIS_KEY
        is_lift_axis = axis.key == _LIFT_AXIS_KEY
        is_powder_axis = axis.key == _POWDER_AXIS_KEY

        def _new_actions_box() -> tuple[QWidget, QHBoxLayout]:
            box = QWidget()
            box_layout = QHBoxLayout(box)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.setSpacing(_AXIS_BTN_SPACING)
            return box, box_layout

        # 前段按钮：使能 / 回基准点 / 回原点
        pre_box, pre_actions = _new_actions_box()
        btn_servo = _make_axis_btn("使能")
        btn_servo.clicked.connect(
            lambda _c=False, k=axis.key: self._run_axis_action(
                motion_axis_servo_on, k, f"{axis.label} 使能"
            )
        )
        self._buttons.append(btn_servo)
        _add_axis_btn_slot(pre_actions, btn_servo)

        if is_stir_axis:
            _add_axis_btn_slot(pre_actions, None)
            _add_axis_btn_slot(pre_actions, None)
        else:
            btn_datum = _make_axis_btn("回基准点")
            btn_datum.clicked.connect(
                lambda _c=False, k=axis.key: self._run_axis_action(
                    motion_axis_go_datum, k, f"{axis.label} 回基准点"
                )
            )
            self._buttons.append(btn_datum)
            _add_axis_btn_slot(pre_actions, btn_datum)

            if is_horizontal_axis:
                btn_origin = _make_axis_btn("回原点")
                btn_origin.clicked.connect(
                    lambda _c=False: self._run_horizontal_go_origin()
                )
                self._buttons.append(btn_origin)
                _add_axis_btn_slot(pre_actions, btn_origin)
            elif is_indexing_axis:
                btn_origin = _make_axis_btn("回原点")
                btn_origin.clicked.connect(
                    lambda _c=False: self._run_indexing_go_origin()
                )
                self._buttons.append(btn_origin)
                _add_axis_btn_slot(pre_actions, btn_origin)
            elif is_lift_axis:
                btn_origin = _make_axis_btn("回原点")
                btn_origin.clicked.connect(
                    lambda _c=False: self._run_lift_go_origin()
                )
                self._buttons.append(btn_origin)
                _add_axis_btn_slot(pre_actions, btn_origin)
            elif is_powder_axis:
                btn_origin = _make_axis_btn("回原点")
                btn_origin.clicked.connect(
                    lambda _c=False: self._run_powder_go_origin()
                )
                self._buttons.append(btn_origin)
                _add_axis_btn_slot(pre_actions, btn_origin)
            else:
                _add_axis_btn_slot(pre_actions, None)
        layout.addWidget(pre_box, 3)

        # 目标 / 速度：放在定位按钮前
        target_box = QWidget()
        target_wrap = QHBoxLayout(target_box)
        target_wrap.setContentsMargins(0, 0, 0, 0)
        target_wrap.setSpacing(4)
        t_lab = QLabel("" if is_stir_axis else "目标")
        t_lab.setObjectName("Field")
        t_lab.setFixedWidth(_FIELD_LABEL_WIDTH)
        target_wrap.addWidget(t_lab)
        if is_stir_axis:
            placeholder = QWidget()
            placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            target_wrap.addWidget(placeholder, 1)
        else:
            target_input = QLineEdit()
            target_input.setMinimumWidth(_TARGET_INPUT_MIN_WIDTH)
            target_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            pos_validator = QDoubleValidator(axis.pos_min, axis.pos_max, 3)
            pos_validator.setNotation(QDoubleValidator.StandardNotation)
            target_input.setValidator(pos_validator)
            target_input.setPlaceholderText(axis.unit)
            target_input.editingFinished.connect(
                lambda k=axis.key: self._sync_axis_params(k)
            )
            self._target_inputs[axis.key] = target_input
            target_wrap.addWidget(target_input, 1)
        layout.addWidget(target_box, 2)

        vel_box = QWidget()
        vel_wrap = QHBoxLayout(vel_box)
        vel_wrap.setContentsMargins(0, 0, 0, 0)
        vel_wrap.setSpacing(4)
        v_lab = QLabel("转速" if is_stir_axis else "速度")
        v_lab.setObjectName("Field")
        v_lab.setFixedWidth(_FIELD_LABEL_WIDTH)
        vel_input = QLineEdit()
        vel_input.setMinimumWidth(_VELOCITY_INPUT_MIN_WIDTH)
        vel_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        vel_validator = (
            QDoubleValidator(-5000.0, 5000.0, 2)
            if is_stir_axis
            else QDoubleValidator(0.1, 5000.0, 2)
        )
        vel_validator.setNotation(QDoubleValidator.StandardNotation)
        vel_input.setValidator(vel_validator)
        vel_input.setPlaceholderText(f"{axis.vel_default:g}{axis.unit}/s")
        vel_input.editingFinished.connect(lambda k=axis.key: self._sync_axis_params(k))
        self._velocity_inputs[axis.key] = vel_input
        vel_wrap.addWidget(v_lab)
        vel_wrap.addWidget(vel_input, 1)
        layout.addWidget(vel_box, 2)

        # 后段按钮：定位 / 工位下拉+分度（仅分度轴）/ 停止
        post_box, post_actions = _new_actions_box()
        move_label = "启动" if is_stir_axis else "定位"
        btn_move = _make_axis_btn(move_label, primary=True)
        btn_move.clicked.connect(
            lambda _c=False, k=axis.key: self._run_axis_action(
                motion_axis_move_abs, k, f"{axis.label} {move_label}"
            )
        )
        self._buttons.append(btn_move)
        _add_axis_btn_slot(post_actions, btn_move)

        if is_indexing_axis:
            station_combo = QComboBox()
            for station in motion_cfg.iter_indexing_stations():
                station_combo.addItem(f"工位{station}", station)
            station_combo.setMinimumWidth(_AXIS_BTN_MIN_WIDTH)
            station_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._indexing_station_combo = station_combo
            _add_axis_btn_slot(post_actions, station_combo)

            btn_index = _make_axis_btn("分度", primary=True)
            btn_index.clicked.connect(lambda _c=False: self._run_indexing_go_station())
            self._buttons.append(btn_index)
            _add_axis_btn_slot(post_actions, btn_index)
        else:
            _add_axis_btn_slot(post_actions, None)
            _add_axis_btn_slot(post_actions, None)

        btn_stop = _make_axis_btn("停止", danger=True)
        btn_stop.clicked.connect(
            lambda _c=False, k=axis.key: self._run_axis_stop(k)
        )
        self._stop_buttons.append(btn_stop)
        _add_axis_btn_slot(post_actions, btn_stop)
        layout.addWidget(post_box, 4)

        status = QLabel("未读取")
        status.setObjectName("Status")
        status.setMinimumWidth(_AXIS_STATUS_MIN_WIDTH)
        status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._axis_status_labels[axis.key] = status
        layout.addWidget(status, 3)
        return row

    def _build_do_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        for do_item in motion_cfg.DO_OUTPUTS:
            layout.addWidget(self._build_do_item(do_item), 1)
        return card

    def _build_do_item(self, do_item: DoOutputMap) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        name = QLabel(do_item.label)
        name.setObjectName("Name")
        row.addWidget(name)

        status = QLabel("关闭")
        status.setObjectName("Status")
        self._do_status_labels[do_item.key] = status
        row.addWidget(status)

        btn_off = _make_btn("关闭", danger=True)
        btn_on = _make_btn("开启", primary=True)
        btn_off.clicked.connect(
            lambda _c=False, k=do_item.key, n=f"{do_item.label} 关闭": self._run_do_action(
                k, False, n
            )
        )
        btn_on.clicked.connect(
            lambda _c=False, k=do_item.key, n=f"{do_item.label} 开启": self._run_do_action(
                k, True, n
            )
        )
        self._do_off_buttons[do_item.key] = btn_off
        self._do_on_buttons[do_item.key] = btn_on
        self._buttons.extend([btn_off, btn_on])
        row.addWidget(btn_on, 1)
        row.addWidget(btn_off, 1)
        return box

    def _build_di_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        title = QLabel("传感器（DI）")
        title.setObjectName("Title")
        layout.addWidget(title)

        for di_item in motion_cfg.DI_INPUTS:
            layout.addWidget(self._build_di_item(di_item), 1)
        layout.addStretch(1)
        return card

    def _build_di_item(self, di_item: DiInputMap) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        name = QLabel(f"{di_item.label}（{di_item.x_name}）")
        name.setObjectName("Name")
        row.addWidget(name)

        status = QLabel("无瓶")
        status.setObjectName("Status")
        self._di_status_labels[di_item.key] = status
        row.addWidget(status)
        return box

    def _build_scanner_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        title = QLabel(f"扫码枪 · {scanner_cfg.SCANNER_MODEL}")
        title.setObjectName("Title")
        layout.addWidget(title)

        self._scanner_status_label = QLabel("状态：未测试")
        self._scanner_status_label.setObjectName("Status")
        layout.addWidget(self._scanner_status_label)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btn_test = _make_btn("连接")
        btn_scan = _make_btn("扫码", primary=True)
        btn_clear = _make_btn("清空")
        btn_test.clicked.connect(
            lambda: self._run_scanner_action(scanner_test_connection, "扫码枪连接测试")
        )
        btn_scan.clicked.connect(
            lambda: self._run_scanner_action(scanner_trigger_read, "触发扫码")
        )
        btn_clear.clicked.connect(
            lambda: self._run_scanner_action(scanner_clear_result, "清空读码结果")
        )
        self._buttons.extend([btn_test, btn_scan, btn_clear])
        btns.addWidget(btn_test, 1)
        btns.addWidget(btn_scan, 1)
        btns.addWidget(btn_clear, 1)
        layout.addLayout(btns)

        self._scanner_result_label = QLabel("读码：—")
        self._scanner_result_label.setObjectName("Result")
        layout.addWidget(self._scanner_result_label)
        return card

    def _build_balance_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        title = QLabel(f"天平 · {balance_cfg.BALANCE_MODEL}")
        title.setObjectName("Title")
        layout.addWidget(title)

        self._balance_status_label = QLabel("状态：未测试")
        self._balance_status_label.setObjectName("Status")
        layout.addWidget(self._balance_status_label)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        btn_test = _make_btn("连接")
        btn_read = _make_btn("读重", primary=True)
        btn_tare = _make_btn("去皮")
        btn_test.clicked.connect(
            lambda: self._run_balance_action(balance_test_connection, "天平连接测试")
        )
        btn_read.clicked.connect(
            lambda: self._run_balance_action(balance_read_weight, "读取重量")
        )
        btn_tare.clicked.connect(lambda: self._run_balance_action(balance_tare, "去皮"))
        self._buttons.extend([btn_test, btn_read, btn_tare])
        row1.addWidget(btn_test, 1)
        row1.addWidget(btn_read, 1)
        row1.addWidget(btn_tare, 1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        btn_zero = _make_btn("清零")
        btn_clear = _make_btn("清空")
        btn_zero.clicked.connect(lambda: self._run_balance_action(balance_zero, "清零"))
        btn_clear.clicked.connect(
            lambda: self._run_balance_action(balance_clear_result, "清空读重结果")
        )
        self._buttons.extend([btn_zero, btn_clear])
        row2.addWidget(btn_zero, 1)
        row2.addWidget(btn_clear, 1)
        row2.addStretch(1)
        layout.addLayout(row2)

        self._balance_result_label = QLabel("读重：—")
        self._balance_result_label.setObjectName("Result")
        layout.addWidget(self._balance_result_label)
        return card

    def _compact_status(self, axis_key: str) -> str:
        if self._ctx is None:
            return "未读取"
        st = self._ctx.motion.axis(axis_key)
        axis = motion_cfg.AXIS_BY_KEY[axis_key]
        if axis.key == _STIR_AXIS_KEY:
            flags = ["ON" if st.servo_enabled else "OFF", "速度轴"]
        else:
            flags = ["ON" if st.servo_enabled else "OFF", "基准" if st.datum_ok else "未基准"]
        if st.moving:
            flags.append("运动")
        if st.alarm:
            flags.append("报警")
        if axis_key == _INDEXING_AXIS_KEY:
            station = self._ctx.motion.indexing_station
            rotate = "可转" if self._ctx.motion.indexing_rotation_allowed else "禁转"
            return (
                f"{' '.join(flags)}  {rotate}  工位{station} "
                f"{st.actual_position:g}{axis.unit}"
            )
        return f"{' '.join(flags)}  {st.actual_position:g}{axis.unit}"

    def _load_motion_params_from_state(self) -> None:
        if self._ctx is None:
            return
        for axis in motion_cfg.AXES:
            st = self._ctx.motion.axis(axis.key)
            target_input = self._target_inputs.get(axis.key)
            vel_input = self._velocity_inputs.get(axis.key)
            if target_input is not None:
                target_input.setText(f"{st.target_position:g}")
            if vel_input is not None:
                vel_input.setText(f"{st.velocity:g}")

    def _sync_axis_params(self, axis_key: str) -> None:
        if self._ctx is None:
            return
        st = self._ctx.motion.axis(axis_key)
        target_input = self._target_inputs.get(axis_key)
        vel_input = self._velocity_inputs.get(axis_key)
        if target_input is not None and target_input.hasAcceptableInput():
            st.target_position = float(target_input.text())
        if vel_input is not None and vel_input.hasAcceptableInput():
            st.velocity = float(vel_input.text())

    def _sync_all_params(self) -> None:
        for axis in motion_cfg.AXES:
            self._sync_axis_params(axis.key)

    def _refresh_status(self) -> None:
        if self._ctx is None or self._status_label is None:
            return
        motion = self._ctx.motion
        mode = "仿真" if motion.simulation_mode else "联机"
        conn = "已连接" if motion.plc_connected or motion.simulation_mode else "未连接"
        self._status_label.setText(
            f"PLC：{conn} · {mode} · 最近：{motion.last_action or '—'}"
        )
        for axis in motion_cfg.AXES:
            label = self._axis_status_labels.get(axis.key)
            if label is not None:
                label.setText(self._compact_status(axis.key))
        self._update_do_controls()
        self._update_di_labels()

    def _refresh_scanner_status(self) -> None:
        if self._scanner_ctx is None:
            return
        st = self._scanner_ctx.scanner
        if self._scanner_status_label is not None:
            mode = "仿真" if st.simulation_mode else "联机"
            conn = "已连接" if st.connected or st.simulation_mode else "未连接"
            self._scanner_status_label.setText(
                f"状态：{conn} · {mode} · 最近：{st.last_action or '—'}"
            )
        if self._scanner_result_label is not None:
            barcode = st.last_barcode or "—"
            history = " / ".join(st.read_history[:3]) if st.read_history else ""
            text = f"读码：{barcode}"
            if history:
                text += f"  ·  历史：{history}"
            self._scanner_result_label.setText(text)

    def _refresh_balance_status(self) -> None:
        if self._balance_ctx is None:
            return
        st = self._balance_ctx.balance
        if self._balance_status_label is not None:
            mode = "仿真" if st.simulation_mode else "联机"
            conn = "已连接" if st.connected or st.simulation_mode else "未连接"
            self._balance_status_label.setText(
                f"状态：{conn} · {mode} · 最近：{st.last_action or '—'}"
            )
        if self._balance_result_label is not None:
            weight = st.last_weight_display or "—"
            text = f"读重：{weight}"
            if st.last_raw:
                text += f"  ·  原始：{st.last_raw}"
            self._balance_result_label.setText(text)

    def _update_do_controls(self) -> None:
        if self._ctx is None:
            return
        for do_item in motion_cfg.DO_OUTPUTS:
            on = self._ctx.motion.do_states.get(do_item.key, False)
            status_label = self._do_status_labels.get(do_item.key)
            if status_label is not None:
                status_label.setText("开启" if on else "关闭")
            btn_on = self._do_on_buttons.get(do_item.key)
            btn_off = self._do_off_buttons.get(do_item.key)
            if btn_on is not None:
                btn_on.setObjectName("PrimaryBtn" if on else "")
                btn_on.setStyleSheet(_STYLESHEET)
            if btn_off is not None:
                btn_off.setObjectName("DangerBtn" if not on else "")
                btn_off.setStyleSheet(_STYLESHEET)

    def _update_di_labels(self) -> None:
        if self._ctx is None:
            return
        for di_item in motion_cfg.DI_INPUTS:
            present = self._ctx.motion.di_states.get(di_item.key, False)
            label = self._di_status_labels.get(di_item.key)
            if label is not None:
                label.setText("有瓶" if present else "无瓶")

    def _poll_di(self) -> None:
        if (
            self._di_closing
            or self._ctx is None
            or self._thread is not None
            or self._di_busy
        ):
            return
        self._di_busy = True
        self._di_pool.start(_DiPollTask(self._di_bridge))

    def _on_di_polled(self, status, err: str) -> None:
        self._di_busy = False
        if self._di_closing or self._ctx is None:
            return
        if status is not None:
            apply_status_to_state(self._ctx.motion, status)
            self._ctx.motion.set_indexing_station(
                get_indexing_disc().current_station
            )
            self._refresh_status()
        if err and self._thread is None:
            self._append_log(False, err, "读状态")

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for btn in self._buttons:
            btn.setEnabled(enabled)
        if self._indexing_station_combo is not None:
            self._indexing_station_combo.setEnabled(enabled)
        # 运动过程中停止必须可点
        for btn in self._stop_buttons:
            btn.setEnabled(True)

    def _run_axis_stop(self, axis_key: str) -> None:
        """停止可打断正在进行的定位/回零：与动作线程并行下发 CmdStop。"""
        if self._ctx is None:
            return
        if self._reject_if_system_busy():
            return
        action_name = f"{motion_cfg.AXIS_BY_KEY[axis_key].label} 停止"
        # 先置取消标志，让定位/回零轮询尽快退出并自写 CmdStop
        get_motion_driver().request_stop()
        if self._stop_thread is not None:
            return
        self._stop_thread = _AxisActionThread(
            motion_axis_stop, self._ctx, axis_key, action_name
        )
        self._arm_worker(self._stop_thread, self._on_stop_finished)

    def _on_stop_finished(self, ok: bool, msg: str, action_name: str) -> None:
        self._stop_thread = None
        self._refresh_status()
        self._append_log(ok, msg, action_name)

    def _append_log(self, ok: bool, msg: str, action_name: str) -> None:
        prefix = "成功" if ok else "失败"
        text = f"日志：{prefix} · {action_name} · {msg}"
        if self._log_label is not None:
            self._log_label.setText(text)
        if self._action_log_handler is not None:
            self._action_log_handler(text)

    def _on_action_finished(self, ok: bool, msg: str, action_name: str) -> None:
        self._thread = None
        self._set_buttons_enabled(True)
        self._refresh_status()
        self._refresh_scanner_status()
        self._refresh_balance_status()
        self._append_log(ok, msg, action_name)

    def _run_global_action(self, fn: ActionFn, action_name: str) -> None:
        if self._ctx is None or self._thread is not None:
            return
        if self._reject_if_system_busy():
            return
        self._sync_all_params()
        self._set_buttons_enabled(False)
        self._thread = _MotionActionThread(fn, self._ctx, action_name)
        self._arm_worker(self._thread, self._on_action_finished)

    def _run_axis_action(
        self,
        fn: Callable[[SolidDoserMotionContext, str], Tuple[bool, str]],
        axis_key: str,
        action_name: str,
    ) -> None:
        if self._ctx is None or self._thread is not None:
            return
        if self._reject_if_system_busy():
            return
        self._sync_axis_params(axis_key)
        self._set_buttons_enabled(False)
        self._thread = _AxisActionThread(fn, self._ctx, axis_key, action_name)
        self._arm_worker(self._thread, self._on_action_finished)

    def _run_horizontal_go_origin(self) -> None:
        if self._ctx is None or self._thread is not None:
            return
        if self._reject_if_system_busy():
            return
        self._sync_axis_params(_HORIZONTAL_AXIS_KEY)
        self._set_buttons_enabled(False)
        self._thread = _MotionActionThread(
            motion_horizontal_go_origin,
            self._ctx,
            "水平电机 回原点",
        )
        self._arm_worker(self._thread, self._on_action_finished)

    def _run_indexing_go_origin(self) -> None:
        if self._ctx is None or self._thread is not None:
            return
        if self._reject_if_system_busy():
            return
        self._sync_axis_params(_INDEXING_AXIS_KEY)
        self._set_buttons_enabled(False)
        self._thread = _MotionActionThread(
            motion_indexing_go_origin,
            self._ctx,
            "分度电机 回原点",
        )
        self._arm_worker(self._thread, self._on_action_finished)

    def _run_indexing_go_station(self) -> None:
        if self._ctx is None or self._thread is not None:
            return
        if self._reject_if_system_busy():
            return
        combo = self._indexing_station_combo
        if combo is None:
            return
        station = int(combo.currentData())
        self._sync_axis_params(_INDEXING_AXIS_KEY)
        self._set_buttons_enabled(False)
        self._thread = _MotionActionThread(
            lambda ctx, s=station: indexing_disc_go_to_station(ctx, s),
            self._ctx,
            f"分度盘 → 工位 {station}",
        )
        self._arm_worker(self._thread, self._on_action_finished)

    def _run_lift_go_origin(self) -> None:
        if self._ctx is None or self._thread is not None:
            return
        if self._reject_if_system_busy():
            return
        self._sync_axis_params(_LIFT_AXIS_KEY)
        self._set_buttons_enabled(False)
        self._thread = _MotionActionThread(
            motion_lift_go_origin,
            self._ctx,
            "升降电机 回原点",
        )
        self._arm_worker(self._thread, self._on_action_finished)

    def _run_powder_go_origin(self) -> None:
        if self._ctx is None or self._thread is not None:
            return
        if self._reject_if_system_busy():
            return
        self._sync_axis_params(_POWDER_AXIS_KEY)
        self._set_buttons_enabled(False)
        self._thread = _MotionActionThread(
            motion_powder_go_origin,
            self._ctx,
            "承粉电机 回原点",
        )
        self._arm_worker(self._thread, self._on_action_finished)

    def _run_do_action(self, do_key: str, on: bool, action_name: str) -> None:
        if self._ctx is None or self._thread is not None:
            return
        if self._reject_if_system_busy():
            return
        if self._di_busy:
            self._di_pool.waitForDone(500)
        self._set_buttons_enabled(False)
        self._thread = _MotionActionThread(
            lambda ctx: motion_do_set(ctx, do_key, on),
            self._ctx,
            action_name,
        )
        self._arm_worker(self._thread, self._on_action_finished)

    def _run_scanner_action(self, fn: ScannerActionFn, action_name: str) -> None:
        if self._scanner_ctx is None or self._thread is not None:
            return
        if self._reject_if_system_busy():
            return
        self._set_buttons_enabled(False)
        self._thread = _ScannerActionThread(fn, self._scanner_ctx, action_name)
        self._arm_worker(self._thread, self._on_action_finished)

    def _run_balance_action(self, fn: BalanceActionFn, action_name: str) -> None:
        if self._balance_ctx is None or self._thread is not None:
            return
        if self._reject_if_system_busy():
            return
        self._set_buttons_enabled(False)
        self._thread = _BalanceActionThread(fn, self._balance_ctx, action_name)
        self._arm_worker(self._thread, self._on_action_finished)
