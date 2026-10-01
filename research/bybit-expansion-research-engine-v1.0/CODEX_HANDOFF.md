# Codex Handoff v1.0

This is the research release candidate.

Objective:
Complete the end-to-end discovery system.

Required final pipeline:

1. Scan all Bybit USDT perpetual contracts.
2. Apply liquidity filter:
   turnover >= 500k USDT.
3. Download historical:
   15m, 1H, 4H.
4. Detect expansion events.
5. Build pre-event timelines.
6. Calculate features.

Required features:

Context:
- 4H EMA20/EMA50 relationship
- EMA slopes
- volatility state

Setup:
- compression
- higher low
- failed breakdown
- momentum repair
- volume expansion

Futures:
- open interest
- funding
- liquidation behaviour

Analysis:

Separate:
- winners
- failed breakouts

Run:
- market regime analysis
- walk-forward validation

Final output:

A validated research report containing:

1. Common pre-expansion behaviour.
2. Strongest separating features.
3. Candidate setup definition.
4. Validation results.

Do not implement live execution yet.
Only after validation should trading logic be designed.
