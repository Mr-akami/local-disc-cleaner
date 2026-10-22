"""Tkinter GUI。"""

from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from . import breakdown, docker_backend, nix_backend, scanner
from .disks import disks
from .model import SAFETY_LABEL, Item
from .util import human

SAFETY_COLOR = {"safe": "#d8f5d0", "review": "#fff3c4", "caution": "#f8d0d0", "unknown": ""}
COLUMNS = {
    "score": ("おすすめ度", 80, "e"),
    "size": ("サイズ", 90, "e"),
    "pct": ("%", 55, "e"),
    "safety": ("判定", 80, "center"),
    "source": ("種別", 90, "center"),
    "name": ("名前 / パス", 480, "w"),
    "mtime": ("更新日", 90, "center"),
    "reason": ("理由", 300, "w"),
}
SORT_KEYS: dict[str, Callable[[Item], object]] = {
    "score": lambda i: i.score,
    "size": lambda i: i.size or 0,
    "pct": lambda i: i.size or 0,
    "safety": lambda i: i.safety,
    "source": lambda i: (i.source, i.kind),
    "name": lambda i: i.name,
    "mtime": lambda i: i.mtime or 0,
    "reason": lambda i: i.reason,
}


class ItemTable(ttk.Frame):
    """Item 一覧。見出しクリックでソート。"""

    def __init__(self, master, columns: list[str], on_open: Callable[[Item], None] | None = None):
        super().__init__(master)
        self.columns = columns
        self.items: list[Item] = []
        self.total = 0
        self.sort_col, self.sort_desc = "score", True
        self.short_names = False
        self.tree = ttk.Treeview(self, columns=columns, show="headings", selectmode="extended")
        for c in columns:
            label, width, anchor = COLUMNS[c]
            self.tree.heading(c, text=label, command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=width, anchor=anchor, stretch=c in ("name", "reason"))
        for safety, color in SAFETY_COLOR.items():
            if color:
                self.tree.tag_configure(safety, background=color, foreground="#222")
        ys = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        if on_open:
            self.tree.bind("<Double-1>", lambda _e: (it := self.focused()) and on_open(it))
            self.tree.bind("<Return>", lambda _e: (it := self.focused()) and on_open(it))

    def set_items(self, items: list[Item], total: int | None = None) -> None:
        self.items = list(items)
        self.total = total if total is not None else sum(i.size or 0 for i in items)
        self.refresh()

    def sort_by(self, col: str, desc: bool | None = None) -> None:
        if desc is None:
            desc = not self.sort_desc if col == self.sort_col else col not in ("name", "source", "reason")
        self.sort_col, self.sort_desc = col, desc
        self.refresh()

    def refresh(self) -> None:
        self.items.sort(key=SORT_KEYS[self.sort_col], reverse=self.sort_desc)
        self.tree.delete(*self.tree.get_children())
        for idx, it in enumerate(self.items):
            self.tree.insert("", "end", iid=str(idx), values=[self._cell(it, c) for c in self.columns], tags=(it.safety,))
        for c in self.columns:
            arrow = (" ▼" if self.sort_desc else " ▲") if c == self.sort_col else ""
            self.tree.heading(c, text=COLUMNS[c][0] + arrow)

    def _cell(self, it: Item, col: str) -> str:
        match col:
            case "score":
                return f"{it.score:.1f}"
            case "size":
                return human(it.size)
            case "pct":
                return f"{(it.size or 0) / self.total * 100:.1f}" if self.total else ""
            case "safety":
                return SAFETY_LABEL[it.safety]
            case "source":
                return f"{it.source}/{it.kind}"
            case "name":
                return (os.path.basename(it.name) or it.name) + ("/" if it.kind == "dir" else "") if self.short_names else it.name
            case "mtime":
                return time.strftime("%Y-%m-%d", time.localtime(it.mtime)) if it.mtime else ""
            case "reason":
                return it.reason
        return ""

    def selected(self) -> list[Item]:
        return [self.items[int(i)] for i in self.tree.selection()]

    def focused(self) -> Item | None:
        f = self.tree.focus()
        return self.items[int(f)] if f else None

    def remove(self, items: list[Item]) -> None:
        ids = {id(i) for i in items}
        self.items = [i for i in self.items if id(i) not in ids]
        self.refresh()


class App(tk.Tk):
    def __init__(self, start: str | None = None):
        super().__init__()
        self.title("Local Disc Cleaner")
        self.geometry("1280x820")
        self.q: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.scan_result: scanner.ScanResult | None = None
        self.cwd = os.path.abspath(start or os.path.expanduser("~"))
        self.file_cands: list[Item] = []
        self.docker_items: list[Item] = []
        self.nix_items: list[Item] = []
        self.busy = 0
        self.breakdown_cancel = threading.Event()
        self.breakdown_done = False

        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Treeview", rowheight=24)
        style.configure("red.Horizontal.TProgressbar", background="#d9534f")
        style.configure("yellow.Horizontal.TProgressbar", background="#f0ad4e")
        style.configure("green.Horizontal.TProgressbar", background="#5cb85c")

        self._build_disks()
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=4)
        self._build_recommend_tab()
        self._build_breakdown_tab()
        self._build_files_tab()
        self._build_docker_tab()
        self._build_nix_tab()
        status = ttk.Frame(self)
        status.pack(fill="x", padx=8, pady=(0, 6))
        self.status = tk.StringVar(value="準備完了")
        self.spinner = ttk.Progressbar(status, mode="indeterminate", length=120)
        self.spinner.pack(side="right")
        ttk.Label(status, textvariable=self.status).pack(side="left", fill="x", expand=True)

        self.after(100, self._poll)
        self.refresh_disks()
        self.start_scan()
        self.refresh_docker()
        self.refresh_nix()

    # ---------- infra ----------
    def _poll(self) -> None:
        try:
            while True:
                fn, arg = self.q.get_nowait()
                fn(arg)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def set_status(self, msg: str) -> None:
        self.q.put((self.status.set, msg))

    def bg(self, label: str, work: Callable[[], object], done: Callable[[object], None]) -> None:
        self._busy(+1)
        self.status.set(label)

        def target():
            try:
                r = work()
                self.q.put((done, r))
            except Exception as e:  # noqa: BLE001
                self.q.put((lambda err: messagebox.showerror("エラー", f"{label}\n\n{err}"), e))
                self.q.put((self.status.set, f"失敗: {label}"))
            finally:
                self.q.put((self._busy, -1))

        threading.Thread(target=target, daemon=True).start()

    def _busy(self, delta: int) -> None:
        self.busy += delta
        if self.busy > 0:
            self.spinner.start(12)
        else:
            self.busy = 0
            self.spinner.stop()

    # ---------- disks ----------
    def _build_disks(self) -> None:
        box = ttk.LabelFrame(self, text="ディスク使用量")
        box.pack(fill="x", padx=8, pady=6)
        self.disk_frame = ttk.Frame(box)
        self.disk_frame.pack(side="left", fill="x", expand=True, padx=6, pady=4)
        ttk.Button(box, text="更新", command=self.refresh_disks).pack(side="right", padx=6)

    def refresh_disks(self) -> None:
        for w in self.disk_frame.winfo_children():
            w.destroy()
        for row, d in enumerate(disks()):
            pct = d.percent
            style = "red" if pct >= 90 else "yellow" if pct >= 75 else "green"
            ttk.Label(self.disk_frame, text=d.mount, width=24).grid(row=row, column=0, sticky="w")
            bar = ttk.Progressbar(self.disk_frame, length=420, maximum=100, style=f"{style}.Horizontal.TProgressbar")
            bar["value"] = pct
            bar.grid(row=row, column=1, padx=6, pady=1)
            ttk.Label(
                self.disk_frame,
                text=f"{pct:5.1f}%   使用 {human(d.used)} / {human(d.total)}   空き {human(d.free)}   ({d.device})",
            ).grid(row=row, column=2, sticky="w")

    # ---------- recommend ----------
    def _build_recommend_tab(self) -> None:
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="おすすめ削除")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=4)
        ttk.Button(bar, text="すべて再分析", command=self.refresh_all).pack(side="left")
        ttk.Button(bar, text="選択を削除", command=lambda: self.delete(self.rec_table)).pack(side="left", padx=6)
        ttk.Label(
            bar, text="おすすめ度 = サイズ(GB) × 安全度 × 古さ × 100   緑:安全  黄:要確認  赤:使用中",
        ).pack(side="right")
        self.rec_table = ItemTable(tab, ["score", "size", "safety", "source", "name", "mtime", "reason"])
        self.rec_table.pack(fill="both", expand=True)

    def update_recommend(self) -> None:
        items = [i for i in self.file_cands + self.docker_items + self.nix_items if i.delete and i.score > 0]
        self.rec_table.set_items(items)
        total = sum(i.size or 0 for i in items if i.safety == "safe")
        self.status.set(f"おすすめ {len(items)} 件 / 安全に解放できる見込み {human(total)}")

    def refresh_all(self) -> None:
        self.refresh_disks()
        self.start_scan()
        self.refresh_docker()
        self.refresh_nix()

    # ---------- breakdown ----------
    def _build_breakdown_tab(self) -> None:
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="内訳")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=4)
        ttk.Label(bar, text="マウント:").pack(side="left")
        mounts = [d.mount for d in disks()]
        self.bd_mount = tk.StringVar(value="/" if "/" in mounts else (mounts[0] if mounts else "/"))
        ttk.Combobox(bar, textvariable=self.bd_mount, values=mounts, state="readonly", width=20).pack(side="left", padx=4)
        ttk.Button(bar, text="計測", command=self.run_breakdown).pack(side="left", padx=6)
        ttk.Button(bar, text="中止", command=lambda: self.breakdown_cancel.set()).pack(side="left")
        ttk.Label(bar, text="ダブルクリックでファイルタブを開いてスキャン").pack(side="right")
        self.bd_info = tk.StringVar(value="ホームのスキャン完了後に自動計測します")
        ttk.Label(tab, textvariable=self.bd_info, font=("TkDefaultFont", 11, "bold")).pack(fill="x", pady=(0, 4))
        self.bd_table = ItemTable(tab, ["size", "pct", "name", "reason"], on_open=self.open_breakdown_item)
        self.bd_table.sort_col = "size"
        self.bd_table.pack(fill="both", expand=True)

    def run_breakdown(self) -> None:
        mount = self.bd_mount.get()
        disk = next((d for d in disks() if d.mount == mount), None)
        if not disk:
            return
        self.breakdown_done = True
        self.breakdown_cancel.set()
        cancel = self.breakdown_cancel = threading.Event()
        known = self.scan_result.dir_sizes if self.scan_result else {}

        def done(items):
            if cancel is not self.breakdown_cancel or not items:
                return
            self.bd_table.set_items(items, total=disk.used)
            other = items[-1].size or 0
            self.bd_info.set(
                f"{mount}  使用 {human(disk.used)}  =  計測済み {human(disk.used - other)}"
                f"  +  未計測 {human(other)}   (各タブは一部しか表示しない: "
                f"ファイル=スキャン範囲 / Nix=GC対象のみ / Docker=固有サイズのみ)"
            )
            self.status.set(f"内訳計測完了: {mount}")

        self.bg(f"内訳を計測中: {mount}",
                lambda: breakdown.breakdown(mount, disk.used, known, cancel, self.set_status), done)

    def open_breakdown_item(self, it: Item) -> None:
        if it.kind == "dir":
            self.nb.select(2)
            self.start_scan(it.key)
        elif it.kind == "docker":
            self.nb.select(3)

    # ---------- files ----------
    def _build_files_tab(self) -> None:
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="ファイル")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=4)
        ttk.Button(bar, text="↑ 上へ", command=self.go_up).pack(side="left")
        self.path_var = tk.StringVar(value=self.cwd)
        entry = ttk.Entry(bar, textvariable=self.path_var)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        entry.bind("<Return>", lambda _e: self.open_dir(self.path_var.get()))
        ttk.Button(bar, text="参照…", command=self.browse).pack(side="left")
        ttk.Button(bar, text="ここをスキャン", command=lambda: self.start_scan(self.cwd)).pack(side="left", padx=6)
        ttk.Button(bar, text="中止", command=lambda: self.cancel.set()).pack(side="left")
        bar2 = ttk.Frame(tab)
        bar2.pack(fill="x", pady=(0, 4))
        ttk.Label(bar2, text="並び順:").pack(side="left")
        self.sort_var = tk.StringVar(value="サイズ順")
        sort = ttk.Combobox(bar2, textvariable=self.sort_var, values=["サイズ順", "おすすめ順"], state="readonly", width=12)
        sort.pack(side="left", padx=4)
        sort.bind("<<ComboboxSelected>>", lambda _e: self.file_table.sort_by(
            "size" if self.sort_var.get() == "サイズ順" else "score", True))
        ttk.Button(bar2, text="選択を削除", command=lambda: self.delete(self.file_table)).pack(side="left", padx=6)
        self.dir_info = tk.StringVar()
        ttk.Label(bar2, textvariable=self.dir_info).pack(side="right")
        self.file_table = ItemTable(
            tab, ["size", "pct", "score", "safety", "name", "mtime", "reason"], on_open=self.open_item
        )
        self.file_table.short_names = True
        self.file_table.sort_col = "size"
        self.file_table.pack(fill="both", expand=True)

    def browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.cwd)
        if d:
            self.start_scan(d)

    def go_up(self) -> None:
        self.open_dir(os.path.dirname(self.cwd))

    def open_item(self, it: Item) -> None:
        if it.kind == "dir":
            self.open_dir(it.key)

    def open_dir(self, path: str) -> None:
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            messagebox.showwarning("移動できません", path)
            return
        self.cwd = path
        self.path_var.set(path)
        res = self.scan_result
        if res and not (path == res.root or path.startswith(res.root.rstrip("/") + "/")):
            res = None
        items = scanner.list_dir(path, res, cands=self.file_cands if res else None)
        total = res.dir_sizes.get(path) if res else None
        self.file_table.set_items(items, total)
        note = "" if res else "  (未スキャン: ディレクトリのサイズは「ここをスキャン」で計算)"
        self.dir_info.set(f"{len(items)} 項目 / 合計 {human(total)}{note}")

    def start_scan(self, root: str | None = None) -> None:
        root = os.path.abspath(root or self.cwd)
        self.cancel.set()
        cancel = self.cancel = threading.Event()
        self.open_dir(root)

        def work():
            res = scanner.scan(root, cancel, self.set_status)
            return res, ([] if res.cancelled else scanner.candidates(res))

        def done(r):
            res, cands = r
            if cancel is not self.cancel:
                return
            if res.cancelled:
                self.status.set("スキャン中止")
                return
            self.scan_result, self.file_cands = res, cands
            self.open_dir(self.cwd)
            self.update_recommend()
            if not self.breakdown_done:
                self.run_breakdown()

        self.bg(f"スキャン中: {root}", work, done)

    # ---------- docker ----------
    def _build_docker_tab(self) -> None:
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="Docker")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=4)
        ttk.Button(bar, text="更新", command=self.refresh_docker).pack(side="left")
        ttk.Button(bar, text="選択を削除", command=lambda: self.delete(self.docker_table)).pack(side="left", padx=6)
        ttk.Button(bar, text="builder prune", command=lambda: self.run_action(
            "docker builder prune -f", docker_backend.builder_prune, self.refresh_docker)).pack(side="left")
        ttk.Button(bar, text="system prune", command=lambda: self.run_action(
            "docker system prune -f (停止コンテナ・未使用ネットワーク・dangling・ビルドキャッシュ)",
            docker_backend.system_prune, self.refresh_docker)).pack(side="left", padx=6)
        self.docker_info = tk.StringVar()
        ttk.Label(bar, textvariable=self.docker_info).pack(side="right")
        self.docker_table = ItemTable(tab, ["score", "size", "safety", "source", "name", "mtime", "reason"])
        self.docker_table.pack(fill="both", expand=True)

    def refresh_docker(self) -> None:
        if not docker_backend.available():
            self.docker_info.set("docker が見つかりません")
            return

        def done(items):
            self.docker_items = items
            self.docker_table.set_items(items)
            self.docker_info.set(f"{len(items)} 項目 / 合計 {human(sum(i.size or 0 for i in items))}")
            self.update_recommend()

        self.bg("Docker 情報を取得中…", docker_backend.list_items, done)

    # ---------- nix ----------
    def _build_nix_tab(self) -> None:
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="Nix")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=4)
        ttk.Button(bar, text="更新", command=self.refresh_nix).pack(side="left")
        ttk.Button(bar, text="選択を削除", command=lambda: self.delete(self.nix_table)).pack(side="left", padx=6)
        ttk.Button(bar, text="GC (nix-collect-garbage)", command=lambda: self.run_action(
            "nix-collect-garbage", nix_backend.collect_garbage, self.refresh_nix)).pack(side="left")
        ttk.Button(bar, text="古い世代も削除 + GC (-d)", command=lambda: self.run_action(
            "nix-collect-garbage -d (全プロファイルの古い世代を削除。ロールバック不可)",
            lambda: nix_backend.collect_garbage(delete_old=True), self.refresh_nix)).pack(side="left", padx=6)
        self.nix_info = tk.StringVar()
        ttk.Label(bar, textvariable=self.nix_info).pack(side="right")
        self.nix_table = ItemTable(tab, ["score", "size", "safety", "source", "name", "mtime", "reason"])
        self.nix_table.pack(fill="both", expand=True)

    def refresh_nix(self) -> None:
        if not nix_backend.available():
            self.nix_info.set("nix が見つかりません")
            return

        def done(items):
            self.nix_items = items
            self.nix_table.set_items(items)
            self.nix_info.set(f"{len(items)} 項目")
            self.update_recommend()

        self.bg("Nix 情報を取得中…", lambda: nix_backend.list_items(self.set_status), done)

    # ---------- actions ----------
    def run_action(self, label: str, fn: Callable[[], object], after: Callable[[], None]) -> None:
        if not messagebox.askyesno("確認", f"実行しますか?\n\n{label}"):
            return

        def done(_):
            self.status.set(f"完了: {label}")
            self.refresh_disks()
            after()

        self.bg(f"実行中: {label}", fn, done)

    def delete(self, table: ItemTable) -> None:
        items = table.selected()
        if not items:
            messagebox.showinfo("削除", "削除する項目を選択してください")
            return
        blocked = [i for i in items if not i.delete]
        items = [i for i in items if i.delete]
        if blocked:
            messagebox.showwarning("削除不可", "以下は削除できません (使用中/保護):\n" + "\n".join(i.name for i in blocked[:10]))
        if not items:
            return
        total = sum(i.size or 0 for i in items)
        lines = "\n".join(f"  {human(i.size)}  {i.name}" for i in items[:15])
        more = f"\n  …ほか {len(items) - 15} 件" if len(items) > 15 else ""
        warn = "\n\n⚠ 要確認の項目が含まれています" if any(i.safety != "safe" for i in items) else ""
        if not messagebox.askyesno("完全に削除", f"{len(items)} 件 / {human(total)} を完全に削除します (元に戻せません)。\n\n{lines}{more}{warn}"):
            return

        def work():
            ok, errors = [], []
            for i in items:
                self.set_status(f"削除中: {i.name}")
                try:
                    i.delete()
                    ok.append(i)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{i.name}: {e}")
            return ok, errors

        def done(r):
            ok, errors = r
            for t in (self.rec_table, self.file_table, self.docker_table, self.nix_table):
                t.remove(ok)
            gone = {id(i) for i in ok}
            self.file_cands = [i for i in self.file_cands if id(i) not in gone]
            self.docker_items = [i for i in self.docker_items if id(i) not in gone]
            self.nix_items = [i for i in self.nix_items if id(i) not in gone]
            self._forget_sizes([i for i in ok if i.source == "file"])
            self.refresh_disks()
            self.status.set(f"{len(ok)} 件削除 / {human(sum(i.size or 0 for i in ok))} 解放")
            if errors:
                messagebox.showerror("一部失敗", "\n".join(errors[:20]))
            if any(i.source == "nix" and i.kind == "generation" for i in ok):
                self.status.set(self.status.get() + "  ※世代削除後は GC を実行すると容量が解放されます")

        self.bg("削除中…", work, done)

    def _forget_sizes(self, removed: list[Item]) -> None:
        """削除したパスの分だけ親ディレクトリのサイズを減らす。"""
        res = self.scan_result
        if not res:
            return
        for it in removed:
            size = it.size or 0
            prefix = it.key.rstrip("/") + "/"
            for k in [k for k in res.dir_sizes if k == it.key or k.startswith(prefix)]:
                del res.dir_sizes[k]
            p = os.path.dirname(it.key)
            while p in res.dir_sizes:
                res.dir_sizes[p] -= size
                if p == res.root:
                    break
                p = os.path.dirname(p)
        self.open_dir(self.cwd)


def main() -> None:
    import sys

    App(sys.argv[1] if len(sys.argv) > 1 else None).mainloop()


if __name__ == "__main__":
    main()
