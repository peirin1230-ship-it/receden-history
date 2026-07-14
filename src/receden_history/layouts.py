"""config/layouts/*.yaml の読込と、CSV行→フィールド辞書のマッピング。

カラム位置をソースコードにハードコードしない(CLAUDE.md 鉄則2)ため、
列位置・キー割当はすべてこのモジュール経由で YAML から取得する。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

import yaml

from .config import LAYOUT_FILES, Project

# 差分比較の対象外とする「帳簿系」フィールド。
# これらは値の変化が業務上の変更を意味しない(更新のたびに変わる・コードの構成要素である)ため、
# tracked/その他項目いずれの比較からも除外する(REQUIREMENTS §6.3)。
NON_COMPARED_KEYS = frozenset(
    {
        "change_kubun",  # 変更区分: 直近更新の異動種別。毎回変わる
        "master_kind",  # マスター種別: 定数
        "code",  # 主キーそのもの
        "kubun",  # C: 区分(コードの構成要素)
        "pattern",  # C: パターン(同上)
        "serial",  # C: 一連番号(同上)
        "changed_at",  # 変更年月日: 帳簿情報
        "abolished_at",  # 廃止年月日: 帳簿情報(廃止イベントの日付決定には使う)
        "transition_at",  # T: 経過措置年月日(同上)
        "pub_order",  # 公表順序番号: 並び順であり内容変更ではない
        "name_chg_flag",  # S: 漢字名称変更区分(フラグ。名称自体は short_name で比較する)
    }
)


@dataclass(frozen=True)
class ColumnSpec:
    col: int  # 1始まりの列位置
    key: str
    name: str
    expect: str | None = None  # 全行この値であることを期待(validate用)
    pattern: str | None = None  # 値が満たすべき正規表現(validate用)


@dataclass(frozen=True)
class Layout:
    """ある世代グループに適用されるカラム定義。"""

    master: str
    id: str
    verified: bool
    total_columns: int
    columns: tuple[ColumnSpec, ...]  # 列位置昇順

    def specs_by_key(self) -> dict[str, ColumnSpec]:
        return {c.key: c for c in self.columns}

    def keys(self) -> frozenset[str]:
        return frozenset(c.key for c in self.columns)

    def map_row(self, row: list[str]) -> dict[str, str]:
        """CSV行(0始まりlist)→ {key: 値}。行に存在しない列は辞書に含めない。

        世代によって列数が異なる前提(CLAUDE.md 鉄則3)なので、
        行が短くてもエラーにしない(validate 側で列数分布として報告する)。
        """
        fields: dict[str, str] = {}
        for spec in self.columns:
            idx = spec.col - 1
            if idx < len(row):
                fields[spec.key] = row[idx]
        return fields


@dataclass(frozen=True)
class MasterLayouts:
    """1マスター分のレイアウト定義一式(世代→Layout の解決を担う)。"""

    master: str
    code_length: int
    tracked_fields: tuple[str, ...]
    has_code_derivation: bool  # C: コメントコード導出式を持つか
    _by_era: dict[str, Layout]

    def for_era(self, era_id: str) -> Layout:
        try:
            return self._by_era[era_id]
        except KeyError:
            raise KeyError(
                f"マスター {self.master} のレイアウト定義に世代 {era_id} がありません"
                f"(config/layouts/{LAYOUT_FILES[self.master]} の applies_to を確認)"
            ) from None


def _resolve_entries(raw_layouts: list[dict], master: str) -> dict[str, Layout]:
    """layouts エントリ列を解決する。inherit は先行エントリの columns を継承し、
    columns で上書き・追加、drop_columns で削除する。"""
    resolved: dict[str, dict[int, ColumnSpec]] = {}  # layout id -> {col: spec}
    by_era: dict[str, Layout] = {}
    layouts_by_id: dict[str, Layout] = {}

    for entry in raw_layouts:
        lid = str(entry["id"])
        cols: dict[int, ColumnSpec] = {}
        if "inherit" in entry:
            parent = str(entry["inherit"])
            if parent not in resolved:
                raise ValueError(f"layout {lid}: inherit 先 {parent} が未定義(先に定義すること)")
            cols.update(resolved[parent])
        for col_no, spec in (entry.get("columns") or {}).items():
            c = int(col_no)
            cols[c] = ColumnSpec(
                col=c,
                key=str(spec["key"]),
                name=str(spec.get("name", spec["key"])),
                expect=None if spec.get("expect") is None else str(spec["expect"]),
                pattern=None if spec.get("pattern") is None else str(spec["pattern"]),
            )
        for col_no in entry.get("drop_columns") or []:
            cols.pop(int(col_no), None)

        total = entry.get("total_columns")
        if total is None:
            raise ValueError(f"layout {lid}: total_columns が未定義")
        total = int(total)
        over = [c for c in cols if c > total]
        if over:
            raise ValueError(f"layout {lid}: total_columns={total} を超える列定義 {over}")

        resolved[lid] = cols
        layout = Layout(
            master=master,
            id=lid,
            verified=bool(entry.get("verified", False)),
            total_columns=total,
            columns=tuple(cols[c] for c in sorted(cols)),
        )
        layouts_by_id[lid] = layout
        for era_id in entry.get("applies_to") or []:
            era_id = str(era_id)
            if era_id in by_era:
                raise ValueError(f"世代 {era_id} が複数のレイアウトに割り当てられています")
            by_era[era_id] = layout
    return by_era


@cache
def _load_master_layouts_cached(layouts_dir: str, master: str) -> MasterLayouts:
    with open(f"{layouts_dir}/{LAYOUT_FILES[master]}", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if str(data.get("master")) != master:
        raise ValueError(f"{LAYOUT_FILES[master]}: master が {master} ではありません")
    by_era = _resolve_entries(data["layouts"], master)
    return MasterLayouts(
        master=master,
        code_length=int(data.get("code_length", 9)),
        tracked_fields=tuple(str(k) for k in data.get("tracked_fields") or []),
        has_code_derivation="code_derivation" in data,
        _by_era=by_era,
    )


def load_master_layouts(project: Project, master: str) -> MasterLayouts:
    return _load_master_layouts_cached(str(project.layouts_dir), master)


def derive_comment_code(pattern: str, serial: str) -> str:
    """コメントコード導出式(FILE_LAYOUTS.md §4): "8" + パターン2桁 + 一連番号6桁。"""
    return "8" + pattern.strip().zfill(2) + serial.strip().zfill(6)


def compare_keys(layout_a: Layout, layout_b: Layout) -> frozenset[str]:
    """2世代のレイアウトで共に定義されているフィールドのみ比較する(REQUIREMENTS §6.3)。"""
    return (layout_a.keys() & layout_b.keys()) - NON_COMPARED_KEYS
