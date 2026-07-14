"""validate.py: 検証レポートのテスト。"""

from receden_history.validate import run_validate, validate_file
from tests.conftest import c_row, s_row, t_row, write_csv


def _fill_all(project):
    write_csv(project, "h24", "S", [s_row("111000110", price="270.00", ncols=122)])
    write_csv(project, "h26", "S", [s_row("111000110", price="282.00", ncols=122)])
    write_csv(project, "r06", "S", [s_row("111000110", price="291.00", ncols=150)])
    write_csv(project, "h24", "T", [t_row("700010000", ncols=37)])
    write_csv(project, "h26", "T", [t_row("700010000", ncols=37)])
    write_csv(project, "r06", "T", [t_row("700010000", ncols=38)])
    write_csv(project, "h24", "C", [c_row("10", "1", ncols=19)])
    write_csv(project, "h26", "C", [c_row("10", "1", ncols=19)])
    write_csv(project, "r06", "C", [c_row("10", "1", code="810000001", ncols=30)])


def test_validate_ok(project3):
    _fill_all(project3)
    assert run_validate(project3, log=lambda *a: None) is True


def test_validate_reports_bad_master_kind(project3):
    rows = [s_row("111000110", ncols=122)]
    rows[0][1] = "X"  # マスター種別を壊す(ファイル自動判定を通すため直接検証する)
    path = write_csv(project3, "h24", "S", rows)
    rep = validate_file(project3, "h24", "S", path)
    assert rep.has_error
    assert any(f.check == "expect:master_kind" and f.level == "ERROR" for f in rep.findings)


def test_validate_reports_duplicates(project3):
    path = write_csv(
        project3,
        "h24",
        "S",
        [
            s_row("111000110", ncols=122),
            s_row("111000110", ncols=122),
        ],
    )
    rep = validate_file(project3, "h24", "S", path)
    assert any(f.check == "duplicates" and f.level == "ERROR" for f in rep.findings)


def test_validate_bad_code_rate(project3):
    path = write_csv(project3, "h24", "S", [s_row("12345", ncols=122)])  # 9桁でない
    rep = validate_file(project3, "h24", "S", path)
    assert any(f.check == "code" and f.level == "ERROR" for f in rep.findings)


def test_validate_column_count_mismatch_is_warning(project3):
    """列数不一致は即エラーにせず warning(CLAUDE.md 鉄則3)。"""
    path = write_csv(project3, "h24", "S", [s_row("111000110", ncols=122)[:100]])
    rep = validate_file(project3, "h24", "S", path)
    assert any(f.check == "columns" and f.level == "WARN" for f in rep.findings)
    assert not rep.has_error  # 主要列(87/88等)は100列内に収まっているのでエラーなし


def test_validate_c_code_derivation_mismatch(project3):
    path = write_csv(
        project3,
        "r06",
        "C",
        [
            c_row("10", "1", code="899999999", ncols=30),  # 導出値 810000001 と不一致
        ],
    )
    rep = validate_file(project3, "r06", "C", path)
    assert any(f.check == "code_derivation" and f.level == "ERROR" for f in rep.findings)


def test_validate_decode_error(project3):
    path = project3.raw_dir / "h24" / "s_broken.csv"
    # cp932 として不正なバイト列を含む行
    line = "0,S,111000110,2,".encode("cp932") + b"\x81\xad" + ",x\r\n".encode("cp932")
    path.write_bytes(line)
    rep = validate_file(project3, "h24", "S", path)
    assert any(f.check == "encoding" and f.level == "ERROR" for f in rep.findings)
