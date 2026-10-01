CREATE OR REPLACE FUNCTION reject_research_row_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'candidate feature and outcome rows are immutable';
END;
$$;

DROP TRIGGER IF EXISTS candidate_features_immutable ON candidate_features;
CREATE TRIGGER candidate_features_immutable
BEFORE UPDATE OR DELETE ON candidate_features
FOR EACH ROW EXECUTE FUNCTION reject_research_row_update();

DROP TRIGGER IF EXISTS candidate_outcomes_immutable ON candidate_outcomes;
CREATE TRIGGER candidate_outcomes_immutable
BEFORE UPDATE OR DELETE ON candidate_outcomes
FOR EACH ROW EXECUTE FUNCTION reject_research_row_update();
