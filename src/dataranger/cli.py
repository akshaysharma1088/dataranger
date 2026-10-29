"""Command-line entry point. The patrol loop lands in 0.1.0; this release reserves the name."""
import argparse

from . import __version__

STAGES = [
    ("Detect", "rules and statistics find the problem; no LLM"),
    ("Diagnose", "an LLM answers one narrow question about the cause"),
    ("Propose fix", "a templated fix; the LLM fills parameters only"),
    ("Verify", "pipeverify checks the fix against source data, on a copy"),
    ("Gate", "apply as a new version if proven and low risk; otherwise escalate"),
    ("Report", "every decision lands on the dashboard and the audit log"),
]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dataranger", description=__doc__)
    parser.add_argument("--version", action="version", version=f"dataranger {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("plan", help="show the patrol loop DataRanger will run")
    args = parser.parse_args(argv)
    if args.command == "plan":
        for i, (name, what) in enumerate(STAGES, 1):
            print(f"{i}. {name}: {what}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
