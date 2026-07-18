# 要件定義書: レセ電コード変更履歴ツール (receden-history)

最終更新: 2026-07-14 / 作成: Claude (claude.ai での事前調査に基づく引き継ぎ資料)

## 1. 目的

医科診療報酬のレセ電コード(医科診療行為・医薬品・特定器材・コメントの4マスター)について、
平成24年度改定以降の全件マスターを取り込み、**レセ電コードごとに
「いつ新設され、いつ何が変更され、いつ廃止されたか」を照会できる**ようにする。
照会UIは **GitHub Pages でホストする静的サイト** とし、ブラウザから検索・
タイムライン閲覧できるようにする(CLIは開発・検証用の補助と位置づける)。

## 2. 背景(設計の前提)

- 全件マスターの各レコードが持つ履歴情報は「変更区分(直近更新での異動種別)」
  「変更年月日(直近1回の変更日)」「廃止年月日(使用期限)」のみ。
  **収載年月日(新設日)を持たない**(傷病名マスターと異なる)。
- 変更区分 `9`(廃止)のレコードは次回マスター更新時に削除される。
  → 廃止済みコードは最新の全件ファイルに存在しない。
- 支払基金サイトには改定世代ごと(平成24〜令和6 + 現行)の全件ファイルが
  アーカイブされている。各世代ページの全件ファイルは基本的に
  **その世代の最終状態(次期改定直前のスナップショット)** である。
- したがって「世代スナップショットを古い順に突き合わせた差分」が履歴復元の基本戦略となる。
  世代内(随時改定)の細かい日付は、レコードの変更年月日・廃止年月日で補正する。

## 3. スコープ

### 対象

- マスター: 医科診療行為(S)、医薬品(Y)、特定器材(T)、コメント(C)
  ※ 医薬品は 2026-07 に追加。薬価は中間年にも改定されるため、医薬品のみの世代
  (例: r07 = 令和7年度薬価改定)を `config/eras.yaml` の `masters: [Y]` で定義できる
- 期間: 平成24年度改定(2012年度)〜現行(令和8年度)
- 入力: 各世代の全件マスターCSV(ユーザーが手動ダウンロードして `data/raw/{era}/` に配置)

### 対象外(ただし拡張しやすい設計にする)

- 歯科(H)・調剤(M)・傷病名(B)等の他マスター
- 改定分ファイル(差分CSV)の取込 → Phase 2(M5)として設計だけ考慮
- 労災レセプト電算処理マスター

## 4. 入力データ

- 配置: `data/raw/{era}/*.csv`(era は `config/eras.yaml` の id: h24, h26, h28, h30, r01, r02, r04, r06, r07, r08)
- 形式: ヘッダ行なし、カンマ区切り、cp932、CRLF。詳細は `docs/FILE_LAYOUTS.md`
- 1世代ディレクトリにその世代が対象とするマスター(通常 S/Y/T/C の4ファイル、
  薬価改定世代は Y のみ)を想定。ファイル名は任意
  (推奨: `s_ALLyyyymmdd.csv` 等)だが、**ツールはファイル名に依存せず
  2列目のマスター種別(S/Y/T/C)で自動判定**する
- ZIPのまま置かれた場合の自動展開は必須ではない(あれば便利程度)

## 5. 機能要件

| ID | コマンド | 概要 |
|---|---|---|
| F1 | `validate` | 配置済みCSVの品質・レイアウト検証レポート(取込前に実行可能) |
| F2 | `ingest` | CSV → SQLite(snapshots / records)。冪等(再実行で同一結果) |
| F3 | `build-history` | 世代間差分から events を生成 |
| F4 | `show` / `search` | コード単位のタイムライン表示、名称からのコード検索(開発・検証用) |
| F5 | `export` | events.csv / summary.md 等の出力(任意・M5) |
| F6 | `export-site` | **GitHub Pages 用の静的サイト生成**(HTML/JS + 事前計算JSON) |

### F1 validate の検査項目

ファイル(=世代×マスター)ごとに以下を検査し、テキストレポートを出力する:

1. cp932 デコード例外が0件
2. 2列目がすべて期待するマスター種別文字(S/Y/T/C)
3. コード列が `^\d{9}$` に一致する率 ≥ 99%(コメントは導出後のコードで判定)
4. 変更年月日・廃止年月日列が `^(0|\d{8})$` に一致する率 ≥ 99%
   → **不一致が多い場合はその世代のカラム位置がconfigとズレている疑い**。
   実測でズレを見つけたら `config/layouts/` に世代別定義を追加する(FILE_LAYOUTS.md 参照)
5. 名称列に非ASCII文字(日本語)を含む率が高いこと(列ズレ検知の補助)
6. 主キー(マスター種別+コード)の重複が0件
7. 実測列数の最頻値・分布(世代間のレイアウト差の把握用に snapshots に記録)
8. 行数(公式ページ掲載の件数と目視突合するための出力)

### F2 ingest の仕様

- `config/eras.yaml` の順序で各世代を処理
- レコード正規化:
  - コード: S/Y/T は3列目そのまま。C は列23(コメントコード)があればそれ、
    なければ `"8" + パターン.zfill(2) + 一連番号.zfill(6)` で導出
  - 日付列: `"0"`・空 → NULL、それ以外は `YYYYMMDD` 文字列のまま保持
  - layout で key を割り当てた列は個別カラム or `extra_json` に格納
  - 原文行(`raw`)を必ず保持(監査・デバッグ用)
- ファイルの sha256 を snapshots に記録し、同一ファイルの再取込はスキップ

### F3 build-history の仕様 → §6

### F4 show の出力イメージ

```
$ receden show S 111000110
S 111000110 初診料
  2012-04-01 [baseline]     平成24年度時点で収載済 (270点)
  2014-04-01 [era_boundary] 変更: 点数 270 → 282
  2019-10-01 [era_boundary] 変更: 点数 282 → 288
  2024-06-01 [era_boundary] 変更: 点数 288 → 291
  ...
```

- `--format json` で機械可読出力
- `search <keyword>` は省略名称・基本名称の部分一致でコード一覧を返す

### F5 export(任意・M5)

- `exports/events.csv`(全イベントのフラットダンプ)
- `exports/summary.md`(世代別の 新設/変更/廃止 件数表。
  支払基金の「改定分内容」PDFと目視突合できる粒度)

### F6 export-site(本ツールの主要な出力)

- `_site/` に静的サイト一式(index.html / assets / data/*.json)を生成する
- ページ構成・JSONスキーマ・GitHub Pages 特有の制約(相対パス、ハッシュルーティング等)は
  **`docs/SITE_SPEC.md` に従うこと**
- デプロイは同梱の `.github/workflows/deploy-pages.yml` で行う(§10 M4)

## 6. 差分・履歴復元アルゴリズム

### 6.1 スナップショットの順序と期間窓

`config/eras.yaml` の並び順(古い順)を正とする。
**期間窓はマスター別に計算する**: まず対象マスターが `masters` に含まれる世代だけに絞り、
各世代の施行日を `effective_date_overrides`(あれば。例: 薬価改定は4/1)で差し替えた列を使う
(実装: `config.EraSet.for_master`)。他マスター専用の世代(例: r07 = 薬価改定)が
S/T/C の期間窓を分断してはならない。
その列で、世代 N のスナップショットの「期間窓」= `[eras[N].effective_date, eras[N+1].effective_date)`。
最終世代は `[effective_date, 取込日]`。

### 6.2 レコードキー

`(master, code)`。コードの再利用は原則ないとされるが、検知したら
`reappeared` イベントとして警告フラグを付ける(§6.4)。

### 6.3 比較対象フィールド

- 各 layout の `tracked_fields`(名称・点数/金額・基本名称など)を主対象とする
- **両世代の layout で共に定義されているフィールドのみ比較する**。
  片方の世代にしか存在しない列(後年追加された列)は差分判定から除外する
  (「列が増えた」ことを「値の変更」と誤検知しないため)
- tracked 以外のマップ済みフィールドが変わった場合は
  `その他項目変更` として changed_fields に field 名を記録する

### 6.4 イベント種別と判定

連続する2スナップショット S_n(世代n) と S_{n+1}(世代n+1) を突き合わせる:

| 判定 | イベント |
|---|---|
| 最古の世代(h24)に存在 | `baseline`(収載日は不明。「平成24年時点で収載済」) |
| S_{n+1} にあり S_n にない | `new`(新設) |
| 両方にあり比較フィールドが異なる | `changed`(変更。changed_fields に old→new を記録) |
| S_n にあり S_{n+1} にない | `abolished`(廃止) |
| 過去に消えたコードが再出現 | `reappeared`(警告フラグ付き) |

補助情報として、各レコードの変更区分(1:抹消 / 3:新規 / 5:変更 / 9:廃止)も
イベントに添付する。変更区分 `9` の行はそのスナップショットには「存在する」
ものとして扱い(消えるのは次回)、廃止予定フラグとして記録する。
変更区分 `1`(抹消)は誤収載等の削除を意味するため、通常の廃止と区別して記録する。

### 6.5 イベント日付の決定規則

| イベント | 日付の決め方 | precision |
|---|---|---|
| baseline | h24 の effective_date (2012-04-01) | `baseline` |
| new | S_{n+1} 側レコードの変更年月日が期間窓内 → その日付 | `exact` |
|  | 上記以外 → 世代n+1 の effective_date | `era_boundary` |
| changed | S_{n+1} 側レコードの変更年月日が期間窓内 → その日付 | `exact` |
|  | 上記以外 → 世代n+1 の effective_date | `era_boundary` |
| abolished | S_n 側レコードの廃止年月日(特定器材・医薬品は経過措置年月日も考慮し遅い方)があればその日付(=使用期限) | `exact` |
|  | なければ 世代n+1 の effective_date | `era_boundary` |

### 6.6 既知の制約(ドキュメント化すること)

- 世代内で複数回変更があっても、スナップショット差分では**1イベントに圧縮される**
  (最後の状態しか見えない)。改定分ファイル取込(M5)で解消可能
- h24 より前の履歴は復元不能(baseline 扱い)
- 世代内で「新設→廃止」まで完了したコードはスナップショットに現れず**検出漏れになる**。
  この制約も M5 で解消可能。**サイトの注意書き(SITE_SPEC.md §5)に明記すること**

## 7. データベーススキーマ(案)

実装時に調整してよいが、趣旨(スナップショット原本の保持とイベントの分離)は維持する。

```sql
CREATE TABLE snapshots(
  id           INTEGER PRIMARY KEY,
  era          TEXT NOT NULL,          -- eras.yaml の id
  master       TEXT NOT NULL,          -- 'S' / 'Y' / 'T' / 'C'
  file_name    TEXT NOT NULL,
  file_sha256  TEXT NOT NULL,
  column_count INTEGER,                -- 実測列数(最頻値)
  row_count    INTEGER,
  UNIQUE(era, master)
);

CREATE TABLE records(
  snapshot_id  INTEGER NOT NULL REFERENCES snapshots(id),
  code         TEXT NOT NULL,          -- 正規化済み9桁
  change_kubun TEXT,                   -- 変更区分(0/1/3/5/9)
  name         TEXT,                   -- 省略漢字名称 / コメント文
  basic_name   TEXT,                   -- 基本漢字名称(ある世代のみ)
  price_type   TEXT,                   -- 点数識別 / 金額種別
  price        TEXT,                   -- 新又は現点数 / 新又は現金額
  changed_at   TEXT,                   -- 変更年月日 YYYYMMDD or NULL
  abolished_at TEXT,                   -- 廃止年月日 YYYYMMDD or NULL
  extra_json   TEXT,                   -- layoutでkey付けしたその他項目の辞書
  raw          TEXT NOT NULL,          -- 原文行
  PRIMARY KEY(snapshot_id, code)
);

CREATE TABLE events(
  id             INTEGER PRIMARY KEY,
  master         TEXT NOT NULL,
  code           TEXT NOT NULL,
  event_type     TEXT NOT NULL,        -- baseline/new/changed/abolished/reappeared
  event_date     TEXT NOT NULL,        -- YYYY-MM-DD
  date_precision TEXT NOT NULL,        -- exact/era_boundary/baseline
  from_era       TEXT,
  to_era         TEXT,
  change_kubun   TEXT,                 -- 参考: レコード側の変更区分
  changed_fields TEXT                  -- JSON: [{"field":..,"old":..,"new":..}]
);
CREATE INDEX idx_events_code ON events(master, code);
```

## 8. CLI仕様(案)

```
receden validate [--era ERA] [--master S|Y|T|C]
receden ingest   [--era ERA] [--force]
receden build-history
receden show <MASTER> <CODE> [--format text|json]
receden search <KEYWORD> [--master S|Y|T|C]
receden export      [--out exports/]     # 任意(M5)
receden export-site [--out _site]        # GitHub Pages 用サイト生成
```

## 9. 非機能要件

- 再現性: 同じ入力からは同じDB・同じイベントが生成される(冪等)
- 規模感: 1世代あたり 医科 約1.2万行 / 特定器材 数千行 / コメント 数千〜1万行 × 9世代。
  SQLite + 標準ライブラリで十分。性能チューニング不要
- 監査性: すべてのイベントは元スナップショット(from/to era)まで遡れること

## 10. マイルストーン

### M1: 取込と検証

- `layouts.py` / `validate` / `ingest` を実装
- 完了条件:
  - 配置済み全ファイルが ingest でき、世代×マスターの行数レポートが出る
  - validate の全検査項目が実装され、実データでのズレ(あれば)がレポートされる
  - 名称カラムが文字化けなく格納される(サンプル表示で確認)
  - pytest: fixtures での取込テスト green

### M2: 履歴構築

- `diff.py` / `build-history` / `show` を実装
- 完了条件:
  - `show S 111000110`(初診料)のタイムラインが受け入れテスト(§11)と一致
  - r04→r06 間で消失したコードが `abolished` として検出される
  - reappeared の検知ロジックがあり、fixtures でテストされている

### M3: 静的サイト生成

- `export-site` と `web/` フロントエンド(検索・サマリ・タイムライン)を実装
  (仕様: `docs/SITE_SPEC.md`)。`search` CLI もこのタイミングで実装
  (検索インデックス生成とロジックを共用)
- 完了条件:
  - `receden export-site --out _site` で SITE_SPEC.md §2 の構成が生成される
  - `python -m http.server --directory _site` でローカル閲覧でき、
    検索 → コード詳細 → タイムライン表示が一通り動く
  - fetch・アセットのパスがすべて相対で、`#/S/<code>` 形式の直リンクが機能する

### M4: GitHub Pages 公開

- `.github/workflows/deploy-pages.yml` の push トリガーを有効化してデプロイ
  (リポジトリ Settings → Pages → Source を「GitHub Actions」に設定)
- 完了条件: 公開URLで SITE_SPEC.md §8 の受け入れ確認をすべて満たす

### M5(任意・Phase 2)

- 改定分ファイル(支払基金の更新日単位CSV)の取込 → 世代内イベントの日付を exact 化、
  世代内「新設→廃止」の検出漏れも解消
- 特定器材の「廃止・新設関連」(列27)による新旧コードの系譜リンク(サイト詳細画面に表示)
- `export`(events.csv / summary.md)によるオフライン突合用ダンプ
- GitHub Actions で pytest 自動実行(CI)

## 11. 受け入れテスト(実データでのスモークテスト)

1. **初診料 S 111000110**: 全世代に存在し、点数が
   270(h24) → 282(h26) → 288(r01) → 291(r06) と変化するイベントが出ること。
   ※ 282/288 は消費税対応改定。r08 での点数は実データで確認して期待値を確定させる
2. **廃止検出**: 隣接世代間で消失したコード件数が0でないこと
   (件数の妥当性は支払基金「改定分内容」PDFと目視突合)
3. **コメント**: 導出コードが9桁 `^8\d{8}$` になること
4. **文字コード**: 名称に「①」等の機種依存文字を含むレコードが化けないこと
5. **サイト**: 公開URL(または `python -m http.server`)で 1. の初診料を検索し、
   詳細タイムラインが CLI `show` の結果と一致すること
6. **直リンク**: `#/S/111000110` を直接開いて(リロード含め)表示できること

## 12. リスク・エッジケース一覧

| # | 項目 | 対応方針 |
|---|---|---|
| 1 | cp932 でないと化ける文字(①㈱等) | encoding='cp932' 固定。デコード失敗は validate で報告 |
| 2 | 引用符の有無が世代・マスターで異なる可能性 | csv モジュールで両対応。手書きsplitしない |
| 3 | 数字モードの前ゼロ省略 | 日付 "0"=未設定。コメント一連番号は zfill(6) |
| 4 | 世代間の列数差 | layouts.yaml + validate プローブ。ハードコード禁止 |
| 5 | 予備列(すべて"0") | 差分対象にしない |
| 6 | コメントマスター列23が古い世代に無い可能性 | 無ければ導出式で代替 |
| 7 | 変更区分9の行 | 存在扱い+廃止予定フラグ。次世代で消えるのが正常 |
| 8 | 変更区分1(抹消) | 廃止と区別して記録 |
| 9 | 同一コード重複行 | validate でエラー報告(取込は後勝ちにせず停止) |
| 10 | ヘッダ行なし | 1行目からデータとして読む |
| 11 | 世代内複数変更の圧縮・世代内新設→廃止の検出漏れ | 制約として明記(サイトにも表示)。M5で解消 |
| 12 | コード再利用の可能性 | reappeared として警告 |
| 13 | 生データの再配布可否 | docs/DATA_SOURCES.md の注意書き参照 |
| 14 | Pagesは `/{repo}/` 配下で配信 → 絶対パスは404 | fetch・アセットは相対パスのみ(SITE_SPEC.md §1) |
| 15 | サイト公開=マスター派生データの公衆送信 | 公開前に利用条件を確認し、出典・免責をサイトに明記(SITE_SPEC.md §5〜6) |
| 16 | 検索インデックス肥大で初回ロードが重い | サイズ予算と分割方針に従う(SITE_SPEC.md §7) |
