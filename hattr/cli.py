from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_study
from .engine import run_study
from .prereg import preregister


def main() -> None:
    parser = argparse.ArgumentParser(prog="hattr")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run")
    run_parser.add_argument("study")
    run_parser.add_argument("--limit", type=int, default=None)
    run_parser.add_argument("--mock", action="store_true")

    prereg_parser = sub.add_parser("preregister")
    prereg_parser.add_argument("study")
    prereg_parser.add_argument("--timestamp", required=True)

    args = parser.parse_args()
    if args.command == "run":
        study = load_study(args.study)
        result = run_study(study, limit=args.limit, mock=args.mock)
        print(
            f"wrote {result['meta']['results_dir']}; "
            f"verdict={result['analysis']['verdict']['verdict']}; "
            f"errors={result['analysis']['errors']}/{result['analysis']['rows']}"
        )
    elif args.command == "preregister":
        study = load_study(args.study)
        out = preregister(Path(args.study).resolve(), study.raw, args.timestamp)
        print(f"wrote {out}")


if __name__ == "__main__":
    main()

