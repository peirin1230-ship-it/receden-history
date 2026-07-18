"""layouts.py: レイアウト定義の読込・inherit・世代解決のテスト。"""

import pytest

from receden_history.config import Project
from receden_history.layouts import (
    NON_COMPARED_KEYS,
    compare_keys,
    derive_comment_code,
    load_master_layouts,
)
from tests.conftest import REPO_ROOT


@pytest.fixture
def repo_project() -> Project:
    return Project(root=REPO_ROOT)


def test_s_layout_resolution(repo_project):
    ml = load_master_layouts(repo_project, "S")
    v122_early = ml.for_era("h24")  # h24/h26: 点数表区分番号なし
    v122 = ml.for_era("h28")  # h28/h30/r01: 点数表区分番号あり(実測)
    v150 = ml.for_era("r08")
    assert v122_early.total_columns == 122
    assert v122.total_columns == 122
    assert v150.total_columns == 150
    assert v122_early.verified and v122.verified and v150.verified
    # inherit + drop_columns: h24/h26 のみ点数表区分番号を持たない
    assert "tensuhyo_kubun" in v150.keys()
    assert "tensuhyo_kubun" in v122.keys()
    assert "tensuhyo_kubun" not in v122_early.keys()
    # 主要列は同位置
    for key in ["code", "short_name", "price", "changed_at", "abolished_at", "basic_name"]:
        assert v122_early.specs_by_key()[key].col == v150.specs_by_key()[key].col


def test_t_layout_resolution(repo_project):
    ml = load_master_layouts(repo_project, "T")
    v37 = ml.for_era("r04")
    v38 = ml.for_era("r06")
    assert v37.total_columns == 37
    assert v38.total_columns == 38
    assert "remanufactured" in v38.keys()
    assert "remanufactured" not in v37.keys()
    assert v37.specs_by_key()["transition_at"].col == 29


def test_c_layout_resolution(repo_project):
    ml = load_master_layouts(repo_project, "C")
    v19 = ml.for_era("h28")
    v30 = ml.for_era("h30")
    assert v19.total_columns == 19
    assert v30.total_columns == 30
    assert "code" not in v19.keys()
    assert "sentakushiki" not in v19.keys()
    assert v19.specs_by_key()["changed_at"].col == 18
    assert v30.specs_by_key()["changed_at"].col == 21
    assert ml.has_code_derivation


def test_y_layout_resolution(repo_project):
    ml = load_master_layouts(repo_project, "Y")
    v35 = ml.for_era("h24")
    v42 = ml.for_era("r07")  # 薬価改定世代も v42 に解決される
    assert v35.total_columns == 35
    assert v42.total_columns == 42
    assert v35.verified and v42.verified
    # 一般名処方関連(37/38)は42列レイアウトのみ
    assert "generic_name" in v42.keys() and "generic_name_code" in v42.keys()
    assert "generic_name" not in v35.keys() and "generic_name_code" not in v35.keys()
    # 主要列は全世代で同位置(実測プローブで確認)
    for key in ["code", "short_name", "price", "changed_at", "abolished_at", "transition_at", "basic_name"]:
        assert v35.specs_by_key()[key].col == v42.specs_by_key()[key].col
    assert v42.specs_by_key()["changed_at"].col == 30
    assert v42.specs_by_key()["abolished_at"].col == 31
    assert v42.specs_by_key()["transition_at"].col == 34
    assert v42.specs_by_key()["basic_name"].col == 35
    assert ml.code_length == 9
    assert not ml.has_code_derivation


def test_unknown_era_raises(repo_project):
    ml = load_master_layouts(repo_project, "S")
    with pytest.raises(KeyError):
        ml.for_era("h99")


def test_compare_keys_cross_layout(repo_project):
    """両世代のレイアウトに共通するフィールドのみ比較する(REQUIREMENTS §6.3)。"""
    ml = load_master_layouts(repo_project, "S")
    keys = compare_keys(ml.for_era("h26"), ml.for_era("h28"))
    assert "tensuhyo_kubun" not in keys  # h26(v122_early)側に存在しない
    assert "price" in keys and "short_name" in keys and "basic_name" in keys
    assert not (keys & NON_COMPARED_KEYS)
    # h28以降は両世代に存在するため比較対象になる
    keys2 = compare_keys(ml.for_era("r01"), ml.for_era("r02"))
    assert "tensuhyo_kubun" in keys2

    mlc = load_master_layouts(repo_project, "C")
    keys_c = compare_keys(mlc.for_era("h28"), mlc.for_era("h30"))
    assert "sentakushiki" not in keys_c  # v19側に存在しない
    assert "short_name" in keys_c


def test_map_row_short_row(repo_project):
    """行が layout の想定より短い場合、末尾列は「存在しない」扱い(エラーにしない)。"""
    ml = load_master_layouts(repo_project, "S")
    v150 = ml.for_era("r08")
    row = ["0", "S", "111000110", "2", "初診"]
    fields = v150.map_row(row)
    assert fields["code"] == "111000110"
    assert fields["short_name"] == "初診"
    assert "changed_at" not in fields
    assert "basic_name" not in fields


def test_derive_comment_code():
    assert derive_comment_code("10", "1") == "810000001"
    assert derive_comment_code("20", "100001") == "820100001"
    assert derive_comment_code("2", "34") == "802000034"
