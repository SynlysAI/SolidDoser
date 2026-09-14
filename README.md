# SolidDoser

固体加样上位机：汇川 AM600 运动控制、基恩士扫码、赛多利斯天平，含自动流程与调试界面。

## 启动

```bash
python main.py              # 完整入口（含流程导入 / UDP）
python main_soliddoser.py   # 精简入口（自动 + 调试）
```

## 主要目录

| 目录 | 说明 |
|------|------|
| `Drivers/` | AM600 运动、扫码枪、天平、串口服务器 |
| `BusinessActions/` | 业务动作（运动 / 系统状态机 / 扫码 / 天平） |
| `UIInteraction/` | 主界面、自动页、调试页、参数模型 |
| `ActionSequence/` | 流程导入与执行（对标自动页：设备初始化/物料初始化/加料等）；样例见 `order_save/debug_smoke.txt` |
| `Dependencies/` | 现场硬件手册与 PLC 工程 |

## 依赖

见 `requirements.txt`（PySide6、pymodbus、pyserial、pyinstaller）。
