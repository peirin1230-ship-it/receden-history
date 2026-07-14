"""世代間スナップショット差分からのイベント生成(F3 / REQUIREMENTS §6)。

イベント種別: baseline / new / changed / abolished / reappeared
日付決定規則は §6.5 に従う(exact / era_boundary / baseline)。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import MASTERS, EraSet, Project, load_eras
from .ingest import connect
from .layouts import MasterLayouts, compare_keys, load_master_layouts

# 同一コード・同一日付のイベントの表示順(挿入順の決定にも使う)
TYPE_ORDER = {"baseline": 0, "new": 1, "reappeared": 1, "changed": 2, "abolished": 3}


@dataclass(frozen=True)
class Event:
    master: str
    code: str
    event_type: str  # baseline/new/changed/abolished/reappeared
    event_date: str  # YYYY-MM-DD
    date_precision: str  # exact/era_boundary/baseline
    from_era: str | None
    to_era: str | None
    change_kubun: str | None
    changed_fields: list[dict] | None  # [{"field":..,"old":..,"new":..}] 変更イベントのみ


@dataclass(frozen=True)
class SnapRecord:
    code: str
    change_kubun: str | None
    changed_at: str | None  # YYYYMMDD or None
    abolished_at: str | None
    transition_at: str | None
    fields: dict  # 比較対象フィールド(個別カラム + extra_json)


def _iso(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def _in_window(yyyymmdd: str, start: str, end: str | None) -> bool:
    """期間窓 [start, end) 判定。end=None は上限なし(最終世代)。8桁文字列の辞書順=日付順。"""
    if len(yyyymmdd) != 8 or not yyyymmdd.isdigit():
        return False
    return yyyymmdd >= start and (end is None or yyyymmdd < end)


def load_snapshot_records(conn: sqlite3.Connection, snapshot_id: int) -> dict[str, SnapRecord]:
    out: dict[str, SnapRecord] = {}
    cur = conn.execute(
        "SELECT code, change_kubun, name, basic_name, price_type, price,"
        " changed_at, abolished_at, transition_at, extra_json"
        " FROM records WHERE snapshot_id=?",
        (snapshot_id,),
    )
    for code, kubun, name, basic, ptype, price, chg, abo, tra, extra in cur:
        fields = {
            "short_name": name,
            "basic_name": basic,
            "price_type": ptype,
            "price": price,
        }
        if extra:
            fields.update(json.loads(extra))
        out[code] = SnapRecord(
            code=code,
            change_kubun=kubun,
            changed_at=chg,
            abolished_at=abo,
            transition_at=tra,
            fields=fields,
        )
    return out


def _snapshot_sequence(
    conn: sqlite3.Connection, master: str, eras: EraSet
) -> list[tuple[str, int, str | None]]:
    """取込済みスナップショットを eras.yaml の順序で返す [(era_id, snapshot_id, ingested_at), ...]。"""
    rows = {
        era: (sid, ing)
        for era, sid, ing in conn.execute(
            "SELECT era, id, ingested_at FROM snapshots WHERE master=?", (master,)
        )
    }
    return [(e.id, rows[e.id][0], rows[e.id][1]) for e in eras if e.id in rows]


def _day_after(yyyymmdd: str) -> str:
    d = datetime.strptime(yyyymmdd, "%Y%m%d") + timedelta(days=1)
    return d.strftime("%Y%m%d")


def _event_date_for_arrival(
    rec: SnapRecord, era_start: str, era_end: str | None, boundary_iso: str
) -> tuple[str, str]:
    """new / changed の日付決定(§6.5): 変更年月日が期間窓内なら exact、それ以外は世代境界。"""
    if rec.changed_at and _in_window(rec.changed_at, era_start, era_end):
        return _iso(rec.changed_at), "exact"
    return boundary_iso, "era_boundary"


def _event_date_for_abolished(rec: SnapRecord, boundary_iso: str) -> tuple[str, str]:
    """abolished の日付決定(§6.5): 廃止年月日(Tは経過措置年月日も考慮)=使用期限。

    両方設定されている場合は遅い方を使用期限とみなす(経過措置は廃止後も一定期間の
    使用を認める制度のため)。どちらも無ければ次世代の施行日で代用(era_boundary)。
    """
    candidates = [d for d in (rec.abolished_at, rec.transition_at) if d]
    if candidates:
        return _iso(max(candidates)), "exact"
    return boundary_iso, "era_boundary"


def _diff_fields(
    cur_rec: SnapRecord, nxt_rec: SnapRecord, keys: frozenset[str], ml: MasterLayouts
) -> list[dict]:
    """比較対象フィールドの差分。tracked_fields を先頭に、その他はキー名順。"""
    ordered = [k for k in ml.tracked_fields if k in keys]
    ordered += sorted(keys - set(ordered))
    diffs = []
    for k in ordered:
        old, new = cur_rec.fields.get(k), nxt_rec.fields.get(k)
        if old != new:
            diffs.append({"field": k, "old": old, "new": new})
    return diffs


def build_events_for_master(
    conn: sqlite3.Connection, project: Project, master: str, eras: EraSet
) -> list[Event]:
    seq = _snapshot_sequence(conn, master, eras)
    if not seq:
        return []
    ml = load_master_layouts(project, master)
    events: list[Event] = []

    first_era_id, first_snap_id, _first_ing = seq[0]
    cur_records = load_snapshot_records(conn, first_snap_id)

    # baseline: 最古世代に存在 → 収載日は不明(REQUIREMENTS §6.4)
    baseline_iso = eras.by_id(first_era_id).effective_date
    for code in sorted(cur_records):
        events.append(
            Event(
                master=master,
                code=code,
                event_type="baseline",
                event_date=baseline_iso,
                date_precision="baseline",
                from_era=None,
                to_era=first_era_id,
                change_kubun=cur_records[code].change_kubun,
                changed_fields=None,
            )
        )

    ever_seen: set[str] = set()
    cur_era_id = first_era_id
    for nxt_era_id, nxt_snap_id, nxt_ingested_at in seq[1:]:
        nxt_records = load_snapshot_records(conn, nxt_snap_id)
        ever_seen |= set(cur_records)

        era_start, era_end = eras.window(nxt_era_id)
        if era_end is None and nxt_ingested_at:
            # 最終世代の期間窓は [施行日, 取込日](§6.1)。取込日より未来の変更年月日
            # (事前告知された改定日)を「窓内」と誤判定しないようにする
            era_end = _day_after(nxt_ingested_at)
        boundary_iso = eras.by_id(nxt_era_id).effective_date
        keys = compare_keys(ml.for_era(cur_era_id), ml.for_era(nxt_era_id))

        cur_codes, nxt_codes = set(cur_records), set(nxt_records)

        # 新設 / 再出現(§6.4)
        for code in sorted(nxt_codes - cur_codes):
            rec = nxt_records[code]
            date, prec = _event_date_for_arrival(rec, era_start, era_end, boundary_iso)
            events.append(
                Event(
                    master=master,
                    code=code,
                    event_type="reappeared" if code in ever_seen else "new",
                    event_date=date,
                    date_precision=prec,
                    from_era=cur_era_id,
                    to_era=nxt_era_id,
                    change_kubun=rec.change_kubun,
                    changed_fields=None,
                )
            )

        # 変更(両世代の layout で共に定義されているフィールドのみ比較 §6.3)
        for code in sorted(cur_codes & nxt_codes):
            diffs = _diff_fields(cur_records[code], nxt_records[code], keys, ml)
            if not diffs:
                continue
            rec = nxt_records[code]
            date, prec = _event_date_for_arrival(rec, era_start, era_end, boundary_iso)
            events.append(
                Event(
                    master=master,
                    code=code,
                    event_type="changed",
                    event_date=date,
                    date_precision=prec,
                    from_era=cur_era_id,
                    to_era=nxt_era_id,
                    change_kubun=rec.change_kubun,
                    changed_fields=diffs,
                )
            )

        # 廃止(次世代のスナップショットから消えたコード)
        for code in sorted(cur_codes - nxt_codes):
            rec = cur_records[code]
            date, prec = _event_date_for_abolished(rec, boundary_iso)
            events.append(
                Event(
                    master=master,
                    code=code,
                    event_type="abolished",
                    event_date=date,
                    date_precision=prec,
                    from_era=cur_era_id,
                    to_era=nxt_era_id,
                    change_kubun=rec.change_kubun,
                    changed_fields=None,
                )
            )

        cur_records = nxt_records
        cur_era_id = nxt_era_id

    return events


def run_build_history(project: Project, *, log=print) -> None:
    """全マスターのイベントを再生成する(冪等: 毎回全削除→再構築)。"""
    eras = load_eras(project)
    conn = connect(project)
    try:
        all_events: list[Event] = []
        for master in MASTERS:
            evs = build_events_for_master(conn, project, master, eras)
            all_events.extend(evs)
            counts: dict[str, int] = {}
            for ev in evs:
                counts[ev.event_type] = counts.get(ev.event_type, 0) + 1
            log(f"  {master}: {len(evs)}イベント {counts}")

        all_events.sort(key=lambda e: (e.master, e.code, e.event_date, TYPE_ORDER.get(e.event_type, 9)))
        with conn:
            conn.execute("DELETE FROM events")
            conn.executemany(
                "INSERT INTO events(master, code, event_type, event_date, date_precision,"
                " from_era, to_era, change_kubun, changed_fields) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    (
                        e.master,
                        e.code,
                        e.event_type,
                        e.event_date,
                        e.date_precision,
                        e.from_era,
                        e.to_era,
                        e.change_kubun,
                        json.dumps(e.changed_fields, ensure_ascii=False) if e.changed_fields else None,
                    )
                    for e in all_events
                ),
            )
        log(f"  合計 {len(all_events)}イベントを events に書き込みました")
    finally:
        conn.close()
