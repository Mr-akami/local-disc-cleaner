# local-disc-cleaner

Linux のディスク使用量を GUI (Tkinter) で確認・削除するツール。ファイル / Docker / Nix に対応。

## 機能

- **ディスク使用量**: マウントごとの使用率バー
- **おすすめ削除**: ファイル・Docker・Nix の削除候補を「おすすめ度」順に一覧
  - おすすめ度 = サイズ(GB) × 安全度 × 古さ × 100
  - 判定: 緑=安全(再生成可) / 黄=要確認 / 赤=使用中・保護
- **ファイル**: `du` でスキャンし、使用量の大きい順 or おすすめ順にディレクトリ/ファイルを表示。ダブルクリックで潜る
  - 検出: `node_modules`, `.venv`, `__pycache__`, Rust `target`, `~/.cache/*`, ゴミ箱, npm/Cargo/Maven/Go キャッシュ, 巨大ファイル, ログ, アーカイブ 等
- **Docker**: イメージ / コンテナ / ボリューム / ビルドキャッシュ。`builder prune`, `system prune`
- **Nix**: 不要ストアパス (GC 対象) の合計サイズ、古いプロファイル世代。`nix-collect-garbage [-d]`
- 削除ボタン (確認ダイアログあり、完全削除)
  - Docker / Nix の削除は必ず専用コマンド経由 (`docker rmi` / `docker rm` / `docker volume rm` / `docker builder prune` / `nix-collect-garbage` / `nix-env --delete-generations`)
  - `/nix`, `/var/lib/docker`, `~/.local/state/nix`, `~/.local/share/docker` 等はファイル削除不可 (保護)。`/usr` やホーム自体も保護

## 使い方

```sh
sudo apt install python3-tk   # 未導入なら
uv run disc-cleaner            # ホームをスキャン
uv run disc-cleaner /path/to/dir
```

> uv 管理の Python (python-build-standalone) の Tk は X11 環境でクラッシュすることがあるため、
> `pyproject.toml` で `python-preference = "only-system"` を指定しシステム Python を使用。

## テスト

```sh
uv run pytest
```
