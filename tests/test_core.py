import os
import threading

from disc_cleaner import docker_backend, nix_backend, scanner
from disc_cleaner.model import score
from disc_cleaner.rules import classify_dir, classify_file
from disc_cleaner.safety import protected_reason
from disc_cleaner.util import human, parse_size


def test_parse_size():
    assert parse_size("914MB") == 914_000_000
    assert parse_size("1.5GB") == 1_500_000_000
    assert parse_size("0B") == 0
    assert parse_size("161MB (virtual 2GB)") == 161_000_000
    assert parse_size("") == 0
    assert human(1536) == "1.5 KB"


def test_score_order():
    old = 0.0
    assert score(10**9, "safe") > score(10**9, "review") > score(10**9, "unknown")
    assert score(10**9, "caution") == 0
    assert score(10**9, "safe", mtime=old) > score(10**9, "safe", mtime=None) * 0.99


def test_protected():
    home = "/home/u"
    assert protected_reason("/", home)
    assert protected_reason("/usr/lib/x", home)
    assert protected_reason("/nix/store/abc", home)
    assert protected_reason("/home", home)
    assert protected_reason("/home/u", home)
    assert protected_reason("/var/lib/docker/overlay2", home)
    assert protected_reason("/home/u/.local/state/nix/profiles", home)
    assert protected_reason("/home/u/.local/share/docker/volumes", home)
    assert protected_reason("/home/u/proj/node_modules", home) is None


def test_classify(tmp_path):
    home = str(tmp_path)
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "Cargo.toml").write_text("")
    assert classify_dir(f"{home}/p/node_modules", home)[0] == "safe"
    assert classify_dir(f"{home}/p/target", home)[0] == "safe"
    assert classify_dir(f"{home}/q/target", home) is None
    assert classify_dir(f"{home}/.cache/pip", home)[0] == "safe"
    assert classify_dir(f"{home}/.cache", home) is None
    assert classify_file(f"{home}/Downloads/x.iso", 10, home)[0] == "review"
    assert classify_file(f"{home}/a/core", 10, home)[0] == "safe"
    assert classify_file(f"{home}/a/small.txt", 10, home) is None


def test_scan_and_candidates(tmp_path, monkeypatch):
    nm = tmp_path / "proj" / "node_modules" / "pkg" / "node_modules"
    nm.mkdir(parents=True)
    (nm / "big.bin").write_bytes(os.urandom(2 << 20))
    res = scanner.scan(str(tmp_path), threading.Event())
    assert res.dir_sizes[str(tmp_path)] >= 2 << 20
    cands = scanner.candidates(res, home=str(tmp_path))
    assert [c.key for c in cands] == [str(tmp_path / "proj" / "node_modules")]
    items = scanner.list_dir(str(tmp_path / "proj"), res, home=str(tmp_path))
    assert items[0].size >= 2 << 20 and items[0].safety == "safe"
    items[0].delete()
    assert not (tmp_path / "proj" / "node_modules").exists()


def test_docker_parse():
    data = {
        "Images": [
            {"ID": "sha256:aaa", "Repository": "<none>", "Tag": "<none>", "Containers": "0",
             "UniqueSize": "1GB", "CreatedAt": "2026-10-16 19:48:26 +0900 JST"},
            {"ID": "sha256:bbb", "Repository": "app", "Tag": "v1", "Containers": "1", "UniqueSize": "2GB"},
        ],
        "Containers": [{"ID": "c1", "Names": "x", "Image": "app", "State": "exited", "Size": "5MB"}],
        "Volumes": [{"Name": "v", "Links": "0", "Size": "3MB"}],
        "BuildCache": [{"InUse": "false", "Size": "1GB"}, {"InUse": "true", "Size": "9GB"}],
    }
    items = docker_backend.parse_df(data)
    by = {i.kind + ":" + i.name: i for i in items}
    assert by["image:aaa"].safety == "safe" and by["image:aaa"].size == 10**9 and by["image:aaa"].mtime
    assert by["image:app:v1"].safety == "caution" and by["image:app:v1"].delete is None
    assert by["container:x (app)"].safety == "review"
    assert by["volume:v"].delete is not None
    assert [i.size for i in items if i.kind == "cache"] == [10**9]


def test_nix_generations():
    text = "   1   2024-08-02 10:00:00   \n   2   2024-09-01 11:00:00   (current)\n"
    gens = nix_backend.parse_generations(text)
    assert [(g, c) for g, _, c in gens] == [(1, False), (2, True)]


def test_list_dir_inner_score(tmp_path):
    nm = tmp_path / "proj" / "sub" / "node_modules"
    nm.mkdir(parents=True)
    (nm / "big.bin").write_bytes(os.urandom(2 << 20))
    res = scanner.scan(str(tmp_path), threading.Event())
    cands = scanner.candidates(res, home=str(tmp_path))
    [proj] = scanner.list_dir(str(tmp_path), res, home=str(tmp_path), cands=cands)
    assert proj.score == cands[0].score > 0 and "削除候補 1 件" in proj.reason
