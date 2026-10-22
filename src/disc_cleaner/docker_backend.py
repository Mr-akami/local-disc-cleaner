"""Docker のイメージ / コンテナ / ボリューム / ビルドキャッシュ。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime

from .model import Item
from .util import parse_size, run_checked


def available() -> bool:
    return shutil.which("docker") is not None


def _created(text: str) -> float | None:
    # "2026-10-16 19:48:26 +0900 JST"
    try:
        return datetime.strptime(" ".join(text.split()[:3]), "%Y-%m-%d %H:%M:%S %z").timestamp()
    except (ValueError, AttributeError):
        return None


def _cmd(*args: str):
    return lambda: run_checked(["docker", *args], timeout=600) and None


def parse_df(data: dict) -> list[Item]:
    items: list[Item] = []
    for img in data.get("Images") or []:
        repo, tag = img.get("Repository", ""), img.get("Tag", "")
        dangling = repo == "<none>"
        name = img["ID"].removeprefix("sha256:")[:12] if dangling else f"{repo}:{tag}"
        if dangling:
            safety, reason = "safe", "dangling イメージ (タグなし)"
        elif img.get("Containers", "0") not in ("0", ""):
            safety, reason = "caution", f"コンテナ {img['Containers']} 個が使用中"
        else:
            safety, reason = "review", "未使用イメージ (再pull/再buildが必要)"
        target = img["ID"] if dangling or tag == "<none>" else name
        items.append(Item(
            "docker", "image", name, img["ID"], parse_size(img.get("UniqueSize") or img.get("Size")),
            safety, reason, _created(img.get("CreatedAt", "")),
            None if safety == "caution" else _cmd("rmi", target),
        ))
    for c in data.get("Containers") or []:
        running = c.get("State") == "running"
        safety, reason = ("caution", "実行中") if running else ("review", f"停止中 ({c.get('Status', '')})")
        items.append(Item(
            "docker", "container", f"{c.get('Names')} ({c.get('Image')})", c["ID"], parse_size(c.get("Size")),
            safety, reason, _created(c.get("CreatedAt", "")),
            None if running else _cmd("rm", c["ID"]),
        ))
    for v in data.get("Volumes") or []:
        unused = v.get("Links", "0") == "0"
        safety, reason = ("review", "未使用ボリューム (データ消失注意)") if unused else ("caution", "使用中ボリューム")
        items.append(Item(
            "docker", "volume", v["Name"], v["Name"], parse_size(v.get("Size")), safety, reason, None,
            _cmd("volume", "rm", v["Name"]) if unused else None,
        ))
    caches = [b for b in data.get("BuildCache") or [] if b.get("InUse") != "true"]
    if caches:
        size = sum(parse_size(b.get("Size")) for b in caches)
        items.append(Item(
            "docker", "cache", f"ビルドキャッシュ ({len(caches)} 件)", "builder-prune", size,
            "safe", "未使用ビルドキャッシュ (docker builder prune)", None, _cmd("builder", "prune", "-f"),
        ))
    return items


def list_items() -> list[Item]:
    out = run_checked(["docker", "system", "df", "-v", "--format", "{{json .}}"], timeout=300)
    return parse_df(json.loads(out))


def system_prune() -> str:
    return run_checked(["docker", "system", "prune", "-f"], timeout=1800)


def builder_prune() -> str:
    return run_checked(["docker", "builder", "prune", "-f"], timeout=1800)
