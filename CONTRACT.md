# Implementation contract
Python 3.9+, standard library only. Run `python3 server.py`, localhost:8000. Static files in web/. Production data lives in Supabase. Supabase Auth email/password sessions are held by the browser; API requests carry `Authorization: Bearer <access_token>`. The server checks the token with Supabase Auth and accesses PostgREST with the user's JWT. RLS owns sets and events by `owner_id = auth.uid()`. Tests can use explicit `DB_PATH` SQLite fixtures. JSON errors use `{error: string}` with appropriate HTTP status. All paths below prefixed /api. `SUPABASE_URL` and a publishable/anon project key are required for account storage; no service-role key is used.

## Shared shapes
Card: `{id, term, definition, starred: bool, aliases: [], parts: [], explanation?: string, usage?: string, sentence?: string}`. Optional `explanation`, `usage`, `sentence` default to empty strings.
Settings: `{directions: ['term_to_definition'], types: ['multiple_choice','flashcard_self_assessed','written'], starredOnly: false, shuffle: true, prior: 'new', typo: true, oneAnswer: false, retype: true, audio: false, slow: false}`.
Set: `{id,title,description,cards: Card[],settings: Settings,pathStarted: bool,progress: Progress}`.
Progress: `{not_studied: number,still_learning: number,mastered: number,total: number,streak: number}`.
Question: `{token,cardId,direction,type,prompt,choices: [{id,text}],progress}`. No written canonical answer exposed. Flashcard reveal through endpoint.
Feedback: `{attemptId,correct,answer,submitted,overridden,retypeRequired,progress}`.

## API
GET /config -> {supabaseUrl,publishableKey}. Public browser Auth configuration.
GET /sets -> {sets: [{id,title,description,cardCount,progress}]}.
POST /sets body {title,description,cards:[{term,definition,aliases?,parts?}]} -> Set.
GET /sets/:id -> Set.
PUT /sets/:id body {title,description,cards:[{id?,term,definition,starred?,aliases?,parts?}]} -> Set. Preserve unchanged card IDs/history, invalidate progress of changed card content.
DELETE /sets/:id -> {ok:true}.
POST /sets/:id/star body {cardId,starred} -> {ok:true}.
POST /sets/:id/start body {settings} -> {ok:true}. Validate settings; do not create graded progress until answer.
PATCH /sets/:id/settings body {settings} -> {ok:true,progress}.
GET /sets/:id/next -> Question or {complete:true,progress}. Return existing unanswered question on refresh. Next remains blocked until required retype is done.
POST /sets/:id/reveal body {token} -> {answer} (flashcard only).
POST /sets/:id/answer body {token,answer: string or array,elapsedMs:number} -> Feedback. MC answer is choice ID; flashcard 'knew' or 'missed'; unknown is '__dont_know__'. Server enforces single use and expiry.
POST /sets/:id/override body {attemptId} -> Feedback. Only most recent attempt, once, replay state from before that attempt.
POST /sets/:id/retype body {attemptId,answer} -> {ok:true} or error.
POST /sets/:id/reset body {confirmed:true} -> {ok:true}. Keep cards and stars.

## Engine Python interface (engine.py)
DEFAULT_SETTINGS dict.
normalize(text, exact=False) -> str.
grade(answer, canonical, aliases=None, settings=None, exact=False) -> bool. Handles oneAnswer and typo; exact suppresses typo/punctuation stripping.
estimate_recall(state, now, question_type) -> float. now Unix seconds.
new_state(prior='new') -> dict.
update_state(state, correct, question_type, now, settings) -> NEW dict; no in-place mutation.
status(state, settings, now, card=None, direction='term_to_definition') -> 'not_studied'/'still_learning'/'mastered'.
progress(cards, states, settings, now, streak=0) -> Progress. states keyed `cardId:direction`.
choose_question(cards, states, settings, recent, now) -> plan or None. recent list of keys chronological. plan `{cardId,direction,type,prompt,answer,choices:[{id,text}],correctIds?:[]}`. Exclude mastered candidates, anti-repeat, adaptive priority. Backend strips answer/correctIds. Plan may contain internal fields.
Engine state schema uses camelCase counters: attempts, correctAttempts, incorrectAttempts, consecutiveCorrect, lastOutcome, lastAttemptAt, previousAttemptAt, lastQuestionType, initialPrior. State timestamps Unix seconds. Engine may add fields.

## Storage contract

Supabase migrations are the database source of truth. `sets.version` is an internal revision. `save_set_document(target_set_id, expected_version, new_document, pending_events, clear_events)` updates a set and its events in one transaction only when the revision matches; a false result means the caller must reload and retry or report a conflict. The event records are always assigned the target set and current account by the function. Legacy `users` rows and recovery-key sets are inaccessible to browser roles. Active records use `owner_id`; their old `owner` field is null.
