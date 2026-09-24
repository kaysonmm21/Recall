"""End-to-end contract checks against a real HTTP server and temporary database."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        cls.env = dict(os.environ, PORT=str(cls.port), DB_PATH=os.path.join(cls.tmp.name, 'test.db'))
        cls.launch()

    @classmethod
    def launch(cls):
        cls.process = subprocess.Popen([sys.executable, 'server.py'], cwd=str(ROOT), env=cls.env,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(100):
            if cls.process.poll() is not None:
                raise RuntimeError(cls.process.stderr.read().decode())
            try:
                urllib.request.urlopen('http://127.0.0.1:%s/' % cls.port, timeout=1).close()
                return
            except OSError:
                time.sleep(.05)
        raise RuntimeError('Server failed to start')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.communicate(timeout=5)
        cls.tmp.cleanup()

    def api(self, method, path, data=None, key=None):
        headers = {'Content-Type': 'application/json'}
        if key or getattr(self, 'key', None):
            headers['X-User-Key'] = key or self.key
        request = urllib.request.Request('http://127.0.0.1:%s/api%s' % (self.port, path),
                                         data=json.dumps(data).encode() if data is not None else None,
                                         headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def setUp(self):
        self.key = self.api('POST', '/identity', {})['key']
        self.study = self.api('POST', '/sets', {'title': 'Acceptance vocabulary', 'description': '',
                                              'cards': [{'term': 'laconic', 'definition': 'using few words'},
                                                        {'term': 'lucid', 'definition': 'clear and easy to understand'},
                                                        {'term': 'tenacious', 'definition': 'persistent and determined'},
                                                        {'term': 'ephemeral', 'definition': 'lasting a short time'}]})
        self.base = '/sets/' + self.study['id']

    def start(self, **changes):
        settings = dict(directions=['term_to_definition'], types=['written'], starredOnly=False,
                        shuffle=False, prior='new', typo=False, oneAnswer=False, retype=True,
                        audio=False, slow=False)
        settings.update(changes)
        self.api('POST', self.base + '/start', {'settings': settings})
        return settings

    def answer(self, question, value):
        return self.api('POST', self.base + '/answer', {'token': question['token'], 'answer': value,
                                                      'elapsedMs': 250})

    def test_wrong_answer_retype_and_single_use(self):
        self.start()
        q = self.api('GET', self.base + '/next')
        self.assertNotIn('answer', q)
        feedback = self.answer(q, 'wrong')
        self.assertFalse(feedback['correct'])
        self.assertTrue(feedback['retypeRequired'])
        self.assertEqual(feedback['progress']['streak'], 0)
        with self.assertRaises(urllib.error.HTTPError):
            self.answer(q, 'wrong')
        with self.assertRaises(urllib.error.HTTPError):
            self.api('POST', self.base + '/retype', {'attemptId': feedback['attemptId'], 'answer': 'wrong'})
        self.api('POST', self.base + '/retype', {'attemptId': feedback['attemptId'], 'answer': feedback['answer']})
        self.assertIn('token', self.api('GET', self.base + '/next'))

    def test_override_restores_streak(self):
        self.start()
        q = self.api('GET', self.base + '/next')
        result = self.answer(q, 'wrong')
        fixed = self.api('POST', self.base + '/override', {'attemptId': result['attemptId']})
        self.assertTrue(fixed['correct'])
        self.assertTrue(fixed['overridden'])
        self.assertFalse(fixed['retypeRequired'])
        self.assertEqual(fixed['progress']['streak'], 1)

    def test_both_directions_and_settings_preserve_history(self):
        settings = self.start(directions=['term_to_definition', 'definition_to_term'])
        q = self.api('GET', self.base + '/next')
        card = next(c for c in self.study['cards'] if c['id'] == q['cardId'])
        result = self.answer(q, card['definition'] if q['direction'] == 'term_to_definition' else card['term'])
        self.assertEqual(result['progress']['mastered'], 0)
        self.assertEqual(result['progress']['still_learning'], 1)
        settings['types'] = ['multiple_choice']
        changed = self.api('PATCH', self.base + '/settings', {'settings': settings})
        self.assertEqual(changed['progress']['streak'], 0)
        self.assertEqual(changed['progress']['still_learning'], 1)

    def test_resume_and_identity_isolation(self):
        self.start()
        q = self.api('GET', self.base + '/next')
        self.assertEqual(q['token'], self.api('GET', self.base + '/next')['token'])
        other = self.api('POST', '/identity', {})['key']
        with self.assertRaises(urllib.error.HTTPError):
            self.api('GET', self.base, key=other)
        self.assertEqual(self.study['id'], self.api('GET', self.base, key=self.key)['id'])

    def test_reset_preserves_cards_stars(self):
        card = self.study['cards'][0]
        self.api('POST', self.base + '/star', {'cardId': card['id'], 'starred': True})
        self.start()
        q = self.api('GET', self.base + '/next')
        self.answer(q, '__dont_know__')
        with self.assertRaises(urllib.error.HTTPError):
            self.api('POST', self.base + '/reset', {'confirmed': False})
        self.api('POST', self.base + '/reset', {'confirmed': True})
        current = self.api('GET', self.base)
        self.assertEqual(len(current['cards']), 4)
        self.assertTrue(next(c for c in current['cards'] if c['id'] == card['id'])['starred'])
        self.assertEqual(current['progress']['not_studied'], 4)

    def test_flashcard_requires_reveal(self):
        self.start(types=['flashcard_self_assessed'])
        q = self.api('GET', self.base + '/next')
        with self.assertRaises(urllib.error.HTTPError):
            self.answer(q, 'knew')
        self.assertIn('answer', self.api('POST', self.base + '/reveal', {'token': q['token']}))
        self.assertTrue(self.answer(q, 'knew')['correct'])

    def test_progress_survives_server_restart(self):
        self.start()
        q = self.api('GET', self.base + '/next')
        card = next(c for c in self.study['cards'] if c['id'] == q['cardId'])
        self.answer(q, card['definition'])
        cls = type(self)
        cls.process.terminate()
        cls.process.communicate(timeout=5)
        cls.launch()
        current = self.api('GET', self.base)
        self.assertEqual(current['progress']['still_learning'], 1)
        self.assertEqual(current['progress']['streak'], 1)
        self.assertTrue(current['pathStarted'])

    def test_four_unique_mc_choices(self):
        self.start(types=['multiple_choice'])
        q = self.api('GET', self.base + '/next')
        self.assertEqual(len(q['choices']), 4)
        self.assertEqual(len({c['text'].lower() for c in q['choices']}), 4)
        card = next(c for c in self.study['cards'] if c['id'] == q['cardId'])
        choice = next(c for c in q['choices'] if c['text'] == card['definition'])
        self.assertTrue(self.answer(q, choice['id'])['correct'])


if __name__ == '__main__':
    unittest.main()
