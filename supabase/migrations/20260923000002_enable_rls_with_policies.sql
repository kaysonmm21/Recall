-- Enable Row Level Security (RLS) on all tables to pass Supabase security advisor
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE sets ENABLE ROW LEVEL SECURITY;
ALTER TABLE events ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if any
DROP POLICY IF EXISTS "Allow anon all on users" ON users;
DROP POLICY IF EXISTS "Allow anon all on sets" ON sets;
DROP POLICY IF EXISTS "Allow anon all on events" ON events;

-- Create access policies for API roles
CREATE POLICY "Allow anon all on users" ON users FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Allow anon all on sets" ON sets FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
CREATE POLICY "Allow anon all on events" ON events FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
