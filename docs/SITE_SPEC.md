# 静的サイト仕様: GitHub Pages ビューア

最終更新: 2026-07-14

## 0. 位置づけ

- 履歴DB(events / records)の内容をブラウザで検索・閲覧するための**静的サイト**。GitHub Pages でホストする
- サーバ処理は一切なし。`receden export-site` が全データを**事前計算済みの静的JSON**に書き出し、
  フロントエンドはそれを fetch して表示するだけ
- CLI(`show` / `search`)は開発・検証用として残すが、利用者向けUIはこのサイト

## 1. GitHub Pages の制約(設計の前提。違反しやすいので注意)

1. 静的ファイルのみ。API・DB・サーバサイド処理なし → **全データを事前生成**
2. プロジェクトページは `https://{user}.github.io/{repo}/` 配下で配信される
   → **アセット・fetch のパスは必ず相対パス(`./data/...`, `./assets/...`)**。
   先頭 `/` の絶対パスはローカルで動いても本番で404になる
3. SPA の history ルーティングは直リンクで404になる
   → **ハッシュルーティング(`#/S/111000110`)を使う**(404.html ハックは使わない)
4. サイト上限 約1GB / 1ファイル100MB。テキストは自動でgzip配信される
5. Pages サイトは(GitHub Enterprise のアクセス制御を除き)**常に一般公開**。
   private リポジトリで Pages を使うには有料プランが必要な点にも注意 → §6

## 2. 出力構成(`receden export-site --out _site`)

```
_site/
├── index.html
├── assets/
│   ├── app.js          # フレームワークなしの ES modules(ビルド工程なし)
│   └── style.css
└── data/
    ├── meta.json       # 生成日時・世代一覧・免責文・件数サマリ
    ├── summary.json    # 世代 × マスター × イベント種別の件数
    ├── search/
    │   ├── S.json      # 検索インデックス(マスター別)
    │   ├── Y.json
    │   ├── T.json
    │   └── C.json
    └── history/
        ├── S/{shard}.json   # shard = コード先頭3桁(大きい場合は深掘り。meta.shard_len 参照)
        ├── Y/{shard}.json   # 医薬品はコードが62000台に集中するため先頭6桁
        ├── T/{shard}.json
        └── C/{shard}.json
```

- フロントエンドの原本は リポジトリの `web/`(index.html / assets)に置き、
  export-site が `_site/` へコピー + `data/` を生成する
- 出力はすべて **UTF-8**(cp932→UTF-8 変換は ingest 時に完了している)
- shard は 1ファイル gzip前 1〜2MB 以下を目安とし、超える場合は先頭桁数を深掘りする
  (上限6桁。実際の桁数は meta.json の shard_len にマスター別に記録される)

## 3. データJSONスキーマ

### meta.json

```json
{
  "generated_at": "2026-07-20T12:34:56+09:00",
  "eras": [{"id": "h24", "label": "平成24年度改定", "effective_date": "2012-04-01"}, ...],
  "counts": {"S": {"codes": 12345, "events": 45678}, "Y": {...}, "T": {...}, "C": {...}},
  "disclaimer": "本サイトは社会保険診療報酬支払基金「基本マスター」を加工して作成した非公式の資料です。..."
}
```

### summary.json

```json
{"S": {"h26": {"new": 123, "changed": 456, "abolished": 78}, ...}, "Y": {...}, "T": {...}, "C": {...}}
```

### search/{M}.json(サイズ節約のため配列の配列)

```json
{
  "master": "S",
  "columns": ["code", "name", "status", "abolished_date"],
  "items": [
    ["111000110", "初診料", "active", null],
    ["1130xxxxx", "×××加算", "abolished", "2024-05-31"]
  ]
}
```

- `name` は最新スナップショット時点の省略名称(廃止済みは最終在籍世代のもの)
- `status`: `active`(最新世代に存在) / `abolished`

### history/{M}/{shard}.json

```json
{
  "111000110": {
    "name": "初診料",
    "events": [
      {"date": "2012-04-01", "precision": "baseline", "type": "baseline",
       "snapshot_fields": {"price": "270", "price_type": "3"}},
      {"date": "2014-04-01", "precision": "era_boundary", "type": "changed",
       "from_era": "h24", "to_era": "h26",
       "changes": [{"field": "price", "old": "270", "new": "282"}]}
    ]
  }
}
```

## 4. 画面仕様(MVP)

1. **トップ**: 検索ボックス + マスター種別タブ(S/Y/T/C) + サマリ表(世代別 新設/変更/廃止件数)
   + データの注意書き(§5)
2. **検索結果**: コード前方一致 or 名称部分一致(まずは素朴な `includes` でよい。
   全角/半角・カナ正規化は改善候補としてTODOコメントに)。廃止コードは「廃止」バッジ表示
3. **コード詳細**(`#/S/111000110`): タイムラインを縦に表示。
   - `changed` は field / old → new を表形式で
   - `abolished` は使用期限(廃止年月日)を表示
   - precision バッジ + 凡例(exact=マスター記載日 / era_boundary=施行日で代用 / baseline=平成24年時点で収載済)
4. **直リンク**: ハッシュルーティングにより、詳細画面のURLを共有・ブックマーク可能

### 挙動

- 検索インデックスは「そのマスターを初めて検索したとき」に遅延ロードし、メモリにキャッシュ
- 詳細表示は該当 shard のみ fetch
- fetch 失敗時は画面にエラーメッセージ表示 + console にリクエストURLを出す(相対パス誤り検知用)
- スマホ幅でも崩れないシンプルなレスポンシブCSS

## 5. サイトに明記する注意書き(meta.json に持たせ、トップとフッターに表示)

1. 出典: 社会保険診療報酬支払基金「基本マスター」を加工して作成(全件ファイルの取得日を明記)
2. 非公式であり正確性を保証しない。実務判断は原本(告示・仕様説明書・各マスター)で確認すること
3. 履歴復元の制約(REQUIREMENTS.md §6.6):
   - 世代内の複数回変更は1イベントに圧縮される
   - 世代内で新設→廃止まで完了したコードは検出できない
   - 平成24年度より前の履歴は不明(baseline 扱い)

## 6. デプロイ

- **推奨**: 同梱の `.github/workflows/deploy-pages.yml` による GitHub Actions デプロイ
  - リポジトリ Settings → Pages → Build and deployment → Source を **「GitHub Actions」** に設定
  - 前提: `data/raw/` の全件CSVがコミットされていること
    (ワークフローが validate → ingest → build-history → export-site を実行して配信)
  - push トリガーは M4 でコメントを外して有効化(それまでは手動実行のみ)
- **代替**(生データをコミットしない場合): ローカルで `receden export-site --out _site` を実行し、
  `_site/` を .gitignore から外してコミット。ワークフローは upload-pages-artifact + deploy のみに簡略化
- **公開範囲の注意**: Pages サイトは公開される(private リポジトリでも)。
  公開前に支払基金サイトの利用条件( https://www.ssk.or.jp/riyo.html )を確認し、
  §5 の出典・免責を必ず表示すること(docs/DATA_SOURCES.md §7 も参照)

## 7. サイズ・性能予算

- 検索インデックス: gzip後 500KB/マスター以下を目安。超える場合はコード先頭桁で分割
- history shard: gzip前 1〜2MB 以下を目安(§2)
- 外部CDN・npm依存を持たない(オフライン再現性とサプライチェーンリスク回避)。
  どうしてもライブラリが必要な場合はリポジトリに同梱する

## 8. 受け入れ確認(M4 完了条件)

1. 公開URL(`https://{user}.github.io/{repo}/`)でトップが表示される
2. 「初診」で検索 → `S 111000110 初診料` の詳細を開き、タイムラインが
   REQUIREMENTS.md §11-1 の期待値(270→282→288→291…)と一致する
3. `#/S/111000110` を新しいタブで直接開いて(リロード含め)表示できる
4. 廃止コード(r04→r06間で消失した任意の例)が検索で「廃止」バッジ付きで出て、
   詳細に abolished イベントが表示される
5. スマホ幅(375px程度)で表示が崩れない
6. ブラウザの開発者ツールで、fetch がすべて相対パスで成功している(404なし)
