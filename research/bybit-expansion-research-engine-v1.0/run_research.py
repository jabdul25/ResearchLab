#!/usr/bin/env python3
"""Explicitly preflight or run public-market expansion research; never trade."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from src.config import Settings
from src.db import engine_for
from src.pipeline.preflight import (
    check_campaign_gate,
    launch_campaign,
    run_preflight,
    verify_database,
)


PROJECT_ROOT = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bybit expansion evidence research (no trading)")
    parser.add_argument("--config", default="config/system.yaml")
    subcommands = parser.add_subparsers(dest="command")
    preflight = subcommands.add_parser("preflight", help="verify architecture and write preflight evidence only")
    preflight.add_argument("--verify-database", action="store_true", help="read-only database provenance verification")
    campaign = subcommands.add_parser("campaign", help="run after reviewing a matching passing preflight")
    campaign.add_argument("--preflight-id", required=True, type=int)
    campaign.add_argument("--check-only", action="store_true", help="verify the campaign gate without running stages")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.load(args.config)
    engine = engine_for(settings.database_url)
    try:
        if args.command == "preflight":
            if args.verify_database:
                result = verify_database(engine, settings)
                print(json.dumps(result, indent=2, sort_keys=True, default=str))
                return 0 if result["passed"] else 1
            result = run_preflight(engine, settings, PROJECT_ROOT)
            print(json.dumps(result.payload, indent=2, sort_keys=True))
            return 0 if result.passed else 1
        if args.check_only:
            result = check_campaign_gate(engine, settings, PROJECT_ROOT, args.preflight_id)
        else:
            result = launch_campaign(engine, settings, PROJECT_ROOT, args.preflight_id)
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
