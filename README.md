# Recall — adaptive vocabulary practice

A web app with a Python standard-library study engine, Supabase Auth and database storage, and a plain HTML/CSS/JavaScript interface. No package installation or AI API is required.

## Supabase setup

1. Create a Supabase project. Enable Email authentication in **Authentication → Providers**. Set the site URL and allowed redirect URLs to your app's origin so confirmation and password reset links return to Recall.
2. In the SQL Editor, apply the files in `supabase/migrations/` in timestamp order for a new project. If the old recall tables already exist, apply only `20260926000000_account_auth.sql`. It removes the old anonymous access policies, adds account ownership policies, and installs the atomic-save function. `supabase_schema.sql` is only an index to these migrations.
3. Set `SUPABASE_URL` to the project URL and `SUPABASE_PUBLISHABLE_KEY` to its publishable key. For local use, put them in an ignored `.env.local` file in the project root:

   ```dotenv
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_PUBLISHABLE_KEY=your-publishable-key
   ```

   A legacy anon key also works as `SUPABASE_ANON_KEY`. This project key identifies the Supabase project; it is not a personal recovery key. Do not use a service role or secret key. Set the same variables in Vercel for deployment.
4. Run the server:

```sh
python3 server.py
```

Open http://localhost:8000. Python 3.9 or later is required. `PORT` overrides the server port. A deliberately set `DB_PATH` enables the old SQLite mode for tests only. Without the Supabase variables, account storage cannot start.

Create or edit a set, star cards, then choose Learn. Practice uses adaptive multiple choice, self-assessed flashcards, and written recall. Each direction has its own history. Misses return sooner; progress measures mastery, not cards viewed. Settings, overrides, corrective retyping, and progress are saved on the server.

Create an account, confirm the email if your project requires it, then sign in. Your sets and progress follow your account across devices. New accounts start with an empty library. The browser stores a Supabase session; sets and progress are stored in Supabase. Old recovery-key libraries are not transferred. The existing `learn.sqlite3` file is untouched by this migration and is not used for account saves.

## Verify

```sh
python3 -m unittest discover -s tests -v
```

Tests cover the learning engine and HTTP flows using an explicit temporary SQLite fixture. After applying the Supabase migration, check account creation, saving progress, signing in on another device, and isolation between two accounts. The linked project can be migrated with the Supabase CLI; `.env.local` supplies the local app configuration.

## Scope

The implementation prioritizes the core study loop. The recall estimator is a transparent approximation from the build brief, not Quizlet's proprietary algorithm. Semantic AI grading, media uploads, and dedicated Write/Spell modes are not part of this core release.

Files are separated into the learning engine (`engine.py`), HTTP/storage layer (`server.py`, `storage.py`), browser interface (`web/`), and SQL migrations (`supabase/migrations/`). Shared interfaces are documented in `CONTRACT.md`.
