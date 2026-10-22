"""Nix ストアのガベージとプロファイル世代。"""

from __future__ import annotations

import glob
import os
import re
import shutil
from datetime import datetime
from typing import Callable

from .model import Item
from .util import run, run_checked

_GEN_RE = re.compile(r"^\s*(\d+)\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*(\(current\))?")


def available() -> bool:
    return shutil.which("nix-store") is not None


def dead_paths() -> list[str]:
    out = run_checked(["nix-store", "--gc", "--print-dead"], timeout=600)
    return [line for line in out.splitlines() if line.startswith("/nix/store/")]


def total_size(paths: list[str], chunk: int = 500) -> int:
    total = 0
    for i in range(0, len(paths), chunk):
        r = run(["nix-store", "-q", "--size", *paths[i : i + chunk]], timeout=600)
        total += sum(int(x) for x in r.stdout.split() if x.isdigit())
    return total


def profiles() -> list[str]:
    user = os.environ.get("USER", "")
    patterns = [
        os.path.expanduser("~/.local/state/nix/profiles/*"),
        f"/nix/var/nix/profiles/per-user/{user}/*",
        "/nix/var/nix/profiles/default",
        "/nix/var/nix/profiles/system",
    ]
    found = []
    for pat in patterns:
        for p in sorted(glob.glob(pat)):
            if os.path.islink(p) and not re.search(r"-\d+-link$", p) and p not in found:
                found.append(p)
    return found


def parse_generations(text: str) -> list[tuple[int, float, bool]]:
    gens = []
    for line in text.splitlines():
        if m := _GEN_RE.match(line):
            ts = datetime.strptime(m[2], "%Y-%m-%d %H:%M:%S").timestamp()
            gens.append((int(m[1]), ts, bool(m[3])))
    return gens


def _delete_gen(profile: str, gen: int) -> Callable[[], None]:
    return lambda: run_checked(["nix-env", "-p", profile, "--delete-generations", str(gen)]) and None


def list_items(progress: Callable[[str], None] = lambda _: None) -> list[Item]:
    items: list[Item] = []
    for prof in profiles():
        r = run(["nix-env", "-p", prof, "--list-generations"], timeout=60)
        writable = os.access(os.path.dirname(prof), os.W_OK)
        for gen, ts, current in parse_generations(r.stdout):
            if current:
                continue
            reason = "古い世代 (削除後 GC で容量解放 / ロールバック不可)"
            if not writable:
                reason += " ※root権限が必要"
            items.append(Item(
                "nix", "generation", f"{prof} #{gen}", f"{prof}#{gen}", None, "review", reason, ts,
                _delete_gen(prof, gen) if writable else None,
            ))
    progress("Nix: 不要パスを集計中…")
    dead = dead_paths()
    size = total_size(dead)
    items.insert(0, Item(
        "nix", "garbage", f"Nixストアの不要パス ({len(dead):,} 個)", "nix-gc", size, "safe",
        "どこからも参照されていないストアパス (nix-collect-garbage)", None, lambda: collect_garbage() and None,
    ))
    return items


def collect_garbage(delete_old: bool = False) -> str:
    cmd = ["nix-collect-garbage"] + (["-d"] if delete_old else [])
    return run_checked(cmd, timeout=3600)
