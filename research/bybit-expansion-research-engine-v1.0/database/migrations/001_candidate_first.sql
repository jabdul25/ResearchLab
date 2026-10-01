ALTER TABLE instruments ADD COLUMN IF NOT EXISTS launch_time TIMESTAMPTZ;

ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS configuration_manifest JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS configuration_hash TEXT;
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS code_revision_state JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS campaign_start TIMESTAMPTZ;
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS campaign_end TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS collection_jobs (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL REFERENCES instruments(symbol),
    interval_minutes INTEGER,
    requested_start TIMESTAMPTZ NOT NULL,
    requested_end TIMESTAMPTZ NOT NULL,
    next_end TIMESTAMPTZ,
    next_cursor TEXT,
    observed_start TIMESTAMPTZ,
    observed_end TIMESTAMPTZ,
    expected_count BIGINT NOT NULL DEFAULT 0,
    stored_count BIGINT NOT NULL DEFAULT 0,
    gap_count BIGINT NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(source, symbol, interval_minutes, requested_start, requested_end)
);

CREATE TABLE IF NOT EXISTS breakout_candidates (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL REFERENCES instruments(symbol),
    interval_minutes INTEGER NOT NULL CHECK (interval_minutes IN (15,60,240)),
    breakout_open_time TIMESTAMPTZ NOT NULL,
    decision_time TIMESTAMPTZ NOT NULL,
    structure_lookback INTEGER NOT NULL CHECK (structure_lookback > 0),
    breakout_level NUMERIC NOT NULL CHECK (breakout_level > 0),
    breakout_close NUMERIC NOT NULL CHECK (breakout_close > 0),
    candidate_status TEXT NOT NULL CHECK (candidate_status IN ('admitted','rejected_duplicate')),
    duplicate_of_candidate_id BIGINT REFERENCES breakout_candidates(id),
    rejection_reason TEXT,
    duplicate_window_end TIMESTAMPTZ,
    turnover_24h_before_event NUMERIC,
    volume_24h_before_event NUMERIC,
    liquidity_expected_bars INTEGER,
    liquidity_observed_bars INTEGER,
    liquidity_eligible BOOLEAN,
    liquidity_reason TEXT,
    regime TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(run_id,symbol,interval_minutes,breakout_open_time,structure_lookback),
    CHECK (
      (candidate_status='admitted' AND duplicate_of_candidate_id IS NULL)
      OR (candidate_status='rejected_duplicate' AND duplicate_of_candidate_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS breakout_candidates_analysis_idx
ON breakout_candidates(run_id, candidate_status, liquidity_eligible, decision_time);

CREATE TABLE IF NOT EXISTS candidate_features (
    candidate_id BIGINT PRIMARY KEY REFERENCES breakout_candidates(id) ON DELETE CASCADE,
    feature_time TIMESTAMPTZ NOT NULL,
    feature_version TEXT NOT NULL,
    regime TEXT NOT NULL,
    features JSONB NOT NULL,
    timeline JSONB NOT NULL,
    candle_input_max_time TIMESTAMPTZ,
    oi_input_max_time TIMESTAMPTZ,
    funding_input_max_time TIMESTAMPTZ,
    input_fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS candidate_outcomes (
    candidate_id BIGINT PRIMARY KEY REFERENCES breakout_candidates(id) ON DELETE CASCADE,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    observed_candles INTEGER NOT NULL CHECK (observed_candles = 12),
    classification TEXT NOT NULL CHECK (classification IN ('winner','reversal','failed_breakout','weak_expansion')),
    threshold NUMERIC NOT NULL CHECK (threshold > 0),
    threshold_reached BOOLEAN NOT NULL,
    mfe NUMERIC NOT NULL,
    mae NUMERIC NOT NULL,
    maximum_extension NUMERIC NOT NULL,
    final_vs_breakout_level NUMERIC NOT NULL,
    giveback_fraction NUMERIC,
    label_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION enforce_admitted_candidate_child()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM breakout_candidates
        WHERE id = NEW.candidate_id AND candidate_status = 'admitted'
    ) THEN
        RAISE EXCEPTION 'features and outcomes require an admitted candidate';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS candidate_features_admitted_only ON candidate_features;
CREATE TRIGGER candidate_features_admitted_only
BEFORE INSERT OR UPDATE ON candidate_features
FOR EACH ROW EXECUTE FUNCTION enforce_admitted_candidate_child();

DROP TRIGGER IF EXISTS candidate_outcomes_admitted_only ON candidate_outcomes;
CREATE TRIGGER candidate_outcomes_admitted_only
BEFORE INSERT OR UPDATE ON candidate_outcomes
FOR EACH ROW EXECUTE FUNCTION enforce_admitted_candidate_child();

CREATE TABLE IF NOT EXISTS research_run_snapshots (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES research_runs(id) ON DELETE RESTRICT,
    snapshot_phase TEXT NOT NULL CHECK (snapshot_phase IN ('started','completed','failed')),
    git_commit_hash TEXT NOT NULL,
    code_revision_state JSONB NOT NULL,
    configuration_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    schema_checksums JSONB NOT NULL,
    database_identity JSONB NOT NULL,
    campaign_start TIMESTAMPTZ NOT NULL,
    campaign_end TIMESTAMPTZ NOT NULL,
    symbol_universe_count INTEGER NOT NULL CHECK (symbol_universe_count >= 0),
    candidate_counts JSONB NOT NULL,
    execution_timestamp TIMESTAMPTZ NOT NULL,
    UNIQUE(run_id, snapshot_phase)
);

CREATE OR REPLACE FUNCTION reject_research_run_snapshot_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'research_run_snapshots are immutable';
END;
$$;

DROP TRIGGER IF EXISTS research_run_snapshots_immutable ON research_run_snapshots;
CREATE TRIGGER research_run_snapshots_immutable
BEFORE UPDATE OR DELETE ON research_run_snapshots
FOR EACH ROW EXECUTE FUNCTION reject_research_run_snapshot_mutation();
