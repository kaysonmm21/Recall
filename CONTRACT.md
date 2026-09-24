# Implementation contract
Python 3.9+, standard library only. Run `python3 server.py`, localhost:8000. Static files in web/. SQLite durable data. JSON API, errors `{error: string}` with appropriate HTTP status. Identity: X-User-Key bearer recovery key, browser stores it in localStorage; POST /api/identity without key returns `{key}`. All other API requests require key. All paths below prefixed /api.

## Shared shapes
Card: `{id, term, definition, starred: bool, aliases: [], parts: [], explanation?: string, usage?: string, sentence?: string}`. Optional `explanation`, `usage`, `sentence` default to empty strings.
Settings: `{directions: ['term_to_definition'], types: ['multiple_choice','flashcard_self_assessed','written'], starredOnly: false, shuffle: true, prior: 'new', typo: true, oneAnswer: false, retype: true, audio: false, slow: false}`.
Set: `{id,title,description,cards: Card[],settings: Settings,pathStarted: bool,progress: Progress}`.
Progress: `{not_studied: number,still_learning: number,mastered: number,total: number,streak: number}`.
Question: `{token,cardId,direction,type,prompt,choices: [{id,text}],progress}`. No written canonical answer exposed. Flashcard reveal through endpoint.
Feedback: `{attemptId,correct,answer,submitted,overridden,retypeRequired,progress}`.

## API
POST /identity -> {key}. New identity gets a 20-card GRE starter set.
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

## Boundaries
Agent engine owns engine.py + tests/test_engine.py.
Agent backend owns server.py + storage.py + tests/test_api.py.
Agent frontend owns web/*.
Integrator owns CONTRACT.md, tests/test_acceptance.py, tests/fixtures.json, README.md, .gitignore.
No dependencies or network services. No semantic AI. Write/Spell optional deferred. Keep implementation simple. Communicate contract amendments before changing interfaces.
