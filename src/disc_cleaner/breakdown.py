"""マウント単位の使用量内訳 (トップレベル du + Docker + 未計測分)。"""

from __future__ import annotations

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import docker_backend
from .model import Item
from .util import human, parse_size, run

UNMEASURED_REASON = (
    "権限不足で読めない領域 (root 所有)、削除済みだが開かれたままのファイル、"
    "ファイルシステムのメタデータ等"
)


def du_total(path: str, cancel: threading.Event) -> tuple[int, bool]:
    """(サイズ, 完全に計測できたか)。"""
    proc = subprocess.Popen(["du", "-x", "-s", "-B1", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    while proc.poll() is None:
        if cancel.wait(0.2):
            proc.kill()
            proc.wait()
            return 0, False
    out, err = proc.communicate()
    size = int(out.split(b"\t", 1)[0] or 0) if out else 0
    return size, proc.returncode == 0 and not err


def docker_usage() -> tuple[str, int] | None:
    """(Docker root dir, docker system df の合計)。"""
    if not docker_backend.available():
        return None
    r = run(["docker", "info", "--format", "{{.DockerRootDir}}"], timeout=30)
    df = run(["docker", "system", "df", "--format", "{{.Size}}"], timeout=120)
    if r.returncode or df.returncode:
        return None
    return r.stdout.strip(), sum(parse_size(line) for line in df.stdout.splitlines())


def _same_dev(path: str, dev: int) -> bool:
    try:
        return os.lstat(path).st_dev == dev
    except OSError:
        return False


def breakdown(
    mount: str,
    used: int,
    known: dict[str, int],
    cancel: threading.Event,
    progress: Callable[[str], None] = lambda _: None,
) -> list[Item]:
    """mount 直下の各エントリのサイズ + Docker + 未計測分。known はスキャン済みのディレクトリサイズ。"""
    dev = os.lstat(mount).st_dev
    items: list[Item] = []
    dirs: list[str] = []
    for e in os.scandir(mount):
        st = e.stat(follow_symlinks=False)
        if st.st_dev != dev or e.is_symlink():
            continue  # 別マウント (/proc, /boot/efi 等) やシンボリックリンク
        if e.is_dir(follow_symlinks=False):
            if e.path in known:
                items.append(Item("breakdown", "dir", e.path, e.path, known[e.path], reason="スキャン済み"))
            else:
                dirs.append(e.path)
        else:
            items.append(Item("breakdown", "file", e.path, e.path, st.st_blocks * 512, reason="ファイル"))

    def measure(path: str) -> Item:
        progress(f"内訳を計測中: {path}")
        size, complete = du_total(path, cancel)
        return Item("breakdown", "dir", path, path, size, reason="" if complete else "⚠ 権限不足で一部未計測")

    with ThreadPoolExecutor(max_workers=4) as pool:
        items += pool.map(measure, dirs)
    if cancel.is_set():
        return []

    docker = docker_usage()
    if docker and _same_dev(docker[0], dev) and not os.access(docker[0], os.R_OK | os.X_OK):
        root, size = docker
        items.append(Item(
            "breakdown", "docker", f"Docker ({root})", root, size,
            reason="docker system df の合計 (root 権限のため du で読めない分)",
        ))

    measured = sum(i.size or 0 for i in items)
    rest = used - measured
    items.append(Item(
        "breakdown", "other", "未計測 (使用量 − 計測合計)", "", max(rest, 0),
        reason=UNMEASURED_REASON if rest >= 0 else f"計測が使用量を {human(-rest)} 超過 (ハードリンク等)",
    ))
    return items
