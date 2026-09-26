"""Account storage behavior without a live Supabase project."""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from server import APIError, Application
from storage import Store, SupabaseConnection


ACCOUNT_A = '11111111-1111-4111-8111-111111111111'
ACCOUNT_B = '22222222-2222-4222-8222-222222222222'


class SupabaseBackendTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'SUPABASE_URL': 'https://example.supabase.co',
            'SUPABASE_PUBLISHABLE_KEY': 'publishable-test-key',
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_supabase_auth_verifies_token_and_rejects_invalid_token(self):
        store = Store()
        self.assertIsNone(store.user_for_token(''))
        self.assertIsNone(store.user_for_token('x' * 4097))

        def urlopen(req, timeout):
            self.assertEqual(req.full_url, 'https://example.supabase.co/auth/v1/user')
            self.assertEqual(req.get_header('Authorization'), 'Bearer valid-token')
            return io.BytesIO(json.dumps({'id': ACCOUNT_A}).encode())

        with patch('storage.urllib.request.urlopen', side_effect=urlopen):
            self.assertEqual(store.user_for_token('valid-token'), ACCOUNT_A)
        unauthorized = urllib.error.HTTPError('https://example.supabase.co/auth/v1/user', 401,
                                               'Unauthorized', {}, io.BytesIO(b''))
        with patch('storage.urllib.request.urlopen', side_effect=unauthorized):
            self.assertIsNone(store.user_for_token('bad-token'))

    def test_account_queries_use_verified_owner_and_user_token(self):
        app = Application()
        calls = []

        def request(conn, method, path, payload=None, prefer=None):
            calls.append((conn.token, method, path, payload))
            return []

        with patch.object(Store, 'user_for_token', side_effect=lambda t: ACCOUNT_A if t == 'token-a' else ACCOUNT_B if t == 'token-b' else None), \
             patch.object(SupabaseConnection, '_req', request):
            self.assertEqual(app.request('GET', '/api/sets', 'token-a'), {'sets': []})
            self.assertEqual(app.request('GET', '/api/sets', 'token-b'), {'sets': []})
            with self.assertRaises(APIError) as error:
                app.request('GET', '/api/sets', 'invalid-token')
            self.assertEqual(error.exception.status, 401)
            with self.assertRaises(APIError) as error:
                app.request('GET', '/api/sets/someone-elses-set', 'token-b')
            self.assertEqual(error.exception.status, 404)

        self.assertEqual([call[0] for call in calls], ['token-a', 'token-b', 'token-b'])
        self.assertIn('owner_id=eq.' + ACCOUNT_A, calls[0][2])
        self.assertIn('owner_id=eq.' + ACCOUNT_B, calls[1][2])
        self.assertIn('owner_id=eq.' + ACCOUNT_B, calls[2][2])

    def test_document_and_events_commit_together_with_revision(self):
        store = Store()
        conn = SupabaseConnection(store.supabase_url, store.supabase_key, 'token-a')
        calls = []

        def request(method, path, payload=None, prefer=None):
            calls.append((method, path, payload))
            if path == 'rpc/save_set_document':
                return True
            return []

        conn._req = request
        conn.versions['set-id'] = 4
        store.event(conn, ACCOUNT_A, 'set-id', 'attempt', {'answer': 'clear'}, 123.0)
        self.assertEqual(calls, [])
        conn.execute('DELETE FROM events WHERE set_id=? AND owner=?', ('set-id', ACCOUNT_A))
        store.event(conn, ACCOUNT_A, 'set-id', 'attempt', {'answer': 'new'}, 124.0)
        store.save(conn, ACCOUNT_A, {'id': 'set-id', 'states': {'card': 'learned'}})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 'rpc/save_set_document')
        self.assertEqual(calls[0][2]['expected_version'], 4)
        self.assertTrue(calls[0][2]['clear_events'])
        self.assertEqual(len(calls[0][2]['pending_events']), 1)
        self.assertEqual(conn.versions['set-id'], 5)

    def test_stale_revision_returns_conflict_instead_of_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = Application(Path(directory) / 'source.db')
            key = legacy.request('POST', '/api/identity')['key']
            document = legacy.request('POST', '/api/sets', key,
                                      {'title': 'Original', 'cards': [
                                          {'term': 'lucid', 'definition': 'clear'}]})
            with legacy.store.transaction() as connection:
                row = connection.execute('SELECT document FROM sets WHERE id=?',
                                         (document['id'],)).fetchone()
                document = json.loads(row['document'])
            app = Application()
            calls = []

            def request(conn, method, path, payload=None, prefer=None):
                calls.append((method, path, payload))
                if method == 'GET' and path.startswith('sets?id='):
                    return [{'document': document, 'version': 7}]
                if path == 'rpc/save_set_document':
                    return False
                raise AssertionError((method, path))

            with patch.object(Store, 'user_for_token', return_value=ACCOUNT_A), \
                 patch.object(SupabaseConnection, '_req', request):
                with self.assertRaises(APIError) as error:
                    app.request('PUT', '/api/sets/' + document['id'], 'token-a',
                                {'title': 'Stale edit'})
            self.assertEqual(error.exception.status, 409)
            self.assertEqual(calls[-1][2]['expected_version'], 7)


if __name__ == '__main__':
    unittest.main()
