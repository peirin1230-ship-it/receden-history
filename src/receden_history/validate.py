"""配置済みCSVの品質・レイアウト検証(F1)。取込前に単独で実行できる。

検査項目(REQUIREMENTS §5 F1):
  1. cp932 デコード例外が0件
  2. 2列目がすべて期待するマスター種別文字
  3. コード列が ^\\d{9}$ に一致する率 ≥ 99%(コメントは導出後コード)
  4. 日付列が ^(0|\\d{8})$ に一致する率 ≥ 99%
  5. 名称列の非ASCII率(列ズレ検知の補助)
  6. 主キー重複が0件
  7. 実測列数の最頻値・分布
  8. 行数
追加: コメントコード列(存在時)と導出式の一致確認(FILE_LAYOUTS.md §4)
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .config import MASTERS, Project, load_eras
from .ingest import ENCODING, scan_era_files
from .layouts import derive_comment_code, load_master_layouts

RATE_THRESHOLD = 0.99
NONASCII_WARN_THRESHOLD = 0.30

_CODE9 = re.compile(r"^\d{9}$")
_CODE_C = re.compile(r"^8\d{8}$")


@dataclass
class Finding:
    level: str  # "OK" | "WARN" | "ERROR"
    check: str
    message: str


@dataclass
class FileReport:
    era: str
    master: str
    path: Path
    row_count: int = 0
    column_counts: Counter = field(default_factory=Counter)
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: str, check: str, message: str) -> None:
        self.findings.append(Finding(level, check, message))

    @property
    def has_error(self) -> bool:
        return any(f.level == "ERROR" for f in self.findings)


def _nonascii_rate(values: list[str]) -> float | None:
    """非空値のうち非ASCII文字を含む割合。全て空なら None。"""
    filled = [v for v in values if v]
    if not filled:
        return None
    hit = sum(1 for v in filled if any(ord(ch) > 127 for ch in v))
    return hit / len(filled)


def validate_file(project: Project, era_id: str, master: str, path: Path) -> FileReport:
    rep = FileReport(era=era_id, master=master, path=path)
    ml = load_master_layouts(project, master)
    layout = ml.for_era(era_id)

    # 1. cp932 デコード(行単位でエラーを数え、以降の検査はデコードできた行で行う)
    decode_errors = 0
    lines: list[str] = []
    with open(path, "rb") as f:
        for raw_line in f.read().splitlines():
            try:
                lines.append(raw_line.decode(ENCODING))
            except UnicodeDecodeError as e:
                decode_errors += 1
                lines.append(raw_line.decode(ENCODING, errors="replace"))
                if decode_errors == 1:
                    rep.add("ERROR", "encoding", f"cp932デコード失敗(最初の例: {e})")
    if decode_errors:
        rep.add("ERROR", "encoding", f"cp932デコード失敗行: {decode_errors}件")
    else:
        rep.add("OK", "encoding", "cp932デコード例外 0件")

    rows = [r for r in csv.reader(io.StringIO("\n".join(lines))) if r]
    rep.row_count = len(rows)
    if not rows:
        rep.add("ERROR", "rows", "レコードが0件")
        return rep

    # 7. 実測列数の分布
    rep.column_counts = Counter(len(r) for r in rows)
    mode_cols = rep.column_counts.most_common(1)[0][0]
    dist = ", ".join(f"{c}列×{n}" for c, n in rep.column_counts.most_common())
    if mode_cols != layout.total_columns:
        rep.add(
            "WARN",
            "columns",
            f"実測列数(最頻 {mode_cols})が layout {layout.id} の {layout.total_columns} と不一致。"
            f"分布: {dist} → config/layouts の世代別定義を確認",
        )
    elif len(rep.column_counts) > 1:
        rep.add("WARN", "columns", f"列数が行によって異なる: {dist}")
    else:
        rep.add("OK", "columns", f"列数 {mode_cols}(layout {layout.id} と一致)")

    mapped = [layout.map_row(r) for r in rows]

    # 2. マスター種別 + expect 指定列
    for spec in layout.columns:
        if spec.expect is None:
            continue
        bad = sum(1 for m in mapped if m.get(spec.key) != spec.expect)
        if bad:
            rep.add("ERROR", f"expect:{spec.key}", f"列{spec.col}({spec.name})≠'{spec.expect}' が {bad}件")
        else:
            rep.add("OK", f"expect:{spec.key}", f"列{spec.col}({spec.name})='{spec.expect}' 全件一致")

    # 3. コード率(Cは導出後コードで判定)
    if ml.has_code_derivation:
        codes = [
            (m.get("code") or "").strip() or derive_comment_code(m.get("pattern", ""), m.get("serial", ""))
            for m in mapped
        ]
        code_re = _CODE_C
    else:
        codes = [(m.get("code") or "").strip() for m in mapped]
        code_re = _CODE9
    rate = sum(1 for c in codes if code_re.match(c)) / len(codes)
    lv = "OK" if rate >= RATE_THRESHOLD else "ERROR"
    rep.add(lv, "code", f"コード形式一致率 {rate:.2%}(閾値 {RATE_THRESHOLD:.0%})")

    # 4. 日付・パターン指定列(expect列とcode列以外)
    for spec in layout.columns:
        if spec.pattern is None or spec.key == "code":
            continue
        pat = re.compile(spec.pattern)
        vals = [m.get(spec.key) for m in mapped if spec.key in m]
        if not vals:
            rep.add("WARN", f"pattern:{spec.key}", f"列{spec.col}({spec.name})が行に存在しない")
            continue
        rate = sum(1 for v in vals if pat.match(v)) / len(vals)
        lv = "OK" if rate >= RATE_THRESHOLD else "ERROR"
        msg = f"列{spec.col}({spec.name})パターン一致率 {rate:.2%}"
        if lv == "ERROR":
            msg += " → カラム位置が config とズレている疑い(FILE_LAYOUTS.md §5)"
        rep.add(lv, f"pattern:{spec.key}", msg)

    # 5. 名称列の非ASCII率
    name_rate = _nonascii_rate([m.get("short_name", "") for m in mapped])
    if name_rate is None:
        rep.add("WARN", "name", "名称列が全行空")
    elif name_rate < NONASCII_WARN_THRESHOLD:
        rep.add("WARN", "name", f"名称列の非ASCII率 {name_rate:.2%} が低い → 列ズレの疑い")
    else:
        filled = sum(1 for m in mapped if m.get("short_name"))
        rep.add("OK", "name", f"名称列(非空 {filled}件)の非ASCII率 {name_rate:.2%}")

    # 6. 主キー重複
    dup = [c for c, n in Counter(codes).items() if n > 1]
    if dup:
        rep.add("ERROR", "duplicates", f"主キー重複 {len(dup)}件(例: {dup[:5]})")
    else:
        rep.add("OK", "duplicates", "主キー重複 0件")

    # 追加: コメントコード列と導出式の一致
    if ml.has_code_derivation and "code" in layout.keys():
        filled = [
            (m.get("code", "").strip(), derive_comment_code(m.get("pattern", ""), m.get("serial", "")))
            for m in mapped
            if m.get("code", "").strip()
        ]
        if filled:
            mismatch = sum(1 for got, want in filled if got != want)
            if mismatch:
                rep.add(
                    "ERROR", "code_derivation", f"コメントコード列と導出式の不一致 {mismatch}/{len(filled)}件"
                )
            else:
                rep.add("OK", "code_derivation", f"コメントコード列と導出式が全件一致({len(filled)}件)")
        else:
            rep.add("OK", "code_derivation", "コメントコード列は空(導出式で補完)")

    # 8. 行数
    rep.add("OK", "rows", f"行数 {rep.row_count}(公式ページ掲載件数と目視突合すること)")
    return rep


def run_validate(project: Project, *, era: str | None = None, master: str | None = None, log=print) -> bool:
    """検証レポートを出力する。ERROR がなければ True。"""
    eras = load_eras(project)
    reports: list[FileReport] = []
    missing: list[str] = []
    problems: list[str] = []
    unexpected: list[str] = []
    for e in eras:
        if era and e.id != era:
            continue
        files, era_problems = scan_era_files(project, e.id)
        problems.extend(f"{e.id}/{p.name}: {reason}" for p, reason in era_problems)
        for m in MASTERS:
            if master and m != master:
                continue
            if m not in e.masters:
                if m in files:
                    unexpected.append(f"{e.id}/{m}({files[m].name})")
                continue
            if m not in files:
                missing.append(f"{e.id}/{m}")
                continue
            reports.append(validate_file(project, e.id, m, files[m]))

    ok = not problems
    for p in problems:
        log(f"[ERROR] ファイル走査: {p}")
    for u in unexpected:
        log(f"[WARN ] ファイル走査: {u}: この世代の対象外マスターのCSV(ingest ではスキップされます)")
    for rep in reports:
        status = "NG" if rep.has_error else "OK"
        log(f"\n=== [{status}] {rep.era}/{rep.master} {rep.path.name} ({rep.row_count}行) ===")
        for f in rep.findings:
            log(f"  [{f.level:<5}] {f.check}: {f.message}")
        if rep.has_error:
            ok = False
    if missing:
        log(f"\n未配置: {', '.join(missing)}(data/raw/ にCSVを配置してください)")
    log(f"\n検証結果: {'すべてOK' if ok else 'ERRORあり'}({len(reports)}ファイル)")
    return ok
