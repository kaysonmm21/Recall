"""Transparent adaptive learning and deterministic grading; no persistence or HTTP."""
import math
import random
import re
import unicodedata

DEFAULT_SETTINGS = {
    'directions': ['term_to_definition'],
    'types': ['multiple_choice', 'flashcard_self_assessed', 'written'],
    'starredOnly': False, 'shuffle': True, 'prior': 'new', 'typo': True,
    'oneAnswer': False, 'retype': True, 'audio': False, 'slow': False,
}
DIFFICULTY = {'multiple_choice': .24, 'flashcard_self_assessed': .08,
              'written': -.16, 'select_all': -.10, 'spell': -.20}


def normalize(text, exact=False):
    text = unicodedata.normalize('NFKC', str(text)).lower().strip()
    if not exact:
        text = ''.join(' ' if unicodedata.category(c).startswith('P') else c for c in text)
    return ' '.join(text.split())


def _distance(a, b):
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        nxt = [i]
        for j, y in enumerate(b, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j-1] + (x != y)))
        row = nxt
    return row[-1]


def grade(answer, canonical, aliases=None, settings=None, exact=False):
    settings = dict(DEFAULT_SETTINGS, **(settings or {}))
    if not isinstance(answer, (str, list)) or answer == '__dont_know__':
        return False
    if isinstance(canonical, list):
        parts = canonical
        if settings['oneAnswer']:
            return any(grade(answer, p, aliases, dict(settings, oneAnswer=False), exact) for p in parts)
        submitted = answer if isinstance(answer, list) else re.split(r'[,;/\n]+', answer)
        # Match each required part once, independent of order.
        remaining = list(parts)
        for piece in submitted:
            found = next((i for i, p in enumerate(remaining)
                          if grade(piece, p, None, dict(settings, oneAnswer=False), exact)), None)
            if found is None:
                return False
            remaining.pop(found)
        return not remaining
    if isinstance(answer, list):
        return False
    exact = exact or bool(settings.get('exact') or settings.get('language')) or bool(re.search(r'[\d=+*<>^{}\\]|\b(?:return|def|SELECT)\b|\b[a-zA-Z]\s*[-/]\s*[a-zA-Z]\b', canonical))
    variants = [canonical] + list(aliases or [])
    if settings['oneAnswer']:
        variants += [p.strip() for p in re.split(r'[,;/]', canonical) if p.strip()]
    submitted = normalize(answer, exact)
    if not submitted:
        return False
    for variant in variants:
        expected = normalize(variant, exact)
        if submitted == expected:
            return True
        if settings['typo'] and not exact:
            strip_articles = lambda s: ' '.join(w for w in s.split() if w not in ('a', 'an', 'the'))
            if strip_articles(submitted) and strip_articles(submitted) == strip_articles(expected):
                return True
            if abs(len(submitted) - len(expected)) <= max(1, int(.15 * len(expected))):
                if _distance(submitted, expected) <= max(1, int(.15 * len(expected))):
                    return True
    return False


def new_state(prior='new'):
    return dict(attempts=0, correctAttempts=0, incorrectAttempts=0,
                consecutiveCorrect=0, firstTryCorrectCount=0, lastOutcome=None,
                lastAttemptAt=None, previousAttemptAt=None, lastQuestionType=None,
                initialPrior=prior)


def estimate_recall(state, now, question_type):
    s = state or new_state()
    base = {'new': .24, 'some': .42, 'most': .62}.get(s.get('initialPrior'), .24)
    last, previous = s.get('lastAttemptAt'), s.get('previousAttemptAt')
    hours = max(0, (now-last)/3600) if last is not None else 0
    gap = max(0, (last-previous)/3600) if last is not None and previous is not None else 0
    signal = (.22*s.get('consecutiveCorrect', 0) + .04*min(6, s.get('correctAttempts', 0))
              - .30*min(4, s.get('incorrectAttempts', 0))
              + {'correct': .18, 'incorrect': -.25}.get(s.get('lastOutcome'), 0))
    z = math.log(base/(1-base)) + signal - .11*math.log1p(hours) + .055*math.log1p(gap) + DIFFICULTY.get(question_type, 0)
    return 1/(1+math.exp(-max(-700, min(700, z))))


def update_state(state, correct, question_type, now, settings):
    s = dict(state or new_state(settings.get('prior', 'new')))
    s['attempts'] += 1
    s['correctAttempts' if correct else 'incorrectAttempts'] += 1
    s['consecutiveCorrect'] = s['consecutiveCorrect'] + 1 if correct else 0
    s['firstTryCorrectCount'] = s.get('firstTryCorrectCount', 0) + int(correct)
    s['lastOutcome'] = 'correct' if correct else 'incorrect'
    s['previousAttemptAt'], s['lastAttemptAt'] = s.get('lastAttemptAt'), now
    s['lastQuestionType'] = question_type
    s['updatedAt'] = now
    s['recallProbability'] = estimate_recall(s, now, question_type)
    return s


def _sides(card, direction):
    return (card['term'], card['definition']) if direction == 'term_to_definition' else (card['definition'], card['term'])


def _parts(card, direction):
    if direction != 'term_to_definition':
        return []
    parts = card.get('parts', [])
    if not parts:
        parts = re.findall(r'^\s*(?:[-*•]|\d+[.)])\s+(.+)$', card.get('definition', ''), re.M)
    return list(dict.fromkeys(p.strip() for p in parts if p.strip()))


def _allowed(card, direction, settings):
    types = list(settings.get('types', DEFAULT_SETTINGS['types']))
    answer = _sides(card, direction)[1] if card else ''
    if len(answer) > 160:
        types = [t for t in types if t not in ('written', 'spell')]
    if 'multiple_choice' not in types or not card or len(_parts(card, direction)) < 2:
        types = [t for t in types if t != 'select_all']
    return types or ['flashcard_self_assessed']


def status(state, settings, now, card=None, direction='term_to_definition'):
    if not state or not state.get('attempts'):
        return 'not_studied'
    hardest = min(_allowed(card, direction, settings), key=lambda t: DIFFICULTY.get(t, 0))
    if (state.get('correctAttempts', 0) >= 2 and state.get('consecutiveCorrect', 0) >= 2
            and state.get('lastOutcome') == 'correct' and estimate_recall(state, now, hardest) >= .85):
        return 'mastered'
    return 'still_learning'


def progress(cards, states, settings, now, streak=0):
    result = dict(not_studied=0, still_learning=0, mastered=0, total=0, streak=streak)
    for card in cards:
        if settings.get('starredOnly') and not card.get('starred'):
            continue
        groups = [status(states.get(str(card['id'])+':'+d), settings, now, card, d)
                  for d in settings.get('directions', DEFAULT_SETTINGS['directions'])]
        group = 'mastered' if all(g == 'mastered' for g in groups) else ('not_studied' if all(g == 'not_studied' for g in groups) else 'still_learning')
        result[group] += 1
        result['total'] += 1
    return result


def _question_type(card, direction, state, settings, now):
    allowed = _allowed(card, direction, settings)
    p = estimate_recall(state, now, 'written')
    if 'written' in allowed and p >= .78:
        return 'written'
    if 'flashcard_self_assessed' in allowed and p >= .56:
        return 'flashcard_self_assessed'
    if 'select_all' in allowed:
        return 'select_all'
    return next((t for t in ('multiple_choice', 'flashcard_self_assessed', 'written') if t in allowed), allowed[0])


def choose_question(cards, states, settings, recent, now):
    settings = dict(DEFAULT_SETTINGS, **settings)
    eligible = [c for c in cards if not settings['starredOnly'] or c.get('starred')]
    candidates = []
    for card in eligible:
        for direction in settings['directions']:
            key = str(card['id']) + ':' + direction
            state = states.get(key) or new_state(settings['prior'])
            if status(state, settings, now, card, direction) == 'mastered':
                continue
            kind = _question_type(card, direction, state, settings, now)
            urgency = 1-estimate_recall(state, now, kind) + (.18 if state['lastOutcome'] == 'incorrect' else 0)
            other = 'definition_to_term' if direction == 'term_to_definition' else 'term_to_definition'
            other_state = states.get(str(card['id'])+':'+other)
            if other_state and other_state.get('correctAttempts'):
                urgency -= .02
            candidates.append((urgency, key, card, direction, state, kind))
    if not candidates:
        return None
    pool = [c for c in candidates if c[1] not in recent[-2:]]
    if not pool:
        pool = [c for c in candidates if not recent or c[1] != recent[-1]] or candidates
    unseen = [c for c in pool if not c[4]['attempts']]
    window = recent[-10:]
    introduced = sum(1 for k in window if states.get(k, {}).get('attempts') == 1)
    if unseen and (not window or introduced/max(1, len(window)) < .30):
        pool = unseen
    pool.sort(key=lambda c: -c[0])
    band = [c for c in pool[:5] if pool[0][0]-c[0] <= .10]
    chosen = random.choices(band, weights=[math.exp(4*c[0]) for c in band])[0] if settings['shuffle'] else pool[0]
    _, key, card, direction, state, kind = chosen
    prompt, answer = _sides(card, direction)
    plan = dict(cardId=card['id'], direction=direction, type=kind, prompt=prompt, answer=answer, choices=[])
    if kind in ('multiple_choice', 'select_all'):
        correct = _parts(card, direction) if kind == 'select_all' else [answer]
        values = list(correct)
        seen = {normalize(v) for v in values}
        limit = max(4, len(correct)+2) if kind == 'select_all' else 4
        others = list(cards)
        random.shuffle(others)
        for other_card in others:
            for value in (_parts(other_card, direction) if kind == 'select_all' else [_sides(other_card, direction)[1]]):
                if normalize(value) not in seen:
                    values.append(value)
                    seen.add(normalize(value))
                if len(values) >= limit:
                    break
            if len(values) >= limit:
                break
        random.shuffle(values)
        plan['choices'] = [{'id': str(i), 'text': value} for i, value in enumerate(values)]
        plan['correctIds'] = [c['id'] for c in plan['choices'] if c['text'] in correct]
    return plan
