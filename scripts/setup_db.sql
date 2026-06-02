CREATE TABLE IF NOT EXISTS risk_events (
    id              SERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL,
    risk_score      FLOAT       NOT NULL,
    vehicle_count   INT,
    pedestrian_count INT,
    min_proximity   FLOAT,
    frame_path      TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_risk_events_timestamp
    ON risk_events(timestamp);
