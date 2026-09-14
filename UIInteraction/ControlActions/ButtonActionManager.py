import os
import tempfile

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QMessageBox

from DateBaseManager.database_manager import get_active_process_file
from ActionSequence.execute_sequence import (
    Import_Process_Bond,
    generate_execution_sequence,
    process_parameters_by_function,
)
from BusinessActions.DeviceManager import DeviceManager
from BusinessActions.UIFeedback.UIFeedbackHandler import UIFeedbackHandler
from Common.ActionLogger import get_action_logger
from UIInteraction.ParameterManagement.ParameterStorage import ParameterStorage
from UIInteraction.UIGenerator.MainUI import MainUI


class ProcessExecutionThread(QThread):
    finished = Signal(str)
    error = Signal(str)
    status = Signal(str)
    progress = Signal(int, int)
    interrupted = Signal()
    process_started = Signal(str, int)

    def __init__(self, device_manager: DeviceManager, parent=None):
        super().__init__(parent)
        self.device_manager = device_manager
        self.is_running = False

    def run(self):
        self.is_running = True
        try:
            filename, content = get_active_process_file()
            if not filename or not content:
                self.error.emit("没有找到可执行的工艺文件，请先导入工艺文件！")
                return

            self.status.emit(f"开始执行工艺文件：{filename}")
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            ) as temp_file:
                temp_file.write(content)
                temp_file_path = temp_file.name

            try:
                exec_seq = generate_execution_sequence(temp_file_path)
                if not exec_seq:
                    self.error.emit("工艺文件中没有有效命令！")
                    return

                processed_seq = process_parameters_by_function(exec_seq, self.device_manager)
                total = len(processed_seq)
                self.process_started.emit(filename, total)
                self.progress.emit(0, total)

                ok = self._execute_sequence(processed_seq)
                if ok:
                    self.progress.emit(total, total)
                    self.finished.emit(f"工艺文件执行完成：{filename}")
                elif not self.is_running:
                    self.interrupted.emit()
            finally:
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
        except Exception as e:
            self.error.emit(f"执行工艺文件时发生错误：{str(e)}")

    def _execute_sequence(self, sequence):
        if not sequence:
            return True
        for idx, (func, args) in enumerate(sequence, 1):
            if not self.is_running:
                return False
            self.progress.emit(idx, len(sequence))
            self.status.emit(f"执行第 {idx}/{len(sequence)} 个命令：{func.__name__}")
            try:
                args_repr = str(args)
                if len(args_repr) > 200:
                    args_repr = args_repr[:200] + "..."
                get_action_logger().record(
                    f"工艺流程步骤 {idx}/{len(sequence)}: {func.__name__} 参数: {args_repr}"
                )
                result = func(*args)
                if (
                    isinstance(result, tuple)
                    and len(result) >= 1
                    and result[0] is False
                ):
                    detail = result[1] if len(result) > 1 else "执行失败"
                    self.error.emit(f"第 {idx} 个命令执行失败：{detail}")
                    return False
            except Exception as e:
                self.error.emit(f"第 {idx} 个命令执行失败：{str(e)}")
                return False
        return True

    def stop(self):
        self.is_running = False


class ButtonActionManager:
    def __init__(
        self,
        main_window: MainUI,
        device_manager: DeviceManager,
        param_storage: ParameterStorage,
        ui_feedback: UIFeedbackHandler,
    ):
        self.main_window = main_window
        self.device_manager = device_manager
        self.param_storage = param_storage
        self.ui_feedback = ui_feedback
        self.process_execution_thread = None
        self.setup_button_connections()

    def _logged_call(self, description: str, fn, *args, **kwargs):
        get_action_logger().record(description)
        try:
            result = fn(*args, **kwargs)
            self.main_window.set_action_log(description)
            return result
        except Exception as exc:  # noqa: BLE001
            self.main_window.set_action_log(f"失败 · {description} · {exc}")
            raise

    def setup_button_connections(self):
        self.main_window.btn_import_process.clicked.connect(
            lambda: self._logged_call(
                "工艺流程：从文件导入",
                Import_Process_Bond,
                self.main_window.table_process,
            )
        )
        self.main_window.btn_execute_process.clicked.connect(self.execute_process_async)

    def execute_process_async(self):
        if (
            self.process_execution_thread is not None
            and self.process_execution_thread.isRunning()
        ):
            self.process_execution_thread.stop()
            self.process_execution_thread.wait()

        self.param_storage.is_system_busy = True
        self.param_storage.process_execution_running = True
        self.param_storage.process_execution_filename = ""
        self.param_storage.process_execution_current_step = 0
        self.param_storage.process_execution_total_steps = 0

        self.main_window.label_progress.setText("执行进度: 0/0")
        self.main_window.progress_bar.setRange(0, 1)
        self.main_window.progress_bar.setValue(0)
        self.main_window.set_action_log("工艺流程执行中…")

        self.process_execution_thread = ProcessExecutionThread(self.device_manager)
        self.process_execution_thread.status.connect(self._on_execution_status)
        self.process_execution_thread.progress.connect(self._on_process_progress)
        self.process_execution_thread.finished.connect(self._on_execution_finished)
        self.process_execution_thread.error.connect(self._on_execution_error)
        self.process_execution_thread.interrupted.connect(self._on_process_interrupted)
        self.process_execution_thread.process_started.connect(self._on_process_started)

        get_action_logger().record("开始异步执行工艺流程（UI）")
        self.process_execution_thread.start()

    def _on_execution_status(self, message):
        print(f"📋 {message}")
        self.main_window.set_action_log(message)

    def _on_process_progress(self, current: int, total: int):
        if total <= 0:
            self.main_window.label_progress.setText("执行进度: 0/0")
            self.main_window.progress_bar.setRange(0, 1)
            self.main_window.progress_bar.setValue(0)
            self.param_storage.process_execution_current_step = 0
            self.param_storage.process_execution_total_steps = 0
            return
        self.param_storage.process_execution_current_step = current
        self.param_storage.process_execution_total_steps = total
        self.main_window.label_progress.setText(f"执行进度: {current}/{total}")
        self.main_window.progress_bar.setRange(0, total)
        self.main_window.progress_bar.setValue(min(max(current, 0), total))

    def _on_process_started(self, filename: str, total: int):
        self.param_storage.process_execution_filename = filename or ""
        self.param_storage.process_execution_total_steps = total
        self.param_storage.process_execution_current_step = 0
        self.main_window.set_action_log(f"开始执行工艺文件：{filename}")

    def _on_execution_finished(self, message):
        print(f"✅ {message}")
        total = self.main_window.progress_bar.maximum()
        if total > 0:
            self.main_window.label_progress.setText(f"执行进度: {total}/{total}")
            self.main_window.progress_bar.setValue(total)
        self.main_window.set_action_log(f"成功 · {message}")
        self.param_storage.is_system_busy = False
        self._clear_process_execution_state()

    def _on_execution_error(self, message):
        print(f"❌ {message}")
        self.main_window.label_progress.setText("执行进度: 0/0")
        self.main_window.progress_bar.setRange(0, 1)
        self.main_window.progress_bar.setValue(0)
        self.main_window.set_action_log(f"失败 · {message}")
        self.ui_feedback.show_error("执行错误", message)
        self.param_storage.is_system_busy = False
        self._clear_process_execution_state()

    def _on_process_interrupted(self):
        self.main_window.label_progress.setText("执行进度: 0/0")
        self.main_window.progress_bar.setRange(0, 1)
        self.main_window.progress_bar.setValue(0)
        self.main_window.set_action_log("工艺流程已中断")
        self.param_storage.is_system_busy = False
        self._clear_process_execution_state()

    def _clear_process_execution_state(self):
        self.param_storage.process_execution_running = False
        self.param_storage.process_execution_filename = ""
        self.param_storage.process_execution_current_step = 0
        self.param_storage.process_execution_total_steps = 0
