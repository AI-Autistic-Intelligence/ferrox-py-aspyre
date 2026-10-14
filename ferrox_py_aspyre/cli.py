"""
Command Line Interface for ferrox-py-aspyre evaluation and operations.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from examples.evaluation_demo import run_evaluation_benchmark


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="aspyre-ferrox",
        description="Ferrox-Py Enterprise Integration Suite for Aspyre.nl",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Benchmark command
    subparsers.add_parser(
        "benchmark",
        help="Run end-to-end integration benchmark for Twinfield, Exact, SBR, and Sagas",
    )

    args = parser.parse_args()

    if args.command in ("benchmark", None):
        print("Running Ferrox-Py Aspyre Evaluation Suite...")
        asyncio.run(run_evaluation_benchmark())
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
