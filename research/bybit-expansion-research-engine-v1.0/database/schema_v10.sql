-- PostgreSQL schema. All timestamps are UTC; this project stores research only.
CREATE TABLE IF NOT EXISTS instruments (
    symbol TEXT PRIMARY KEY, status TEXT NOT NULL, base_asset TEXT NOT NULL,
    quote_asset TEXT NOT NULL, tick_size NUMERIC, lot_size NUMERIC,
    turnover_24h NUMERIC NOT NULL DEFAULT 0, eligible BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS candles (
    symbol TEXT NOT NULL REFERENCES instruments(symbol),
    interval_minutes INTEGER NOT NULL CHECK (interval_minutes IN (15, 60, 240)),
    open_time TIMESTAMPTZ NOT NULL, open NUMERIC NOT NULL, high NUMERIC NOT NULL,
    low NUMERIC NOT NULL, close NUMERIC NOT NULL, volume NUMERIC NOT NULL,
    turnover NUMERIC NOT NULL, collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, interval_minutes, open_time)
);
CREATE INDEX IF NOT EXISTS candles_lookup_idx ON candles(symbol, interval_minutes, open_time DESC);
CREATE TABLE IF NOT EXISTS open_interest (
    symbol TEXT NOT NULL REFERENCES instruments(symbol), interval_minutes INTEGER NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL, open_interest NUMERIC NOT NULL,
    PRIMARY KEY (symbol, interval_minutes, observed_at)
);
CREATE INDEX IF NOT EXISTS oi_lookup_idx ON open_interest(symbol, observed_at DESC);
CREATE TABLE IF NOT EXISTS funding_rates (
    symbol TEXT NOT NULL REFERENCES instruments(symbol), funding_time TIMESTAMPTZ NOT NULL,
    funding_rate NUMERIC NOT NULL, PRIMARY KEY (symbol, funding_time)
);
CREATE TABLE IF NOT EXISTS expansion_events (
    id BIGSERIAL PRIMARY KEY, symbol TEXT NOT NULL REFERENCES instruments(symbol),
    interval_minutes INTEGER NOT NULL, event_kind TEXT NOT NULL CHECK (event_kind IN ('expansion', 'failure')),
    start_time TIMESTAMPTZ NOT NULL, peak_time TIMESTAMPTZ NOT NULL,
    percentage_move NUMERIC NOT NULL, duration_candles INTEGER NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('normal', 'large', 'major', 'failure')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(symbol, interval_minutes, event_kind, start_time)
);
CREATE INDEX IF NOT EXISTS events_lookup_idx ON expansion_events(interval_minutes, start_time);
CREATE TABLE IF NOT EXISTS event_features (
    event_id BIGINT PRIMARY KEY REFERENCES expansion_events(id) ON DELETE CASCADE,
    regime TEXT NOT NULL, features JSONB NOT NULL, timeline JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research_runs (
    id BIGSERIAL PRIMARY KEY, started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ, status TEXT NOT NULL, summary JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS research_artifacts (
    run_id BIGINT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    artifact_name TEXT NOT NULL, payload JSONB NOT NULL, PRIMARY KEY (run_id, artifact_name)
);
CREATE TABLE IF NOT EXISTS candidate_market_states (
    id BIGSERIAL PRIMARY KEY, run_id BIGINT REFERENCES research_runs(id) ON DELETE SET NULL,
    name TEXT NOT NULL, conditions JSONB NOT NULL, evidence JSONB NOT NULL,
    validation JSONB NOT NULL, status TEXT NOT NULL DEFAULT 'RESEARCH_ONLY',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
