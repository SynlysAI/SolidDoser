"""SolidDoser 轻量启动：流程导入 + 自动 + 调试 + 主系统进料 UDP。"""
import sys

from PySide6.QtWidgets import QApplication

from BusinessActions.DeviceManager import DeviceManager
from BusinessActions.HostComm.bottle_feed_udp import start_bottle_feed_udp
from BusinessActions.SolidDoserSystem.system_context import SolidDoserSystemContext
from BusinessActions.UIFeedback.UIFeedbackHandler import UIFeedbackHandler
from Common.ActionLogger import get_action_logger
from Common.AppLogging import setup_app_logging, stop_log_maintenance
from Common.LocalDatabase import init_schema
from Common.MotionSettingsStore import load_motion_settings
from Common.StationMaterialsStore import load_station_materials
from UIInteraction.ControlActions.ButtonActionManager import ButtonActionManager
from UIInteraction.ParameterManagement.ParameterStorage import ParameterStorage
from UIInteraction.UIGenerator.MainUI import MainUI


if __name__ == "__main__":
    setup_app_logging()
    app = QApplication(sys.argv)
    get_action_logger().record("应用启动")
    init_schema()
    motion_loaded, motion_detail = load_motion_settings()
    if motion_loaded:
        get_action_logger().record(f"已加载运动参数：{motion_detail}")
    main_window = MainUI()
    param_storage = ParameterStorage()
    loaded, load_detail = load_station_materials(param_storage.solid_doser_system)
    if loaded:
        get_action_logger().record(f"已加载上次物料条码：{load_detail}")
    main_window.bind_solid_doser(param_storage)
    ui_feedback = UIFeedbackHandler(main_window)
    device_manager = DeviceManager(param_storage)
    ButtonActionManager(main_window, device_manager, param_storage, ui_feedback)
    main_window.setWindowTitle("SolidDoser 控制软件")
    main_window.show()

    bottle_feed_bridge = start_bottle_feed_udp(SolidDoserSystemContext(param_storage))

    def _on_quit() -> None:
        bottle_feed_bridge.stop()
        stop_log_maintenance()
        get_action_logger().persist()

    app.aboutToQuit.connect(_on_quit)
    sys.exit(app.exec())
