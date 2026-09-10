"""启动设备端模拟软件。

用法（仓库根目录）:
  python -m HostSimulator
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接 python HostSimulator/__main__.py
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PySide6.QtWidgets import QApplication

from HostSimulator.main_window import HostSimulatorWindow


def main() -> int:
    app = QApplication(sys.argv)
    win = HostSimulatorWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
