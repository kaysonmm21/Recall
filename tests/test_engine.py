import unittest
import engine as e


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.settings = dict(e.DEFAULT_SETTINGS, shuffle=False)
        self.cards = [dict(id=str(i), term='term '+str(i), definition='answer '+str(i), starred=False) for i in range(6)]

    def train(self, count=16):
        s = e.new_state()
        for i in range(count):
            s = e.update_state(s, True, 'written', 1000+i, self.settings)
        return s

    def test_grading(self):
        self.assertTrue(e.grade('  THE quick fox! ', 'quick fox'))
        self.assertTrue(e.grade('aberrnt', 'aberrant'))
        self.assertFalse(e.grade('aberrnt', 'aberrant', exact=True))
        self.assertFalse(e.grade('42', '43'))
        self.assertFalse(e.grade('x + y', 'x - y'))
        self.assertFalse(e.grade('x y', 'x - y'))
        self.assertTrue(e.grade('kind', 'friendly', aliases=['kind']))
        self.assertTrue(e.grade('cheerful', 'happy, cheerful; glad', settings={'oneAnswer': True}))
        self.assertFalse(e.grade('cheerful', 'happy, cheerful; glad', settings={'oneAnswer': False}))
        self.assertTrue(e.grade('blue; red', ['red', 'blue']))
        self.assertFalse(e.grade('red', ['red', 'blue']))
        self.assertFalse(e.grade('', 'a'))

    def test_recall_decay_and_spacing(self):
        s = self.train(5)
        self.assertGreater(e.estimate_recall(s, 1100, 'written'), e.estimate_recall(s, 1000000, 'written'))
        self.assertGreater(e.estimate_recall(s, 1100, 'multiple_choice'), e.estimate_recall(s, 1100, 'written'))
        spaced = dict(s, previousAttemptAt=s['lastAttemptAt']-86400)
        self.assertGreater(e.estimate_recall(spaced, 1100, 'written'), e.estimate_recall(s, 1100, 'written'))

    def test_mastery_and_miss(self):
        s = self.train()
        self.assertEqual(e.status(s, self.settings, 1100), 'mastered')
        miss = e.update_state(s, False, 'written', 1100, self.settings)
        self.assertEqual(s['lastOutcome'], 'correct')
        self.assertEqual(e.status(miss, self.settings, 1100), 'still_learning')
        both = dict(self.settings, directions=['term_to_definition', 'definition_to_term'])
        p = e.progress(self.cards[:1], {'0:term_to_definition': s}, both, 1100)
        self.assertEqual(p['still_learning'], 1)
        self.assertEqual(p['mastered'], 0)
        self.assertEqual(e.status(e.new_state('most'), both, 1100), 'not_studied')

    def test_ladder_and_choices(self):
        plan = e.choose_question(self.cards, {}, self.settings, [], 1100)
        self.assertEqual(plan['type'], 'multiple_choice')
        self.assertEqual(len(plan['choices']), 4)
        self.assertEqual(len(plan['correctIds']), 1)
        s = self.train(11)
        plan = e.choose_question(self.cards[:1], {'0:term_to_definition': s}, self.settings, [], 1100)
        self.assertEqual(plan['type'], 'written')
        plan = e.choose_question(self.cards[:1], {}, dict(self.settings, types=['written']), [], 1100)
        self.assertEqual(plan['type'], 'written')
        long_card = dict(self.cards[0], definition='long ' * 40)
        plan = e.choose_question([long_card], {}, dict(self.settings, types=['written']), [], 1100)
        self.assertEqual(plan['type'], 'flashcard_self_assessed')

    def test_antirepeat_and_miss_priority(self):
        states = {c['id']+':term_to_definition': self.train(3) for c in self.cards}
        states['0:term_to_definition'] = e.update_state(states['0:term_to_definition'], False, 'multiple_choice', 1100, self.settings)
        recent = ['0:term_to_definition', '1:term_to_definition']
        plan = e.choose_question(self.cards, states, self.settings, recent, 1110)
        self.assertNotIn(plan['cardId'], ['0', '1'])
        recent.append(plan['cardId']+':term_to_definition')
        plan = e.choose_question(self.cards, states, self.settings, recent, 1120)
        self.assertEqual(plan['cardId'], '0')
        self.assertIsNone(e.choose_question(self.cards, states, dict(self.settings, starredOnly=True), [], 1100))

    def test_starred_scope_still_has_distractors(self):
        cards = [dict(c, starred=(c['id'] == '0')) for c in self.cards]
        plan = e.choose_question(cards, {}, dict(self.settings, starredOnly=True), [], 1100)
        self.assertEqual(plan['cardId'], '0')
        self.assertEqual(len(plan['choices']), 4)

    def test_select_all(self):
        cards = [dict(self.cards[0], parts=['red', 'blue']), dict(self.cards[1], parts=['green', 'yellow'])]
        plan = e.choose_question(cards, {}, dict(self.settings, types=['multiple_choice', 'select_all']), [], 1100)
        self.assertEqual(plan['type'], 'select_all')
        self.assertEqual(len(plan['correctIds']), 2)
        self.assertEqual(len(plan['choices']), 4)


if __name__ == '__main__':
    unittest.main()
