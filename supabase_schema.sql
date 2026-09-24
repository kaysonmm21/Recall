-- Run this script in the Supabase SQL Editor:
-- https://supabase.com/dashboard/project/_/sql

-- 1. Create users table
CREATE TABLE IF NOT EXISTS users (
    key TEXT PRIMARY KEY,
    created DOUBLE PRECISION NOT NULL
);

-- 2. Create sets table
CREATE TABLE IF NOT EXISTS sets (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL REFERENCES users(key) ON DELETE CASCADE,
    document JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS sets_owner_idx ON sets(owner);

-- 3. Create events table
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    set_id TEXT NOT NULL,
    owner TEXT NOT NULL REFERENCES users(key) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    created DOUBLE PRECISION NOT NULL,
    document JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS events_set_idx ON events(set_id);
