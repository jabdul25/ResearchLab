# Bybit Intraday Positioning Breakout — Design

## Purpose

Test whether a 1-hour long signal combines price acceptance with derivatives positioning that can fuel a 24-hour continuation. This is a separate hypothesis following the failure of the daily price/turnover breakout test.

## Universe and liquidity

Use currently Trading, USDT-settled linear perpetuals, excluding non-crypto wrappers: symbols whose base coin or contract metadata identifies stocks, indices, commodities, or ETFs. On every signal, require 1-hour dollar turnover above a configured minimum and use turnover quintiles in the report. The result remains conditional on the currently listed universe.

## Timing and costs

All features are calculated on a completed 1-hour candle `t`. Enter at `t+1` open, exit at `t+25` open, and deduct taker fees plus adverse slippage at both legs. The default total round-trip cost remains 31 bps.

## Features and predeclared candidates

- Price closes above its preceding 6- or 12-hour high after a preceding 6-hour range no wider than 4% or 8%.
- Current hourly turnover is at least 1.5x, 2x, or 3x its prior-six-hour median.
- OI increases over the preceding 1h or 4h while the six-hour price trend is non-negative.
- Latest funding is non-positive or below a configurable mild-positive ceiling.
- Latest account long/short ratio is below one, or falling over its prior observation.

The study evaluates price/turnover candidates and progressively stronger positioning filters. A missing positioning datum never qualifies a positioning-filtered rule.

## Validation and decision rule

Split each symbol's feature rows chronologically 60/40. Select one rule only from pooled development rows with at least 100 trades. Freeze it and evaluate it on holdout rows. Report net mean/median return, win rate, profit factor, bootstrap confidence interval, monthly returns, per-symbol concentration, and a price-only baseline.

Call the signal provisional only if all holdout conditions hold: at least 100 trades, positive net mean and lower 95% bootstrap bound, profit factor above 1.1, profitable results in at least 60% of qualifying months, and no single symbol supplies over 20% of total PnL. Otherwise report no validated edge.

## Data constraints

Bybit supports hourly klines, 1h OI, 1h account long/short ratio, and historical funding. API history limits may restrict the common lookback; use the maximum common period returned, state it, and never backfill with future/current values.

## Outputs

Write separate `output/intraday-*` trade CSV, JSON summary, and report. Preserve the daily study outputs unchanged. The report must state data coverage and all exclusion/error counts.
