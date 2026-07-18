// レセ電コード変更履歴ビューア(フレームワークなしの ES module)
//
// - fetch はすべて相対パス(GitHub Pages の /{repo}/ 配下配信に対応)
// - 画面遷移はハッシュルーティング(#/S/111000110)。直リンク・リロード対応
// - 検索インデックスはマスター初回検索時に遅延ロードしてメモリにキャッシュ
// - 詳細表示は該当 shard の JSON のみ fetch

const MASTER_LABELS = { S: "医科診療行為", Y: "医薬品", T: "特定器材", C: "コメント" };
const PRICE_UNIT = { S: "点", Y: "円", T: "円", C: "" };

// フィールド名の表示ラベル(src/receden_history/report.py の FIELD_LABELS と対応)
const FIELD_LABELS = {
  short_name: "名称",
  basic_name: "基本名称",
  short_kana: "カナ名称",
  price: { S: "点数", T: "金額", C: "点数", Y: "金額(薬価)" },
  price_type: { S: "点数識別", T: "金額種別", C: "点数識別", Y: "金額種別" },
  unit_code: "単位コード",
  unit_name: "単位名称",
  max_points: "上限点数",
  succession_code: "廃止・新設関連",
  sentakushiki: "選択式コメント識別",
  tensuhyo_kubun: "点数表区分番号",
  remanufactured: "再製造単回使用医療機器",
  narcotic_kubun: "麻薬・毒薬・覚醒剤原料・向精神薬",
  biologic: "生物学的製剤",
  generic: "後発品",
  dosage_form: "剤形",
  yj_code: "薬価基準収載医薬品コード",
  generic_name_code: "一般名コード",
  generic_name: "一般名処方の標準的な記載",
};

const PRECISION_LABELS = {
  exact: "マスター記載日",
  era_boundary: "施行日で代用",
  baseline: "平成24年度時点",
};

const TYPE_LABELS = {
  baseline: "収載済",
  new: "新設",
  changed: "変更",
  abolished: "廃止",
  reappeared: "再出現",
};

const state = {
  meta: null,
  summary: null,
  searchIndex: {}, // master -> items(遅延ロードキャッシュ)
  shardCache: {}, // "S/111" -> shardデータ
  master: "S",
  query: "",
};

const app = document.getElementById("app");

function fieldLabel(key, master) {
  const label = FIELD_LABELS[key] || key;
  return typeof label === "object" ? label[master] || key : label;
}

async function fetchJSON(relPath) {
  // 相対パス必須: 先頭 "/" はプロジェクトページ配信時に 404 になる
  try {
    const res = await fetch(relPath);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error("fetch失敗:", relPath, err);
    throw new Error(`データの取得に失敗しました: ${relPath} (${err.message})`);
  }
}

async function ensureMeta() {
  if (!state.meta) {
    state.meta = await fetchJSON("./data/meta.json");
    renderFooter();
  }
  return state.meta;
}

async function ensureSearchIndex(master) {
  if (!state.searchIndex[master]) {
    const data = await fetchJSON(`./data/search/${master}.json`);
    state.searchIndex[master] = data.items; // [code, name, status, abolished_date]
  }
  return state.searchIndex[master];
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of [].concat(children)) {
    node.append(child instanceof Node ? child : document.createTextNode(child));
  }
  return node;
}

function errorBox(message) {
  return el("div", { class: "error-box" }, message);
}

function renderFooter() {
  const meta = state.meta;
  if (!meta) return;
  document.getElementById("footer-disclaimer").textContent = meta.disclaimer;
  const files = meta.eras
    .map((e) => Object.values(e.files || {}))
    .flat();
  document.getElementById("footer-generated").textContent =
    `生成日時: ${meta.generated_at} / 取込ファイル数: ${files.length}`;
}

// ---------- ルーティング ----------

function route() {
  const hash = decodeURIComponent(location.hash || "");
  const m = hash.match(/^#\/([SYTC])\/(\d{9})$/);
  if (m) {
    renderDetail(m[1], m[2]).catch((err) => {
      app.replaceChildren(errorBox(err.message));
    });
    return;
  }
  renderHome().catch((err) => {
    app.replaceChildren(errorBox(err.message));
  });
}

// ---------- トップ(検索 + サマリ) ----------

async function renderHome() {
  document.title = "レセ電コード変更履歴";
  const meta = await ensureMeta();

  const input = el("input", {
    type: "search",
    placeholder: "名称の一部(例: 初診)またはコード前方一致(例: 111)",
    value: state.query,
    autocomplete: "off",
  });
  const resultsHost = el("div");

  let timer = null;
  input.addEventListener("input", () => {
    state.query = input.value;
    clearTimeout(timer);
    timer = setTimeout(() => runSearch(resultsHost), 150);
  });

  // meta.counts にデータがあるマスターだけタブを出す(データ未生成のタブで404にしない)
  const masters = Object.keys(MASTER_LABELS).filter((m) => meta.counts?.[m]);
  if (masters.length && !masters.includes(state.master)) state.master = masters[0];
  const tabs = el(
    "div",
    { class: "tabs" },
    masters.map((m) =>
      el(
        "button",
        {
          class: `tab${state.master === m ? " active" : ""}`,
          onclick: (ev) => {
            state.master = m;
            document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
            ev.currentTarget.classList.add("active");
            runSearch(resultsHost);
            summaryHost.replaceChildren(summaryTable(meta));
          },
        },
        `${MASTER_LABELS[m]}(${m})`
      )
    )
  );

  const notice = el("details", { class: "notice", open: "" }, [
    el("summary", {}, "このサイトについて(必ずお読みください)"),
    el("p", {}, meta.disclaimer),
    el("ul", {}, (meta.limitations || []).map((t) => el("li", {}, t))),
  ]);

  const summaryHost = el("div");
  summaryHost.append(summaryTable(meta));

  app.replaceChildren(
    notice,
    tabs,
    el("div", { class: "search-box" }, [input]),
    el("p", { class: "search-hint" },
      "検索は名称の部分一致(TODO: 全角/半角・カナ正規化は今後の改善候補)とコード前方一致に対応"),
    resultsHost,
    el("h2", {}, "世代別イベント件数"),
    summaryHost,
    legendBlock()
  );

  if (state.query) await runSearch(resultsHost);
  input.focus();
}

async function runSearch(host) {
  const q = state.query.trim();
  if (!q) {
    host.replaceChildren();
    return;
  }
  let items;
  try {
    items = await ensureSearchIndex(state.master);
  } catch (err) {
    host.replaceChildren(errorBox(err.message));
    return;
  }
  // 素朴な検索: コードは前方一致、名称は部分一致(SITE_SPEC §4)
  // TODO: 全角/半角・ひらがな/カタカナの正規化
  const isCode = /^\d+$/.test(q);
  const hits = [];
  for (const [code, name, status, abolishedDate] of items) {
    if (isCode ? code.startsWith(q) : name.includes(q)) {
      hits.push([code, name, status, abolishedDate]);
      if (hits.length >= 300) break;
    }
  }
  const list = el(
    "ul",
    { class: "results" },
    hits.map(([code, name, status, abolishedDate]) => {
      const children = [
        el("span", { class: "code" }, code),
        el("span", {}, name || "(名称なし)"),
      ];
      if (status === "abolished") {
        children.push(el("span", { class: "badge abolished" },
          abolishedDate ? `廃止 ${abolishedDate}` : "廃止"));
      } else if (abolishedDate) {
        children.push(el("span", { class: "badge pending" }, `廃止予定 ${abolishedDate}`));
      }
      return el("li", {}, el("a", { href: `#/${state.master}/${code}` }, children));
    })
  );
  host.replaceChildren(
    list,
    el("p", { class: "result-count" },
      hits.length >= 300 ? "300件以上一致(先頭300件を表示)" : `${hits.length}件`)
  );
}

function summaryTable(meta) {
  const master = state.master;
  const summary = state.summary?.[master] || {};
  const rows = meta.eras
    .filter((e) => summary[e.id])
    .map((e) => {
      const c = summary[e.id];
      // マスター別施行日(例: 薬価改定は4/1)があればそちらを表示
      const date = e.effective_date_overrides?.[master] || e.effective_date;
      return el("tr", {}, [
        el("td", {}, `${e.label}(${date}〜)`),
        el("td", { class: "num" }, String(c.new || 0)),
        el("td", { class: "num" }, String(c.changed || 0)),
        el("td", { class: "num" }, String(c.abolished || 0)),
        el("td", { class: "num" }, String(c.reappeared || 0)),
      ]);
    });
  const table = el("div", { class: "table-wrap" }, [
    el("table", {}, [
      el("thead", {}, el("tr", {},
        ["世代", "新設", "変更", "廃止", "再出現"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows.length ? rows : [
        el("tr", {}, el("td", { colspan: "5" },
          state.summary ? "このマスターのイベントデータはありません" : "データ読み込み中…")),
      ]),
    ]),
  ]);
  if (!state.summary) {
    fetchJSON("./data/summary.json")
      .then((s) => {
        state.summary = s;
        table.replaceWith(summaryTable(meta));
      })
      .catch((err) => table.replaceWith(errorBox(err.message)));
  }
  return table;
}

function legendBlock() {
  return el("div", { class: "legend" }, [
    el("dl", {}, [
      el("dt", {}, "exact"),
      el("dd", {}, "マスターに記載された年月日(変更年月日・廃止年月日)"),
      el("dt", {}, "era_boundary"),
      el("dd", {}, "正確な日付が不明のため、改定施行日で代用"),
      el("dt", {}, "baseline"),
      el("dd", {}, "平成24年度改定時点で収載済(それ以前の履歴は不明)"),
    ]),
  ]);
}

// ---------- コード詳細(タイムライン) ----------

async function renderDetail(master, code) {
  app.replaceChildren(el("p", { class: "loading" }, "読み込み中…"));
  const meta = await ensureMeta();
  const width = meta.shard_len?.[master] || 3;
  const shard = code.slice(0, width);
  const cacheKey = `${master}/${shard}`;
  if (!state.shardCache[cacheKey]) {
    state.shardCache[cacheKey] = await fetchJSON(`./data/history/${master}/${shard}.json`);
  }
  const entry = state.shardCache[cacheKey][code];
  if (!entry) {
    app.replaceChildren(
      backLink(),
      errorBox(`${MASTER_LABELS[master]}マスターにコード ${code} の履歴が見つかりません`)
    );
    return;
  }

  document.title = `${entry.name || code} | レセ電コード変更履歴`;
  const eraLabel = (id) => meta.eras.find((e) => e.id === id)?.label || id || "";

  const lastEvent = entry.events[entry.events.length - 1];
  const isAbolished = lastEvent?.type === "abolished";

  const header = el("div", { class: "detail-header" }, [
    el("div", { class: "code" }, `${MASTER_LABELS[master]}マスター(${master}) ${code}`),
    el("h2", {}, [
      entry.name || "(名称なし)",
      " ",
      isAbolished ? el("span", { class: "badge abolished" }, "廃止済み") : "",
    ]),
  ]);

  const timeline = el(
    "ol",
    { class: "timeline" },
    entry.events.map((ev) => {
      const li = el("li", { class: `ev-${ev.type}` });
      li.append(
        el("span", { class: "ev-date" }, ev.date),
        el("span", { class: `badge type-${ev.type}` }, TYPE_LABELS[ev.type] || ev.type),
        " ",
        el("span", { class: "badge precision" }, PRECISION_LABELS[ev.precision] || ev.precision)
      );
      li.append(eventBody(ev, master, eraLabel));
      return li;
    })
  );

  app.replaceChildren(backLink(), header, timeline, legendBlock());
}

function eventBody(ev, master, eraLabel) {
  const body = el("div", { class: "ev-body" });
  if (ev.type === "baseline") {
    let text = "平成24年度改定時点で収載済(収載日は不明)";
    if (ev.snapshot_fields?.price) {
      text += ` — 当時の${fieldLabel("price", master)}: ${ev.snapshot_fields.price}${PRICE_UNIT[master]}`;
    }
    body.append(text);
    return body;
  }
  if (ev.type === "new") {
    body.append(`新設(${eraLabel(ev.to_era)}の期間中)`);
    return body;
  }
  if (ev.type === "reappeared") {
    body.append(
      `再出現 — 一度マスターから消えたコードが ${eraLabel(ev.to_era)} で再び収載されました。` +
        "コード再利用の可能性があるため注意してください。"
    );
    return body;
  }
  if (ev.type === "abolished") {
    const kubun = ev.change_kubun === "1" ? "抹消(誤登録等による削除)" : "廃止";
    body.append(
      ev.precision === "exact"
        ? `${kubun} — 使用期限 ${ev.date}`
        : `${kubun} — ${eraLabel(ev.to_era)}のマスターに存在しないため、施行日で代用`
    );
    return body;
  }
  // changed: フィールドごとの変更表
  body.append(`${eraLabel(ev.from_era)} → ${eraLabel(ev.to_era)}`);
  const rows = (ev.changes || []).map((c) =>
    el("tr", {}, [
      el("td", {}, fieldLabel(c.field, master)),
      el("td", {}, c.old ?? "(空)"),
      el("td", {}, c.new ?? "(空)"),
    ])
  );
  body.append(
    el("div", { class: "table-wrap" }, [
      el("table", {}, [
        el("thead", {}, el("tr", {}, ["項目", "変更前", "変更後"].map((h) => el("th", {}, h)))),
        el("tbody", {}, rows),
      ]),
    ])
  );
  return body;
}

function backLink() {
  return el("p", { class: "back-link" }, el("a", { href: "#/" }, "← 検索に戻る"));
}

// ---------- 起動 ----------

window.addEventListener("hashchange", route);
ensureMeta()
  .then(route)
  .catch((err) => app.replaceChildren(errorBox(err.message)));
