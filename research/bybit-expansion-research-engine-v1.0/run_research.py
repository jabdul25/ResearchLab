#!/usr/bin/env python3
"""Run public-market expansion research. It never submits orders."""
import argparse, json, logging
from src.config import Settings
from src.db import engine_for, initialize
from src.pipeline.orchestrator import ResearchOrchestrator

def main():
    parser=argparse.ArgumentParser(description="Bybit expansion evidence research (no trading)")
    parser.add_argument("--config",default="config/system.yaml")
    parser.add_argument("--skip-collection",action="store_true",help="analyse data already in PostgreSQL")
    args=parser.parse_args(); logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings=Settings.load(args.config); engine=engine_for(settings.database_url); initialize(engine)
    print(json.dumps(ResearchOrchestrator(engine,settings).run(collect=not args.skip_collection),indent=2))
if __name__ == "__main__": main()
