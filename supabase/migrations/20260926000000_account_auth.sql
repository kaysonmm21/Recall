-- Run after the existing recall migrations. New records belong to Supabase
-- Auth users. Legacy recovery-key records stay inaccessible.
ALTER TABLE public.sets ADD COLUMN IF NOT EXISTS owner_id uuid REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE public.events ADD COLUMN IF NOT EXISTS owner_id uuid REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE public.sets ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE public.sets ADD COLUMN IF NOT EXISTS version bigint NOT NULL DEFAULT 0;
ALTER TABLE public.sets ALTER COLUMN owner DROP NOT NULL;
ALTER TABLE public.events ALTER COLUMN owner DROP NOT NULL;
CREATE INDEX IF NOT EXISTS sets_owner_id_idx ON public.sets(owner_id);
CREATE INDEX IF NOT EXISTS events_owner_id_idx ON public.events(owner_id);

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon all on users" ON public.users;
DROP POLICY IF EXISTS "Allow anon all on sets" ON public.sets;
DROP POLICY IF EXISTS "Allow anon all on events" ON public.events;
REVOKE ALL ON public.users FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.sets FROM PUBLIC, anon;
REVOKE ALL ON public.events FROM PUBLIC, anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.sets TO authenticated;
GRANT SELECT, INSERT, DELETE ON public.events TO authenticated;

DROP POLICY IF EXISTS "Account reads sets" ON public.sets;
DROP POLICY IF EXISTS "Account creates sets" ON public.sets;
DROP POLICY IF EXISTS "Account updates sets" ON public.sets;
DROP POLICY IF EXISTS "Account deletes sets" ON public.sets;
DROP POLICY IF EXISTS "Account reads events" ON public.events;
DROP POLICY IF EXISTS "Account creates events" ON public.events;
DROP POLICY IF EXISTS "Account deletes events" ON public.events;
CREATE POLICY "Account reads sets" ON public.sets FOR SELECT TO authenticated
  USING (owner_id = (SELECT auth.uid()));
CREATE POLICY "Account creates sets" ON public.sets FOR INSERT TO authenticated
  WITH CHECK (owner_id = (SELECT auth.uid()) AND owner IS NULL);
CREATE POLICY "Account updates sets" ON public.sets FOR UPDATE TO authenticated
  USING (owner_id = (SELECT auth.uid()))
  WITH CHECK (owner_id = (SELECT auth.uid()) AND owner IS NULL);
CREATE POLICY "Account deletes sets" ON public.sets FOR DELETE TO authenticated
  USING (owner_id = (SELECT auth.uid()));
CREATE POLICY "Account reads events" ON public.events FOR SELECT TO authenticated
  USING (owner_id = (SELECT auth.uid()));
CREATE POLICY "Account creates events" ON public.events FOR INSERT TO authenticated
  WITH CHECK (owner_id = (SELECT auth.uid()) AND owner IS NULL
    AND EXISTS (SELECT 1 FROM public.sets WHERE id = set_id AND owner_id = (SELECT auth.uid())));
CREATE POLICY "Account deletes events" ON public.events FOR DELETE TO authenticated
  USING (owner_id = (SELECT auth.uid()));

-- Compare-and-swap keeps concurrent browser tabs from overwriting progress.
-- Each RPC invocation is one transaction, including its event changes.
CREATE OR REPLACE FUNCTION public.save_set_document(
  target_set_id text,
  expected_version bigint,
  new_document jsonb,
  pending_events jsonb,
  clear_events boolean
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  account_id uuid := auth.uid();
BEGIN
  IF account_id IS NULL THEN
    RETURN false;
  END IF;
  IF pending_events IS NOT NULL AND pg_catalog.jsonb_typeof(pending_events) <> 'array' THEN
    RAISE EXCEPTION 'pending_events must be an array';
  END IF;
  UPDATE public.sets
    SET document = new_document, version = version + 1
    WHERE id = target_set_id AND owner_id = account_id AND version = expected_version;
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  IF COALESCE(clear_events, false) THEN
    DELETE FROM public.events WHERE set_id = target_set_id AND owner_id = account_id;
  END IF;
  INSERT INTO public.events (id, set_id, owner_id, kind, created, document)
    SELECT item->>'id', target_set_id, account_id, item->>'kind',
      (item->>'created')::double precision, item->'document'
    FROM pg_catalog.jsonb_array_elements(COALESCE(pending_events, '[]'::jsonb)) AS item;
  RETURN true;
END;
$$;
REVOKE ALL ON FUNCTION public.save_set_document(text, bigint, jsonb, jsonb, boolean) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.save_set_document(text, bigint, jsonb, jsonb, boolean) TO authenticated;
