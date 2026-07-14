"""CSV(data/raw/{era}/*.csv)→ SQLite(snapshots / records)取込。

- 文字コードは cp932 固定(CLAUDE.md 鉄則1)
- ファイル名に依存せず2列目のマスター種別で自動判定(REQUIREMENTS §4)
- ファイル sha256 を記録し、同一ファイルの再取込はスキップ(冪等・F2)
- 主キー重複は後勝ちにせず停止(REQUIREMENTS §12-9)
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from .config import MASTERS, EraSet, Project, load_eras
from .layouts import Layout, MasterLayouts, derive_comment_code, load_master_layouts

ENCODING = "cp932"

# 日付の「未設定」表現。仕様書上は 0 だが、実データでは 99999999(無期限)・
# 00000000 も使われている(実測プローブで確認。docs/FILE_LAYOUTS.md §5)
UNSET_DATE_VALUES = frozenset({"", "0", "00000000", "99999999"})

_DATE8 = re.compile(r"^\d{8}$")
_NUMERIC = re.compile(r"^\d+\.\d+$")

# records テーブルの個別カラムに格納するキー。それ以外のマップ済みキーは extra_json へ
RECORD_COLUMN_KEYS = frozenset(
    {
        "code",
        "change_kubun",
        "short_name",
        "basic_name",
        "price_type",
        "price",
        "changed_at",
        "abolished_at",
        "transition_at",
    }
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots(
  id           INTEGER PRIMARY KEY,
  era          TEXT NOT NULL,
  master       TEXT NOT NULL,
  file_name    TEXT NOT NULL,
  file_sha256  TEXT NOT NULL,
  column_count INTEGER,
  row_count    INTEGER,
  ingested_at  TEXT,           -- 取込日 YYYYMMDD(最終世代の期間窓の上限 §6.1)
  UNIQUE(era, master)
);

CREATE TABLE IF NOT EXISTS records(
  snapshot_id  INTEGER NOT NULL REFERENCES snapshots(id),
  code         TEXT NOT NULL,
  change_kubun TEXT,
  name         TEXT,
  basic_name   TEXT,
  price_type   TEXT,
  price        TEXT,
  changed_at   TEXT,
  abolished_at TEXT,
  transition_at TEXT,
  extra_json   TEXT,
  raw          TEXT NOT NULL,
  PRIMARY KEY(snapshot_id, code)
);

CREATE INDEX IF NOT EXISTS idx_records_code ON records(code);

CREATE TABLE IF NOT EXISTS events(
  id             INTEGER PRIMARY KEY,
  master         TEXT NOT NULL,
  code           TEXT NOT NULL,
  event_type     TEXT NOT NULL,
  event_date     TEXT NOT NULL,
  date_precision TEXT NOT NULL,
  from_era       TEXT,
  to_era         TEXT,
  change_kubun   TEXT,
  changed_fields TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_code ON events(master, code);
"""


class IngestError(Exception):
    pass


def connect(project: Project) -> sqlite3.Connection:
    project.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(project.db_path)
    conn.executescript(SCHEMA)
    # 旧スキーマのDBへの後方互換(列がなければ追加)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)")}
    if "ingested_at" not in cols:
        conn.execute("ALTER TABLE snapshots ADD COLUMN ingested_at TEXT")
    return conn


def normalize_date(value: str | None) -> str | None:
    """日付列の正規化: 未設定表現→None、8桁日付はそのまま保持。"""
    if value is None:
        return None
    v = value.strip()
    if v in UNSET_DATE_VALUES:
        return None
    return v  # 8桁以外の異常値も保持する(validate で報告)


def normalize_price(value: str | None) -> str | None:
    """点数・金額の正規化: "270.00" → "270"、"137.50" → "137.5"。

    世代によって小数表記が揺れても差分の誤検知にならないようにする。
    数値でない値はそのまま保持。
    """
    if value is None:
        return None
    v = value.strip()
    if _NUMERIC.match(v):
        v = v.rstrip("0").rstrip(".")
        return v or "0"
    return v


def iter_rows_with_raw(f):
    """csv.reader で行をパースしつつ、元テキスト行(raw)も返す。

    引用符内改行などで1レコードが複数行にまたがっても raw が欠けないよう、
    reader が消費した行をそのまま連結する。
    """
    consumed: list[str] = []

    def tee():
        for line in f:
            consumed.append(line)
            yield line

    reader = csv.reader(tee())
    for row in reader:
        raw = "".join(consumed).rstrip("\r\n")
        consumed.clear()
        yield row, raw


def detect_master(path: Path) -> str | None:
    """先頭行の2列目でマスター種別を判定。対象外(S/T/C以外)は None。

    先頭行が cp932 でデコードできない場合は UnicodeDecodeError を送出する
    (握りつぶすと誤エンコーディングのファイルが黙って無視されてしまうため)。
    """
    with open(path, encoding=ENCODING, newline="") as f:
        row = next(csv.reader(f), None)
    if row and len(row) >= 2 and row[1] in MASTERS:
        return row[1]
    return None


def scan_era_files(project: Project, era_id: str) -> tuple[dict[str, Path], list[tuple[Path, str]]]:
    """世代ディレクトリの CSV をマスター種別ごとに対応付ける。

    戻り値: (マスター → ファイル, 問題のあるファイルのリスト[(path, 理由)])。
    S/T/C 以外のマスター(医薬品等)のCSVは対象外として黙って無視する。
    """
    era_dir = project.raw_dir / era_id
    found: dict[str, Path] = {}
    problems: list[tuple[Path, str]] = []
    if not era_dir.is_dir():
        return found, problems
    for path in sorted(era_dir.iterdir()):
        if path.suffix.lower() != ".csv" or not path.is_file():
            continue
        try:
            master = detect_master(path)
        except UnicodeDecodeError as e:
            problems.append((path, f"先頭行が cp932 でデコードできません(エンコーディング違いの疑い): {e}"))
            continue
        if master is None:
            continue
        if master in found:
            problems.append(
                (path, f"マスター {master} のファイルが複数あります({found[master].name} を先に検出)")
            )
            continue
        found[master] = path
    return found, problems


def find_era_files(project: Project, era_id: str) -> dict[str, Path]:
    """scan_era_files の厳格版(取込用)。問題のあるファイルがあれば停止する。"""
    found, problems = scan_era_files(project, era_id)
    if problems:
        details = "; ".join(f"{p.name}: {reason}" for p, reason in problems)
        raise IngestError(f"data/raw/{era_id}: {details}")
    return found


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_record(master: str, layout: Layout, ml: MasterLayouts, row: list[str], raw: str) -> dict:
    """CSV1行を records 相当の辞書に正規化する(REQUIREMENTS §5 F2)。"""
    fields = layout.map_row(row)

    # コード: S/T は code 列そのまま。C は code 列があればそれ、なければ導出
    code = (fields.get("code") or "").strip()
    if not code and ml.has_code_derivation:
        code = derive_comment_code(fields.get("pattern", ""), fields.get("serial", ""))
    if not code:
        raise IngestError(f"コードを決定できない行: {raw[:80]}")

    extra = {k: v for k, v in fields.items() if k not in RECORD_COLUMN_KEYS and k != "master_kind"}
    return {
        "code": code,
        "change_kubun": fields.get("change_kubun"),
        "name": fields.get("short_name"),
        "basic_name": fields.get("basic_name"),
        "price_type": fields.get("price_type"),
        "price": normalize_price(fields.get("price")),
        "changed_at": normalize_date(fields.get("changed_at")),
        "abolished_at": normalize_date(fields.get("abolished_at")),
        "transition_at": normalize_date(fields.get("transition_at")),
        "extra_json": json.dumps(extra, ensure_ascii=False, sort_keys=True) if extra else None,
        "raw": raw,
    }


def ingest_file(
    conn: sqlite3.Connection,
    project: Project,
    era_id: str,
    master: str,
    path: Path,
    *,
    force: bool = False,
    log=print,
) -> bool:
    """1ファイルを取り込む。取り込んだら True、スキップなら False。"""
    sha = file_sha256(path)
    cur = conn.execute("SELECT id, file_sha256 FROM snapshots WHERE era=? AND master=?", (era_id, master))
    existing = cur.fetchone()
    if existing and existing[1] == sha and not force:
        log(f"  {era_id}/{master}: 変更なし(sha256一致)。スキップ")
        return False

    ml = load_master_layouts(project, master)
    layout = ml.for_era(era_id)

    records: dict[str, dict] = {}
    col_counts: Counter[int] = Counter()
    dup: list[str] = []
    with open(path, encoding=ENCODING, newline="") as f:
        for row, raw in iter_rows_with_raw(f):
            if not row:
                continue
            col_counts[len(row)] += 1
            rec = normalize_record(master, layout, ml, row, raw)
            if rec["code"] in records:
                dup.append(rec["code"])
            else:
                records[rec["code"]] = rec
    if dup:
        # 後勝ちにせず停止(REQUIREMENTS §12-9)
        raise IngestError(f"{path}: 主キー重複 {len(dup)}件(例: {dup[:5]})。取込を中止します")
    if not records:
        raise IngestError(f"{path}: レコードが0件です")

    mode_cols = col_counts.most_common(1)[0][0]
    if mode_cols != layout.total_columns:
        log(
            f"  警告: {era_id}/{master}: 実測列数 {mode_cols} が layout({layout.id}) の "
            f"{layout.total_columns} と不一致(validate で確認してください)"
        )

    with conn:
        if existing:
            conn.execute("DELETE FROM records WHERE snapshot_id=?", (existing[0],))
            conn.execute("DELETE FROM snapshots WHERE id=?", (existing[0],))
        cur = conn.execute(
            "INSERT INTO snapshots(era, master, file_name, file_sha256, column_count, row_count,"
            " ingested_at) VALUES (?,?,?,?,?,?,?)",
            (era_id, master, path.name, sha, mode_cols, len(records), date.today().strftime("%Y%m%d")),
        )
        snapshot_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO records(snapshot_id, code, change_kubun, name, basic_name, price_type,"
            " price, changed_at, abolished_at, transition_at, extra_json, raw)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    snapshot_id,
                    r["code"],
                    r["change_kubun"],
                    r["name"],
                    r["basic_name"],
                    r["price_type"],
                    r["price"],
                    r["changed_at"],
                    r["abolished_at"],
                    r["transition_at"],
                    r["extra_json"],
                    r["raw"],
                )
                for r in records.values()
            ),
        )
    log(f"  {era_id}/{master}: {len(records)}件取込({path.name}, {mode_cols}列)")
    return True


def run_ingest(project: Project, *, era: str | None = None, force: bool = False, log=print) -> None:
    """eras.yaml の順序で各世代のCSVを取り込む(F2)。"""
    eras: EraSet = load_eras(project)
    conn = connect(project)
    try:
        for e in eras:
            if era and e.id != era:
                continue
            files = find_era_files(project, e.id)
            if not files:
                log(f"  {e.id}: CSVなし(data/raw/{e.id}/ にファイルを配置してください)")
                continue
            for master in MASTERS:
                if master in files:
                    ingest_file(conn, project, e.id, master, files[master], force=force, log=log)
        # 取込済み世代×マスターの行数レポート(M1完了条件)
        log("\n== 取込状況(世代×マスター 行数) ==")
        rows = conn.execute(
            "SELECT era, master, row_count, column_count, file_name FROM snapshots"
        ).fetchall()
        by_key = {(r[0], r[1]): r for r in rows}
        header = "era     " + "".join(f"{m:>10}" for m in MASTERS)
        log(header)
        for e in eras:
            cells = []
            for m in MASTERS:
                r = by_key.get((e.id, m))
                cells.append(f"{r[2]:>10}" if r else f"{'-':>10}")
            log(f"{e.id:<8}" + "".join(cells))
    finally:
        conn.close()


if __name__ == "__main__":  # 手元デバッグ用
    run_ingest(Project(root=Path.cwd()), log=lambda *a: print(*a, file=sys.stderr))
