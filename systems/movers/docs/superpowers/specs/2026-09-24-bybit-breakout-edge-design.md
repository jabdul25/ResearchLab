# Bybit 24-Hour Breakout Edge — Design

## Purpose

Test whether a long-only breakout signal has a repeatable, net-of-cost edge across the complete *currently tradable* Bybit linear USDT perpetual universe. The system must produce an auditable out-of-sample result and a current ranked watchlist; it must not turn realised pumps into hindsight-labelled signals.

## Scope and constraints

- Universe discovery comes from Bybit `v5/market/instruments-info` with `category=linear`, selecting USDT-settled perpetual contracts whose status is `Trading`.
- Historical daily candles come from `v5/market/kline`; optional daily open interest comes from `v5/market/open-interest` at 1-day resolution.
- The source does not provide a historical delisting universe. Results are therefore explicitly conditional on the currently tradable universe and may contain survivorship bias.
- No private account data, execution, or live orders are in scope.
- Raw API responses are cached locally. A rerun uses the cache unless `--refresh` is selected.
- Missing or insufficient history causes an instrument/date to be skipped, never imputed.

## Trade definition and anti-leakage timing

A signal is calculated only from a fully completed UTC daily candle at day `t`.

1. Signal features use candles through `t`, including `t` close and turnover, plus OI through `t` when available.
2. A selected signal enters at the open of `t+1`.
3. It exits at the open of `t+2`; holding period is one calendar day.
4. Gross return is `(exit_open / entry_open) - 1`.
5. Net return deducts entry and exit taker fees plus configurable one-way slippage on both legs. Default assumptions: 5.5 bps taker fee and 10 bps slippage per side. The report also gives a zero-cost diagnostic, never as the edge claim.

The backtest excludes the incomplete final candle and requires both future opens before recording a trade.

## Signal families

Each valid day is evaluated independently for each contract with at least 30 prior completed candles.

- **Compression:** prior 10-day high/low range, normalized by prior low.
- **Breakout:** close above prior `N`-day high, where `N` is 10 or 20.
- **Participation:** current turnover divided by median turnover over the prior 10 days.
- **Pre-break trend:** close relative to close five days earlier.
- **Open interest confirmation:** current OI relative to OI two days earlier when both observations exist. OI is a separate filter, so absence does not get silently treated as confirmation.

The candidate rules are a deliberately small, predeclared grid rather than a fitted model. The report includes both a price-and-turnover-only family and an OI-confirmed family, then selects at most one rule based only on development data.

## Walk-forward validation

For each symbol, split usable history chronologically: first 60% development, final 40% holdout. Candidate thresholds are selected by development net expectancy subject to a minimum trade count. The choice is frozen. Holdout metrics are then computed without retuning.

To avoid one or two liquid or extreme names dominating results, the report contains:

- every signal trade and equal-weight daily portfolio return;
- trades and net returns by calendar month;
- trade count, win rate, mean, median, profit factor, maximum drawdown, and bootstrap confidence interval for mean net return;
- baseline results for every raw N-day price breakout, without compression/turnover/OI filters;
- capacity diagnostics: entry-day dollar turnover quantiles and results with a minimum-turnover screen.

An edge is considered *provisional* only if the selected rule is positive net of costs in holdout, has at least 100 holdout trades, a positive bootstrap 95% lower confidence bound for mean trade return, and is not driven by a single symbol or month. Failure of any condition is a negative/indeterminate outcome, not a weakened claim.

## Current watchlist

After the backtest, evaluate the frozen selected rule on the most recent completed candle. Output eligible symbols ranked by a deterministic score composed of breakout distance, turnover multiple, compression, and OI confirmation. The output is a research watchlist, never an execution instruction.

## Outputs

- `data/bybit/`: API cache and metadata.
- `output/trades.csv`: row-level results, including signal date, entry/exit, feature values, gross/net return, and split.
- `output/summary.json`: machine-readable configuration and metrics.
- `output/report.md`: readable findings, limitations, baselines, OOS metrics, and watchlist.
- An executable Node.js CLI with `--refresh`, `--days`, `--fee-bps`, and `--slippage-bps` flags.

## Error handling

Requests retry on 429/5xx with bounded exponential delay. Per-symbol API errors are recorded and the run continues. The report states universe size, completed symbols, skipped symbols, and error reasons, so no coverage gap is concealed.
