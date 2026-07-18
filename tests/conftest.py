"""テスト用フィクスチャ: 実リポジトリの config を使い、人工の小さなCSVを cp932 で生成する。

fixture の世代は h24 / h26 / r06 の3つを使う。これにより各マスターの
新旧レイアウト(S: 122/150列, T: 37/38列, C: 19/30列)と、
レイアウトを跨ぐ世代間差分(REQUIREMENTS §6.3)の両方をテストできる。
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest

from receden_history.config import Project

REPO_ROOT = Path(__file__).resolve().parents[1]

# 実際の eras.yaml と同じ施行日(テストで期間窓の判定に使う)
ERA_DATES = {
    "h24": "2012-04-01",
    "h26": "2014-04-01",
    "h28": "2016-04-01",
    "h30": "2018-04-01",
    "r01": "2019-10-01",
    "r02": "2020-04-01",
    "r04": "2022-04-01",
    "r06": "2024-06-01",
    "r07": "2025-04-01",
    "r08": "2026-06-01",
}

# 世代 → 各マスターの列数(実測。tests は実レイアウト定義に合わせる)
NCOLS = {
    "h24": {"S": 122, "T": 37, "C": 19, "Y": 35},
    "h26": {"S": 122, "T": 37, "C": 19, "Y": 35},
    "h28": {"S": 122, "T": 37, "C": 19, "Y": 35},
    "h30": {"S": 122, "T": 37, "C": 30, "Y": 35},
    "r01": {"S": 122, "T": 37, "C": 30, "Y": 35},
    "r02": {"S": 150, "T": 37, "C": 30, "Y": 35},
    "r04": {"S": 150, "T": 37, "C": 30, "Y": 35},
    "r06": {"S": 150, "T": 38, "C": 30, "Y": 42},
    "r07": {"Y": 42},
    "r08": {"S": 150, "T": 38, "C": 30, "Y": 42},
}


def make_project(
    tmp_path: Path,
    era_ids: list[str],
    *,
    masters: dict[str, list[str]] | None = None,
    overrides: dict[str, dict[str, str]] | None = None,
) -> Project:
    """実 config/layouts をコピーし、指定世代のみの eras.yaml を書いたプロジェクトを作る。

    masters: 世代id → 対象マスターのリスト(省略した世代は全マスター対象)。
    例: masters={"r07": ["Y"]} で医薬品のみの薬価改定世代を作れる。
    overrides: 世代id → {マスター: 施行日} の effective_date_overrides。
    例: overrides={"r06": {"Y": "2024-04-01"}} で薬価改定日(4/1)を表現する。
    """
    (tmp_path / "config").mkdir(exist_ok=True)
    shutil.copytree(REPO_ROOT / "config" / "layouts", tmp_path / "config" / "layouts", dirs_exist_ok=True)
    lines = ["eras:"]
    for eid in era_ids:
        lines += [
            f"  - id: {eid}",
            f"    label: {eid}世代",
            f'    effective_date: "{ERA_DATES[eid]}"',
        ]
        if masters and eid in masters:
            lines.append(f"    masters: [{', '.join(masters[eid])}]")
        if overrides and eid in overrides:
            lines.append("    effective_date_overrides:")
            lines += [f'      {m}: "{d}"' for m, d in overrides[eid].items()]
    (tmp_path / "config" / "eras.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for eid in era_ids:
        (tmp_path / "data" / "raw" / eid).mkdir(parents=True)
    return Project(root=tmp_path)


def _base_row(ncols: int) -> list[str]:
    return ["0"] * ncols


def s_row(
    code: str,
    *,
    name: str = "テスト行為",
    kana: str = "ﾃｽﾄ",
    price: str = "100.00",
    price_type: str = "3",
    kubun: str = "0",
    changed: str = "0",
    abolished: str = "99999999",
    basic: str | None = None,
    tensuhyo: str = "",
    ncols: int = 150,
) -> list[str]:
    r = _base_row(ncols)
    r[1] = "S"
    r[2] = code
    r[4] = name
    r[6] = kana
    r[10] = price_type
    r[11] = price
    r[0] = kubun
    r[86] = changed
    r[87] = abolished
    r[112] = basic if basic is not None else name
    if ncols >= 117:
        r[116] = tensuhyo
    return r


def t_row(
    code: str,
    *,
    name: str = "テスト器材",
    kana: str = "ﾃｽﾄ",
    price: str = "100.00",
    price_type: str = "1",
    unit_code: str = "0",
    unit_name: str = "",
    kubun: str = "0",
    changed: str = "0",
    transition: str = "00000000",
    abolished: str = "99999999",
    basic: str | None = None,
    ncols: int = 38,
) -> list[str]:
    r = _base_row(ncols)
    r[0] = kubun
    r[1] = "T"
    r[2] = code
    r[4] = name
    r[6] = kana
    r[7] = unit_code
    r[9] = unit_name
    r[10] = price_type
    r[11] = price
    r[27] = changed
    r[28] = transition
    r[29] = abolished
    r[36] = basic if basic is not None else name
    return r


def y_row(
    code: str,
    *,
    name: str = "テスト薬",
    kana: str = "ﾃｽﾄ",
    unit_code: str = "16",
    unit_name: str = "錠",
    price: str = "10.00",
    price_type: str = "1",
    generic: str = "",
    kubun: str = "0",
    changed: str = "0",
    abolished: str = "99999999",
    transition: str = "0",
    yj: str = "",
    basic: str | None = None,
    generic_name: str = "",
    ncols: int = 42,
) -> list[str]:
    r = _base_row(ncols)
    r[0] = kubun
    r[1] = "Y"
    r[2] = code
    r[4] = name
    r[6] = kana
    r[7] = unit_code
    r[9] = unit_name
    r[10] = price_type
    r[11] = price
    r[16] = generic
    r[29] = changed
    r[30] = abolished
    r[31] = yj
    r[33] = transition
    r[34] = basic if basic is not None else name
    if ncols >= 42:
        r[37] = generic_name
    return r


def c_row(
    pattern: str,
    serial: str,
    *,
    text: str = "テストコメント",
    kana: str = "ﾃｽﾄ",
    kubun: str = "0",
    changed: str = "0",
    abolished: str = "0",
    sentaku: str = "0",
    code: str = "",
    ncols: int = 30,
) -> list[str]:
    r = _base_row(ncols)
    r[0] = kubun
    r[1] = "C"
    r[2] = "8"
    r[3] = pattern
    r[4] = serial
    r[6] = text
    r[8] = kana
    if ncols >= 30:
        r[19] = sentaku
        r[20] = changed if changed != "0" else "0"
        r[21] = abolished
        r[22] = code
        r[23] = "0"
    else:
        r[17] = changed
        r[18] = abolished
    return r


def write_csv(project: Project, era_id: str, master: str, rows: list[list[str]]) -> Path:
    """cp932 + CRLF + ヘッダなしでCSVを書く(実データと同条件)。"""
    path = project.raw_dir / era_id / f"{master.lower()}_test.csv"
    with open(path, "w", encoding="cp932", newline="") as f:
        w = csv.writer(f, lineterminator="\r\n")
        w.writerows(rows)
    return path


@pytest.fixture
def project3(tmp_path: Path) -> Project:
    """h24 / h26 / r06 の3世代プロジェクト(CSVは各テストが書く)。"""
    return make_project(tmp_path, ["h24", "h26", "r06"])
