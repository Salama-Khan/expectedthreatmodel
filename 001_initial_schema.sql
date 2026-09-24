-- ==============================================================================
-- Phase 1a: Expected Threat (xT) Relational Architecture (FINAL)
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- Block 1: Operational Lineage & External Entities
-- ------------------------------------------------------------------------------

CREATE TABLE ingestion_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_uri TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    provider_schema_version TEXT NOT NULL,
    transformation_version TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    
    -- Fix: NOT NULL and positive constraints
    inserted_count INT NOT NULL DEFAULT 0 CHECK (inserted_count >= 0),
    quarantined_count INT NOT NULL DEFAULT 0 CHECK (quarantined_count >= 0),
    
    -- Fix: Lifecycle check
    CHECK (status != 'completed' OR completed_at IS NOT NULL),
    error_log JSONB
);

CREATE TABLE competitions (
    competition_id INT PRIMARY KEY,
    competition_name TEXT NOT NULL,
    country_name TEXT NOT NULL
);

CREATE TABLE seasons (
    season_id INT PRIMARY KEY,
    competition_id INT NOT NULL REFERENCES competitions(competition_id),
    season_name TEXT NOT NULL
);

CREATE TABLE matches (
    match_id INT PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    season_id INT NOT NULL REFERENCES seasons(season_id),
    match_date DATE NOT NULL,
    kick_off TIME,
    home_score INT CHECK (home_score >= 0),
    away_score INT CHECK (away_score >= 0)
);

CREATE TABLE teams (
    team_id INT PRIMARY KEY,
    team_name TEXT NOT NULL
);

CREATE TABLE players (
    player_id INT PRIMARY KEY,
    player_name TEXT NOT NULL,
    nickname TEXT
);

-- ------------------------------------------------------------------------------
-- Block 2: Contextual Associations
-- ------------------------------------------------------------------------------

CREATE TABLE match_teams (
    match_id INT REFERENCES matches(match_id),
    team_id INT REFERENCES teams(team_id),
    home_or_away TEXT NOT NULL CHECK (home_or_away IN ('home', 'away')),
    PRIMARY KEY (match_id, team_id),
    UNIQUE (match_id, home_or_away)
);

CREATE TABLE match_players (
    match_id INT,
    team_id INT,
    player_id INT REFERENCES players(player_id),
    jersey_number INT CHECK (jersey_number >= 0),
    is_starter BOOLEAN NOT NULL,
    FOREIGN KEY (match_id, team_id) REFERENCES match_teams(match_id, team_id),
    PRIMARY KEY (match_id, team_id, player_id)
);

-- ------------------------------------------------------------------------------
-- Block 3: The Expected Threat Model Registry
-- ------------------------------------------------------------------------------

CREATE TABLE xt_models (
    model_id TEXT,
    model_version TEXT NOT NULL,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    artifact_hash TEXT NOT NULL,
    training_manifest TEXT NOT NULL,
    source_data_hash TEXT NOT NULL,
    grid_columns INT NOT NULL DEFAULT 16 CHECK (grid_columns > 0),
    grid_rows INT NOT NULL DEFAULT 12 CHECK (grid_rows > 0),
    coordinate_convention TEXT NOT NULL,
    solver TEXT NOT NULL,
    tolerance FLOAT NOT NULL CHECK (tolerance > 0),
    iterations INT CHECK (iterations > 0),
    validation_metrics JSONB,
    status TEXT NOT NULL CHECK (status IN ('training', 'ready', 'deprecated', 'invalid')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (model_id, model_version)
);

-- ------------------------------------------------------------------------------
-- Block 4: Observed Facts and Derived Threat
-- ------------------------------------------------------------------------------

CREATE TABLE events (
    event_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id), 
    match_id INT NOT NULL,
    team_id INT NOT NULL,
    
    event_index INT NOT NULL,
    possession_id INT NOT NULL CHECK (possession_id > 0),
    
    period INT NOT NULL CHECK (period >= 1 AND period <= 5),
    minute INT NOT NULL CHECK (minute >= 0),
    
    -- Fix: Changed to FLOAT to retain StatsBomb sub-second precision
    second FLOAT NOT NULL CHECK (second >= 0.0 AND second < 60.0),
    
    type_name TEXT NOT NULL,
    outcome TEXT,
    actor_player_id INT,
    recipient_player_id INT,
    
    location_x FLOAT CHECK (location_x >= 0.0 AND location_x <= 120.0),
    location_y FLOAT CHECK (location_y >= 0.0 AND location_y <= 80.0),
    end_location_x FLOAT CHECK (end_location_x >= 0.0 AND end_location_x <= 120.0),
    end_location_y FLOAT CHECK (end_location_y >= 0.0 AND end_location_y <= 80.0),
    
    FOREIGN KEY (match_id, team_id) REFERENCES match_teams(match_id, team_id),
    FOREIGN KEY (match_id, team_id, actor_player_id) REFERENCES match_players(match_id, team_id, player_id),
    FOREIGN KEY (match_id, team_id, recipient_player_id) REFERENCES match_players(match_id, team_id, player_id),
    
    UNIQUE (match_id, event_index),
    
    CHECK (type_name != 'Pass' OR (actor_player_id IS NOT NULL AND location_x IS NOT NULL AND location_y IS NOT NULL)),
    CHECK (
        type_name != 'Pass' OR outcome IS NOT NULL OR 
        (recipient_player_id IS NOT NULL AND end_location_x IS NOT NULL AND end_location_y IS NOT NULL)
    ),
    CHECK (
        type_name != 'Carry' OR 
        (actor_player_id IS NOT NULL AND location_x IS NOT NULL AND location_y IS NOT NULL 
         AND end_location_x IS NOT NULL AND end_location_y IS NOT NULL)
    )
);

-- Coordinates outside 120x80 never enter events. The row is kept so quarantined_count is explainable.
CREATE TABLE quarantined_events (
    run_id UUID NOT NULL REFERENCES ingestion_runs(run_id),
    event_id UUID NOT NULL,
    match_id INT NOT NULL,
    event_index INT NOT NULL,
    reason TEXT NOT NULL,
    location_x FLOAT,
    location_y FLOAT,
    end_location_x FLOAT,
    end_location_y FLOAT,
    PRIMARY KEY (run_id, event_id)
);

CREATE TABLE event_threat (
    model_id TEXT,
    model_version TEXT,
    event_id UUID REFERENCES events(event_id) ON DELETE CASCADE,
    xt_start FLOAT NOT NULL CHECK (xt_start >= 0.0 AND xt_start <= 1.0),
    xt_end FLOAT NOT NULL CHECK (xt_end >= 0.0 AND xt_end <= 1.0),
    delta_xt FLOAT GENERATED ALWAYS AS (xt_end - xt_start) STORED,
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    FOREIGN KEY (model_id, model_version) REFERENCES xt_models(model_id, model_version) ON DELETE RESTRICT,
    PRIMARY KEY (model_id, model_version, event_id)
);

-- ------------------------------------------------------------------------------
-- Block 5: Query-Driven Indexes
-- ------------------------------------------------------------------------------

-- Foreign Key Indexes to prevent locking
CREATE INDEX idx_matches_run ON matches(run_id);
CREATE INDEX idx_matches_season ON matches(season_id);
CREATE INDEX idx_events_run ON events(run_id);
CREATE INDEX idx_quarantined_events_run ON quarantined_events(run_id);
CREATE INDEX idx_xt_models_run ON xt_models(run_id);

CREATE INDEX idx_events_actor ON events (match_id, team_id, actor_player_id);
CREATE INDEX idx_events_recipient ON events (match_id, team_id, recipient_player_id);

-- Operational lookup
CREATE INDEX idx_event_threat_event_id ON event_threat (event_id);