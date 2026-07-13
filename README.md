# receden-history

医科診療報酬のレセ電コード(医科診療行為 S / 特定器材 T / コメント C)について、
平成24年度改定以降の全件マスターを突き合わせてコード単位の変更履歴(新設・変更・廃止)を復元し、
**GitHub Pages の静的サイトとして検索・閲覧できる**ようにするツール。

## 背景

全件マスターは「現在の状態+直近1回の変更情報」しか持たず、廃止コードは次回更新で
削除されるため、履歴は複数世代のスナップショット差分からしか復元できない。
詳細は `docs/REQUIREMENTS.md` を参照。

## セットアップ(Claude Code への引き継ぎ手順)

1. このリポジトリを GitHub に作成してコミットする
   ```bash
   git init && git add -A && git commit -m "docs: 引き継ぎ資料一式"
   gh repo create receden-history --public --source=. --push
   ```
   ※ 公開/非公開の判断は `docs/DATA_SOURCES.md` §7 を確認。
   GitHub Pages は private リポジトリだと有料プランが必要で、サイト自体はいずれ公開になる
2. `docs/DATA_SOURCES.md` §3 のチェックリストに従い、9世代 × 3マスターの
   全件CSVをダウンロードして `data/raw/{era}/` に配置し、コミットする
3. GitHub のリポジトリ Settings → Pages → Build and deployment → Source を
   **「GitHub Actions」** に設定する
4. リポジトリ直下で Claude Code を起動し、次のように指示する:
   > docs/REQUIREMENTS.md を読んで、M1(取込と検証)から実装してください。
   > 最終ゴールは M4 の GitHub Pages 公開です
5. M4 完了後は、main への push で自動デプロイされる
   (公開URL: `https://{ユーザー名}.github.io/receden-history/`)

`CLAUDE.md` は Claude Code が自動で読み込む。

## ドキュメント

| ファイル | 内容 |
|---|---|
| `CLAUDE.md` | Claude Code 向けプロジェクト指示(技術方針・禁止事項) |
| `docs/REQUIREMENTS.md` | 要件定義・差分アルゴリズム・DBスキーマ・マイルストーン |
| `docs/DATA_SOURCES.md` | データ入手元・世代一覧・ダウンロードチェックリスト |
| `docs/FILE_LAYOUTS.md` | CSVレイアウト・キーカラム位置・世代差異の注意 |
| `docs/SITE_SPEC.md` | GitHub Pages 静的サイトの仕様(画面・JSON・デプロイ) |
| `.github/workflows/deploy-pages.yml` | Pages 自動デプロイ(push トリガーは M4 で有効化) |
| `config/eras.yaml` | 世代(スナップショット)定義 |
| `config/layouts/*.yaml` | マスター別カラム定義(ツールはここから列位置を読む) |

## ステータス

- [x] 引き継ぎ資料(要件・データソース・レイアウト定義・サイト仕様)
- [ ] M1: 取込と検証(validate / ingest)
- [ ] M2: 履歴構築(build-history / show)
- [ ] M3: 静的サイト生成(export-site / web フロントエンド)
- [ ] M4: GitHub Pages 公開
- [ ] M5: 任意拡張(改定分取込・系譜リンク・CSV出力・CI)
