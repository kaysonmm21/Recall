"""Durable storage layer supporting local SQLite and Supabase PostgREST backend."""
import json
import os
import sqlite3
import urllib.request
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


class SupabaseConnection:
    def __init__(self, url, key):
        self.url = url.rstrip('/')
        self.key = key

    def _req(self, method, path, payload=None, prefer=None):
        url = f"{self.url}/rest/v1/{path}"
        headers = {
            'apikey': self.key,
            'Authorization': f"Bearer {self.key}",
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        if prefer:
            headers['Prefer'] = prefer
        data = json.dumps(payload).encode('utf-8') if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                if not raw:
                    return []
                return json.loads(raw.decode('utf-8'))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode('utf-8', errors='ignore')
            raise RuntimeError(f"Supabase HTTP {e.code}: {err_body}")

    def execute(self, sql, params=()):
        sql_lower = sql.lower()
        if 'insert into users' in sql_lower:
            key, created = params
            self._req('POST', 'users', {'key': key, 'created': created})
            return QueryResult(None)
        if 'select key from users' in sql_lower:
            key = params[0]
            res = self._req('GET', f'users?key=eq.{urllib.parse.quote(key)}&select=key')
            return QueryResult(res)
        if 'select document from sets where owner=?' in sql_lower:
            key = params[0]
            res = self._req('GET', f'sets?owner=eq.{urllib.parse.quote(key)}&select=document')
            rows = []
            for r in res:
                doc_str = r['document'] if isinstance(r['document'], str) else json.dumps(r['document'])
                rows.append({'document': doc_str})
            return QueryResult(rows)
        if 'select document from sets where id=? and owner=?' in sql_lower:
            sid, key = params
            res = self._req('GET', f'sets?id=eq.{urllib.parse.quote(sid)}&owner=eq.{urllib.parse.quote(key)}&select=document')
            if not res:
                return QueryResult(None)
            doc_str = res[0]['document'] if isinstance(res[0]['document'], str) else json.dumps(res[0]['document'])
            return QueryResult({'document': doc_str})
        if 'delete from events' in sql_lower:
            sid, key = params
            self._req('DELETE', f'events?set_id=eq.{urllib.parse.quote(sid)}&owner=eq.{urllib.parse.quote(key)}')
            return QueryResult(None)
        if 'delete from sets' in sql_lower:
            sid, key = params
            self._req('DELETE', f'sets?id=eq.{urllib.parse.quote(sid)}&owner=eq.{urllib.parse.quote(key)}')
            return QueryResult(None)
        raise NotImplementedError(f"Unsupported Supabase query: {sql}")


class Store:
    def __init__(self, path=None):
        self.supabase_url = os.environ.get('SUPABASE_URL')
        self.supabase_key = os.environ.get('SUPABASE_KEY') or os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
        self.is_supabase = bool(self.supabase_url and self.supabase_key)

        if not self.is_supabase:
            default_db = '/tmp/learn.sqlite3' if os.environ.get('VERCEL') else 'learn.sqlite3'
            self.path = str(path or os.environ.get('DB_PATH', default_db))
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
    def transaction(self):
        if self.is_supabase:
            conn = SupabaseConnection(self.supabase_url, self.supabase_key)
            yield conn
        else:
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

    def save(self, conn, key, document):
        if self.is_supabase:
            payload = {'id': document['id'], 'owner': key, 'document': document}
            conn._req('POST', 'sets', payload, prefer='resolution=merge-duplicates')
        else:
            conn.execute('INSERT INTO sets(id, owner, document) VALUES(?,?,?) '
                         'ON CONFLICT(id) DO UPDATE SET document=excluded.document',
                         (document['id'], key, json.dumps(document)))

    def event(self, conn, key, set_id, kind, document, now):
        import uuid
        if self.is_supabase:
            payload = {'id': uuid.uuid4().hex, 'set_id': set_id, 'owner': key, 'kind': kind, 'created': now, 'document': document}
            conn._req('POST', 'events', payload)
        else:
            conn.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',
                         (uuid.uuid4().hex, set_id, key, kind, now, json.dumps(document)))
