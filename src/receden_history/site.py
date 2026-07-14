"""GitHub Pages 用静的サイト生成(F6 / docs/SITE_SPEC.md)。

web/(フロントエンド原本)を出力先へコピーし、DB から事前計算済み JSON
(meta / summary / search / history shard)を生成する。出力はすべて UTF-8。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import MASTERS, EraSet, Project, load_eras
from .ingest import connect
from .report import latest_states

JST = timezone(timedelta(hours=9))

# shard 1ファイルの目安サイズ(gzip前)。超えたらコード先頭4桁に深掘りする(SITE_SPEC §2)
SHARD_SIZE_LIMIT = 2_000_000

DISCLAIMER = (
    "本サイトは社会保険診療報酬支払基金「基本マスター」の全件ファイルを加工して作成した"
    "非公式の資料です。正確性は保証されません。実務上の判断は必ず原本"
    "(告示・レセプト電算処理システム マスターファイル仕様説明書・各マスターファイル)で"
    "確認してください。"
)

LIMITATIONS = [
    "同一世代内で複数回変更があった場合、スナップショット差分では最後の状態しか見えないため"
    "1イベントに圧縮されます。",
    "同一世代内で新設から廃止まで完了したコードは検出できません。",
    "平成24年度改定より前の履歴は復元できません(「収載済」= 平成24年度時点で存在)。",
    "日付の precision が era_boundary のイベントは、正確な日付が不明のため改定施行日で代用しています。",
]


def _load_events(conn: sqlite3.Connection, master: str) -> list[dict]:
    rows = conn.execute(
        "SELECT code, event_type, event_date, date_precision, from_era, to_era,"
        " change_kubun, changed_fields FROM events WHERE master=? ORDER BY code, event_date, id",
        (master,),
    ).fetchall()
    out = []
    for code, typ, date, prec, from_era, to_era, kubun, fields in rows:
        out.append(
            {
                "code": code,
                "type": typ,
                "date": date,
                "precision": prec,
                "from_era": from_era,
                "to_era": to_era,
                "change_kubun": kubun,
                "changes": json.loads(fields) if fields else None,
            }
        )
    return out


def _baseline_fields_map(conn: sqlite3.Connection, eras: EraSet, master: str) -> dict[str, dict]:
    """最古世代スナップショットの code → {price, price_type}(baseline イベント用)。"""
    seq = dict(conn.execute("SELECT era, id FROM snapshots WHERE master=?", (master,)).fetchall())
    ordered = [e.id for e in eras if e.id in seq]
    if not ordered:
        return {}
    out = {}
    for code, price, ptype in conn.execute(
        "SELECT code, price, price_type FROM records WHERE snapshot_id=?", (seq[ordered[0]],)
    ):
        fields = {}
        if price not in (None, ""):
            fields["price"] = price
        if ptype not in (None, ""):
            fields["price_type"] = ptype
        if fields:
            out[code] = fields
    return out


def _event_json(ev: dict, baseline_fields: dict[str, dict]) -> dict:
    """イベント1件をサイト用 JSON(SITE_SPEC §3)に変換。"""
    out: dict = {"date": ev["date"], "precision": ev["precision"], "type": ev["type"]}
    if ev["type"] == "baseline":
        fields = baseline_fields.get(ev["code"])
        if fields:
            out["snapshot_fields"] = fields
        return out
    out["from_era"] = ev["from_era"]
    out["to_era"] = ev["to_era"]
    if ev["type"] == "changed":
        out["changes"] = ev["changes"]
    if ev["change_kubun"] in ("1", "9"):
        out["change_kubun"] = ev["change_kubun"]
    return out


def _write_json(path: Path, data) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def export_site(project: Project, out: Path, *, log=print) -> None:
    if not project.web_dir.is_dir():
        raise FileNotFoundError(f"web/ が見つかりません: {project.web_dir}")
    eras = load_eras(project)
    conn = connect(project)
    try:
        out.mkdir(parents=True, exist_ok=True)
        # フロントエンド原本のコピー
        shutil.copytree(project.web_dir, out, dirs_exist_ok=True)

        data_dir = out / "data"
        counts: dict[str, dict[str, int]] = {}
        summary: dict[str, dict[str, dict[str, int]]] = {}
        shard_len: dict[str, int] = {}

        snapshots = conn.execute("SELECT era, master, file_name, row_count FROM snapshots").fetchall()
        files_by_era: dict[str, dict[str, str]] = defaultdict(dict)
        for era_id, master, file_name, _n in snapshots:
            files_by_era[era_id][master] = file_name

        for master in MASTERS:
            events = _load_events(conn, master)
            if not events:
                continue
            states = latest_states(conn, eras, master)
            baseline_fields = _baseline_fields_map(conn, eras, master)

            # search/{M}.json(SITE_SPEC §3: 配列の配列)
            items = [
                [s.code, s.name, s.status, s.abolished_date]
                for s in sorted(states.values(), key=lambda s: s.code)
            ]
            size = _write_json(
                data_dir / "search" / f"{master}.json",
                {"master": master, "columns": ["code", "name", "status", "abolished_date"], "items": items},
            )
            log(f"  search/{master}.json: {len(items)}コード {size / 1024:.0f}KB")

            # summary.json 用集計(baseline を除く世代別件数)
            summary[master] = {}
            for ev in events:
                if ev["type"] == "baseline" or not ev["to_era"]:
                    continue
                era_counts = summary[master].setdefault(
                    ev["to_era"], {"new": 0, "changed": 0, "abolished": 0, "reappeared": 0}
                )
                era_counts[ev["type"]] += 1

            counts[master] = {"codes": len(states), "events": len(events)}

            # history/{M}/{shard}.json(shard = コード先頭N桁。大きすぎる場合は深掘り)
            width = 3
            while True:
                shards: dict[str, dict[str, dict]] = defaultdict(dict)
                for code in sorted({ev["code"] for ev in events}):
                    shards[code[:width]][code] = {
                        "name": states[code].name if code in states else "",
                        "events": [],
                    }
                for ev in events:
                    shards[ev["code"][:width]][ev["code"]]["events"].append(_event_json(ev, baseline_fields))
                sizes = {
                    shard: len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())
                    for shard, data in shards.items()
                }
                if max(sizes.values()) <= SHARD_SIZE_LIMIT or width >= 4:
                    break
                log(
                    f"  history/{master}: shard {max(sizes, key=sizes.get)} が "
                    f"{max(sizes.values()) / 1024:.0f}KB 超過 → 先頭{width + 1}桁に深掘り"
                )
                width += 1
            shard_len[master] = width
            for shard, data in shards.items():
                _write_json(data_dir / "history" / master / f"{shard}.json", data)
            log(
                f"  history/{master}/: {len(shards)}shard(先頭{width}桁, "
                f"最大 {max(sizes.values()) / 1024:.0f}KB)"
            )

        _write_json(data_dir / "summary.json", summary)

        meta = {
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "eras": [
                {
                    "id": e.id,
                    "label": e.label,
                    "effective_date": e.effective_date,
                    "files": files_by_era.get(e.id, {}),
                }
                for e in eras
            ],
            "counts": counts,
            "shard_len": shard_len,
            "disclaimer": DISCLAIMER,
            "limitations": LIMITATIONS,
        }
        size = _write_json(data_dir / "meta.json", meta)
        log(f"  meta.json / summary.json 生成({size / 1024:.1f}KB)")
        log(f"サイトを {out} に生成しました(python -m http.server --directory {out} で確認)")
    finally:
        conn.close()


def run_export_site(project: Project, *, out: Path, log=print) -> None:
    if not out.is_absolute():
        out = project.root / out
    export_site(project, out, log=log)
