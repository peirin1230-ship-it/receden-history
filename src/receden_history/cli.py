"""receden コマンドのエントリポイント(仕様: REQUIREMENTS.md §8)。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import MASTERS, Project


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="receden",
        description="レセ電コード変更履歴ツール(取込・検証・履歴構築・静的サイト生成)",
    )
    p.add_argument(
        "--root", type=Path, default=Path.cwd(), help="リポジトリルート(既定: カレントディレクトリ)"
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("validate", help="配置済みCSVの検証レポート")
    sp.add_argument("--era", help="対象世代id(例: h24)")
    sp.add_argument("--master", choices=MASTERS, help="対象マスター種別")

    sp = sub.add_parser("ingest", help="data/raw → SQLite 取込")
    sp.add_argument("--era", help="対象世代id")
    sp.add_argument("--force", action="store_true", help="sha256一致でも再取込する")

    sub.add_parser("build-history", help="世代間差分からイベント生成")

    sp = sub.add_parser("show", help="コード単位のタイムライン表示")
    sp.add_argument("master", choices=MASTERS)
    sp.add_argument("code")
    sp.add_argument("--format", choices=["text", "json"], default="text")

    sp = sub.add_parser("search", help="名称でコード検索")
    sp.add_argument("keyword")
    sp.add_argument("--master", choices=MASTERS)

    sp = sub.add_parser("export", help="events.csv / summary.md 出力")
    sp.add_argument("--out", type=Path, default=None, help="出力先(既定: exports/)")

    sp = sub.add_parser("export-site", help="GitHub Pages 用静的サイト生成")
    sp.add_argument("--out", type=Path, default=Path("_site"), help="出力先(既定: _site)")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project = Project(root=args.root.resolve())

    # サブコマンド実装は遅延import(部分実装段階でもCLI全体が壊れないように)
    if args.command == "validate":
        from .validate import run_validate

        ok = run_validate(project, era=args.era, master=args.master)
        return 0 if ok else 1

    if args.command == "ingest":
        from .ingest import IngestError, run_ingest

        try:
            run_ingest(project, era=args.era, force=args.force)
        except IngestError as e:
            print(f"エラー: {e}", file=sys.stderr)
            return 1
        return 0

    if args.command == "build-history":
        from .diff import run_build_history

        run_build_history(project)
        return 0

    if args.command == "show":
        from .report import run_show

        return run_show(project, args.master, args.code, fmt=args.format)

    if args.command == "search":
        from .report import run_search

        return run_search(project, args.keyword, master=args.master)

    if args.command == "export":
        from .report import run_export

        run_export(project, out=args.out)
        return 0

    if args.command == "export-site":
        from .site import run_export_site

        run_export_site(project, out=args.out)
        return 0

    raise AssertionError(f"未実装のコマンド: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
