"""プロジェクトのパス解決と世代(eras.yaml)の読込。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

MASTERS = ("S", "Y", "T", "C")

# マスター種別 → レイアウト定義ファイル名
LAYOUT_FILES = {
    "S": "s_ika.yaml",
    "Y": "y_iyakuhin.yaml",
    "T": "t_tokutei_kizai.yaml",
    "C": "c_comment.yaml",
}


@dataclass(frozen=True)
class Era:
    """世代(スナップショット)。eras.yaml の1エントリ。

    masters: この世代が対象とするマスター種別。既定は全マスター。
    医薬品のみの中間年改定(薬価改定)世代は masters: [Y] のように限定する。
    effective_date_overrides: マスター別の施行日。本体(点数表)と施行日が異なる
    マスターに使う(例: 令和6年度は本体 6/1 に対し薬価改定は 4/1)。
    for_master() がこの値で effective_date を差し替えるため、期間窓・世代境界は
    自動的にマスター別の施行日で計算される。
    """

    id: str
    label: str
    effective_date: str  # ISO形式 YYYY-MM-DD(期間窓の開始日)
    archive_url: str = ""
    notes: str = ""
    masters: tuple[str, ...] = MASTERS
    effective_date_overrides: tuple[tuple[str, str], ...] = ()  # ((master, ISO日付), ...)

    @property
    def effective_yyyymmdd(self) -> str:
        return self.effective_date.replace("-", "")

    def effective_date_for(self, master: str) -> str:
        return dict(self.effective_date_overrides).get(master, self.effective_date)


@dataclass(frozen=True)
class Project:
    """リポジトリルート起点のパス群。CLIは cwd、テストは tmp_path をルートにする。"""

    root: Path

    @property
    def eras_path(self) -> Path:
        return self.root / "config" / "eras.yaml"

    @property
    def layouts_dir(self) -> Path:
        return self.root / "config" / "layouts"

    @property
    def raw_dir(self) -> Path:
        return self.root / "data" / "raw"

    @property
    def db_path(self) -> Path:
        return self.root / "data" / "db" / "masters.sqlite"

    @property
    def web_dir(self) -> Path:
        return self.root / "web"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"


@dataclass(frozen=True)
class EraSet:
    """順序付き世代リスト。並び順=eras.yaml の記載順(古い順)を正とする。"""

    eras: tuple[Era, ...] = field(default_factory=tuple)

    def __iter__(self):
        return iter(self.eras)

    def __len__(self) -> int:
        return len(self.eras)

    def ids(self) -> list[str]:
        return [e.id for e in self.eras]

    def by_id(self, era_id: str) -> Era:
        for e in self.eras:
            if e.id == era_id:
                return e
        raise KeyError(f"eras.yaml に存在しない世代id: {era_id}")

    def index(self, era_id: str) -> int:
        return self.ids().index(era_id)

    def for_master(self, master: str) -> EraSet:
        """指定マスターが対象の世代のみに絞った EraSet を返す。

        世代によって対象マスターが異なる(例: 薬価改定世代は医薬品のみ)ため、
        期間窓・世代境界の計算は必ずマスター別に絞った EraSet で行うこと。
        絞らないと、他マスター専用の世代が期間窓を分断してしまう。
        あわせて effective_date をマスター別施行日(effective_date_overrides)に
        差し替えるため、返り値の期間窓・世代境界はそのマスターの施行日基準になる。
        """
        return EraSet(
            tuple(
                replace(e, effective_date=e.effective_date_for(master))
                for e in self.eras
                if master in e.masters
            )
        )

    def window(self, era_id: str) -> tuple[str, str | None]:
        """世代の期間窓 [開始日, 終了日) を YYYYMMDD で返す。

        最終世代の終了日は None(上限なし)。REQUIREMENTS §6.1 は「取込日」とするが、
        取込日に依存すると再現性(§9)が崩れるため開区間として扱う(README に仮定として明記)。
        """
        i = self.index(era_id)
        start = self.eras[i].effective_yyyymmdd
        end = self.eras[i + 1].effective_yyyymmdd if i + 1 < len(self.eras) else None
        return start, end


def load_eras(project: Project) -> EraSet:
    with open(project.eras_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    eras = []
    for entry in data["eras"]:
        raw_masters = entry.get("masters")
        masters = MASTERS if raw_masters is None else tuple(str(m) for m in raw_masters)
        unknown = [m for m in masters if m not in MASTERS]
        if unknown:
            raise ValueError(f"eras.yaml: 世代 {entry['id']} の masters に不明なマスター種別 {unknown}")
        raw_overrides = entry.get("effective_date_overrides") or {}
        unknown = [m for m in raw_overrides if str(m) not in masters]
        if unknown:
            raise ValueError(
                f"eras.yaml: 世代 {entry['id']} の effective_date_overrides に"
                f"対象外のマスター種別 {unknown}"
            )
        eras.append(
            Era(
                id=str(entry["id"]),
                label=str(entry["label"]),
                effective_date=str(entry["effective_date"]),
                archive_url=str(entry.get("archive_url", "")),
                notes=str(entry.get("notes", "")),
                masters=masters,
                effective_date_overrides=tuple((str(m), str(d)) for m, d in raw_overrides.items()),
            )
        )
    if not eras:
        raise ValueError("eras.yaml に世代が定義されていません")
    era_set = EraSet(tuple(eras))
    dates = [e.effective_date for e in eras]
    if dates != sorted(dates):
        raise ValueError("eras.yaml の世代は施行日の古い順に並べてください")
    for master in MASTERS:
        dates = [e.effective_date for e in era_set.for_master(master)]
        if dates != sorted(dates):
            raise ValueError(
                f"eras.yaml: マスター {master} の施行日列(overrides適用後)が古い順になっていません"
            )
    return era_set
