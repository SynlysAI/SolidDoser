"""宇泰 UT-6801A 串口服务器配置（TCP Server 透传 RS232）。

现场：IP 192.168.0.5（出厂默认 192.168.1.125，需在 Web 改为现场网段），
RS232（DB9）接赛多利斯 MCE524S-2CCN-U。
工作模式选 DataSocket → TCP Server；本地监听端口、串口波特率等在 Web 配置，
须与天平 Device→RS-232 一致。手册见 Dependencies/宇泰/UT-6801A/。
"""
from __future__ import annotations

import os

SERIAL_SERVER_MODEL = "UT-6801A"

SERIAL_SERVER_HOST = os.environ.get("SOLIDDOSER_SERIAL_SERVER_HOST", "192.168.0.5")
# TCP Server 本地监听端口，出厂默认 10000；可用环境变量覆盖
SERIAL_SERVER_PORT = int(os.environ.get("SOLIDDOSER_SERIAL_SERVER_PORT", "10000"))

CONNECT_TIMEOUT_S = 3.0
IO_TIMEOUT_S = 5.0

# 串口侧须与天平 DEVICE→COM-RS232 一致（在 UT-6801A Web「串口设置」配置，非上位机 TCP 参数）。
# Cubis II MCE 出厂：9600、8 数据位、奇校验(ODD)、硬件握手(HARDWARE)、协议 SBI。
# 现场可改为双方一致的 NONE 校验；握手双方 NONE（三线）。
# 本机实测：UT-6801A ↔ 天平须用交叉线（按打印键时 RX 灯闪为准）。
SERIAL_PARAMS_HINT = (
    "双方一致（常用 9600 8N1；握手 NONE；协议 SBI；AUTO W/O；现场交叉线）"
)
