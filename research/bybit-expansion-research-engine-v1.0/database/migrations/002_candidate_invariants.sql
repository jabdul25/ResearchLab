ALTER TABLE breakout_candidates ADD COLUMN IF NOT EXISTS candidate_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS breakout_candidates_key_idx
ON breakout_candidates(run_id, candidate_key);

CREATE OR REPLACE FUNCTION reject_breakout_candidate_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'breakout candidate decision snapshots are immutable';
END;
$$;

DROP TRIGGER IF EXISTS breakout_candidates_immutable ON breakout_candidates;
CREATE TRIGGER breakout_candidates_immutable
BEFORE UPDATE ON breakout_candidates
FOR EACH ROW EXECUTE FUNCTION reject_breakout_candidate_update();

CREATE OR REPLACE FUNCTION validate_breakout_candidate_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_run BIGINT;
    parent_symbol TEXT;
    parent_interval INTEGER;
    parent_status TEXT;
BEGIN
    IF NEW.candidate_key IS NULL OR NEW.candidate_key = '' THEN
        RAISE EXCEPTION 'candidate_key is required';
    END IF;
    IF NEW.candidate_status = 'rejected_duplicate' THEN
        SELECT run_id,symbol,interval_minutes,candidate_status
        INTO parent_run,parent_symbol,parent_interval,parent_status
        FROM breakout_candidates WHERE id=NEW.duplicate_of_candidate_id;
        IF parent_status IS DISTINCT FROM 'admitted'
           OR parent_run IS DISTINCT FROM NEW.run_id
           OR parent_symbol IS DISTINCT FROM NEW.symbol
           OR parent_interval IS DISTINCT FROM NEW.interval_minutes THEN
            RAISE EXCEPTION 'duplicate candidate must reference an admitted candidate in the same market and run';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS breakout_candidates_valid_insert ON breakout_candidates;
CREATE TRIGGER breakout_candidates_valid_insert
BEFORE INSERT ON breakout_candidates
FOR EACH ROW EXECUTE FUNCTION validate_breakout_candidate_insert();

CREATE OR REPLACE FUNCTION validate_candidate_feature_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    expected_time TIMESTAMPTZ;
    expected_status TEXT;
BEGIN
    SELECT decision_time,candidate_status INTO expected_time,expected_status
    FROM breakout_candidates WHERE id=NEW.candidate_id;
    IF expected_status IS DISTINCT FROM 'admitted' THEN
        RAISE EXCEPTION 'features require an admitted candidate';
    END IF;
    IF NEW.feature_time IS DISTINCT FROM expected_time THEN
        RAISE EXCEPTION 'feature time must equal candidate decision time';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS candidate_features_admitted_only ON candidate_features;
CREATE TRIGGER candidate_features_admitted_only
BEFORE INSERT OR UPDATE ON candidate_features
FOR EACH ROW EXECUTE FUNCTION validate_candidate_feature_insert();

CREATE OR REPLACE FUNCTION validate_candidate_outcome_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    expected_start TIMESTAMPTZ;
    expected_end TIMESTAMPTZ;
    expected_status TEXT;
BEGIN
    SELECT decision_time,
           decision_time + make_interval(mins => interval_minutes * 12),
           candidate_status
    INTO expected_start,expected_end,expected_status
    FROM breakout_candidates WHERE id=NEW.candidate_id;
    IF expected_status IS DISTINCT FROM 'admitted' THEN
        RAISE EXCEPTION 'outcomes require an admitted candidate';
    END IF;
    IF NEW.window_start IS DISTINCT FROM expected_start
       OR NEW.window_end IS DISTINCT FROM expected_end THEN
        RAISE EXCEPTION 'outcome window must be the next 12 candidate-timeframe candles';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS candidate_outcomes_admitted_only ON candidate_outcomes;
CREATE TRIGGER candidate_outcomes_admitted_only
BEFORE INSERT OR UPDATE ON candidate_outcomes
FOR EACH ROW EXECUTE FUNCTION validate_candidate_outcome_insert();
