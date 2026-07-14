"""プロジェクトのパス解決と世代(eras.yaml)の読込。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

MASTERS = ("S", "T", "C")

# マスター種別 → レイアウト定義ファイル名
LAYOUT_FILES = {
    "S": "s_ika.yaml",
    "T": "t_tokutei_kizai.yaml",
    "C": "c_comment.yaml",
}


@dataclass(frozen=True)
class Era:
    """世代(スナップショット)。eras.yaml の1エントリ。"""

    id: str
    label: str
    effective_date: str  # ISO形式 YYYY-MM-DD(期間窓の開始日)
    archive_url: str = ""
    notes: str = ""

    @property
    def effective_yyyymmdd(self) -> str:
        return self.effective_date.replace("-", "")


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
        eras.append(
            Era(
                id=str(entry["id"]),
                label=str(entry["label"]),
                effective_date=str(entry["effective_date"]),
                archive_url=str(entry.get("archive_url", "")),
                notes=str(entry.get("notes", "")),
            )
        )
    if not eras:
        raise ValueError("eras.yaml に世代が定義されていません")
    dates = [e.effective_date for e in eras]
    if dates != sorted(dates):
        raise ValueError("eras.yaml の世代は施行日の古い順に並べてください")
    return EraSet(tuple(eras))
