"""show / search / export(F4・F5)。検索用の最新状態インデックスは site.py と共用する。"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import MASTERS, EraSet, Project, load_eras
from .ingest import connect

# 表示用のフィールド名(キー → 日本語ラベル。マスター別の揺れは辞書で表現)
FIELD_LABELS: dict[str, str | dict[str, str]] = {
    "short_name": "名称",
    "basic_name": "基本名称",
    "short_kana": "カナ名称",
    "price": {"S": "点数", "T": "金額", "C": "点数", "Y": "金額(薬価)"},
    "price_type": {"S": "点数識別", "T": "金額種別", "C": "点数識別", "Y": "金額種別"},
    "unit_code": "単位コード",
    "unit_name": "単位名称",
    "max_points": "上限点数",
    "succession_code": "廃止・新設関連",
    "sentakushiki": "選択式コメント識別",
    "tensuhyo_kubun": "点数表区分番号",
    "remanufactured": "再製造単回使用医療機器",
    "narcotic_kubun": "麻薬・毒薬・覚醒剤原料・向精神薬",
    "biologic": "生物学的製剤",
    "generic": "後発品",
    "dosage_form": "剤形",
    "yj_code": "薬価基準収載医薬品コード",
    "generic_name_code": "一般名コード",
    "generic_name": "一般名処方の標準的な記載",
}

PRICE_UNIT = {"S": "点", "T": "円", "C": "", "Y": "円"}

PRECISION_LEGEND = {
    "exact": "マスター記載の年月日",
    "era_boundary": "改定施行日で代用",
    "baseline": "平成24年度時点で収載済(それ以前は不明)",
}


def field_label(key: str, master: str) -> str:
    label = FIELD_LABELS.get(key, key)
    if isinstance(label, dict):
        return label.get(master, key)
    return label


@dataclass(frozen=True)
class CodeState:
    """あるコードの最新状態(検索インデックスの1行)。"""

    code: str
    name: str
    status: str  # active / abolished
    abolished_date: str | None  # 廃止日(ISO)。active でも廃止予定日があればその日付
    last_era: str


def latest_states(conn: sqlite3.Connection, eras: EraSet, master: str) -> dict[str, CodeState]:
    """全コードの最新状態を返す(検索・サイト出力で共用)。

    - name: 最後に在籍した世代の省略名称(廃止済みは最終在籍世代のもの)
    - status: 最新世代のスナップショットに存在すれば active、なければ abolished
    - abolished_date: abolished イベントの日付。active でも廃止予定日(廃止年月日・
      経過措置年月日)がマスターに記載されていればその日付を入れる(変更区分9=廃止予定の扱い、§6.4)
    """
    eras = eras.for_master(master)
    seq = conn.execute("SELECT era, id FROM snapshots WHERE master=?", (master,)).fetchall()
    by_era = dict(seq)
    ordered = [(e.id, by_era[e.id]) for e in eras if e.id in by_era]
    if not ordered:
        return {}

    latest: dict[str, tuple[str, str, str | None]] = {}  # code -> (name, era, abolished_at)
    for era_id, snap_id in ordered:
        for code, name, abo, tra in conn.execute(
            "SELECT code, name, abolished_at, transition_at FROM records WHERE snapshot_id=?",
            (snap_id,),
        ):
            dates = [d for d in (abo, tra) if d]
            pending = max(dates) if dates else None
            latest[code] = (name or "", era_id, pending)

    active_codes = {
        row[0] for row in conn.execute("SELECT code FROM records WHERE snapshot_id=?", (ordered[-1][1],))
    }

    abolished_dates = dict(
        conn.execute(
            "SELECT code, MAX(event_date) FROM events"
            " WHERE master=? AND event_type='abolished' GROUP BY code",
            (master,),
        ).fetchall()
    )

    out: dict[str, CodeState] = {}
    for code, (name, era_id, pending) in latest.items():
        if code in active_codes:
            status = "active"
            abolished_date = f"{pending[:4]}-{pending[4:6]}-{pending[6:]}" if pending else None
        else:
            status = "abolished"
            abolished_date = abolished_dates.get(code)
        out[code] = CodeState(
            code=code, name=name, status=status, abolished_date=abolished_date, last_era=era_id
        )
    return out


def _fetch_events(conn: sqlite3.Connection, master: str, code: str) -> list[dict]:
    rows = conn.execute(
        "SELECT event_type, event_date, date_precision, from_era, to_era, change_kubun,"
        " changed_fields FROM events WHERE master=? AND code=? ORDER BY event_date, id",
        (master, code),
    ).fetchall()
    events = []
    for typ, date, prec, from_era, to_era, kubun, fields in rows:
        events.append(
            {
                "type": typ,
                "date": date,
                "precision": prec,
                "from_era": from_era,
                "to_era": to_era,
                "change_kubun": kubun,
                "changes": json.loads(fields) if fields else None,
            }
        )
    return events


def _baseline_snapshot_fields(conn: sqlite3.Connection, eras: EraSet, master: str, code: str) -> dict | None:
    """baseline イベントに添える、最古世代時点の主要フィールド(SITE_SPEC §3)。"""
    seq = dict(conn.execute("SELECT era, id FROM snapshots WHERE master=?", (master,)).fetchall())
    ordered = [e.id for e in eras.for_master(master) if e.id in seq]
    if not ordered:
        return None
    row = conn.execute(
        "SELECT price, price_type FROM records WHERE snapshot_id=? AND code=?",
        (seq[ordered[0]], code),
    ).fetchone()
    if row is None:
        return None
    fields = {}
    if row[0] not in (None, ""):
        fields["price"] = row[0]
    if row[1] not in (None, ""):
        fields["price_type"] = row[1]
    return fields or None


def format_event_detail(ev: dict, master: str, eras: EraSet, baseline_fields: dict | None) -> str:
    """イベント1件のテキスト説明(show 用)。"""
    typ = ev["type"]
    if typ == "baseline":
        era = eras.by_id(ev["to_era"])
        text = f"{era.label}時点で収載済"
        if baseline_fields and baseline_fields.get("price"):
            text += f" ({baseline_fields['price']}{PRICE_UNIT.get(master, '')})"
        return text
    if typ in ("new", "reappeared"):
        text = "新設"
        if typ == "reappeared":
            text = "再出現(警告: コード再利用の可能性。過去に消えたコードが再び現れました)"
        return text
    if typ == "changed":
        parts = [
            f"{field_label(c['field'], master)} {c['old'] if c['old'] not in (None, '') else '(空)'}"
            f" → {c['new'] if c['new'] not in (None, '') else '(空)'}"
            for c in ev["changes"] or []
        ]
        return "変更: " + "、".join(parts)
    if typ == "abolished":
        if ev.get("change_kubun") == "1":
            base = "抹消(誤登録等による削除)"
        else:
            base = "廃止"
        if ev["precision"] == "exact":
            base += f"(使用期限 {ev['date']})"
        return base
    return typ


def run_show(project: Project, master: str, code: str, *, fmt: str = "text", log=print) -> int:
    eras = load_eras(project)
    conn = connect(project)
    try:
        events = _fetch_events(conn, master, code)
        if not events:
            log(f"{master} {code}: イベントが見つかりません(build-history 実行済みか確認してください)")
            return 1
        states = latest_states(conn, eras, master)
        state = states.get(code)
        name = state.name if state else ""
        baseline_fields = _baseline_snapshot_fields(conn, eras, master, code)

        if fmt == "json":
            log(
                json.dumps(
                    {"master": master, "code": code, "name": name, "events": events},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        header = f"{master} {code} {name}".rstrip()
        if state and state.status == "abolished":
            header += "(廃止済み)"
        log(header)
        for ev in events:
            detail = format_event_detail(ev, master, eras, baseline_fields)
            log(f"  {ev['date']} [{ev['precision']:<12}] {detail}")
        return 0
    finally:
        conn.close()


def run_search(project: Project, keyword: str, *, master: str | None = None, log=print) -> int:
    eras = load_eras(project)
    conn = connect(project)
    try:
        hits = 0
        for m in MASTERS:
            if master and m != master:
                continue
            for state in sorted(latest_states(conn, eras, m).values(), key=lambda s: s.code):
                if keyword in state.name or state.code.startswith(keyword):
                    badge = "" if state.status == "active" else " [廃止]"
                    log(f"{m} {state.code} {state.name}{badge}")
                    hits += 1
        if hits == 0:
            log(f"「{keyword}」に一致するコードは見つかりませんでした")
            return 1
        return 0
    finally:
        conn.close()


def run_export(project: Project, *, out: Path | None = None, log=print) -> None:
    """exports/events.csv と exports/summary.md を出力する(F5)。"""
    eras = load_eras(project)
    out_dir = out or project.exports_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(project)
    try:
        rows = conn.execute(
            "SELECT master, code, event_type, event_date, date_precision, from_era, to_era,"
            " change_kubun, changed_fields FROM events ORDER BY master, code, event_date, id"
        ).fetchall()

        csv_path = out_dir / "events.csv"
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "master",
                    "code",
                    "event_type",
                    "event_date",
                    "date_precision",
                    "from_era",
                    "to_era",
                    "change_kubun",
                    "changed_fields",
                ]
            )
            w.writerows(rows)
        log(f"  {csv_path}: {len(rows)}行")

        # 世代別サマリ(支払基金「改定分内容」PDFとの目視突合用)
        counts: dict[tuple[str, str, str], int] = {}
        for master_, _code, typ, *_rest in rows:
            to_era = _rest[3]
            if typ == "baseline" or to_era is None:
                continue
            counts[(master_, to_era, typ)] = counts.get((master_, to_era, typ), 0) + 1

        lines = ["# 世代別イベント件数サマリ", ""]
        for m in MASTERS:
            lines += [
                f"## マスター {m}",
                "",
                "| 世代 | 新設 | 変更 | 廃止 | 再出現 |",
                "|---|---|---|---|---|",
            ]
            for e in eras:
                row = [counts.get((m, e.id, t), 0) for t in ("new", "changed", "abolished", "reappeared")]
                if any(row):
                    lines.append(f"| {e.id}({e.label}) | {row[0]} | {row[1]} | {row[2]} | {row[3]} |")
            lines.append("")
        md_path = out_dir / "summary.md"
        md_path.write_text("\n".join(lines), encoding="utf-8")
        log(f"  {md_path}")
    finally:
        conn.close()
