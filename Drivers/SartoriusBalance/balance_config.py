"""赛多利斯 Cubis II MCE524S-2CCN-U 天平配置（SBI，经 UT-6801A RS232 透传）。"""
from __future__ import annotations

BALANCE_MODEL = "MCE524S-2CCN-U"
BALANCE_USE_SIMULATION = False

# SBI 控制命令：Esc + 字符，可选 CR LF
CMD_PRINT = b"\x1bP\r\n"  # Esc P：打印/读当前显示重量
CMD_TARE = b"\x1bT\r\n"  # Esc T：去皮
CMD_ZERO = b"\x1bV\r\n"  # Esc V：清零
CMD_RESTART = b"\x1bS\r\n"  # Esc S：重启

# 现场：天平 AUTO W/O 约 400ms 一行；长连接收流即可
STREAM_LINE_TIMEOUT_S = 2.0
# 加料闭环：相对加料起点净增量达到目标即停
DOSE_MAX_WAIT_S = 300.0
# 提前停料余量（mg），抵消空中余粉；0 表示到量即停
DOSE_EARLY_STOP_MG = 0.0
