from __future__ import annotations

import argparse
from pathlib import Path

from .autogen import autogen_variants
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
    run_parser.add_argument("--screen", action="store_true")

    prereg_parser = sub.add_parser("preregister")
    prereg_parser.add_argument("study")
    prereg_parser.add_argument("--timestamp", required=True)

    autogen_parser = sub.add_parser("autogen")
    autogen_parser.add_argument("study")
    autogen_parser.add_argument("--timestamp", required=True)
    autogen_parser.add_argument("--generator-model", default=None)

    args = parser.parse_args()
    if args.command == "run":
        study = load_study(args.study)
        result = run_study(study, limit=args.limit, mock=args.mock, screen=args.screen)
        if args.screen:
            print(
                f"wrote {result['meta']['results_dir']}; "
                f"triage={result['analysis']['triage']['label']}; "
                f"errors={result['analysis']['errors']}/{result['analysis']['rows']}"
            )
        else:
            print(
                f"wrote {result['meta']['results_dir']}; "
                f"verdict={result['analysis']['verdict']['verdict']}; "
                f"errors={result['analysis']['errors']}/{result['analysis']['rows']}"
            )
    elif args.command == "preregister":
        study = load_study(args.study)
        out = preregister(Path(args.study).resolve(), study.raw, args.timestamp)
        print(f"wrote {out}")
    elif args.command == "autogen":
        autogen_variants(
            Path(args.study).resolve(),
            timestamp=args.timestamp,
            generator_model=args.generator_model,
        )


if __name__ == "__main__":
    main()
