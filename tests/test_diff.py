"""diff.py: イベント生成(baseline/new/changed/abolished/reappeared)と日付決定規則のテスト。

fixture 世代: h24(2012-04-01) → h26(2014-04-01) → r06(2024-06-01)
  h26 の期間窓 = [20140401, 20240601)、r06 の期間窓 = [20240601, ∞)
"""

import json

from receden_history.config import load_eras
from receden_history.diff import build_events_for_master, run_build_history
from receden_history.ingest import connect, run_ingest
from tests.conftest import c_row, make_project, s_row, t_row, write_csv, y_row


def _build(project):
    run_ingest(project, log=lambda *a: None)
    run_build_history(project, log=lambda *a: None)
    return connect(project)


def _events(conn, master, code):
    rows = conn.execute(
        "SELECT event_type, event_date, date_precision, from_era, to_era, change_kubun,"
        " changed_fields FROM events WHERE master=? AND code=? ORDER BY event_date, id",
        (master, code),
    ).fetchall()
    return [
        {
            "type": r[0],
            "date": r[1],
            "precision": r[2],
            "from_era": r[3],
            "to_era": r[4],
            "kubun": r[5],
            "changes": json.loads(r[6]) if r[6] else None,
        }
        for r in rows
    ]


def test_baseline_and_changed_exact_vs_boundary(project3):
    """変更年月日が期間窓内なら exact、窓外(古い日付の持ち越し)なら era_boundary。"""
    write_csv(
        project3,
        "h24",
        "S",
        [
            s_row("111000110", name="初診", price="270.00", changed="20120401", ncols=122),
            s_row("222000220", name="再診", price="69.00", changed="20120401", ncols=122),
        ],
    )
    write_csv(
        project3,
        "h26",
        "S",
        [
            # 窓内の変更年月日 → exact
            s_row("111000110", name="初診", price="282.00", changed="20140401", ncols=122),
            # 変更年月日が古いまま(価格だけ変化)→ era_boundary
            s_row("222000220", name="再診", price="72.00", changed="20120401", ncols=122),
        ],
    )
    write_csv(
        project3,
        "r06",
        "S",
        [
            s_row("111000110", name="初診料", price="291.00", changed="20240601", ncols=150),
            s_row("222000220", name="再診", price="72.00", changed="20120401", ncols=150),
        ],
    )
    conn = _build(project3)
    try:
        evs = _events(conn, "S", "111000110")
        assert [e["type"] for e in evs] == ["baseline", "changed", "changed"]
        assert evs[0] == {
            "type": "baseline",
            "date": "2012-04-01",
            "precision": "baseline",
            "from_era": None,
            "to_era": "h24",
            "kubun": "0",
            "changes": None,
        }
        assert evs[1]["date"] == "2014-04-01" and evs[1]["precision"] == "exact"
        assert {"field": "price", "old": "270", "new": "282"} in evs[1]["changes"]
        # h26→r06: 名称と点数の両方が変わる
        assert evs[2]["precision"] == "exact"
        fields = {c["field"] for c in evs[2]["changes"]}
        assert {"short_name", "price", "basic_name"} <= fields

        evs2 = _events(conn, "S", "222000220")
        assert evs2[1]["precision"] == "era_boundary"
        assert evs2[1]["date"] == "2014-04-01"  # 世代境界=施行日で代用
        assert len(evs2) == 2  # r06 では変化なし → イベントなし
    finally:
        conn.close()


def test_new_and_abolished(project3):
    write_csv(
        project3,
        "h24",
        "S",
        [
            s_row("111000110", ncols=122),
            s_row("300000010", name="消える行為", abolished="20140331", kubun="9", ncols=122),
            s_row("300000020", name="黙って消える", ncols=122),
        ],
    )
    write_csv(
        project3,
        "h26",
        "S",
        [
            s_row("111000110", ncols=122),
            s_row("400000010", name="新設行為", changed="20150101", ncols=122),
        ],
    )
    write_csv(
        project3,
        "r06",
        "S",
        [
            s_row("111000110", ncols=122 + 28),
            s_row("400000010", name="新設行為", changed="20150101", ncols=150),
        ],
    )
    conn = _build(project3)
    try:
        # 廃止年月日あり → exact(使用期限)
        evs = _events(conn, "S", "300000010")
        assert evs[-1]["type"] == "abolished"
        assert evs[-1]["date"] == "2014-03-31" and evs[-1]["precision"] == "exact"
        assert evs[-1]["kubun"] == "9"
        assert evs[-1]["from_era"] == "h24" and evs[-1]["to_era"] == "h26"

        # 廃止年月日なし → era_boundary(次世代の施行日)
        evs = _events(conn, "S", "300000020")
        assert evs[-1]["type"] == "abolished"
        assert evs[-1]["date"] == "2014-04-01" and evs[-1]["precision"] == "era_boundary"

        # 新設: 変更年月日が期間窓内 → exact
        evs = _events(conn, "S", "400000010")
        assert [e["type"] for e in evs] == ["new"]
        assert evs[0]["date"] == "2015-01-01" and evs[0]["precision"] == "exact"
    finally:
        conn.close()


def test_final_era_window_capped_at_ingest_date(project3):
    """最終世代の期間窓は [施行日, 取込日](§6.1)。

    事前告知された未来の変更年月日(例: 2027-06-01 施行の改定日)を持つ新設コードは
    exact ではなく era_boundary(施行日)になること(実データ r08 で観察されたケース)。
    """
    write_csv(project3, "h24", "S", [s_row("111000110", ncols=122)])
    write_csv(project3, "h26", "S", [s_row("111000110", ncols=122)])
    write_csv(
        project3,
        "r06",
        "S",
        [
            s_row("111000110", ncols=150),
            # 取込日(今日)より未来の変更年月日を持つ新設コード
            s_row("600000010", name="未来日付の新設", changed="20991231", ncols=150),
            # 窓内(過去)の変更年月日を持つ新設コード
            s_row("600000020", name="窓内の新設", changed="20240701", ncols=150),
        ],
    )
    conn = _build(project3)
    try:
        evs = _events(conn, "S", "600000010")
        assert evs[0]["type"] == "new"
        assert evs[0]["precision"] == "era_boundary"
        assert evs[0]["date"] == "2024-06-01"  # 施行日で代用(2099-12-31 にしない)

        evs = _events(conn, "S", "600000020")
        assert evs[0]["precision"] == "exact" and evs[0]["date"] == "2024-07-01"
    finally:
        conn.close()


def test_t_abolished_uses_transition_date(project3):
    """特定器材は経過措置年月日を使用期限として考慮する(§6.5)。"""
    write_csv(
        project3,
        "h24",
        "T",
        [
            t_row("700010000", ncols=37),
            t_row(
                "700020000",
                name="経過措置あり",
                transition="20140930",
                abolished="99999999",
                kubun="9",
                ncols=37,
            ),
            t_row(
                "700030000", name="両方あり", transition="20150331", abolished="20140331", kubun="9", ncols=37
            ),
        ],
    )
    write_csv(project3, "h26", "T", [t_row("700010000", ncols=37)])
    write_csv(project3, "r06", "T", [t_row("700010000", ncols=38)])
    conn = _build(project3)
    try:
        evs = _events(conn, "T", "700020000")
        assert evs[-1]["type"] == "abolished"
        assert evs[-1]["date"] == "2014-09-30" and evs[-1]["precision"] == "exact"
        # 両方設定されている場合は遅い方(=実際の使用期限)
        evs = _events(conn, "T", "700030000")
        assert evs[-1]["date"] == "2015-03-31"
    finally:
        conn.close()


def test_reappeared_warning(project3):
    """一度消えたコードの再出現は reappeared として区別する(§6.4)。"""
    write_csv(project3, "h24", "S", [s_row("111000110", ncols=122), s_row("500000050", ncols=122)])
    write_csv(project3, "h26", "S", [s_row("111000110", ncols=122)])
    write_csv(project3, "r06", "S", [s_row("111000110", ncols=150), s_row("500000050", ncols=150)])
    conn = _build(project3)
    try:
        evs = _events(conn, "S", "500000050")
        assert [e["type"] for e in evs] == ["baseline", "abolished", "reappeared"]
        assert evs[2]["from_era"] == "h26" and evs[2]["to_era"] == "r06"
    finally:
        conn.close()


def test_cross_layout_no_false_changes(project3):
    """レイアウトが増えた世代境界で「列が増えた」を変更と誤検知しない(§6.3)。

    h26(v122: tensuhyo_kubun なし)→ r06(v150: あり)で、
    実質同一のレコードに changed イベントが立たないこと。
    """
    write_csv(project3, "h26", "S", [s_row("111000110", name="初診料", price="288.00", ncols=122)])
    write_csv(
        project3,
        "r06",
        "S",
        [
            s_row("111000110", name="初診料", price="288.00", tensuhyo="A000", ncols=150),
        ],
    )
    write_csv(project3, "h24", "S", [s_row("111000110", name="初診料", price="288.00", ncols=122)])
    conn = _build(project3)
    try:
        evs = _events(conn, "S", "111000110")
        assert [e["type"] for e in evs] == ["baseline"]  # changed が立たない
    finally:
        conn.close()


def test_c_sentakushiki_not_compared_across_v19_v30(project3):
    """C: v19→v30 の境界では選択式コメント識別を比較しない(v19に列がない)。"""
    write_csv(project3, "h24", "C", [c_row("10", "1", text="コメントA", ncols=19)])
    write_csv(project3, "h26", "C", [c_row("10", "1", text="コメントA", ncols=19)])
    write_csv(
        project3,
        "r06",
        "C",
        [
            c_row("10", "1", text="コメントA", code="810000001", sentaku="1", ncols=30),
        ],
    )
    conn = _build(project3)
    try:
        evs = _events(conn, "C", "810000001")
        assert [e["type"] for e in evs] == ["baseline"]
    finally:
        conn.close()


def test_untracked_field_changes_recorded(project3):
    """tracked 以外のマップ済みフィールド(カナ名称など)の変化も changed_fields に記録(§6.3)。"""
    write_csv(project3, "h24", "S", [s_row("111000110", kana="ｼｮｼﾝ", ncols=122)])
    write_csv(project3, "h26", "S", [s_row("111000110", kana="ｼｮｼﾝﾘｮｳ", ncols=122)])
    write_csv(project3, "r06", "S", [s_row("111000110", kana="ｼｮｼﾝﾘｮｳ", ncols=150)])
    conn = _build(project3)
    try:
        evs = _events(conn, "S", "111000110")
        assert evs[1]["type"] == "changed"
        assert evs[1]["changes"] == [{"field": "short_kana", "old": "ｼｮｼﾝ", "new": "ｼｮｼﾝﾘｮｳ"}]
    finally:
        conn.close()


def test_build_history_idempotent(project3):
    write_csv(project3, "h24", "S", [s_row("111000110", ncols=122)])
    write_csv(project3, "h26", "S", [s_row("111000110", price="200.00", ncols=122)])
    write_csv(project3, "r06", "S", [s_row("111000110", price="200.00", ncols=150)])
    conn = _build(project3)
    first = conn.execute("SELECT master, code, event_type, event_date FROM events").fetchall()
    conn.close()
    run_build_history(project3, log=lambda *a: None)
    conn = connect(project3)
    second = conn.execute("SELECT master, code, event_type, event_date FROM events").fetchall()
    conn.close()
    assert first == second


def test_events_only_for_ingested_masters(project3):
    """マスターが一部しか無くても動く(欠けは黙って飛ばす)。"""
    write_csv(project3, "h24", "S", [s_row("111000110", ncols=122)])
    run_ingest(project3, log=lambda *a: None)
    conn = connect(project3)
    try:
        eras = load_eras(project3)
        evs = build_events_for_master(conn, project3, "T", eras)
        assert evs == []
        evs = build_events_for_master(conn, project3, "S", eras)
        assert [e.event_type for e in evs] == ["baseline"]
    finally:
        conn.close()


def test_yakka_era_masters_y_only(tmp_path):
    """薬価改定世代(masters: [Y])とマスター別施行日(effective_date_overrides)。

    - r07 は Y の期間窓のみを分割し、S/T/C の期間窓に影響しない
    - r06/r08 の Y は薬価改定日(4/1)が施行日になり、本体施行日(6/1)は使われない
    """
    project = make_project(
        tmp_path,
        ["r04", "r06", "r07", "r08"],
        masters={"r07": ["Y"]},
        overrides={"r06": {"Y": "2024-04-01"}, "r08": {"Y": "2026-04-01"}},
    )

    # S: r04 → r06 → r08(r07 のスナップショットは存在しない)
    write_csv(project, "r04", "S", [s_row("111000110", name="初診", price="288.00", ncols=150)])
    write_csv(
        project,
        "r06",
        "S",
        [
            s_row("111000110", name="初診", price="291.00", changed="20240601", ncols=150),
            # r06 期間中(かつ r07 施行日 2025-04-01 以降)の新設。
            # r07 が S の期間窓を分断すると era_boundary に化けるリグレッションを検知する
            s_row("222000220", name="新行為", changed="20250601", ncols=150),
        ],
    )
    write_csv(
        project,
        "r08",
        "S",
        [
            s_row("111000110", name="初診", price="291.00", changed="20240601", ncols=150),
            s_row("222000220", name="新行為", changed="20250601", ncols=150),
        ],
    )

    # Y: 4世代すべてに在籍し、毎年の薬価改定で金額が変わる
    write_csv(project, "r04", "Y", [y_row("610000001", price="10.00", changed="20220401", ncols=35)])
    write_csv(
        project,
        "r06",
        "Y",
        [
            # 薬価改定日ちょうどの変更。Y の r06 窓は override により [2024-04-01, 2025-04-01)
            y_row("610000001", price="9.00", changed="20240401", ncols=42),
            y_row("620000002", name="経過措置薬", transition="20250331", kubun="9", ncols=42),
        ],
    )
    write_csv(project, "r07", "Y", [y_row("610000001", price="8.00", changed="20250401", ncols=42)])
    write_csv(project, "r08", "Y", [y_row("610000001", price="7.00", changed="20260401", ncols=42)])

    conn = _build(project)
    try:
        # S の新設: r06 の期間窓は [2024-06-01, 2026-06-01)(r07 で分断されない)→ exact
        new = [e for e in _events(conn, "S", "222000220") if e["type"] == "new"][0]
        assert new["date"] == "2025-06-01" and new["precision"] == "exact"
        assert (new["from_era"], new["to_era"]) == ("r04", "r06")

        # Y の変更: 毎年の薬価改定がすべて exact 日付で復元される
        # (r06/r08 は effective_date_overrides により窓が 4/1 開始になるため)
        evs = _events(conn, "Y", "610000001")
        assert [e["type"] for e in evs] == ["baseline", "changed", "changed", "changed"]
        assert [(e["date"], e["precision"]) for e in evs[1:]] == [
            ("2024-04-01", "exact"),  # r04→r06(令和6年度薬価改定)
            ("2025-04-01", "exact"),  # r06→r07(令和7年度薬価改定)
            ("2026-04-01", "exact"),  # r07→r08(令和8年度薬価改定)
        ]
        r07_change = evs[2]
        assert (r07_change["from_era"], r07_change["to_era"]) == ("r06", "r07")
        assert {"field": "price", "old": "9", "new": "8"} in r07_change["changes"]

        # Y の廃止: 廃止年月日が未設定でも経過措置年月日(列34)を使用期限として使う(Tと同様)
        abo = [e for e in _events(conn, "Y", "620000002") if e["type"] == "abolished"][0]
        assert abo["date"] == "2025-03-31" and abo["precision"] == "exact"
        assert (abo["from_era"], abo["to_era"]) == ("r06", "r07")
    finally:
        conn.close()
