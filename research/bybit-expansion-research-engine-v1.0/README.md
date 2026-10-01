# Bybit Expansion Research Engine v1.0

Research Release Candidate.

Objective:
Produce a validated description of the market state that appears before large Bybit USDT perpetual altcoin expansions.

Pipeline:
Universe -> Data -> Events -> Features -> Evidence -> Regime -> Validation -> Candidate Setup

This is research software, not a live trading system.

## Run

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
createdb expansion_research
psql expansion_research < database/schema_v10.sql
cp .env.example .env # then set DATABASE_URL
python run_research.py
```

The collector dynamically filters `Trading` linear USDT perpetuals by 24-hour
turnover (default 500,000 USDT), requests 15m/1h/4h candles, and resumes with
database primary keys after interruption. Reports are written to `reports/`.
Use `python run_research.py --skip-collection` to rerun discovery and reports
from the stored data. The program calls public market-data endpoints only.

Run the deterministic analysis checks after installing dependencies:

```bash
python -m unittest tests.test_analysis
```
