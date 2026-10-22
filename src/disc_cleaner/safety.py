from __future__ import annotations

import os
import shutil
from pathlib import Path

# この配下はファイル削除しない (Nix/Docker は専用コマンドで消す)
PROTECTED_TREES = (
    "/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/libx32", "/nix",
    "/proc", "/run", "/sbin", "/snap", "/sys", "/usr", "/var/lib/docker",
    "/var/lib/containerd", "/var/lib/dpkg", "/var/lib/apt",
)

# ホーム配下で Nix / Docker / Podman が管理する領域 (専用コマンドで消す)
PROTECTED_HOME_TREES = (
    ".nix-profile", ".nix-defexpr", ".local/state/nix", ".local/state/home-manager",
    ".local/share/docker", ".local/share/containers", ".docker",
)


def protected_reason(path: str | os.PathLike[str], home: str | None = None) -> str | None:
    p = os.path.normpath(os.path.abspath(path))
    home = os.path.normpath(home or os.path.expanduser("~"))
    if p == "/" or p.count("/") < 2:
        return "トップレベルのディレクトリ"
    if p == home or home.startswith(p + "/"):
        return "ホームディレクトリ (またはその親)"
    for tree in PROTECTED_TREES:
        if p == tree or p.startswith(tree + "/"):
            return f"システム保護領域 ({tree})"
    for rel in PROTECTED_HOME_TREES:
        tree = f"{home}/{rel}"
        if p == tree or p.startswith(tree + "/"):
            return f"Nix/Docker 管理領域 (~/{rel}): 専用コマンドで削除"
    if os.path.ismount(p):
        return "マウントポイント"
    return None


def delete_path(path: str) -> None:
    reason = protected_reason(path)
    if reason:
        raise PermissionError(f"{path}: 削除禁止 - {reason}")
    p = Path(path)
    if p.is_symlink() or not p.is_dir():
        p.unlink()
    else:
        shutil.rmtree(p)
