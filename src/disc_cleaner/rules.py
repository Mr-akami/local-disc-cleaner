"""パスから「消してよいか」を判定するルール群。"""

from __future__ import annotations

import os
import re

Verdict = tuple[str, str]  # (safety, reason)

# ディレクトリ名だけで判定できるもの
NAME_RULES: dict[str, Verdict] = {
    "node_modules": ("safe", "npm依存 (npm install で復元可)"),
    "__pycache__": ("safe", "Pythonバイトコードキャッシュ"),
    ".pytest_cache": ("safe", "pytestキャッシュ"),
    ".mypy_cache": ("safe", "mypyキャッシュ"),
    ".ruff_cache": ("safe", "ruffキャッシュ"),
    ".tox": ("safe", "tox環境"),
    ".nox": ("safe", "nox環境"),
    ".next": ("safe", "Next.jsビルドキャッシュ"),
    ".nuxt": ("safe", "Nuxtビルドキャッシュ"),
    ".turbo": ("safe", "Turborepoキャッシュ"),
    ".parcel-cache": ("safe", "Parcelキャッシュ"),
    ".svelte-kit": ("safe", "SvelteKitビルド成果物"),
    ".gradle": ("safe", "Gradleキャッシュ"),
    ".venv": ("safe", "Python仮想環境 (uv sync 等で再作成可)"),
}

# ホームからの相対パス: (safety, reason, 子要素ごとに候補にするか)
HOME_RULES: dict[str, tuple[str, str, bool]] = {
    ".cache": ("safe", "ユーザーキャッシュ", True),
    ".local/share/Trash": ("safe", "ゴミ箱", False),
    ".npm/_cacache": ("safe", "npmキャッシュ", False),
    ".cargo/registry": ("safe", "Cargoレジストリキャッシュ", False),
    ".cargo/git": ("safe", "Cargo gitキャッシュ", False),
    ".m2/repository": ("safe", "Mavenキャッシュ", False),
    "go/pkg/mod": ("safe", "Goモジュールキャッシュ", False),
    ".gradle/caches": ("safe", "Gradleキャッシュ", False),
    ".conda/pkgs": ("safe", "condaパッケージキャッシュ", False),
    ".local/share/pnpm/store": ("safe", "pnpmストア", False),
    ".rustup/toolchains": ("review", "Rustツールチェーン (未使用なら削除)", True),
    ".pyenv/versions": ("review", "pyenvのPython (未使用なら削除)", True),
    ".nvm/versions/node": ("review", "nvmのNode (未使用なら削除)", True),
    "Downloads": ("review", "ダウンロード済みファイル", True),
    "ダウンロード": ("review", "ダウンロード済みファイル", True),
}

_BUILD_MARKERS = ("package.json", "pyproject.toml", "setup.py", "CMakeLists.txt", "Makefile")
_CORE_RE = re.compile(r"^core(\.\d+)?$")
_ARCHIVE_EXT = (".iso", ".img", ".zip", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".7z", ".deb", ".rpm", ".AppImage")


def _under(path: str, base: str) -> bool:
    return path == base or path.startswith(base + "/")


def _has(parent: str, *names: str) -> bool:
    return any(os.path.exists(os.path.join(parent, n)) for n in names)


def classify_dir(path: str, home: str) -> Verdict | None:
    parent, name = os.path.split(path)
    rel = os.path.relpath(path, home) if _under(path, home) else None
    if rel is not None:
        if rel in HOME_RULES and not HOME_RULES[rel][2]:
            safety, reason, _ = HOME_RULES[rel]
            return safety, reason
        prel = os.path.relpath(parent, home)
        if prel in HOME_RULES and HOME_RULES[prel][2]:
            safety, reason, _ = HOME_RULES[prel]
            return safety, reason
    if name in NAME_RULES:
        return NAME_RULES[name]
    if name == "venv" and os.path.exists(os.path.join(path, "pyvenv.cfg")):
        return "safe", "Python仮想環境 (再作成可)"
    if name == "target" and _has(parent, "Cargo.toml"):
        return "safe", "Rustビルド成果物 (cargo clean相当)"
    if name in ("build", "dist", "out") and _has(parent, *_BUILD_MARKERS):
        return "review", "ビルド成果物 (再ビルドで復元可)"
    return None


def classify_file(path: str, size: int, home: str) -> Verdict | None:
    parent, name = os.path.split(path)
    rel_parent = os.path.relpath(parent, home) if _under(parent, home) else None
    if rel_parent in HOME_RULES and HOME_RULES[rel_parent][2]:
        safety, reason, _ = HOME_RULES[rel_parent]
        return safety, reason
    if _CORE_RE.match(name):
        return "safe", "コアダンプ"
    if name.endswith((".log", ".log.1", ".log.gz")) or re.search(r"\.log\.\d+(\.gz)?$", name):
        return "review", "ログファイル"
    if name.endswith((".pyc", ".o", ".tmp", ".swp")):
        return "safe", "一時/中間ファイル"
    if name.endswith(_ARCHIVE_EXT):
        return "review", "アーカイブ/イメージ (展開済みなら不要)"
    if size >= 1 << 30:
        return "review", "巨大ファイル"
    return None
