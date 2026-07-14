"""ingest.py: 取込・正規化・冪等性のテスト。"""

import json

import pytest

from receden_history.ingest import (
    IngestError,
    connect,
    normalize_date,
    normalize_price,
    run_ingest,
)
from tests.conftest import c_row, s_row, t_row, write_csv


def test_normalize_date():
    assert normalize_date("0") is None
    assert normalize_date("") is None
    assert normalize_date("00000000") is None
    assert normalize_date("99999999") is None  # 実データの「無期限」表現
    assert normalize_date("20240601") == "20240601"
    assert normalize_date(None) is None


def test_normalize_price():
    assert normalize_price("270.00") == "270"
    assert normalize_price("137.50") == "137.5"
    assert normalize_price("0.00") == "0"
    assert normalize_price("0") == "0"
    assert normalize_price("") == ""
    assert normalize_price("A123") == "A123"  # 数値以外はそのまま


def test_ingest_basic(project3):
    """3世代×3マスターの取込と正規化。"""
    write_csv(
        project3,
        "h24",
        "S",
        [
            s_row("111000110", name="初診", price="270.00", ncols=122),
            s_row("999999999", name="廃止予定", price="10.00", kubun="9", abolished="20140331", ncols=122),
        ],
    )
    write_csv(
        project3,
        "h26",
        "S",
        [
            s_row("111000110", name="初診", price="282.00", changed="20140401", ncols=122),
        ],
    )
    write_csv(
        project3,
        "r06",
        "S",
        [
            s_row("111000110", name="初診料", price="291.00", tensuhyo="A000", ncols=150),
        ],
    )
    write_csv(
        project3,
        "h24",
        "T",
        [
            t_row("700010000", name="半切", price="137.00", ncols=37),
        ],
    )
    write_csv(
        project3,
        "h26",
        "T",
        [
            t_row("700010000", name="半切", price="139.00", ncols=37),
        ],
    )
    write_csv(
        project3,
        "r06",
        "T",
        [
            t_row("700010000", name="半切", price="120.00", ncols=38),
        ],
    )
    # C: h24/h26 は19列(コード導出)、r06 は30列(コード列あり)
    write_csv(
        project3,
        "h24",
        "C",
        [
            c_row("10", "1", text="①テスト㈱", ncols=19),
        ],
    )
    write_csv(
        project3,
        "h26",
        "C",
        [
            c_row("10", "1", text="①テスト㈱", ncols=19),
        ],
    )
    write_csv(
        project3,
        "r06",
        "C",
        [
            c_row(
                "10",
                "1",
                text="①テスト㈱",
                code="810000001",
                changed="20240601",
                abolished="99999999",
                ncols=30,
            ),
        ],
    )

    run_ingest(project3, log=lambda *a: None)
    conn = connect(project3)
    try:
        snaps = conn.execute(
            "SELECT era, master, row_count, column_count FROM snapshots ORDER BY era, master"
        ).fetchall()
        assert len(snaps) == 9

        # 価格の正規化("270.00" → "270")
        price = conn.execute(
            "SELECT r.price FROM records r JOIN snapshots s ON s.id=r.snapshot_id"
            " WHERE s.era='h24' AND s.master='S' AND r.code='111000110'"
        ).fetchone()[0]
        assert price == "270"

        # 日付の正規化(99999999 → NULL、実日付は保持)
        row = conn.execute(
            "SELECT r.abolished_at, r.change_kubun FROM records r JOIN snapshots s"
            " ON s.id=r.snapshot_id WHERE s.era='h24' AND s.master='S' AND r.code='999999999'"
        ).fetchone()
        assert row == ("20140331", "9")
        row = conn.execute(
            "SELECT r.abolished_at FROM records r JOIN snapshots s ON s.id=r.snapshot_id"
            " WHERE s.era='h26' AND s.master='S' AND r.code='111000110'"
        ).fetchone()
        assert row[0] is None

        # C のコード導出(19列)と cp932 拡張文字(①㈱)
        row = conn.execute(
            "SELECT r.code, r.name FROM records r JOIN snapshots s ON s.id=r.snapshot_id"
            " WHERE s.era='h24' AND s.master='C'"
        ).fetchone()
        assert row == ("810000001", "①テスト㈱")

        # C 30列はコード列を使う
        row = conn.execute(
            "SELECT r.code FROM records r JOIN snapshots s ON s.id=r.snapshot_id"
            " WHERE s.era='r06' AND s.master='C'"
        ).fetchone()
        assert row[0] == "810000001"

        # extra_json に tensuhyo_kubun(v150のみ)が入る
        extra = conn.execute(
            "SELECT r.extra_json FROM records r JOIN snapshots s ON s.id=r.snapshot_id"
            " WHERE s.era='r06' AND s.master='S' AND r.code='111000110'"
        ).fetchone()[0]
        assert json.loads(extra)["tensuhyo_kubun"] == "A000"

        # raw 原文が保持される
        raw = conn.execute(
            "SELECT r.raw FROM records r JOIN snapshots s ON s.id=r.snapshot_id"
            " WHERE s.era='h24' AND s.master='S' AND r.code='111000110'"
        ).fetchone()[0]
        assert raw.startswith("0,S,111000110,")
    finally:
        conn.close()


def test_ingest_idempotent(project3):
    """同一ファイルの再取込はスキップされ、結果が変わらない(F2)。"""
    write_csv(project3, "h24", "S", [s_row("111000110", price="270.00", ncols=122)])
    run_ingest(project3, log=lambda *a: None)
    conn = connect(project3)
    before = conn.execute("SELECT id, file_sha256 FROM snapshots").fetchall()
    conn.close()

    run_ingest(project3, log=lambda *a: None)
    conn = connect(project3)
    after = conn.execute("SELECT id, file_sha256 FROM snapshots").fetchall()
    n_records = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    conn.close()
    assert before == after  # snapshot id も sha も不変
    assert n_records == 1


def test_ingest_force_replaces(project3):
    write_csv(project3, "h24", "S", [s_row("111000110", price="270.00", ncols=122)])
    run_ingest(project3, log=lambda *a: None)
    # 同じ era/master のファイルを書き換えたら取り込み直される
    write_csv(project3, "h24", "S", [s_row("111000110", price="271.00", ncols=122)])
    run_ingest(project3, log=lambda *a: None)
    conn = connect(project3)
    price = conn.execute("SELECT price FROM records").fetchone()[0]
    n = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    conn.close()
    assert price == "271"
    assert n == 1


def test_ingest_stops_on_undecodable_file(project3):
    """cp932 でデコードできないCSVを黙って無視せず、取込を停止する。"""
    write_csv(project3, "h24", "S", [s_row("111000110", ncols=122)])
    # UTF-16(BOM付き)は cp932 として1行目からデコード不能
    (project3.raw_dir / "h24" / "s_utf16.csv").write_bytes("0,S,999999999,2,初診\r\n".encode("utf-16"))
    with pytest.raises(IngestError, match="デコード"):
        run_ingest(project3, log=lambda *a: None)


def test_ingest_duplicate_code_stops(project3):
    """主キー重複は後勝ちにせず停止(REQUIREMENTS §12-9)。"""
    write_csv(
        project3,
        "h24",
        "S",
        [
            s_row("111000110", price="270.00", ncols=122),
            s_row("111000110", price="999.00", ncols=122),
        ],
    )
    with pytest.raises(IngestError, match="重複"):
        run_ingest(project3, log=lambda *a: None)
