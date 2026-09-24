# Recall — adaptive vocabulary practice

A small web app with a Python standard-library server, SQLite persistence, and a plain HTML/CSS/JavaScript interface. No package installation or AI API is required.

## Run

```sh
python3 server.py
```

Open http://localhost:8000. Python 3.9 or later is required. `PORT` and `DB_PATH` environment variables override the server port and database location.

Create or edit a set, star cards, then choose Learn. Practice uses adaptive multiple choice, self-assessed flashcards, and written recall. Each direction has its own history. Misses return sooner; progress measures mastery, not cards viewed. Settings, overrides, corrective retyping, and progress are saved on the server.

The starter collection contains 20 GRE vocabulary cards. Your browser stores a private recovery key. Use the same key in another browser connected to this server to resume your collection and progress. Treat this key like a password. This is a local prototype; public deployment requires HTTPS and production hosting/authentication hardening. Separate servers do not synchronize databases.

## Verify

```sh
python3 -m unittest discover -s tests -v
```

Tests cover the learning engine, server API, and end-to-end HTTP flows against a temporary SQLite database.

## Scope

The implementation prioritizes the core study loop. The recall estimator is a transparent approximation from the build brief, not Quizlet's proprietary algorithm. Semantic AI grading, media uploads, and dedicated Write/Spell modes are not part of this core release.

Files are separated into the learning engine (`engine.py`), HTTP/storage layer (`server.py`, `storage.py`), and browser interface (`web/`). Shared interfaces are documented in `CONTRACT.md`.
