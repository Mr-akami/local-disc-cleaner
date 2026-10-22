"""ファイルシステムのスキャン (du / find を利用) とおすすめ候補の抽出。"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable

from .model import Item
from .rules import classify_dir, classify_file
from .safety import delete_path, protected_reason
from .util import human

BIG_FILE_MB = 200


@dataclass
class ScanResult:
    root: str
    dir_sizes: dict[str, int] = field(default_factory=dict)
    big_files: list[tuple[str, int, float]] = field(default_factory=list)  # path, size, mtime
    cancelled: bool = False


def _stream(cmd: list[str], cancel: threading.Event, on_line: Callable[[str], None]) -> bool:
    """コマンドの NUL 区切り出力を逐次処理。キャンセル時 False。"""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout
    buf = b""
    try:
        while chunk := proc.stdout.read(1 << 16):
            if cancel.is_set():
                proc.kill()
                return False
            buf += chunk
            *records, buf = buf.split(b"\0")
            for rec in records:
                rec = rec.lstrip(b"\n")
                if rec:
                    on_line(os.fsdecode(rec))
    finally:
        proc.wait()
    return True


def scan(root: str, cancel: threading.Event, progress: Callable[[str], None] = lambda _: None) -> ScanResult:
    root = os.path.abspath(root)
    res = ScanResult(root)
    count = 0

    def on_du(line: str) -> None:
        nonlocal count
        size, _, path = line.partition("\t")
        res.dir_sizes[path] = int(size)
        count += 1
        if count % 5000 == 0:
            progress(f"スキャン中 {count:,} dirs: {path}")

    if not _stream(["du", "-x", "-0", "-B1", root], cancel, on_du):
        res.cancelled = True
        return res
    progress("大きいファイルを検索中…")

    def on_find(line: str) -> None:
        size, mtime, path = line.split("\t", 2)
        res.big_files.append((path, int(size), float(mtime)))

    find_cmd = ["find", root, "-xdev", "-type", "f", "-size", f"+{BIG_FILE_MB}M", "-printf", r"%s\t%T@\t%p\0"]
    res.cancelled = not _stream(find_cmd, cancel, on_find)
    return res


def _mtime(path: str) -> float | None:
    try:
        return os.lstat(path).st_mtime
    except OSError:
        return None


def _file_item(path: str, size: int | None, mtime: float | None, verdict, is_dir: bool) -> Item:
    if protected_reason(path):
        safety, reason = "caution", f"保護: {protected_reason(path)}"
    elif verdict:
        safety, reason = verdict
    else:
        safety, reason = "unknown", ""
    return Item(
        source="file", kind="dir" if is_dir else "file", name=path, key=path, size=size,
        safety=safety, reason=reason, mtime=mtime,
        delete=None if safety == "caution" else (lambda p=path: delete_path(p)),
    )


def list_dir(
    path: str, result: ScanResult | None, home: str | None = None, cands: list[Item] | None = None
) -> list[Item]:
    """ディレクトリ直下の一覧 (サイズはスキャン結果 / lstat)。

    未分類ディレクトリは内部の削除候補 (cands) からおすすめ度を算出する。"""
    home = home or os.path.expanduser("~")
    sizes = result.dir_sizes if result else {}
    items = []
    try:
        entries = list(os.scandir(path))
    except OSError:
        return []
    for e in entries:
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        is_dir = e.is_dir(follow_symlinks=False)
        if is_dir:
            size = sizes.get(e.path)
            verdict = classify_dir(e.path, home)
        else:
            size = st.st_blocks * 512
            verdict = classify_file(e.path, size, home)
        item = _file_item(e.path, size, st.st_mtime, verdict, is_dir)
        if is_dir and verdict is None and cands and item.safety != "caution":
            inner = [c for c in cands if c.key.startswith(e.path + "/")]
            if inner:
                item.score_override = round(sum(c.score for c in inner), 1)
                item.reason = f"内部に削除候補 {len(inner)} 件 / {human(sum(c.size or 0 for c in inner))}"
        items.append(item)
    return items


def candidates(result: ScanResult, home: str | None = None) -> list[Item]:
    """スキャン結果から削除候補 (ネストしないもの) を抽出。"""
    home = home or os.path.expanduser("~")
    picked: dict[str, Item] = {}

    def under_picked(p: str) -> bool:
        while (parent := os.path.dirname(p)) != p:
            if parent in picked:
                return True
            p = parent
        return False

    for path in sorted(result.dir_sizes):  # 親が先に来る
        size = result.dir_sizes[path]
        if size < 1 << 20:
            continue
        verdict = classify_dir(path, home)
        if verdict and not under_picked(path):
            picked[path] = _file_item(path, size, _mtime(path), verdict, True)
    for path, size, mtime in result.big_files:
        if under_picked(path):
            continue
        verdict = classify_file(path, size, home) or ("review", "大きいファイル")
        picked[path] = _file_item(path, size, mtime, verdict, False)
    return [i for i in picked.values() if i.safety != "caution"]
