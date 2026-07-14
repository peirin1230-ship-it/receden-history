"""report.py: show / search / latest_states のテスト。"""

import json

from receden_history.config import load_eras
from receden_history.diff import run_build_history
from receden_history.ingest import connect, run_ingest
from receden_history.report import latest_states, run_search, run_show
from tests.conftest import s_row, write_csv


def _setup(project):
    """初診料モデル: 270(h24) → 282(h26) → 291(r06)+名称変更。廃止コード1つ。"""
    write_csv(
        project,
        "h24",
        "S",
        [
            s_row("111000110", name="初診", price="270.00", changed="20120401", ncols=122),
            s_row("300000010", name="旧行為", abolished="20140331", kubun="9", ncols=122),
        ],
    )
    write_csv(
        project,
        "h26",
        "S",
        [
            s_row("111000110", name="初診", price="282.00", changed="20140401", ncols=122),
        ],
    )
    write_csv(
        project,
        "r06",
        "S",
        [
            s_row("111000110", name="初診料", price="291.00", changed="20240601", ncols=150),
        ],
    )
    run_ingest(project, log=lambda *a: None)
    run_build_history(project, log=lambda *a: None)


def test_show_text_timeline(project3):
    _setup(project3)
    lines = []
    rc = run_show(project3, "S", "111000110", log=lines.append)
    assert rc == 0
    out = "\n".join(lines)
    assert lines[0] == "S 111000110 初診料"
    assert "2012-04-01 [baseline" in out and "時点で収載済 (270点)" in out
    assert "2014-04-01 [exact" in out and "点数 270 → 282" in out
    assert "2024-06-01 [exact" in out and "291" in out
    assert "名称 初診 → 初診料" in out


def test_show_abolished_code(project3):
    _setup(project3)
    lines = []
    rc = run_show(project3, "S", "300000010", log=lines.append)
    assert rc == 0
    out = "\n".join(lines)
    assert "(廃止済み)" in lines[0]
    assert "廃止(使用期限 2014-03-31)" in out


def test_show_unknown_code(project3):
    _setup(project3)
    lines = []
    rc = run_show(project3, "S", "999999999", log=lines.append)
    assert rc == 1


def test_show_json(project3):
    _setup(project3)
    lines = []
    rc = run_show(project3, "S", "111000110", fmt="json", log=lines.append)
    assert rc == 0
    data = json.loads("\n".join(lines))
    assert data["name"] == "初診料"
    assert [e["type"] for e in data["events"]] == ["baseline", "changed", "changed"]


def test_latest_states(project3):
    _setup(project3)
    conn = connect(project3)
    try:
        states = latest_states(conn, load_eras(project3), "S")
        assert states["111000110"].status == "active"
        assert states["111000110"].name == "初診料"
        assert states["111000110"].abolished_date is None
        assert states["300000010"].status == "abolished"
        assert states["300000010"].name == "旧行為"  # 最終在籍世代の名称
        assert states["300000010"].abolished_date == "2014-03-31"
    finally:
        conn.close()


def test_search(project3):
    _setup(project3)
    lines = []
    rc = run_search(project3, "初診", log=lines.append)
    assert rc == 0
    assert any("111000110" in line for line in lines)
    # 廃止コードにはバッジ
    lines = []
    run_search(project3, "旧行為", log=lines.append)
    assert any("[廃止]" in line for line in lines)
    # コード前方一致
    lines = []
    rc = run_search(project3, "1110001", log=lines.append)
    assert rc == 0 and any("111000110" in line for line in lines)
