# Bybit Expansion Research Engine v1.0

Candidate-first quantitative research system. It does not trade.

Objective:
Produce a validated description of the market state that appears before large Bybit USDT perpetual altcoin expansions.

Pipeline:
Universe -> Breakout Candidates -> Point-in-Time Liquidity -> Frozen Features -> Future Labels -> Matched Evidence -> Validation -> Reports

This is research software, not a live trading system.

## Run

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
createdb expansion_research
cp .env.example .env # then set DATABASE_URL
python -c "from src.config import Settings; from src.db import engine_for,initialize; s=Settings.load(); initialize(engine_for(s.database_url))"
python run_research.py preflight
```

The collector discovers every currently trading Linear USDT perpetual without
using current turnover as a historical filter. Candidate eligibility is decided
only from the 96 completed 15-minute candles immediately preceding each
breakout close. The default threshold is 500,000 USDT trailing turnover.

Preflight writes `reports/00_preflight.md` and `reports/00_preflight.json`. It
checks exchange reachability, UTC time, disk space, database/schema provenance,
active collection jobs, campaign bounds, source revision state, and leakage
tests. It does not collect market data.

Review the preflight evidence before explicitly starting a campaign:

```bash
python run_research.py preflight --verify-database
python run_research.py campaign --preflight-id ID --check-only
python run_research.py campaign --preflight-id ID
```

The campaign command rejects a missing, failed, or stale preflight. Every
campaign attempt writes an immutable started provenance snapshot and an
immutable completed or failed terminal snapshot. Running `run_research.py`
without a subcommand prints help and performs no work.

Run the deterministic analysis checks after installing dependencies:

```bash
python -m unittest discover -s tests -v
```

Reports describe historically associated market conditions. They are not
causal claims, entry rules, position-sizing logic, stops, or execution advice.
