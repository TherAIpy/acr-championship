-- Campionato
CREATE TABLE championship (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    dnf_penalty_sec INTEGER DEFAULT 300,
    points_system INTEGER[] DEFAULT ARRAY[25,18,15,12,10,8,6,4,2,1]
);

-- Rally
CREATE TABLE rally (
    id SERIAL PRIMARY KEY,
    championship_id INTEGER REFERENCES championship(id),
    name TEXT NOT NULL,
    date DATE,
    status TEXT DEFAULT 'draft' CHECK (status IN ('draft','open','closed','archived')),
    discord_webhook_url TEXT,
    discord_message_id TEXT  -- ID del messaggio pinnato per l'edit in place
);

-- Leg
CREATE TABLE leg (
    id SERIAL PRIMARY KEY,
    rally_id INTEGER REFERENCES rally(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    order_index INTEGER NOT NULL
);

-- Stage
CREATE TABLE stage (
    id SERIAL PRIMARY KEY,
    leg_id INTEGER REFERENCES leg(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    order_index INTEGER NOT NULL
);

-- Risultati Stage
CREATE TABLE stage_result (
    id SERIAL PRIMARY KEY,
    stage_id INTEGER REFERENCES stage(id) ON DELETE CASCADE,
    driver_name TEXT NOT NULL,
    time_ms INTEGER NOT NULL,
    penalty_sec REAL DEFAULT 0,
    screenshot_url TEXT,  -- link a Supabase Storage
    created_at TIMESTAMPTZ DEFAULT NOW()
);