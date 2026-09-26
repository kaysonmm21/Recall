"""Persistence and transactional edge cases that complement HTTP acceptance tests."""
import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import APIError, Application


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Application(Path(self.tmp.name) / 'test.db')
        self.key = self.app.request('POST', '/api/identity')['key']
        self.doc = self.call('POST', '/api/sets', {'title': 'Test', 'cards': [
            {'term': 'lucid', 'definition': 'clear'}, {'term': 'brief', 'definition': 'short'}]})
        self.base = '/api/sets/' + self.doc['id']
        self.call('POST', self.base + '/start', {'settings': {'types': ['written'], 'typo': False}})

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, method, path, body=None):
        return self.app.request(method, path, self.key, body)

    def answer(self, question, answer):
        return self.call('POST', self.base + '/answer', {'token': question['token'], 'answer': answer})

    def snapshot(self):
        with self.app.store.transaction() as conn:
            return json.loads(conn.execute('SELECT document FROM sets WHERE id=?', (self.doc['id'],)).fetchone()['document'])

    def correct(self, question):
        return next(c['definition'] for c in self.doc['cards'] if c['id'] == question['cardId'])

    def test_setup_has_no_progress_until_graded(self):
        q = self.call('GET', self.base + '/next')
        self.assertFalse(self.call('GET', self.base)['pathStarted'])
        self.assertEqual(self.snapshot()['states'], {})
        self.answer(q, self.correct(q))
        self.assertTrue(self.call('GET', self.base)['pathStarted'])

    def test_atomic_double_submit(self):
        q = self.call('GET', self.base + '/next')
        def submit():
            try:
                return self.answer(q, self.correct(q))
            except APIError as error:
                return error.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertIn(409, results)
        self.assertEqual(self.snapshot()['metrics']['attempts'], 1)

    def test_token_expiry(self):
        q = self.call('GET', self.base + '/next')
        expires = self.snapshot()['pending']['expiresAt']
        with patch('server.time.time', return_value=expires + 1):
            with self.assertRaises(APIError) as caught:
                self.answer(q, self.correct(q))
            self.assertEqual(caught.exception.status, 409)
            replacement = self.call('GET', self.base + '/next')
        self.assertNotEqual(q['token'], replacement['token'])
        self.assertEqual(self.snapshot()['states'], {})

    def test_edit_invalidates_only_changed_card(self):
        q = self.call('GET', self.base + '/next')
        self.answer(q, self.correct(q))
        doc = self.call('GET', self.base)
        self.call('PUT', self.base, {'title': 'Renamed', 'cards': doc['cards']})
        self.assertEqual(len(self.snapshot()['states']), 1)
        next(c for c in doc['cards'] if c['id'] == q['cardId'])['definition'] = 'a changed meaning'
        self.call('PUT', self.base, {'cards': doc['cards']})
        self.assertEqual(self.snapshot()['states'], {})

    def test_kick_current_word_removes_it_and_its_pending_question(self):
        question = self.call('GET', self.base + '/next')
        result = self.call('POST', self.base + '/kick', {'cardId': question['cardId']})
        self.assertEqual(len(result['cards']), 1)
        self.assertNotIn(question['cardId'], [card['id'] for card in result['cards']])
        self.assertIsNone(self.snapshot()['pending'])
        remaining = self.call('GET', self.base + '/next')
        self.assertNotEqual(remaining['cardId'], question['cardId'])
        empty = self.call('POST', self.base + '/kick', {'cardId': remaining['cardId']})
        self.assertEqual(empty['cards'], [])
        self.assertTrue(self.call('GET', self.base + '/next')['complete'])

    def test_kick_answered_word_clears_its_progress(self):
        question = self.call('GET', self.base + '/next')
        result = self.answer(question, self.correct(question))
        self.assertEqual(result['cardId'], question['cardId'])
        self.call('POST', self.base + '/kick', {'cardId': question['cardId']})
        snapshot = self.snapshot()
        self.assertFalse(any(key.startswith(question['cardId'] + ':') for key in snapshot['states']))
        self.assertFalse(any(key.startswith(question['cardId'] + ':') for key in snapshot['recent']))
        self.assertIsNone(snapshot['lastAttempt'])

    def test_override_preserves_original_audit_and_replays_streak(self):
        q = self.call('GET', self.base + '/next')
        self.answer(q, self.correct(q))
        q = self.call('GET', self.base + '/next')
        miss = self.answer(q, 'wrong')
        fixed = self.call('POST', self.base + '/override', {'attemptId': miss['attemptId']})
        self.assertEqual(fixed['progress']['streak'], 2)
        snap = self.snapshot()
        self.assertEqual(snap['metrics']['attempts'], 2)
        self.assertEqual(snap['metrics']['incorrect'], 0)
        self.assertEqual(snap['metrics']['overrides'], 1)
        with self.app.store.transaction() as conn:
            events = [(r['kind'], json.loads(r['document'])) for r in conn.execute('SELECT kind,document FROM events WHERE set_id=?', (self.doc['id'],))]
        originals = [e for kind, e in events if kind == 'attempt' and e['id'] == miss['attemptId']]
        self.assertFalse(originals[0]['automaticGrade'])
        self.assertFalse(originals[0]['correct'])
        self.assertEqual(sum(kind == 'override' for kind, _ in events), 1)

    def test_retype_blocks_reload_and_does_not_add_mastery(self):
        q = self.call('GET', self.base + '/next')
        miss = self.answer(q, 'wrong')
        with self.assertRaises(APIError) as caught:
            self.call('GET', self.base + '/next')
        self.assertEqual(caught.exception.payload['feedback']['attemptId'], miss['attemptId'])
        self.call('POST', self.base + '/retype', {'attemptId': miss['attemptId'], 'answer': miss['answer']})
        state = next(iter(self.snapshot()['states'].values()))
        self.assertEqual(state['attempts'], 1)
        self.assertEqual(state['correctAttempts'], 0)

    def test_settings_apply_next_question_and_override_keeps_reset(self):
        q = self.call('GET', self.base + '/next')
        self.call('PATCH', self.base + '/settings', {'settings': {'types': ['multiple_choice'], 'retype': False}})
        self.assertEqual(q['token'], self.call('GET', self.base + '/next')['token'])
        miss = self.answer(q, 'wrong')
        self.assertTrue(miss['retypeRequired'])  # Grading uses the question's settings snapshot.
        self.call('PATCH', self.base + '/settings', {'settings': {'types': ['flashcard_self_assessed']}})
        fixed = self.call('POST', self.base + '/override', {'attemptId': miss['attemptId']})
        self.assertTrue(fixed['correct'])
        self.assertEqual(fixed['progress']['streak'], 0)
        self.assertEqual(self.call('GET', self.base + '/next')['type'], 'flashcard_self_assessed')

    def test_unauthorized_keys_and_empty_scope(self):
        with self.assertRaises(APIError) as caught:
            self.app.request('GET', self.base, 'invalid-key')
        self.assertEqual(caught.exception.status, 401)
        with self.assertRaises(APIError):
            self.call('POST', self.base + '/start', {'settings': {'starredOnly': True}})
        with self.assertRaises(APIError):
            self.call('PATCH', self.base + '/settings', {'settings': {'types': []}})


if __name__ == '__main__':
    unittest.main()
