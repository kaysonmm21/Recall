"""Durable storage layer supporting local SQLite and Supabase PostgREST backend."""
import json
import os
import sqlite3
import urllib.request
import urllib.error
import urllib.parse
from contextlib import contextmanager
from pathlib import Path


class QueryResult:
    def __init__(self, data):
        if data is None:
            self._data = []
        elif isinstance(data, list):
            self._data = data
        else:
            self._data = [data]

    def fetchone(self):
        return self._data[0] if self._data else None

    def __iter__(self):
        return iter(self._data)


class ConcurrentUpdateConflict(Exception):
    """A newer version of a set has already been saved."""


class SupabaseConnection:
    def __init__(self, url, key, token):
        self.url = url.rstrip('/')
        self.key = key
        self.token = token
        self.versions = {}
        self.pending_events = {}
        self.clear_events = set()

    def _req(self, method, path, payload=None, prefer=None):
        url = f"{self.url}/rest/v1/{path}"
        headers = {
            'apikey': self.key,
            'Authorization': f"Bearer {self.token}",
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        if prefer:
            headers['Prefer'] = prefer
        data = json.dumps(payload).encode('utf-8') if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                if not raw:
                    return []
                return json.loads(raw.decode('utf-8'))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode('utf-8', errors='ignore')
            raise RuntimeError(f"Supabase HTTP {e.code}: {err_body}")

    def execute(self, sql, params=()):
        sql_lower = sql.lower()
        if 'select document from sets where owner=?' in sql_lower:
            key = params[0]
            res = self._req('GET', f'sets?owner_id=eq.{urllib.parse.quote(key)}&select=document,version&order=created_at.asc')
            rows = []
            for r in res:
                doc_str = r['document'] if isinstance(r['document'], str) else json.dumps(r['document'])
                rows.append({'document': doc_str})
            return QueryResult(rows)
        if 'select document from sets where id=? and owner=?' in sql_lower:
            sid, key = params
            res = self._req('GET', f'sets?id=eq.{urllib.parse.quote(sid)}&owner_id=eq.{urllib.parse.quote(key)}&select=document,version')
            if not res:
                return QueryResult(None)
            self.versions[sid] = res[0]['version']
            doc_str = res[0]['document'] if isinstance(res[0]['document'], str) else json.dumps(res[0]['document'])
            return QueryResult({'document': doc_str})
        if 'delete from events' in sql_lower:
            sid, key = params
            self.clear_events.add(sid)
            self.pending_events.pop(sid, None)
            return QueryResult(None)
        if 'delete from sets' in sql_lower:
            sid, key = params
            if sid in self.clear_events:
                self._req('DELETE', f'events?set_id=eq.{urllib.parse.quote(sid)}&owner_id=eq.{urllib.parse.quote(key)}')
                self.clear_events.discard(sid)
            self._req('DELETE', f'sets?id=eq.{urllib.parse.quote(sid)}&owner_id=eq.{urllib.parse.quote(key)}')
            return QueryResult(None)
        raise NotImplementedError(f"Unsupported Supabase query: {sql}")


class Store:
    def __init__(self, path=None):
        self.supabase_url = os.environ.get('SUPABASE_URL')
        self.supabase_key = os.environ.get('SUPABASE_PUBLISHABLE_KEY') or os.environ.get('SUPABASE_ANON_KEY')
        # An explicit local database path (including DB_PATH for legacy tests)
        # is the only way to run the former recovery-key storage mode.
        self.is_supabase = bool(self.supabase_url and self.supabase_key and not path and not os.environ.get('DB_PATH'))

        if not self.is_supabase:
            if not path and not os.environ.get('DB_PATH'):
                self.path = None
                return
            self.path = str(path or os.environ['DB_PATH'])
            if self.path != ':memory:':
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            with self.transaction() as conn:
                conn.executescript('''
                    CREATE TABLE IF NOT EXISTS users (key TEXT PRIMARY KEY, created REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS sets (
                        id TEXT PRIMARY KEY, owner TEXT NOT NULL, document TEXT NOT NULL,
                        FOREIGN KEY(owner) REFERENCES users(key)
                    );
                    CREATE INDEX IF NOT EXISTS sets_owner ON sets(owner);
                    CREATE TABLE IF NOT EXISTS events (
                        id TEXT PRIMARY KEY, set_id TEXT NOT NULL, owner TEXT NOT NULL,
                        kind TEXT NOT NULL, created REAL NOT NULL, document TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS events_set ON events(set_id);
                ''')

    @contextmanager
    def transaction(self, token=None):
        if self.is_supabase:
            conn = SupabaseConnection(self.supabase_url, self.supabase_key, token)
            yield conn
        else:
            if not self.path:
                raise RuntimeError('Set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY to use account storage.')
            conn = sqlite3.connect(self.path, timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            try:
                conn.execute('BEGIN IMMEDIATE')
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def user_for_token(self, token):
        if not token or len(token) > 4096:
            return None
        req = urllib.request.Request(
            self.supabase_url.rstrip('/') + '/auth/v1/user',
            headers={'apikey': self.supabase_key, 'Authorization': 'Bearer ' + token})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                user = json.load(response)
            return user.get('id') if isinstance(user, dict) else None
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                return None
            raise

    def save(self, conn, key, document):
        if self.is_supabase:
            sid = document['id']
            if sid in conn.versions:
                saved = conn._req('POST', 'rpc/save_set_document', {
                    'target_set_id': sid, 'expected_version': conn.versions[sid],
                    'new_document': document, 'pending_events': conn.pending_events.get(sid, []),
                    'clear_events': sid in conn.clear_events})
                if not saved:
                    raise ConcurrentUpdateConflict()
                conn.versions[sid] += 1
                conn.pending_events.pop(sid, None)
                conn.clear_events.discard(sid)
            else:
                payload = {'id': sid, 'owner_id': key, 'document': document}
                conn._req('POST', 'sets', payload)
        else:
            conn.execute('INSERT INTO sets(id, owner, document) VALUES(?,?,?) '
                         'ON CONFLICT(id) DO UPDATE SET document=excluded.document',
                         (document['id'], key, json.dumps(document)))

    def event(self, conn, key, set_id, kind, document, now):
        import uuid
        if self.is_supabase:
            payload = {'id': uuid.uuid4().hex, 'set_id': set_id, 'owner_id': key, 'kind': kind, 'created': now, 'document': document}
            conn.pending_events.setdefault(set_id, []).append(payload)
        else:
            conn.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',
                         (uuid.uuid4().hex, set_id, key, kind, now, json.dumps(document)))
