# ------------------------------
# 第一步：添加已实现的现有实际动作函数
# ------------------------------
import sys
import os
import tempfile
import json
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem


# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 导入数据库管理模块
from DateBaseManager.database_manager import save_process_file, get_active_process_file

from BusinessActions.SingleStepActions.MotorAction import Wait
from BusinessActions.DeviceManager import DeviceManager
from BusinessActions.ProcessActions import process_auto_actions as auto
from UIInteraction.ParameterManagement.ParameterStorage import ParameterStorage
from Common.ActionLogger import get_action_logger




# ------------------------------
# 第二步：核心配置（命令名→函数映射）
# ------------------------------
# 关键：TXT中的命令名必须和字典的key完全一致（中英均可）
command_to_func = {
    "WAIT": Wait,
    "DEVICE_INIT": auto.DEVICE_INIT,
    "SERVO_ON": auto.SERVO_ON,
    "GO_DATUM": auto.GO_DATUM,
    "GO_ORIGIN": auto.GO_ORIGIN,
    "MATERIAL_INIT": auto.MATERIAL_INIT,
    "DOSE": auto.DOSE,
    "BOTTLE_DISCHARGE": auto.BOTTLE_DISCHARGE,
    "设备初始化": auto.DEVICE_INIT,
    "使能": auto.SERVO_ON,
    "回基准点": auto.GO_DATUM,
    "回原点": auto.GO_ORIGIN,
    "物料初始化": auto.MATERIAL_INIT,
    "加料": auto.DOSE,
    "加样": auto.DOSE,
    "试剂瓶出料": auto.BOTTLE_DISCHARGE,
}

# ------------------------------
# 第三步：读取TXT生成执行序列
# ------------------------------
def generate_execution_sequence(file_path):
    """读取TXT命令，生成 (函数对象, 参数列表) 的可执行序列"""
    execution_sequence = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            for line_num, line in enumerate(file, 1):
                stripped_line = line.strip()
                
                # 过滤注释行（#开头）和空行
                if not stripped_line or stripped_line.startswith('#'):
                    continue
                
                # 只处理S开头的有效命令
                if stripped_line.startswith('S '):
                    try:
                        # 提取命令名（S 后面 → ( 前面）
                        bracket_left = stripped_line.find('(')
                        bracket_right = stripped_line.find(')')
                        if bracket_left == -1 or bracket_right == -1:
                            raise ValueError("缺少 '(' 或 ')'")
                        
                        command_name = stripped_line[len('S '):bracket_left].strip()
                        if not command_name:
                            raise ValueError("命令名为空")
                        
                        # 提取参数（( 里面 → ) 前面，按逗号分割）
                        param_str = stripped_line[bracket_left+1:bracket_right].strip()
                        parameters = [p.strip() for p in param_str.split(',')] if param_str else []
                        
                        # 检查命令是否有对应函数
                        if command_name not in command_to_func:
                            raise ValueError(f"无对应执行函数")
                        
                        # 加入执行序列（函数对象 + 参数列表）
                        execution_sequence.append( (command_to_func[command_name], parameters) )
                        
                    except Exception as e:
                        print(f"⚠️  第{line_num}行命令无效，跳过：{e} | 原始内容：{stripped_line}")
    
    except FileNotFoundError:
        print(f"❌ 错误：找不到文件 {file_path}，请检查文件路径是否正确")
    except Exception as e:
        print(f"❌ 读取文件失败：{e}")
    
    return execution_sequence

def generate_execution_sequence_from_content(content):
    """
    根据传入的文本内容生成 (函数对象, 参数列表) 的可执行序列
    
    :param content: 工艺文件的文本内容
    :return: 执行序列，格式为 [(函数对象, 参数列表), ...]
    """
    execution_sequence = []
    
    try:
        # 将文本内容按行分割
        lines = content.splitlines()
        
        for line_num, line in enumerate(lines, 1):
            stripped_line = line.strip()
            
            # 过滤注释行（#开头）和空行
            if not stripped_line or stripped_line.startswith('#'):
                continue
            
            # 只处理S开头的有效命令
            if stripped_line.startswith('S '):
                try:
                    # 提取命令名（S 后面 → ( 前面）
                    bracket_left = stripped_line.find('(')
                    bracket_right = stripped_line.find(')')
                    if bracket_left == -1 or bracket_right == -1:
                        raise ValueError("缺少 '(' 或 ')'")
                    
                    command_name = stripped_line[len('S '):bracket_left].strip()
                    if not command_name:
                        raise ValueError("命令名为空")
                    
                    # 提取参数（( 里面 → ) 前面，按逗号分割）
                    param_str = stripped_line[bracket_left+1:bracket_right].strip()
                    parameters = [p.strip() for p in param_str.split(',')] if param_str else []
                    
                    # 检查命令是否有对应函数
                    if command_name not in command_to_func:
                        raise ValueError(f"无对应执行函数")
                    
                    # 加入执行序列（函数对象 + 参数列表）
                    execution_sequence.append( (command_to_func[command_name], parameters) )
                    
                except Exception as e:
                    print(f"⚠️  第{line_num}行命令无效，跳过：{e} | 原始内容：{stripped_line}")
    
    except Exception as e:
        print(f"❌ 处理文本内容失败：{e}")
    
    return execution_sequence


# ------------------------------
# 第四步：根据函数名称处理参数
# ------------------------------
def process_parameters_by_function(sequence, device_manager):
    """根据函数名称对参数做不同处理
    
    Args:
        sequence: 原始执行序列，格式为 [(函数对象, 参数列表), ...]
        device_manager: DeviceManager实例，用于传递给需要设备访问的函数
        
    Returns:
        list: 处理后的执行序列，格式与输入相同
    """
    processed_sequence = []
    
    if not sequence:
        return processed_sequence
    
    for func, args in sequence:
        # 创建参数副本以避免修改原始参数
        processed_args = args.copy()
        func_name = func.__name__
        
        # 处理每个参数，进行类型转换
        for i in range(len(processed_args)):
            param = processed_args[i].strip().lower()
            
            # 1. 如果参数中有 "reactor" 字样，则提取后面的数字作为int
            if "reactor" in param:
                # 提取reactor后面的数字部分
                import re
                match = re.search(r'reactor_(\d+)', param)
                if match:
                    processed_args[i] = int(match.group(1))
            # 2. 如果参数中有 "post" 字样，则提取后面的数字作为int
            elif "post" in param:
                # 提取post后面的数字部分
                import re
                match = re.search(r'post(\d+)', param)
                if match:
                    processed_args[i] = int(match.group(1))
        # 再次遍历参数，将文本数字转换为int或float
        for i in range(len(processed_args)):
            arg = processed_args[i]
            # 跳过device_manager对象
            if arg is device_manager:
                continue
            # 如果是字符串，尝试转换为数字
            if isinstance(arg, str):
                # 尝试先转int
                try:
                    processed_args[i] = int(arg)
                    continue
                except ValueError:
                    pass
                # 再尝试转float
                try:
                    processed_args[i] = float(arg)
                except ValueError:
                    # 转换失败则保持原字符串
                    pass

        # 除了Wait函数外，其他函数都将device_manager添加到第一个参数位置
        if func_name.upper() != "WAIT":
            # 将device_manager添加到参数列表的第一个位置
            processed_args.insert(0, device_manager)
        elif func_name == "Wait":
            # Wait函数不添加device_manager参数
            pass
        
        # 将处理后的参数添加到新序列
        processed_sequence.append((func, processed_args))
    
    return processed_sequence


# ------------------------------
# 第五步：按序列执行函数
# ------------------------------
def execute_sequence(sequence):
    """遍历序列，按顺序执行每个函数；失败则中断。"""
    logger = get_action_logger()
    print("="*60)
    print("📋 命令序列执行开始")
    print("="*60)
    
    if not sequence:
        print("⚠️  未提取到任何有效命令")
        return
    
    for idx, (func, args) in enumerate(sequence, 1):
        print(f"\n【第{idx}/{len(sequence)}个命令】")
        print(f"命令名：{func.__name__}")
        print(f"参数：{args}")
        try:
            result = func(*args)
            if (
                isinstance(result, tuple)
                and len(result) >= 1
                and result[0] is False
            ):
                detail = result[1] if len(result) > 1 else "执行失败"
                raise RuntimeError(str(detail))
            print("状态：执行成功")
        except Exception as e:
            print(f"状态：执行失败 | 错误原因：{e}")
            logger.record(f"工艺序列中断：第{idx}步 {func.__name__} · {e}")
            raise
    
    print("\n" + "="*60)
    print("🏁 命令序列执行结束")
    print("="*60)


# 工艺文件导入按钮的绑定函数
def Import_Process_Bond(table_widget):
    """工艺导入按钮的绑定函数
    
    功能：打开文件选择框选择txt文件，读取文件内容并显示在传入的PySide6表格中
    
    参数:
        table_widget: PySide6.QtWidgets.QTableWidget - 用于显示数据的表格控件
    """
    try:
        # 使用PySide6的文件对话框
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from PySide6.QtCore import Qt
        import shutil
        
        # 打开文件选择对话框，限制只能选择txt文件
        file_path, _ = QFileDialog.getOpenFileName(
            caption="选择工艺文件",
            filter="文本文件 (*.txt);;所有文件 (*)"
        )
        
        # 如果用户取消选择，直接返回
        if not file_path:
            print("❌ 文件选择已取消")
            return
        
        print(f"📂 已选择文件：{file_path}")
        
        # 读取文件内容并保存到数据库
        with open(file_path, 'r', encoding='utf-8') as file:
            file_content = file.read()
        
        # 保存文件到数据库
        original_filename = os.path.basename(file_path)
        save_process_file(original_filename, file_content)
        print(f"✅ 工艺文件已保存到数据库")
        
        # 使用generate_execution_sequence函数处理文件
        exec_seq = generate_execution_sequence(file_path)
        
        # 清空现有表格内容
        table_widget.setRowCount(0)
        
        # 确保表格有足够的列（根据MainUI中的设置，我们使用2列：函数名和参数）
        if table_widget.columnCount() < 2:
            table_widget.setColumnCount(2)
        
        # 填充表格数据
        if exec_seq:
            for i, (func, args) in enumerate(exec_seq):
                # 查找函数名对应的命令名
                cmd_name = "未知命令"
                for cmd, f in command_to_func.items():
                    if f == func:
                        cmd_name = cmd
                        break
                
                # 添加新行
                table_widget.insertRow(i)
                
                # 设置单元格内容
                # 第0列：命令名
                command_item = QTableWidgetItem(cmd_name)
                command_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                command_item.setFlags(command_item.flags() & ~Qt.ItemIsEditable)  # 设为不可编辑
                table_widget.setItem(i, 0, command_item)
                
                # 第1列：参数列表
                params_item = QTableWidgetItem(str(tuple(args)))
                params_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                params_item.setFlags(params_item.flags() & ~Qt.ItemIsEditable)  # 设为不可编辑
                table_widget.setItem(i, 1, params_item)
            
            # 自动调整列宽
            table_widget.horizontalHeader().setStretchLastSection(True)
        else:
            # 如果没有数据，显示提示信息
            table_widget.insertRow(0)
            
            no_data_item = QTableWidgetItem("未读取到任何有效命令")
            no_data_item.setTextAlignment(Qt.AlignCenter)
            no_data_item.setForeground(Qt.red)
            no_data_item.setFlags(no_data_item.flags() & ~Qt.ItemIsEditable)
            
            # 合并单元格显示提示信息
            table_widget.setItem(0, 0, no_data_item)
            table_widget.setSpan(0, 0, 1, 2)  # 合并第0行的0-1列
            
        # 发出信号通知表格内容已更新
        table_widget.viewport().update()
        
        # 显示成功消息
        print(f"✅ 成功导入工艺文件，共读取到 {len(exec_seq)} 条命令")
        
    except Exception as e:
        error_msg = f"导入工艺文件时发生错误：{e}"
        print(f"❌ {error_msg}")
        
        # 尝试显示PySide6错误消息框
        try:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                None,  # 或者传入table_widget作为父窗口
                "错误", 
                f"导入工艺文件失败！\n错误原因：{str(e)}"
            )
        except:
            # 如果PySide6消息框也失败，就只打印错误信息
            pass
    
# 从数据库直接导入工艺文件显示到特定表格
def Import_Process_UDP(table_widget):
    """从数据库直接导入工艺文件显示到特定表格

    功能：从数据库中读取当前活跃的工艺文件，解析并显示到指定的表格中
    """
    from PySide6.QtCore import Qt
    # 从数据库获取当前活跃的工艺文件
    filename, content = get_active_process_file()
    print(f"当前活跃的工艺文件：{filename}")
    #把活跃的工艺文件内容保存到临时路径上，再使用临时路径作为参数进行解析
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as temp_file:
        temp_file.write(content)
        temp_file_path = temp_file.name
    exec_seq = generate_execution_sequence(temp_file_path)
    _populate_process_table(table_widget, exec_seq, Qt)
    
    # 显示成功消息
    print(f"✅ 成功导入工艺文件，共读取到 {len(exec_seq)} 条命令")
    # 移除临时文件
    os.remove(temp_file_path)


def _populate_process_table(table_widget, exec_seq, qt_module):
    """将工艺解析结果填充到表格中，供不同导入入口复用。"""
    table_widget.setRowCount(0)

    if table_widget.columnCount() < 2:
        table_widget.setColumnCount(2)

    if exec_seq:
        for i, (func, args) in enumerate(exec_seq):
            cmd_name = "未知命令"
            for cmd, mapped_func in command_to_func.items():
                if mapped_func == func:
                    cmd_name = cmd
                    break

            table_widget.insertRow(i)

            command_item = QTableWidgetItem(cmd_name)
            command_item.setTextAlignment(qt_module.AlignLeft | qt_module.AlignVCenter)
            command_item.setFlags(command_item.flags() & ~qt_module.ItemIsEditable)
            table_widget.setItem(i, 0, command_item)

            params_item = QTableWidgetItem(str(tuple(args)))
            params_item.setTextAlignment(qt_module.AlignLeft | qt_module.AlignVCenter)
            params_item.setFlags(params_item.flags() & ~qt_module.ItemIsEditable)
            table_widget.setItem(i, 1, params_item)

        table_widget.horizontalHeader().setStretchLastSection(True)
    else:
        table_widget.insertRow(0)
        no_data_item = QTableWidgetItem("未读取到任何有效命令")
        no_data_item.setTextAlignment(qt_module.AlignCenter)
        no_data_item.setForeground(qt_module.red)
        no_data_item.setFlags(no_data_item.flags() & ~qt_module.ItemIsEditable)
        table_widget.setItem(0, 0, no_data_item)
        table_widget.setSpan(0, 0, 1, 2)

    table_widget.viewport().update()


def Import_Process_By_Path_UDP(table_widget, file_path):
    """
    根据UDP传入路径导入工艺文件，保存到数据库并刷新UI表格。

    :return: dict, 包含 success/message/steps_count 字段
    """
    from PySide6.QtCore import Qt

    try:
        normalized_path = str(file_path).strip().strip('"').strip("'")
        if not normalized_path:
            return {"success": False, "message": "导入失败：文件路径为空", "steps_count": 0}

        if not os.path.exists(normalized_path):
            return {"success": False, "message": f"导入失败：文件不存在 {normalized_path}", "steps_count": 0}

        if not os.path.isfile(normalized_path):
            return {"success": False, "message": f"导入失败：路径不是文件 {normalized_path}", "steps_count": 0}

        with open(normalized_path, "r", encoding="utf-8") as process_file:
            file_content = process_file.read()

        if not file_content.strip():
            return {"success": False, "message": "导入失败：工艺文件为空", "steps_count": 0}

        original_filename = os.path.basename(normalized_path)
        save_process_file(original_filename, file_content)

        exec_seq = generate_execution_sequence(normalized_path)
        _populate_process_table(table_widget, exec_seq, Qt)
        steps_count = len(exec_seq)

        return {
            "success": True,
            "message": f"导入成功：{original_filename}，共{steps_count}条命令",
            "steps_count": steps_count
        }
    except UnicodeDecodeError:
        return {"success": False, "message": "导入失败：文件编码不是UTF-8", "steps_count": 0}
    except Exception as e:
        return {"success": False, "message": f"导入失败：{e}", "steps_count": 0}

    
def Import_Parament_UDP(message,device_manager:DeviceManager,send_response_func=None):
    """
    处理接收到的UDP消息，使用generate_execution_sequence_from_content函数解析，并打印执行序列
    
    :param message: 接收到的UDP消息字符串
    :param device_manager: 设备管理器实例
    :param send_response_func: UDP响应发送函数（可以是预先设定了参数的闭包函数）
    """
    print(f"接收到的原始UDP消息: {message}")
    
    # 获取参数管理器实例
    parameter_storage = device_manager.parameter_storage
    
    # 使用正则表达式解析带参数的消息格式
    import re
    cmd_pattern = re.compile(r'^\S+\s+([A-Z_]+)\(([^)]+)\)$')
    match = cmd_pattern.match(message)
    
    if match:
        # 提取命令和参数
        cmd = match.group(1)
        param = match.group(2).strip()
        print(f"解析到命令: {cmd}, 参数: {param}")
    else:
        # 没有参数的简单命令
        cmd = message.strip()
        param = None
        print(f"解析到命令: {cmd}, 无参数")
    
    if cmd == "GET_PROCESS_EXECUTION_STATE":
        print("处理GET_PROCESS_EXECUTION_STATE请求")
        running = parameter_storage.process_execution_running
        state = {
            "status": "success",
            "command": "GET_PROCESS_EXECUTION_STATE",
            "executing": running,
            "process_name": parameter_storage.process_execution_filename if running else "",
            "current_step": parameter_storage.process_execution_current_step if running else 0,
            "total_steps": parameter_storage.process_execution_total_steps if running else 0,
            "current_command": parameter_storage.process_execution_current_command if running else "",
        }
        if send_response_func:
            try:
                send_response_func(state)
            except Exception as e:
                print(f"发送工艺流程执行状态响应时出错: {e}")
        return
    
    # 如果是其他消息，继续执行原有的处理逻辑
    # 使用generate_execution_sequence_from_content函数解析UDP消息
    execution_sequence = generate_execution_sequence_from_content(message)
    # 打印执行序列内容
    print(f"\n解析后的执行序列:")
    if execution_sequence:
        for idx, (func, args) in enumerate(execution_sequence, 1):
            # 获取函数名
            func_name = func.__name__
            # 查找对应的命令名
            cmd_name = "未知命令"
            for cmd, f in command_to_func.items():
                if f == func:
                    cmd_name = cmd
                    break
            print(f"  {idx}. 命令: {cmd_name}, 函数: {func_name}, 参数: {args}")
    else:
        print("  没有解析到有效的执行序列")
    # 处理参数
    processed_sequence = process_parameters_by_function(execution_sequence, device_manager) 
    
        
    # 执行序列
    execute_sequence(processed_sequence)

    # 执行完后发送UDP响应
    if send_response_func:
        try:
            # 调用预先设定了参数的闭包函数
            send_response_func()
        except Exception as e:
            print(f"❌ 发送UDP响应失败: {e}")
    
    


# ------------------------------
# 工艺执行按钮的绑定函数
# ------------------------------
def Execute_Bond(device_manager:DeviceManager):
    """工艺执行按钮的绑定函数
    
    功能：从数据库中读取当前活跃的工艺文件，解析并执行
    """
    try:
        # 从数据库获取当前活跃的工艺文件
        filename, content = get_active_process_file()
        
        if not filename or not content:
            print("❌ 没有找到可执行的工艺文件")
            # 尝试显示PySide6错误消息框
            try:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    None,
                    "警告",
                    "没有找到可执行的工艺文件，请先导入工艺文件！"
                )
            except:
                pass
            return
        
        print(f"📋 开始执行工艺文件：{filename}")
        
        # 创建临时文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as temp_file:
            temp_file.write(content)
            temp_file_path = temp_file.name
        
        try:
            # 生成执行序列
            exec_seq = generate_execution_sequence(temp_file_path)
            
            if not exec_seq:
                print("❌ 工艺文件中没有有效命令")
                # 尝试显示PySide6错误消息框
                try:
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        None,
                        "警告",
                        "工艺文件中没有有效命令！"
                    )
                except:
                    pass
                return
            
            # 处理参数
            processed_seq = process_parameters_by_function(exec_seq, device_manager)
            
            # 执行序列
            execute_sequence(processed_seq)
            
            print(f"✅ 工艺文件执行完成：{filename}")
            
        finally:
            # 清理临时文件
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
                print(f"🗑️  临时文件已清理：{temp_file_path}")
    
    except Exception as e:
        error_msg = f"执行工艺文件时发生错误：{e}"
        print(f"❌ {error_msg}")
        
        # 尝试显示PySide6错误消息框
        try:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                None,
                "错误",
                f"执行工艺文件失败！\n错误原因：{str(e)}"
            )
        except:
            # 如果PySide6消息框也失败，就只打印错误信息
            pass

# ------------------------------
# 主程序（直接运行入口）
# ------------------------------
if __name__ == "__main__":
    # --------------------------
    # 关键：修改为你的TXT文件路径
    # --------------------------
    # 示例1：文件和.py脚本在同一文件夹（直接写文件名）
    # 使用正斜杠避免转义字符问题
    TXT_FILE_PATH = "C:/Users/93712/Desktop/llm返回指令案例集合/test1.txt"
    # 示例2：文件在其他路径（Windows）
    # TXT_FILE_PATH = "D:/项目文件/示例命令2.txt"
    # 示例3：文件在其他路径（Mac/Linux）
    # TXT_FILE_PATH = "/Users/xxx/项目/示例命令2.txt"
    
    # 1. 生成执行序列
    exec_seq = generate_execution_sequence(TXT_FILE_PATH)
    
    # 打印读取到的执行序列
    print("\n" + "="*60)
    print("📋 读取到的执行序列")
    print("="*60)
    if exec_seq:
        for i, (func, args) in enumerate(exec_seq, 1):
            func_name = func.__name__ if hasattr(func, '__name__') else str(func)
            # 查找函数名对应的命令名
            cmd_name = "未知命令"
            for cmd, f in command_to_func.items():
                if f == func:
                    cmd_name = cmd
                    break
            print(f"步骤 {i}: {cmd_name} -> {func_name}{tuple(args)}")
    else:
        print("❌ 未读取到任何执行序列")
    
    # 2. 创建ParameterStorage和DeviceManager实例
    param_storage = ParameterStorage()
    device_manager = DeviceManager(param_storage)
    
    # 3. 根据函数名称处理参数 - 测试参数类型转换
    print("\n" + "="*60)
    print("🔄 参数类型转换测试")
    print("="*60)
    
    # 调用参数处理函数
    processed_seq = process_parameters_by_function(exec_seq, device_manager)
    
    # 打印处理后的序列，检查参数类型转换
    if processed_seq:
        print("✅ 参数类型转换结果：")
        for i, (func, args) in enumerate(processed_seq, 1):
            func_name = func.__name__ if hasattr(func, '__name__') else str(func)
            # 查找函数名对应的命令名
            cmd_name = "未知命令"
            for cmd, f in command_to_func.items():
                if f == func:
                    cmd_name = cmd
                    break
            # 打印参数及其类型
            typed_args = []
            for arg in args:
                # 跳过device_manager对象，只显示其他参数
                if arg is not device_manager:
                    typed_args.append(f"{arg} ({type(arg).__name__})")
                else:
                    typed_args.append("<DeviceManager>")
            print(f"步骤 {i}: {cmd_name} -> {func_name}({', '.join(typed_args)})")
    else:
        print("❌ 处理后的序列为空")
    
    # 4. 对输出的序列还要进行二次处理，主要是处理反应器命名和编号的对应，还有添加进设备驱动的实例对象
    #########
    ##待补充##
    #########
    
    # 5. 按序执行
    #execute_sequence(processed_seq)
