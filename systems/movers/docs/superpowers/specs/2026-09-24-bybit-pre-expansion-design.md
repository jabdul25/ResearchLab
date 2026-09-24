# Bybit Pre-Expansion Pump Detection — Design

## Objective

Test whether liquid Bybit USDT crypto perpetuals can be ranked during a compressed accumulation phase and entered on their first 15-minute breakout for a positive 24-hour net return.

## Point-in-time candidate state

On each completed 15-minute candle, require: (1) price is within 1% of the preceding four-hour high; (2) prior four-hour range is at or below an instrument-relative compression threshold; (3) prior four-hour return is below 3%; (4) current turnover is at least 1.5x its preceding 16-candle median; (5) OI rose over the preceding one to four hours while price remained contained; and (6) liquidity meets a dollar-turnover/OI floor. Funding and account ratio are additional, separately tested filters.

## Trade and validation

The first completed 15-minute close above the preceding four-hour high is the trigger. Enter at the next 15-minute open, exit at the open 24 hours later, and deduct costs on both legs. A 60/40 chronological per-symbol split selects a small predeclared grid only on development data; holdout remains untouched. A provisional edge requires 100+ holdout trades, positive mean and lower bootstrap confidence bound, profit factor >1.1, profitable majority of months, and no single-symbol PnL concentration over 20%.

## Exclusions

Exclude Bybit stock/index/ETF wrapper contracts, signals after >5% instantaneous extension from the range high, missing positioning data for positioning-required rules, and inadequate liquidity. Current-universe survivorship is disclosed.
