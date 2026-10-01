# Candidate-First Bybit Expansion Research Engine Design

Date: 2026-10-01

Status: Proposed for implementation

Scope: Bybit Linear USDT perpetual market-state research only

## 1. Purpose

The engine will identify measurable, causal market conditions that distinguish successful upward expansions from comparable failed breakout attempts. It will not create entries, exits, leverage rules, position sizing, execution logic, or a live trading system.

The primary unit of analysis is a point-in-time breakout candidate. Every candidate is discovered using information available at the close of its breakout candle. Its liquidity, features, and market regime are frozen at that decision time. Only its outcome label may use the following 12 candles.

The system must answer:

> What measurable conditions, known when a breakout candle closes, separate successful Bybit perpetual expansions from failed, weak, or reversing breakout attempts?

## 2. Required Outcomes

The first research campaign will:

- collect all currently trading Bybit Linear USDT perpetual contracts without a current-turnover eligibility filter;
- retain at least 12 months of 15-minute, 1-hour, and 4-hour candles when the contract has that much history;
- collect available open-interest and funding history;
- generate all point-in-time breakout signals using a configurable structure lookback;
- preserve both admitted candidates and rejected duplicate signals;
- apply the 500,000 USDT liquidity threshold from historical turnover at each candidate decision time;
- snapshot causal features and market regime at the decision time;
- label the next 12 candles with mutually exclusive outcomes;
- compare winners with matched failure cohorts by timeframe, regime, and chronological split;
- generate five evidence reports, including population and leakage audits.

Contracts launched less than 12 months ago will be collected from launch. Their shorter histories must be reported rather than silently treated as complete 12-month histories.

## 3. Non-Goals

The engine will not:

- place, simulate, or recommend trades;
- define entries, stops, targets, leverage, or execution behavior;
- optimize structure lookback, thresholds, feature cutoffs, or label rules;
- cherry-pick symbols or outcomes;
- use present-day turnover to qualify historical candidates;
- use future data in liquidity, features, regime, or candidate detection;
- call the resulting market state a strategy.

## 4. Configuration

Research parameters belong in configuration and are recorded with every run:

```yaml
candidates:
  structure_lookback: 20
  outcome_window_candles: 12
  duplicate_window_candles: 12
  reversal_min_threshold_fraction: 0.50
  reversal_giveback_fraction: 0.70

liquidity:
  minimum_turnover_24h: 500000
  source_interval_minutes: 15

events:
  thresholds:
    15: 0.08
    60: 0.15
    240: 0.25

features:
  ema_slope_lookback_bars: 6
  volatility_percentile_lookback_bars: 90
  high_volatility_percentile: 0.80

analysis:
  minimum_winners_per_split: 20
  minimum_failures_per_split: 20
```

The default `structure_lookback` is 20. Later campaigns may explicitly run 10, 20, 30, or 50 without code changes. A run uses one declared lookback; the first campaign uses 20. This is parameterized research support, not an authorization to optimize across values.

## 5. Time Semantics and Causal Boundary

All stored candle timestamps are candle-open times. For a candle with interval `I`:

```text
decision_time = open_time + I
feature_time  = decision_time
label_window  = the next 12 completed candles after the breakout candle
```

At `decision_time`, the breakout candle is complete. Candidate detection, liquidity, features, and regime may use that candle and any earlier completed candle. They must not access any later candle or observation.

The causal boundary is enforced structurally:

- decision-time candidate facts are stored in `breakout_candidates`;
- decision-time features are stored in `candidate_features`;
- future-derived results are stored separately in `candidate_outcomes`;
- analysis joins outcomes only after candidate and feature snapshots exist.

An admitted candidate without 12 subsequent completed candles remains `pending`. It is never included in outcome comparisons until its label window is complete.

## 6. Universe and Historical Collection

### 6.1 Collection universe

The collector pages through Bybit V5 instrument metadata and selects only contracts satisfying all of these conditions:

- category is `linear`;
- contract type is `LinearPerpetual`;
- quote asset is `USDT`;
- status is `Trading`.

Current ticker turnover may be stored as descriptive collection metadata, but it cannot set historical eligibility and cannot remove a trading contract from collection.

### 6.2 Collection windows

For each symbol and interval, the requested start is the later of:

- campaign end minus configured history months; or
- instrument launch time.

Only completed candles are stored. The active, still-forming candle is excluded.

### 6.3 Correct pagination

Bybit kline and funding responses are newest-first. Collection therefore moves a bounded `end` cursor backward from the campaign end toward the requested start. The next request ends immediately before the earliest timestamp returned by the previous page.

Open-interest collection follows Bybit's `nextPageCursor`. The query's fixed start and end bounds and latest cursor are persisted.

Each page is committed in the same database transaction as its checkpoint update. Primary keys make replay idempotent. A restart resumes an incomplete range rather than inferring completeness from the maximum stored timestamp.

### 6.4 Coverage audit

Collection jobs store requested bounds, observed bounds, expected bar count, stored bar count, gaps, status, last cursor, error details, and retry count. Campaign analysis is blocked when required candle ranges contain unexplained gaps. Missing OI or funding is retained as missing data and reported; it does not fabricate a zero value.

## 7. Candidate Detection

For each symbol and timeframe, candles are processed chronologically. At index `i`, after the candle closes:

```text
breakout_level = max(high of candles [i - structure_lookback, i))
raw_signal = close[i] > breakout_level
```

The current candle is excluded from the structure-high calculation. The signal is therefore a close above the highest high of the preceding `structure_lookback` completed candles.

### 7.1 Admission and duplicate audit

If no admitted candidate exists within the preceding 12-candle outcome window, the signal becomes an admitted candidate. After admission, later raw signals through the admitted candidate's twelfth outcome candle are stored with:

- `candidate_status = rejected_duplicate`;
- their own timestamp and breakout level;
- `duplicate_of_candidate_id` referencing the admitted candidate;
- the rejection reason and active window bounds.

Rejected duplicates do not receive outcome labels and do not enter feature comparisons. Their counts and timestamps appear in the Candidate Population Report and leakage audit. They are never silently discarded.

## 8. Point-in-Time Liquidity

Liquidity is calculated from stored 15-minute candles for every admitted candidate, regardless of the candidate timeframe. This creates one consistent 24-hour source across 15-minute, 1-hour, and 4-hour candidates.

The liquidity window contains completed 15-minute candles whose close times satisfy:

```text
decision_time - 24 hours < candle_close_time <= decision_time
```

The snapshot stores:

- `turnover_24h_before_event` as the sum of historical USDT turnover;
- `volume_24h_before_event` as the sum of historical base-asset volume;
- the number of expected and observed 15-minute bars;
- `liquidity_eligible = turnover >= 500,000` only when the window is complete;
- a reason when eligibility is false or indeterminate.

Candidates with incomplete liquidity windows are stored as ineligible with reason `incomplete_liquidity_window`. Both eligible and excluded candidates remain queryable. Only historically eligible, admitted, fully labeled candidates enter evidence analysis.

Changing today's ticker, any candle after `decision_time`, or any later collection must not change a persisted liquidity snapshot for an unchanged historical input window.

## 9. Feature Snapshot

Features are calculated only after truncating every input series at `decision_time`. The engine passes bounded data to feature functions; feature functions do not receive the future label window.

No simple moving average is permitted. EMA is used throughout.

### 9.1 Directional strength

Using the latest completed 4-hour candle at or before the decision time:

- EMA20 above EMA50;
- EMA20 slope;
- EMA50 slope;
- normalized EMA separation;
- composite trend strength.

### 9.2 Compression

Using only candidate-timeframe candles through the breakout candle:

- ATR contraction;
- range contraction;
- candle-body reduction;
- volatility percentile;
- composite compression score.

### 9.3 Seller exhaustion and structure

- preceding swing low;
- pullback low;
- higher-low state;
- failed-breakdown attempts;
- buyer-defense score;
- breakout level and distance from it.

### 9.4 Momentum repair

- EMA20 slope improvement;
- EMA20/EMA50 gap improvement;
- count of higher closes;
- momentum-repair score.

### 9.5 Participation

- breakout-candle volume ratio;
- turnover ratio;
- relative volume;
- historical 24-hour volume and turnover snapshot.

### 9.6 Futures behavior

Open interest and funding observations are restricted to timestamps at or before `decision_time`:

- OI change over the configured trailing observations;
- OI expansion state;
- last settled funding rate;
- funding distribution context;
- organic-expansion, short-squeeze, weak-participation, or unclassified state.

Missing futures observations remain null and are counted in report coverage.

## 10. Market Regime Snapshot

Regime uses only completed 4-hour candles available at the decision time. It is stored with the feature snapshot as one of:

- `high_volatility`: normalized ATR is at or above its trailing 80th percentile;
- `bullish_trend`: EMA20 is above EMA50 and both configured slopes are positive;
- `bearish_trend`: EMA20 is below EMA50 and both configured slopes are negative;
- `range`: sufficient history exists but none of the preceding states applies;
- `unknown`: history is insufficient.

High volatility has precedence over directional classifications so its cohort remains distinct. The first campaign uses a 90-bar volatility-percentile window and six-bar EMA slope horizon. These values are configuration entries recorded in the run manifest and are not optimized during the campaign.

## 11. Outcome Labeling

Only the 12 candles after the breakout candle may be used. For breakout close `C`, breakout level `B`, future maximum high `H`, future minimum low `L`, and final close `F`:

```text
MFE                    = H / C - 1
MAE                    = L / C - 1
maximum_extension      = H / B - 1
final_vs_breakout_level = F / B - 1
giveback_fraction      = (H - F) / (H - C), when H > C
threshold_reached      = MFE >= timeframe_threshold
```

Labels use this mutually exclusive precedence:

1. `winner`: `threshold_reached` is true.
2. `reversal`: not a winner, MFE reaches at least half the timeframe threshold, giveback is at least 70%, and the final close is below the breakout level.
3. `failed_breakout`: not a winner or reversal, and the final close is below the breakout level.
4. `weak_expansion`: every other complete, sub-threshold outcome.

The stored outcome includes classification, MFE, MAE, threshold and whether it was reached, maximum extension, final position relative to the breakout level, giveback fraction, window bounds, and observed candle count.

## 12. Database Model

Schema changes are versioned and recorded in `schema_migrations`. They do not depend on rerunning one monolithic schema file.

### 12.1 Core collection tables

- `instruments`: symbol metadata, launch time, status, and descriptive current ticker values.
- `candles`: unique by symbol, interval, and open time.
- `open_interest`: unique by symbol, interval, and observation time.
- `funding_rates`: unique by symbol and funding time.
- `collection_jobs`: source, symbol, interval, fixed requested bounds, cursor/checkpoint, coverage metrics, status, retries, and error details.

### 12.2 Research tables

- `research_runs`: immutable configuration manifest, code/schema version, campaign bounds, status, and summary.
- `breakout_candidates`: identity, symbol, timeframe, breakout candle and decision timestamps, structure lookback, breakout level/close, candidate status, duplicate reference, duplicate reason/window, 24-hour liquidity fields, observed liquidity bars, eligibility, regime, and run ID.
- `candidate_features`: candidate ID, feature timestamp, version, feature JSON, input maximum timestamps for candles/OI/funding, and deterministic input fingerprint.
- `candidate_outcomes`: candidate ID, label-window bounds, classification, threshold, threshold reached, MFE, MAE, maximum extension, final position, giveback, observed candles, and label version.
- `research_artifacts`: versioned JSON evidence emitted by a run.
- `validated_market_states`: descriptive research states and their walk-forward evidence, always marked `RESEARCH_ONLY`.

Database constraints enforce:

- duplicate references point to an admitted candidate;
- only admitted candidates may have feature or outcome rows;
- feature timestamps equal candidate decision times;
- liquidity values and eligibility reasons are present together;
- outcomes use only allowed classifications;
- one candidate definition per symbol, timeframe, breakout candle, lookback, and run.

## 13. Evidence and Walk-Forward Analysis

The analysis population contains only candidates that are:

- admitted rather than rejected duplicates;
- historically liquidity eligible;
- fully labeled from exactly 12 future candles;
- backed by a valid feature snapshot;
- free of required candle-coverage gaps.

Winners are compared with:

- all non-winners combined;
- reversals;
- failed breakouts;
- weak expansions.

Comparisons are produced overall and by timeframe and regime. Boolean features report frequency, absolute difference, and sample size. Continuous features report robust location summaries, standardized effect, and sample size. Missing-data coverage is shown for every feature.

Walk-forward validation uses chronological candidate decision times:

- train: first 6 months;
- validation: next 3 months;
- test: final 3 months.

Feature direction is learned from the train split only. Validation and test report the unchanged feature definition and direction. No threshold or parameter tuning is performed on validation or test. A feature is described as surviving only when its separation direction is consistent across all three splits and each split contains at least 20 winners and 20 failures. Otherwise it is reported as insufficient rather than treated as zero lift. These support thresholds are recorded in configuration and are not optimized.

## 14. Reports

Each report includes campaign bounds, code/schema version, configuration hash, population filters, and sample sizes.

### 14.1 Expansion Anatomy Report

Shows successful expansion counts and pre-expansion feature frequencies/distributions, futures behavior, timeframe and regime breakdowns, major/large classifications, average move, MFE, and missing-data coverage.

### 14.2 Failed Breakout Report

Explains reversals, failed breakouts, and weak expansions separately and combined. It reports the most common differentiating conditions without implying causation.

### 14.3 Feature Separation Report

Ranks transparent winner-versus-failure separation, including successful frequency, failure frequency, difference, sample size, split stability, timeframe, and regime. It does not fit or optimize a trading model.

### 14.4 Leakage Audit Report

Confirms and provides evidence that:

- collection used all trading Linear USDT perpetuals without current-turnover qualification;
- historical liquidity used only 15-minute candles ending by the decision time;
- feature input maximum timestamps never exceeded the decision time;
- OI/funding input timestamps never exceeded the decision time;
- label windows started after the breakout candle and contained at most 12 candles;
- future-mutation and future-removal causality tests passed;
- excluded-liquidity, incomplete, missing-data, and duplicate populations are accounted for.

### 14.5 Candidate Population Report

Shows the complete selection funnel:

- raw breakout signals;
- admitted candidates;
- rejected duplicates;
- candidates with complete/incomplete liquidity windows;
- historically eligible and excluded candidates;
- pending and fully labeled candidates;
- winners, reversals, failed breakouts, and weak expansions;
- exclusions by explicit reason, timeframe, symbol, and regime.

## 15. Automated Verification

### 15.1 Collection tests

- newest-first kline pagination retrieves complete ranges without gaps;
- funding pagination retrieves complete bounded ranges;
- OI follows cursors and resumes safely;
- retryable HTTP and Bybit errors back off and retry;
- page replay creates no duplicates;
- restart resumes the persisted checkpoint;
- incomplete live candles are excluded.

### 15.2 Candidate and label tests

- structure high excludes the breakout candle;
- changing `structure_lookback` changes only the declared definition;
- the first breakout is admitted and overlapping signals are retained as rejected duplicates;
- outcome precedence is winner, reversal, failed breakout, then weak expansion;
- MFE, MAE, extension, giveback, and final position are reproducible;
- incomplete outcome windows remain pending.

### 15.3 Required causality tests

1. **Future-candle mutation:** alter candles after the breakout. Candidate facts, liquidity, regime, and features must remain byte-for-byte equivalent; only the outcome may change.
2. **Current/future turnover mutation:** alter current ticker turnover and turnover after the decision time. Historical liquidity values and eligibility must remain unchanged.
3. **Future removal:** remove every candle after the breakout. Candidate detection, liquidity, regime, and feature generation must still succeed; the outcome must become or remain pending.

Additional boundary tests assert that each feature source's maximum timestamp is at or before the decision time and each label source timestamp is strictly after it.

## 16. Error Handling and Operations

- A symbol-level collection failure is recorded and does not corrupt completed symbols.
- Analysis does not silently proceed through unexplained candle gaps.
- API response metadata and actionable errors are logged without secrets.
- Run status moves through collection, candidate generation, feature snapshot, labeling, analysis, reporting, completed, or failed stages.
- Each stage is idempotent and restartable.
- Reports are written atomically and linked to their database artifact rows.

The first campaign preflight verifies database connectivity, schema version, Bybit connectivity, available disk space, configuration, and clock sanity. It then collects data, audits coverage, generates candidates and snapshots, labels complete windows, runs evidence and walk-forward analysis, and writes all five reports.

## 17. Acceptance Criteria

Implementation is ready for the first campaign when:

- the full deterministic test suite passes, including all three required leakage tests;
- official Bybit response fixtures prove correct pagination and parsing;
- all currently trading Linear USDT perpetuals are present in the collection manifest;
- current turnover has no path to historical eligibility;
- all admitted and rejected duplicate signals are accounted for;
- every analyzed candidate has complete point-in-time liquidity, feature, regime, and outcome provenance;
- the 6/3/3-month split is chronological and untouched by optimization;
- all five required reports are generated from persisted evidence;
- no execution, leverage, stop-loss, or strategy logic exists.

The research campaign itself is complete only after coverage and leakage audits pass and the reports contain enough eligible observations to support their claims. If sample sizes are inadequate, the correct result is an explicit insufficiency finding, not relaxed filters or tuned parameters.
