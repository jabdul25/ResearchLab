CREATE TABLE IF NOT EXISTS preflight_results (
    id BIGSERIAL PRIMARY KEY,
    passed BOOLEAN NOT NULL,
    configuration_hash TEXT NOT NULL,
    schema_checksums JSONB NOT NULL,
    code_revision_state JSONB NOT NULL,
    database_identity JSONB NOT NULL,
    campaign_start TIMESTAMPTZ NOT NULL,
    campaign_end TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION reject_preflight_result_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'preflight_results are immutable';
END;
$$;

DROP TRIGGER IF EXISTS preflight_results_immutable ON preflight_results;
CREATE TRIGGER preflight_results_immutable
BEFORE UPDATE OR DELETE ON preflight_results
FOR EACH ROW EXECUTE FUNCTION reject_preflight_result_mutation();
