CREATE TABLE IF NOT EXISTS public.users (
    key TEXT PRIMARY KEY,
    created DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS public.sets (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL REFERENCES public.users(key) ON DELETE CASCADE,
    document JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS sets_owner_idx ON public.sets(owner);

CREATE TABLE IF NOT EXISTS public.events (
    id TEXT PRIMARY KEY,
    set_id TEXT NOT NULL,
    owner TEXT NOT NULL REFERENCES public.users(key) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    created DOUBLE PRECISION NOT NULL,
    document JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS events_set_idx ON public.events(set_id);
