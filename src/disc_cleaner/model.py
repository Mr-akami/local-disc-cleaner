from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

# safe: 再生成可能 / review: 中身を確認 / unknown: 判定材料なし / caution: 使用中・削除不可
SAFETY_WEIGHT = {"safe": 1.0, "review": 0.4, "unknown": 0.1, "caution": 0.0}
SAFETY_LABEL = {"safe": "安全", "review": "要確認", "unknown": "-", "caution": "危険/使用中"}


def score(size: int, safety: str, mtime: float | None = None, now: float | None = None) -> float:
    """削除おすすめ度。大きく・安全で・古いほど高い。"""
    age = 1.0
    if mtime:
        days = max(0.0, ((now or time.time()) - mtime) / 86400)
        age = 0.5 + 0.5 * min(days, 365) / 365
    return round(size / 1e9 * SAFETY_WEIGHT[safety] * age * 100, 1)


@dataclass
class Item:
    source: str  # file / docker / nix
    kind: str  # dir, file, image, container, volume, cache, garbage, generation ...
    name: str
    key: str  # path or id
    size: int | None
    safety: str = "unknown"
    reason: str = ""
    mtime: float | None = None
    delete: Callable[[], None] | None = field(default=None, repr=False, compare=False)
    score_override: float | None = None  # 未分類ディレクトリ: 内部の削除候補のスコア合計

    @property
    def score(self) -> float:
        if self.score_override is not None:
            return self.score_override
        return score(self.size or 0, self.safety, self.mtime)
