from __future__ import annotations

import re
import subprocess

_UNITS = {
    "": 1, "B": 1,
    "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
    "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4,
}


def parse_size(text: str | None) -> int:
    """'1.2GB' / '914MB (virtual 2GB)' / '0B' -> bytes."""
    m = re.match(r"\s*([\d.]+)\s*([A-Za-z]*)", text or "")
    if not m:
        return 0
    return int(float(m[1]) * _UNITS.get(m[2].upper(), 1))


def human(n: int | None) -> str:
    if n is None:
        return "?"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")


def run_checked(cmd: list[str], timeout: float | None = None) -> str:
    r = run(cmd, timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout
