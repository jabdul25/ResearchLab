# Candidate-First Research Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify an auditable candidate-first Bybit research pipeline that measures historically associated decision-time conditions without look-ahead leakage.

**Architecture:** PostgreSQL remains the system of record, while deterministic Python functions detect candidates, freeze point-in-time snapshots, and label future-only outcomes. Collection, decision-time data, and future-derived labels have separate persistence boundaries; a gated CLI generates preflight evidence before it can launch a campaign.

**Tech Stack:** Python 3.11+, pandas, NumPy, Requests, SQLAlchemy, PostgreSQL, PyYAML, python-dotenv, standard-library `unittest`

**Spec:** `docs/superpowers/specs/2026-10-01-candidate-first-research-engine-design.md`

## Global Constraints

- Research all currently trading Bybit `LinearPerpetual` USDT contracts; current turnover never qualifies historical candidates.
- First campaign history is 12 months, shortened only by a contract's launch time.
- Timeframes are exactly 15, 60, and 240 minutes; outcome windows are exactly 12 subsequent candles.
- Expansion thresholds are 8%/15m, 15%/1h, and 25%/4h.
- Default `structure_lookback` is 20 and must support later declared runs of 10, 20, 30, or 50 without optimization.
- Point-in-time liquidity comes from the preceding 24 hours of completed 15-minute candles and requires turnover of at least 500,000 USDT.
- Feature time is the breakout candle close; no later candle, OI, funding, or ticker observation may influence detection, liquidity, features, or regime.
- Outcome precedence is winner, reversal, failed breakout, then weak expansion.
- Use EMA only; do not add SMA, trading, execution, leverage, stop-loss, or strategy logic.
- Describe findings as historically associated, never causal, unless stronger causal evidence is added in a future approved design.
- Do not start the first campaign until tests, leakage tests, preflight, and database provenance all pass.

## Review Focus

- Empty, duplicated, out-of-order, or non-advancing Bybit pages must terminate safely without hiding collection gaps; Task 2 pins this behavior.
- Newly launched contracts and candidates with fewer than 96 preceding 15-minute bars must remain stored with explicit incomplete-history reasons; Tasks 3 and 6 pin this behavior.
- UTC candle-open versus candle-close boundaries must exclude live/future bars at exact interval edges; Tasks 2, 3, and 5 pin this behavior.
- Invalid non-positive prices and negative volume/turnover must fail validation rather than produce infinite features; Tasks 3 through 5 pin this behavior.
- Missing OI/funding must remain null, appear in coverage, and never become zero or `False`; Tasks 5, 7, and 8 pin this behavior.

---

## File Structure

- `src/config.py`: validated configuration and immutable run manifest.
- `src/domain.py`: shared frozen dataclasses and enums for signals, liquidity, and outcomes.
- `src/db.py`: schema initialization, ordered migrations, and checksum provenance.
- `database/migrations/001_candidate_first.sql`: candidate-first schema additions and constraints.
- `src/collectors/bybit.py`: public V5 transport and correct endpoint pagination.
- `src/collectors/checkpoints.py`: restartable collection cursor state.
- `src/analysis/candidates.py`: structure-break detection, duplicate admission, and liquidity snapshots.
- `src/analysis/outcomes.py`: future-only metrics and mutually exclusive labels.
- `src/analysis/features.py`: bounded decision-time feature and regime snapshots.
- `src/persistence/repository.py`: typed PostgreSQL writes/reads for collection and research stages.
- `src/analysis/evidence.py`: matched-population comparisons and population funnel.
- `src/validation/walk_forward.py`: fixed 6/3/3 chronological validation.
- `src/reports/generator.py`: five report renderers and atomic writes.
- `src/pipeline/preflight.py`: environment, coverage, leakage, and provenance gates.
- `src/pipeline/orchestrator.py`: idempotent stage orchestration.
- `run_research.py`: explicit `preflight` and `campaign` commands.
- `tests/`: focused unit, leakage, report, and PostgreSQL integration tests.

### Task 1: Configuration, Domain Types, and Schema Provenance

**Files:**
- Create: `src/domain.py`
- Create: `database/migrations/001_candidate_first.sql`
- Create: `tests/test_config_and_schema.py`
- Modify: `src/config.py`
- Modify: `src/db.py`
- Modify: `config/system.yaml`

**Interfaces:**
- Produces: `CandidateStatus`, `OutcomeClass`, `Regime`, `BreakoutSignal`, `LiquiditySnapshot`, `CandidateOutcome`, `FeatureSnapshot`, and `TimelineSnapshot` frozen types.
- Produces: `Settings.as_manifest() -> dict[str, object]` with every research parameter.
- Produces: `initialize(engine: Engine) -> SchemaProvenance` and `read_schema_provenance(engine: Engine) -> SchemaProvenance`.
- Produces: append-only `research_run_snapshots` with started/terminal lifecycle phases and every required provenance field.

`BreakoutSignal` carries symbol, interval minutes, breakout open time, decision time, structure lookback, breakout level, breakout close, status, optional duplicate key, and optional rejection reason/window. `LiquiditySnapshot` carries decision time, 24-hour volume/turnover, expected/observed bars, eligibility, and reason. `CandidateOutcome` carries the 12-candle bounds/count, classification, threshold/reached flag, MFE, MAE, maximum extension, final position, and giveback. `FeatureSnapshot` carries decision time, regime, feature mapping, seven-horizon timeline mapping, source maximum timestamps, and input fingerprint.

- [ ] **Step 1: Write failing configuration and migration tests**

```python
def test_defaults_match_approved_design():
    assert settings.structure_lookback == 20
    assert settings.outcome_window_candles == 12
    assert settings.minimum_turnover_24h == 500_000
    assert settings.expansion_thresholds == {15: .08, 60: .15, 240: .25}

def test_manifest_is_stable_and_contains_regime_and_support_values():
    assert manifest["features"]["ema_slope_lookback_bars"] == 6
    assert manifest["analysis"]["minimum_winners_per_split"] == 20

def test_migration_checksum_drift_is_rejected():
    assert applying_changed_recorded_migration_raises_checksum_error()

def test_research_run_snapshots_reject_update_and_delete():
    assert immutable_snapshot_mutations_raise_integrity_error()
```

- [ ] **Step 2: Run the tests and confirm the missing interfaces fail**

Run: `venv/bin/python -m unittest -v tests.test_config_and_schema`

Expected: FAIL because domain types, manifest fields, and migration provenance do not exist.

- [ ] **Step 3: Implement validated settings and frozen domain types**

Add the exact configuration values from the spec, reject unsupported timeframes/non-positive windows, and serialize timestamps and mappings deterministically in `Settings.as_manifest()`.

- [ ] **Step 4: Add the candidate-first migration and checksum runner**

Create `schema_migrations`, `collection_jobs`, `breakout_candidates`, `candidate_features`, and `candidate_outcomes`; extend instruments/runs for launch time and manifests. Apply each migration once, record SHA-256, and reject recorded checksum drift.

- [ ] **Step 5: Run Task 1 tests**

Run: `venv/bin/python -m unittest -v tests.test_config_and_schema`

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add config/system.yaml database/migrations/001_candidate_first.sql src/config.py src/db.py src/domain.py tests/test_config_and_schema.py
git commit -m "feat: add candidate research schema provenance"
```

### Task 2: Correct Bybit Collection and Restart Checkpoints

**Files:**
- Create: `src/collectors/checkpoints.py`
- Create: `tests/test_bybit_collection.py`
- Modify: `src/collectors/bybit.py`

**Interfaces:**
- Consumes: `Settings`, schema-backed `collection_jobs`.
- Produces: `BybitPublicClient.kline_pages(symbol, interval, start, end) -> Iterator[MarketPage]`.
- Produces: `funding_pages(...) -> Iterator[MarketPage]` and `open_interest_pages(...) -> Iterator[MarketPage]`.
- Produces: `DataCollector.refresh_universe() -> list[str]` containing every trading Linear USDT perpetual without a turnover filter.
- Produces: `CollectionCheckpointStore` with transactional page/cursor updates.

- [ ] **Step 1: Write failing response-fixture and boundary tests**

```python
def test_kline_pagination_moves_end_before_earliest_returned_bar():
    assert requested_ends == [campaign_end_ms, first_page_earliest_ms - 1]

def test_oi_uses_next_page_cursor_and_stops_on_repeated_cursor():
    assert requested_cursors == [None, "next-1"]
    assert result.stop_reason == "repeated_cursor"

def test_universe_ignores_current_turnover():
    assert set(symbols) == {"HIGHUSDT", "LOWUSDT"}

def test_live_candle_at_campaign_end_is_not_stored():
    assert all(row.close_time <= campaign_end for row in stored)
```

Also cover empty pages, duplicate/out-of-order rows, retryable HTTP/Bybit errors, newest-first funding pages, a contract launch inside the 12-month window, and page replay after restart.

- [ ] **Step 2: Run the collection tests and confirm failure**

Run: `venv/bin/python -m unittest -v tests.test_bybit_collection`

Expected: FAIL because pagination is forward for klines/funding, OI ignores cursors, and universe selection uses current turnover.

- [ ] **Step 3: Implement endpoint-specific backward/cursor pagination**

Normalize every page to ascending unique timestamps before yielding it. Guard against empty and non-advancing pages. Bound collection by `max(history_start, launch_time)` and the last completed candle.

- [ ] **Step 4: Implement transactional checkpoints and idempotent replay**

Persist fixed bounds, endpoint, interval, next cursor/end, counts, gaps, retries, status, and last error in the same transaction as inserted rows.

- [ ] **Step 5: Run Task 2 and existing tests**

Run: `venv/bin/python -m unittest -v tests.test_bybit_collection tests.test_analysis`

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/collectors/bybit.py src/collectors/checkpoints.py tests/test_bybit_collection.py
git commit -m "fix: make bybit collection complete and resumable"
```

### Task 3: Candidate Detection, Duplicate Audit, and Historical Liquidity

**Files:**
- Create: `src/analysis/candidates.py`
- Create: `tests/test_candidates.py`
- Modify: `src/analysis/events.py`

**Interfaces:**
- Consumes: chronological closed-candle frames and `Settings`.
- Produces: `detect_breakout_signals(frame, symbol, interval, settings) -> list[BreakoutSignal]`.
- Produces: `admit_candidates(signals, outcome_window_candles) -> list[BreakoutSignal]` with admitted and rejected-duplicate statuses.
- Produces: `historical_liquidity(candles_15m, decision_time, minimum_turnover) -> LiquiditySnapshot`.

- [ ] **Step 1: Write failing candidate and liquidity tests**

```python
def test_breakout_level_uses_preceding_20_highs_only():
    assert signal.breakout_level == frame.high.iloc[:20].max()
    assert signal.decision_time == frame.open_time.iloc[20] + pd.Timedelta(minutes=15)

def test_overlapping_signal_is_retained_as_rejected_duplicate():
    assert signals[1].status is CandidateStatus.REJECTED_DUPLICATE
    assert signals[1].duplicate_of_key == signals[0].key

def test_liquidity_uses_exactly_96_completed_15m_bars():
    assert snapshot.observed_bars == 96
    assert snapshot.turnover_24h == expected_sum

def test_incomplete_liquidity_is_stored_ineligible():
    assert snapshot.eligible is False
    assert snapshot.reason == "incomplete_liquidity_window"
```

Also test lookbacks 10/20/30/50, exact decision-time boundaries, non-positive prices, negative volume/turnover, and a newly launched contract with insufficient history.

- [ ] **Step 2: Run candidate tests and confirm failure**

Run: `venv/bin/python -m unittest -v tests.test_candidates`

Expected: FAIL because candidate-first detection and historical liquidity do not exist.

- [ ] **Step 3: Implement pure candidate, admission, and liquidity functions**

Use the current candle close against the prior rolling-high window. Preserve every raw signal; assign only the first in each 12-candle window as admitted and reference it from duplicates. Use 15-minute candle close times in `(decision_time - 24h, decision_time]` for liquidity.

- [ ] **Step 4: Retire event-first discovery from the active pipeline**

Keep a documented compatibility wrapper only if an existing caller requires it; the orchestrator must consume candidate-first interfaces.

- [ ] **Step 5: Run Task 3 tests**

Run: `venv/bin/python -m unittest -v tests.test_candidates`

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/analysis/candidates.py src/analysis/events.py tests/test_candidates.py
git commit -m "feat: detect auditable breakout candidates"
```

### Task 4: Future-Only Outcome Labeling

**Files:**
- Create: `src/analysis/outcomes.py`
- Create: `tests/test_outcomes.py`

**Interfaces:**
- Consumes: admitted `BreakoutSignal`, exactly the later candidate-timeframe candles, and timeframe threshold.
- Produces: `label_outcome(signal, future, threshold, settings) -> CandidateOutcome | None`; `None` means pending.

- [ ] **Step 1: Write failing outcome metric and precedence tests**

```python
def test_winner_precedes_reversal_after_threshold_is_reached():
    assert outcome.classification is OutcomeClass.WINNER
    assert outcome.threshold_reached is True

def test_reversal_precedes_failed_breakout():
    assert outcome.classification is OutcomeClass.REVERSAL

def test_subthreshold_close_below_level_is_failed_breakout():
    assert outcome.classification is OutcomeClass.FAILED_BREAKOUT

def test_remaining_subthreshold_candidate_is_weak_expansion():
    assert outcome.classification is OutcomeClass.WEAK_EXPANSION
```

Assert exact MFE, MAE, maximum extension, final position, giveback, 12-candle window bounds, pending behavior for 11 or fewer future candles, and rejection of non-positive future prices.

- [ ] **Step 2: Run outcome tests and confirm failure**

Run: `venv/bin/python -m unittest -v tests.test_outcomes`

Expected: FAIL because the outcome module is absent.

- [ ] **Step 3: Implement `label_outcome` with the approved precedence**

Reject any future frame whose first candle is not strictly after the breakout candle or whose timestamps are non-monotonic. Never read candle 13.

- [ ] **Step 4: Run Task 4 tests**

Run: `venv/bin/python -m unittest -v tests.test_outcomes`

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/analysis/outcomes.py tests/test_outcomes.py
git commit -m "feat: label future breakout outcomes"
```

### Task 5: Frozen Feature and Regime Snapshots with Leakage Tests

**Files:**
- Create: `tests/test_leakage.py`
- Modify: `src/analysis/features.py`
- Modify: `tests/test_analysis.py`

**Interfaces:**
- Consumes: data bounded at `decision_time`, `BreakoutSignal`, and `LiquiditySnapshot`.
- Produces: `extract_candidate_features(trigger, four_h, decision_time, liquidity, oi=None, funding=None, settings=None) -> FeatureSnapshot`.
- Produces: `reconstruct_timeline(trigger, four_h, decision_time, oi=None, funding=None, settings=None) -> dict[str, TimelineSnapshot]` for T-72h, T-48h, T-24h, T-12h, T-6h, T-3h, and T-1h.
- Produces: `regime_at(four_h, decision_time, settings) -> Regime`.

- [ ] **Step 1: Write the three mandatory failing leakage tests**

```python
def test_future_mutation_changes_only_outcome():
    assert mutated.feature_snapshot == original.feature_snapshot
    assert mutated.liquidity == original.liquidity
    assert mutated.outcome != original.outcome

def test_current_and_future_turnover_cannot_change_historical_eligibility():
    assert mutated.liquidity == original.liquidity

def test_features_work_without_future_data():
    assert snapshot.features
    assert label_outcome(signal, empty_future, threshold, settings) is None
```

Add boundary assertions that candle/OI/funding maximum input timestamps are at or before decision time. Test bullish, bearish, range, high-volatility precedence, insufficient-history `unknown`, invalid prices, and null OI/funding preservation.

Add a timeline test asserting the exact seven horizon keys and that every horizon's recorded maximum input timestamp is at or before its horizon timestamp.

- [ ] **Step 2: Run leakage tests and confirm failure**

Run: `venv/bin/python -m unittest -v tests.test_leakage`

Expected: FAIL because snapshots are not typed/fingerprinted and current feature inputs are not strictly bounded by candle close time.

- [ ] **Step 3: Refactor feature extraction around the decision boundary**

Truncate by candle close time, include the completed breakout candle, use only EMA calculations, record input maximum timestamps, and compute a deterministic fingerprint over bounded inputs and configuration. Reuse the same bounded feature primitives to build the seven historical timeline snapshots.

- [ ] **Step 4: Implement five-way point-in-time regime classification**

Use 90 four-hour bars for normalized ATR percentile, six bars for EMA slopes, and precedence `high_volatility`, `bullish_trend`, `bearish_trend`, `range`, then `unknown` for insufficient history.

- [ ] **Step 5: Run feature, leakage, and existing analysis tests**

Run: `venv/bin/python -m unittest -v tests.test_leakage tests.test_analysis`

Expected: PASS.

- [ ] **Step 6: Commit Task 5**

```bash
git add src/analysis/features.py tests/test_analysis.py tests/test_leakage.py
git commit -m "feat: freeze point-in-time feature snapshots"
```

### Task 6: PostgreSQL Repository and Idempotent Candidate Stages

**Files:**
- Create: `src/persistence/__init__.py`
- Create: `src/persistence/repository.py`
- Create: `tests/test_repository_integration.py`
- Modify: `src/pipeline/orchestrator.py`

**Interfaces:**
- Consumes: Tasks 1-5 domain values and migrated PostgreSQL schema.
- Produces: `ResearchRepository.create_run(manifest, bounds) -> int`.
- Produces: `ResearchRepository.append_run_snapshot(run_id, phase, provenance, counts) -> int` and rejects replacement of an existing phase.
- Produces: `upsert_candidates`, `upsert_feature_snapshot`, `upsert_outcome`, `load_analysis_population`, and `population_funnel`.
- Produces orchestrator stages: `collect`, `detect_candidates`, `snapshot_features`, `label_outcomes`, `analyse`, each restartable by run ID.

- [ ] **Step 1: Write failing PostgreSQL integration tests**

```python
def test_decision_and_outcome_rows_are_separate_and_idempotent(db):
    assert repository.upsert_candidates([candidate]) == 1
    assert repository.upsert_candidates([candidate]) == 0
    assert repository.read_outcome(candidate.key) is None

def test_rejected_duplicate_cannot_receive_features_or_outcome(db):
    with self.assertRaises(IntegrityError):
        repository.upsert_outcome(duplicate_outcome)

def test_analysis_population_excludes_ineligible_pending_and_duplicates(db):
    assert [row.id for row in population] == [eligible_labeled.id]
```

Also test immutable liquidity snapshots, manifest persistence, null OI/funding round trips, and explicit incomplete-liquidity reasons.

Test that a started snapshot survives a simulated failed attempt and a terminal snapshot records final symbol-universe and candidate counts without mutating the started row.

- [ ] **Step 2: Run integration tests and confirm failure**

Run: `TEST_DATABASE_URL="$DATABASE_URL" venv/bin/python -m unittest -v tests.test_repository_integration`

Expected: FAIL because repository interfaces do not exist. If PostgreSQL is unavailable, stop and provision the approved local test database rather than skipping this task.

- [ ] **Step 3: Implement repository methods and database invariants**

Use explicit SQLAlchemy transactions and PostgreSQL upserts. Enforce admitted-only feature/outcome rows and immutable decision-time values with constraints/triggers plus repository checks.

- [ ] **Step 4: Replace event-first orchestration with candidate stages**

Persist admitted and rejected signals first, then liquidity/features, then future-only outcomes. Each stage reports processed, inserted, updated, excluded, pending, and error counts.

- [ ] **Step 5: Run integration and leakage suites**

Run: `TEST_DATABASE_URL="$DATABASE_URL" venv/bin/python -m unittest -v tests.test_repository_integration tests.test_leakage`

Expected: PASS.

- [ ] **Step 6: Commit Task 6**

```bash
git add src/persistence src/pipeline/orchestrator.py tests/test_repository_integration.py
git commit -m "feat: persist candidate research stages"
```

### Task 7: Matched Evidence and Fixed Walk-Forward Validation

**Files:**
- Create: `tests/test_evidence_and_validation.py`
- Modify: `src/analysis/evidence.py`
- Modify: `src/validation/walk_forward.py`
- Modify: `src/strategy/candidate.py`

**Interfaces:**
- Consumes: repository analysis population only.
- Produces: `compare_matched_candidates(frame, cohort=None) -> dict`.
- Produces: `candidate_population(frame, raw_counts) -> dict`.
- Produces: `walk_forward(frame, settings) -> dict` with fixed train/validation/test boundaries and insufficiency states.
- Produces: descriptive `ValidatedMarketState`, always `RESEARCH_ONLY`.

- [ ] **Step 1: Write failing matched-population and chronology tests**

```python
def test_comparison_uses_only_matched_outcome_classes():
    assert result["winner_count"] == 2
    assert result["failure_count"] == 6

def test_each_failure_subtype_and_combined_cohort_is_reported():
    assert set(result["cohorts"]) == {"all_failures", "reversal", "failed_breakout", "weak_expansion"}

def test_feature_direction_is_learned_on_train_only():
    assert result["test"]["definitions"] == result["train"]["definitions"]

def test_small_split_is_insufficient_not_zero_lift():
    assert result["validation"]["status"] == "insufficient_sample"
```

Also cover timeframe/regime slices, boolean frequencies, continuous robust summaries, missing-data coverage, and exactly 6/3/3 chronological months.

- [ ] **Step 2: Run evidence tests and confirm failure**

Run: `venv/bin/python -m unittest -v tests.test_evidence_and_validation`

Expected: FAIL because current evidence uses the old expansion/failure population and treats missing lift as zero.

- [ ] **Step 3: Implement transparent matched-cohort evidence**

Report counts and coverage with every statistic. Rank associations by absolute train-set separation while retaining direction and unchanged definitions in validation/test.

- [ ] **Step 4: Implement fixed walk-forward support gates**

Require at least 20 winners and 20 failures per split for a surviving-feature claim. Never tune parameters or directions on validation/test.

- [ ] **Step 5: Run Task 7 tests**

Run: `venv/bin/python -m unittest -v tests.test_evidence_and_validation`

Expected: PASS.

- [ ] **Step 6: Commit Task 7**

```bash
git add src/analysis/evidence.py src/validation/walk_forward.py src/strategy/candidate.py tests/test_evidence_and_validation.py
git commit -m "feat: compare matched breakout outcomes"
```

### Task 8: Five Auditable Reports

**Files:**
- Create: `tests/test_reports.py`
- Modify: `src/reports/generator.py`
- Modify: `src/reports/final_report.py`

**Interfaces:**
- Consumes: run manifest, provenance, coverage, population funnel, evidence, regimes, validation, and leakage results.
- Produces: `write_reports(...) -> list[Path]` containing exactly the five approved Markdown reports plus machine-readable JSON.

- [ ] **Step 1: Write failing report-content tests**

```python
def test_all_five_reports_are_generated(tmp_path):
    assert names == {
        "01_expansion_anatomy.md", "02_failed_breakouts.md",
        "03_feature_separation.md", "04_leakage_audit.md",
        "05_candidate_population.md", "research_results.json",
    }

def test_population_report_accounts_for_every_raw_signal():
    assert raw_signals == admitted + rejected_duplicates

def test_missing_oi_is_reported_as_missing_not_zero():
    assert "OI coverage: 0/" in report
    assert "OI expansion: 0.0%" not in report
```

Assert winner/reversal/failed/weak counts, eligible/excluded counts, feature frequencies and differences, configuration/schema/code identifiers, split stability, and explicit historically-associated wording.

Assert that the Expansion Anatomy Report renders all seven pre-breakout timeline horizons and reports missing horizon coverage explicitly.

- [ ] **Step 2: Run report tests and confirm failure**

Run: `venv/bin/python -m unittest -v tests.test_reports`

Expected: FAIL because current reporting emits four legacy reports without population/provenance/leakage evidence.

- [ ] **Step 3: Implement five focused renderers and atomic writes**

Write temporary files in the report directory and rename after successful rendering. Store the same evidence payload in `research_artifacts` so Markdown is reproducible.

- [ ] **Step 4: Run Task 8 tests**

Run: `venv/bin/python -m unittest -v tests.test_reports`

Expected: PASS.

- [ ] **Step 5: Commit Task 8**

```bash
git add src/reports/generator.py src/reports/final_report.py tests/test_reports.py
git commit -m "feat: generate candidate research evidence reports"
```

### Task 9: Preflight Gate, Provenance Verification, and Explicit CLI

**Files:**
- Create: `src/pipeline/preflight.py`
- Create: `tests/test_preflight.py`
- Modify: `run_research.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `run_preflight(engine, settings, project_root) -> PreflightResult`.
- Produces CLI: `python run_research.py preflight` and `python run_research.py campaign --preflight-id ID`.
- Campaign consumes a successful, matching preflight ID; mismatched configuration/schema/code provenance is rejected.

- [ ] **Step 1: Write failing preflight and campaign-gate tests**

```python
def test_preflight_fails_on_schema_checksum_mismatch():
    assert result.passed is False
    assert "schema_provenance" in result.failures

def test_campaign_rejects_missing_or_stale_preflight():
    with self.assertRaises(PreflightRequired):
        launch_campaign(preflight_id=None)

def test_successful_preflight_writes_markdown_and_json():
    assert report_path.name == "00_preflight.md"
    assert payload["database"]["migration_checksums_verified"] is True
```

Also cover Bybit reachability, UTC clock sanity, 50 GB minimum free disk, database connectivity, required tables, configured 12-month target bounds, absence of conflicting active collection jobs, leakage invariant evidence, configuration hash, and project-scoped code revision/dirty-state provenance. Historical coverage is a campaign-stage audit and is not falsely claimed by preflight before collection.

Assert that the campaign gate creates an immutable started snapshot before stage execution and always appends either a completed or failed terminal snapshot with Git commit, code state/fingerprint, configuration hash, schema checksums, database identity, campaign bounds, universe/candidate counts, and execution timestamp.

- [ ] **Step 2: Run preflight tests and confirm failure**

Run: `venv/bin/python -m unittest -v tests.test_preflight`

Expected: FAIL because no preflight gate or explicit CLI commands exist.

- [ ] **Step 3: Implement preflight result, reports, and provenance matching**

Preflight writes `reports/00_preflight.md` and JSON, persists its result, and fails closed. Campaign requires the exact passing preflight configuration hash, schema checksums, code revision/dirty fingerprint, database identity, and campaign bounds.

- [ ] **Step 4: Replace implicit execution with explicit subcommands**

Running without a subcommand prints help and performs no collection. `preflight` never starts collection. `campaign` refuses to run before a matching pass.

- [ ] **Step 5: Run Task 9 tests**

Run: `venv/bin/python -m unittest -v tests.test_preflight`

Expected: PASS.

- [ ] **Step 6: Commit Task 9**

```bash
git add README.md run_research.py src/pipeline/preflight.py tests/test_preflight.py
git commit -m "feat: gate research campaigns on preflight"
```

### Task 10: Full Verification and Preflight Artifact

**Files:**
- Modify only if a verification failure requires a scoped fix: files owned by Tasks 1-9.
- Generate (ignored runtime artifact): `reports/00_preflight.md`, `reports/00_preflight.json`.

**Interfaces:**
- Consumes: the complete implementation and configured PostgreSQL test/research database.
- Produces: passing automated suite, leakage evidence, database provenance, and a preflight artifact. It does not start the campaign.

- [ ] **Step 1: Run the complete deterministic suite**

Run: `TEST_DATABASE_URL="$DATABASE_URL" venv/bin/python -m unittest discover -s tests -v`

Expected: all tests PASS with no skips for leakage, collection, repository, reports, or preflight.

- [ ] **Step 2: Run static and schema checks**

Run: `venv/bin/python -m compileall -q run_research.py src tests`

Run: `git diff --check`

Expected: both exit 0.

- [ ] **Step 3: Verify database provenance directly**

Run the read-only provenance command exposed by `run_research.py preflight --verify-database`; confirm migration names/checksums, database identity, required constraints, and configuration hash all match.

- [ ] **Step 4: Generate the preflight report**

Run: `venv/bin/python run_research.py preflight`

Expected: exit 0, `PASS`, and both preflight report files. Confirm it reports tests/leakage checks, at least 50 GB free, Bybit connectivity, PostgreSQL provenance, and that no campaign was started.

- [ ] **Step 5: Review the campaign gate without crossing it**

Run the CLI's dry gate-check command using the new preflight ID. Confirm it reports `ready` while collection/candidate counts remain unchanged.

- [ ] **Step 6: Commit any scoped verification fixes and documentation**

```bash
git add README.md run_research.py src tests config database
git commit -m "chore: verify candidate research preflight"
```

- [ ] **Step 7: Stop before campaign execution**

Report the preflight ID, test counts, leakage results, schema/config/code provenance, and exact campaign command. Do not invoke the campaign command until the user confirms the acceptance evidence.
