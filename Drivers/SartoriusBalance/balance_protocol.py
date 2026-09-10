"""赛多利斯 SBI 协议解析（16/22 字符重量行）。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

# 例: "G     + 222.6048 !" / "N +  153.0234 g" / "+     253 pcs"
_WEIGHT_RE = re.compile(
    r"(?P<id>[A-Za-z0-9%]{0,6})\s*"
    r"(?P<sign>[+\- ])\s*"
    r"(?P<value>[0-9.]+)\s*"
    r"(?P<unit>[A-Za-z%]+)?"
)


@dataclass(frozen=True)
class BalanceReading:
    raw: str
    value: Optional[float]
    unit: str
    id_code: str
    display: str
    stable: bool = True

    @property
    def value_mg(self) -> Optional[float]:
        """将读数换算为 mg（默认按 g）。"""
        if self.value is None:
            return None
        unit = (self.unit or "g").strip().lower()
        if unit in ("", "g", "gr"):
            return self.value * 1000.0
        if unit == "mg":
            return self.value
        if unit == "kg":
            return self.value * 1_000_000.0
        return None


def parse_sbi_line(raw: bytes | str) -> Tuple[Optional[BalanceReading], str]:
    if isinstance(raw, bytes):
        text = raw.decode("ascii", errors="replace")
    else:
        text = raw
    line = text.replace("\r", "").replace("\n", "").strip()
    if not line:
        return None, "天平返回空行"

    upper = line.upper()
    if "STAT" in upper and ("H" in upper or "L" in upper or "--" in line):
        return (
            BalanceReading(
                raw=line, value=None, unit="", id_code="Stat", display=line, stable=False
            ),
            "",
        )
    if "ERR" in upper or re.search(r"\bE\s*\d+", upper):
        return None, f"天平错误：{line}"

    stable = "!" not in line
    cleaned = line.replace("!", " ").strip()
    match = _WEIGHT_RE.search(cleaned)
    if not match:
        return (
            BalanceReading(
                raw=line, value=None, unit="", id_code="", display=line, stable=stable
            ),
            "",
        )

    sign = match.group("sign") or "+"
    try:
        value = float(match.group("value"))
    except ValueError:
        return None, f"无法解析重量：{line}"
    if sign.strip() == "-":
        value = -value
    unit = (match.group("unit") or "").strip()
    if not unit:
        unit = "g"
    id_code = (match.group("id") or "").strip()
    display = f"{value:g} {unit}".strip()
    if not stable:
        display = f"{display} !"
    return (
        BalanceReading(
            raw=line,
            value=value,
            unit=unit,
            id_code=id_code,
            display=display,
            stable=stable,
        ),
        "",
    )
