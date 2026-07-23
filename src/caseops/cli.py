from __future__ import annotations

import argparse
from collections.abc import Sequence

from .config import get_settings
from .database import build_engine, build_session_factory
from .seed import seed_reference_data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="caseops",
        description="CaseOps production-oriented reference system",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "seed",
        help="insert idempotent C-102 reference data",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "seed":
        engine = build_engine(get_settings())
        factory = build_session_factory(engine)
        with factory.begin() as session:
            seed_reference_data(session)
        engine.dispose()
        print("CaseOps reference data is ready.")
    return 0
