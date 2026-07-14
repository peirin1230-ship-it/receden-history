"""site.py: 静的サイト生成(SITE_SPEC §2〜3)のテスト。"""

import json
import shutil

from receden_history.diff import run_build_history
from receden_history.ingest import run_ingest
from receden_history.site import export_site
from tests.conftest import REPO_ROOT, c_row, s_row, write_csv


def _setup(project):
    shutil.copytree(REPO_ROOT / "web", project.root / "web")
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
    write_csv(
        project,
        "r06",
        "C",
        [
            c_row("10", "1", text="コメントA", code="810000001", ncols=30),
        ],
    )
    run_ingest(project, log=lambda *a: None)
    run_build_history(project, log=lambda *a: None)


def test_export_site_structure(project3, tmp_path):
    _setup(project3)
    out = tmp_path / "_site"
    export_site(project3, out, log=lambda *a: None)

    # SITE_SPEC §2 の構成
    assert (out / "index.html").is_file()
    assert (out / "assets" / "app.js").is_file()
    assert (out / "assets" / "style.css").is_file()
    assert (out / "data" / "meta.json").is_file()
    assert (out / "data" / "summary.json").is_file()
    assert (out / "data" / "search" / "S.json").is_file()
    assert (out / "data" / "history" / "S" / "111.json").is_file()
    assert (out / "data" / "history" / "C" / "810.json").is_file()


def test_meta_and_summary_schema(project3, tmp_path):
    _setup(project3)
    out = tmp_path / "_site"
    export_site(project3, out, log=lambda *a: None)

    meta = json.loads((out / "data" / "meta.json").read_text(encoding="utf-8"))
    assert meta["counts"]["S"]["codes"] == 2
    assert meta["shard_len"]["S"] == 3
    assert meta["disclaimer"]
    assert meta["limitations"]
    assert [e["id"] for e in meta["eras"]] == ["h24", "h26", "r06"]
    assert meta["eras"][0]["files"]["S"] == "s_test.csv"

    summary = json.loads((out / "data" / "summary.json").read_text(encoding="utf-8"))
    assert summary["S"]["h26"]["changed"] == 1
    assert summary["S"]["h26"]["abolished"] == 1


def test_search_index_schema(project3, tmp_path):
    _setup(project3)
    out = tmp_path / "_site"
    export_site(project3, out, log=lambda *a: None)

    search = json.loads((out / "data" / "search" / "S.json").read_text(encoding="utf-8"))
    assert search["columns"] == ["code", "name", "status", "abolished_date"]
    items = {row[0]: row for row in search["items"]}
    assert items["111000110"][1:] == ["初診料", "active", None]
    assert items["300000010"][1:] == ["旧行為", "abolished", "2014-03-31"]


def test_history_shard_schema(project3, tmp_path):
    _setup(project3)
    out = tmp_path / "_site"
    export_site(project3, out, log=lambda *a: None)

    shard = json.loads((out / "data" / "history" / "S" / "111.json").read_text(encoding="utf-8"))
    entry = shard["111000110"]
    assert entry["name"] == "初診料"
    types = [e["type"] for e in entry["events"]]
    assert types == ["baseline", "changed", "changed"]
    # baseline は snapshot_fields を持つ(SITE_SPEC §3)
    assert entry["events"][0]["snapshot_fields"] == {"price": "270", "price_type": "3"}
    ch = entry["events"][1]
    assert ch["from_era"] == "h24" and ch["to_era"] == "h26"
    assert {"field": "price", "old": "270", "new": "282"} in ch["changes"]

    # 廃止コードのイベント
    shard300 = json.loads((out / "data" / "history" / "S" / "300.json").read_text(encoding="utf-8"))
    evs = shard300["300000010"]["events"]
    assert evs[-1]["type"] == "abolished"
    assert evs[-1]["date"] == "2014-03-31"
    assert evs[-1]["change_kubun"] == "9"


def test_frontend_uses_relative_paths_only(project3, tmp_path):
    """fetch・アセット参照が相対パスのみであること(SITE_SPEC §1、CLAUDE.md 鉄則8)。"""
    _setup(project3)
    out = tmp_path / "_site"
    export_site(project3, out, log=lambda *a: None)

    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="./assets/style.css"' in html
    assert 'src="./assets/app.js"' in html
    assert 'href="/' not in html and 'src="/' not in html  # 先頭 / の絶対パス禁止

    js = (out / "assets" / "app.js").read_text(encoding="utf-8")
    # fetch のURLリテラルが先頭 / で始まっていないこと
    assert 'fetch("/' not in js and "fetch('/" not in js and "fetch(`/" not in js
    assert 'fetchJSON("/' not in js and "fetchJSON(`/" not in js
    assert '"./data/meta.json"' in js


def test_export_is_idempotent_data(project3, tmp_path):
    """meta.json の生成日時以外は再実行で同一(§9 再現性)。"""
    _setup(project3)
    out1, out2 = tmp_path / "a", tmp_path / "b"
    export_site(project3, out1, log=lambda *a: None)
    export_site(project3, out2, log=lambda *a: None)
    for rel in ["data/summary.json", "data/search/S.json", "data/history/S/111.json"]:
        assert (out1 / rel).read_bytes() == (out2 / rel).read_bytes()
