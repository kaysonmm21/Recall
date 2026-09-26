-- This historical migration once granted anonymous access to every row.
-- Keep the version while ensuring a fresh deployment stays private. The
-- account_auth migration adds the narrowly scoped authenticated policies.
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon all on users" ON public.users;
DROP POLICY IF EXISTS "Allow anon all on sets" ON public.sets;
DROP POLICY IF EXISTS "Allow anon all on events" ON public.events;
