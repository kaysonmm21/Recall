-- Retain this migration version for existing projects without disabling RLS
-- during a fresh deployment.
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.events ENABLE ROW LEVEL SECURITY;
