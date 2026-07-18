# receden-history

医科診療報酬のレセ電コード(医科診療行為 S / 医薬品 Y / 特定器材 T / コメント C)について、
平成24年度改定以降の全件マスターを突き合わせてコード単位の変更履歴(新設・変更・廃止)を復元し、
**GitHub Pages の静的サイトとして検索・閲覧できる**ようにするツール。

**公開サイト: https://peirin1230-ship-it.github.io/receden-history/**

main ブランチへの push で自動的に再ビルド・再デプロイされる
(`data/raw/` に新しい全件CSVを追加してプッシュするだけでサイトが更新される)。

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
2. `docs/DATA_SOURCES.md` §3 のチェックリストに従い、各世代 × 各マスターの
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
- [x] M1: 取込と検証(validate / ingest)
- [x] M2: 履歴構築(build-history / show)
- [x] M3: 静的サイト生成(export-site / web フロントエンド)
- [x] M4: GitHub Pages 公開(main への push で自動デプロイ)
- [ ] M5: 任意拡張(改定分取込・系譜リンク・CSV出力・CI)
  ※ `export`(events.csv / summary.md)のみ先行実装済み

## 使い方

```bash
pip install -e .
receden validate                 # 配置済みCSVの検証レポート
receden ingest                   # data/raw → SQLite 取込
receden build-history            # 世代間差分からイベント生成
receden show S 111000110         # コード単位のタイムライン表示
receden search 初診              # 名称・コード前方一致でコード検索
receden export-site --out _site  # GitHub Pages 用静的サイト生成
receden export                   # exports/ に events.csv / summary.md を出力
python -m http.server --directory _site  # ローカルでサイト確認
```

## 実測列数(世代×マスター)

実データの列プローブで確認した実測値(`ingest` 時に snapshots テーブルにも記録される)。
列位置の詳細は `config/layouts/*.yaml` を参照。

| 世代 | S(医科診療行為) | Y(医薬品) | T(特定器材) | C(コメント) |
|---|---|---|---|---|
| h24〜h28 | 122 | 35 | 37 | 19 |
| h30〜r01 | 122 | 35 | 37 | 30 |
| r02〜r04 | 150 | 35 | 37 | 30 |
| r06〜r08 | 150 | 42 | 38 | 30 |

※ r07(令和7年度薬価改定)は医薬品(Y)のみの世代(`config/eras.yaml` の `masters: [Y]`)。

主要列(コード・名称・点数/金額・変更年月日・廃止年月日・基本名称)の位置は
全世代で共通(列追加は末尾側のみ)であることを実測で確認済み。

## 実装上の仮定(実データの観察に基づく)

1. **日付の未設定表現**: 仕様書上は `0` だが、実データでは `99999999`(無期限)・
   `00000000` も未設定として使われているため、いずれも NULL として扱う
2. **特定器材の廃止日**: 廃止年月日(列30)はほぼ常に `99999999` で、実際の使用期限は
   経過措置年月日(列29)に入る。abolished イベントの日付は両者の遅い方を採用する
3. **最終世代の期間窓**: §6.1 のとおり [施行日, 取込日] とする。取込日は `ingest` 実行時に
   snapshots.ingested_at に記録され、`build-history` はこれを期間窓の上限に使う(再現性 §9 を
   維持)。実データには事前告知された未来の変更年月日(例: r08 に 2027-06-01 が298件)が
   存在し、これらの新設イベントは exact ではなく era_boundary(施行日)になる
4. **コメントマスターの旧レイアウト(19列)**: 変更年月日(列18)・廃止年月日(列19)は
   実データで全行 `0`(未設定)のため、h24〜h28 のイベント日付は事実上すべて世代境界になる
5. **点数・金額の正規化**: `270.00` → `270` のように小数の末尾ゼロを落として格納する
   (世代間の表記揺れによる差分誤検知の防止)
6. **医薬品(Y)の世代**: 薬価は中間年にも改定されるため、医薬品のみの世代
   r07(令和7年度薬価改定、2025-04-01 施行)を `masters: [Y]` として定義している。
   期間窓はマスター別に「そのマスターが対象の世代」だけで計算されるので、
   r07 が S/T/C の期間窓を分断することはない。なお令和3・5年度の中間年薬価改定は
   独立した世代を持たない(その改定による変更は r02・r04 世代内の変更として、
   変更年月日が窓内であれば exact の日付で復元される)
7. **医薬品の廃止日**: 特定器材と同様、廃止年月日(列31)はほぼ常に `99999999` で、
   実際の使用期限は経過措置年月日(列34)に入るため、両者の遅い方を採用する
8. **薬価改定日と期間窓のずれ**: r06 以降、本体改定(6/1施行)と薬価改定(4/1施行)の
   施行日が異なるため、`config/eras.yaml` の `effective_date_overrides` で医薬品の
   施行日を 4/1 に差し替えている。医薬品の期間窓・世代境界(era_boundary の代用日付)は
   薬価改定日基準で計算される(実データでは r06/r08 の医薬品の大半が変更年月日=4/1
   であり、この差し替えにより exact の日付で復元される)
9. **医薬品マスターの列名**: 支払基金サイトに本環境から到達できず仕様説明書PDFとの
   突合が未実施のため、`config/layouts/y_iyakuhin.yaml` の列名は実データの値パターン
   から同定した(列位置の同一性は全10ファイルの実測プローブで確認済み)
