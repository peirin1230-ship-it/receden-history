# CLAUDE.md — レセ電コード変更履歴ツール (receden-history)

## プロジェクト概要

診療報酬のレセプト電算処理システム用コード(レセ電コード)について、平成24年度改定以降の各世代の「全件マスター」CSVを突き合わせ、**コード単位の変更履歴(新設・変更・廃止)を復元し、GitHub Pages の静的サイトとして検索・閲覧できるようにするツール**を作る。構成は「CLI(取込・検証・履歴構築)+静的サイト生成(export-site)」で、利用者向けUIはサイト、CLIは開発・検証用。

対象マスターは次の4種類:

| マスター | マスター種別(2列目) | キー |
|---|---|---|
| 医科診療行為マスター | `S` | 診療行為コード(9桁) |
| 医薬品マスター | `Y` | 医薬品コード(9桁) |
| 特定器材マスター | `T` | 特定器材コード(9桁) |
| コメントマスター | `C` | コメントコード(9桁) |

医薬品は薬価の中間年改定があるため、医薬品のみの世代(例: r07)を
`config/eras.yaml` の `masters: [Y]` で定義する。期間窓はマスター別に
「そのマスターが対象の世代」だけで計算される(`config.EraSet.for_master`)。

### なぜ差分復元が必要か(前提知識)

- 全件マスターの各レコードは「現在の状態 + 直近1回の変更情報(変更区分・変更年月日)」しか持たない。**収載年月日(新設日)フィールドは存在しない**。
- 変更区分 `9`(廃止)のレコードは**次回のマスター更新時に削除される**。つまり最新の全件ファイルには廃止済みコードが残らない。
- したがって履歴は、複数世代のスナップショット(全件ファイル)を時系列に突き合わせることでしか復元できない。

## 必読ドキュメント(この順で読むこと)

1. `docs/REQUIREMENTS.md` — 機能要件・差分アルゴリズム・DBスキーマ・マイルストーン
2. `docs/DATA_SOURCES.md` — データ入手元・世代一覧・ファイル配置ルール
3. `docs/FILE_LAYOUTS.md` — CSVレイアウト・キーカラム位置・落とし穴
4. `docs/SITE_SPEC.md` — GitHub Pages 静的サイトの仕様(画面・JSONスキーマ・デプロイ)
5. `config/eras.yaml` — 世代(スナップショット)定義
6. `config/layouts/*.yaml` — マスター別カラム定義(ツールはここから列位置を読む)

## 技術方針

- Python 3.11+。標準ライブラリ中心(`csv`, `sqlite3`, `argparse` または `typer`)。pandas は必須ではない(使うなら理由をコメントに)
- DB: SQLite 1ファイル(`data/db/masters.sqlite`)
- パッケージ: `src/receden_history/` 配下、`pyproject.toml` 管理
- テスト: `pytest`。`tests/fixtures/` に人工の小さなCSV(数行×2〜3世代分)を置いて差分ロジックをテスト
- lint/format: `ruff`
- フロントエンド: `web/` にフレームワークなしの HTML + ES modules(vanilla JS)。
  **npm 等のビルド工程を持たない**。外部CDNにも依存しない(仕様: `docs/SITE_SPEC.md`)
- コメント・docstring は日本語でよい

## 絶対に守ること

1. **CSVは `cp932` で読む**(`shift_jis` ではなく `cp932`。①や㈱などの拡張文字対策)
2. **カラム位置・列数をソースコードにハードコードしない**。必ず `config/layouts/*.yaml` から読む
3. 世代によって**列数が異なる**前提で実装する(列数不一致は即エラーにせず warning とし、`validate` コマンドで検出・報告)
4. 入力CSVに**ヘッダ行はない**
5. `data/raw/` は読み取り専用として扱う。加工結果は DB と `exports/` へ出力
6. 「最新の全件ファイルに存在しないコード」= 不正データではない(廃止済みの正常なケース)
7. 実データの中身を確認したいときは、まず先頭数行を cp932 でダンプして目視する(いきなり全件処理を書かない)
8. サイトの fetch・アセット参照は**必ず相対パス**にする(GitHub Pages は `https://{user}.github.io/{repo}/` 配下で配信されるため、先頭 `/` の絶対パスは本番で404になる)。画面遷移は**ハッシュルーティング**(`#/S/111000110`)を使う

## ディレクトリ構成(目標形)

```
receden-history/
├── CLAUDE.md                  # このファイル
├── README.md
├── pyproject.toml             # ← 未作成。M1で作成する
├── .github/workflows/
│   └── deploy-pages.yml       # GitHub Pages デプロイ(pushトリガーはM4で有効化)
├── config/
│   ├── eras.yaml              # 世代定義(施行日・URL)
│   └── layouts/
│       ├── s_ika.yaml         # 医科診療行為マスターのカラム定義
│       ├── y_iyakuhin.yaml    # 医薬品マスターのカラム定義
│       ├── t_tokutei_kizai.yaml
│       └── c_comment.yaml
├── data/
│   ├── raw/{h24,h26,h28,h30,r01,r02,r04,r06,r07,r08}/   # ユーザーがCSVを配置(r07は医薬品のみ)
│   └── db/masters.sqlite      # 生成物(gitignore)
├── docs/
│   ├── REQUIREMENTS.md
│   ├── DATA_SOURCES.md
│   └── FILE_LAYOUTS.md
├── exports/                   # 生成物(gitignore)
├── web/                       # サイトのフロントエンド原本(index.html / assets)
├── _site/                     # export-site の生成物(gitignore)
├── src/receden_history/
│   ├── __init__.py
│   ├── cli.py                 # エントリポイント
│   ├── layouts.py             # config/layouts の読込・列マッピング
│   ├── ingest.py              # CSV → SQLite
│   ├── validate.py            # レイアウト検証・品質チェック
│   ├── diff.py                # スナップショット差分 → イベント生成
│   ├── report.py              # show / search / export
│   └── site.py                # 静的サイト生成(export-site)
└── tests/
```

## 開発の進め方

`docs/REQUIREMENTS.md` の **M1 → M2 → M3 → M4 → (M5)** の順に実装する。最終ゴールは M4(GitHub Pages 公開)。各マイルストーンの完了条件を満たし、`pytest` が green になったらコミット。実装前に不明点があれば docs を再確認し、それでも曖昧な点は仮定を明記して進める(仮定は README または docs に追記)。

## 実装後に想定するコマンド(仕様は REQUIREMENTS.md §8)

```bash
receden validate                 # 配置済みCSVの検証レポート
receden ingest                   # data/raw → SQLite 取込
receden build-history            # 世代間差分からイベント生成
receden show S 111000110         # コード単位のタイムライン表示
receden search 初診               # 名称でコード検索
receden export-site --out _site  # GitHub Pages 用静的サイト生成
receden export                   # events.csv / summary.md 出力(任意)
```
