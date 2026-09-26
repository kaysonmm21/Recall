"""Run with python3 server.py. No third-party dependencies."""
import copy
import io
import json
import math
import mimetypes
import os
import re
import secrets
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import engine
from storage import ConcurrentUpdateConflict, Store

ROOT = Path(__file__).resolve().parent
TOKEN_TTL = 30 * 60
DIRECTIONS = {'term_to_definition', 'definition_to_term'}
TYPES = {'multiple_choice', 'flashcard_self_assessed', 'written', 'select_all'}
STARTER = [
    ('abate', 'to become less intense'), ('aberrant', 'departing from what is normal'),
    ('abstain', 'to deliberately refrain from something'), ('admonish', 'to warn or reprimand firmly'),
    ('alacrity', 'cheerful readiness'), ('ambivalent', 'having mixed feelings'),
    ('ameliorate', 'to make better'), ('anomaly', 'something that deviates from the expected'),
    ('antipathy', 'a strong feeling of dislike'), ('apathy', 'lack of interest or concern'),
    ('arduous', 'requiring considerable effort'), ('austere', 'severe or plain in appearance'),
    ('bolster', 'to support or strengthen'), ('capricious', 'given to sudden unpredictable changes'),
    ('cogent', 'clear logical and convincing'), ('conundrum', 'a difficult problem or puzzle'),
    ('ephemeral', 'lasting for a very short time'), ('equivocal', 'ambiguous or open to interpretation'),
    ('pragmatic', 'dealing with things in a practical way'), ('reticent', 'reserved or reluctant to speak'),
]


class APIError(Exception):
    def __init__(self, status, message, **details):
        self.status, self.payload = status, {'error': message, **details}
        super().__init__(message)


def fail(message, status=400, **details):
    raise APIError(status, message, **details)


def text_field(value, field, limit, required=False):
    if not isinstance(value, str) or len(value) > limit:
        fail('%s must be text of at most %s characters.' % (field, limit))
    value = value.strip()
    if required and not value:
        fail('%s is required.' % field)
    return value


def validate_settings(changes, current=None):
    if not isinstance(changes, dict):
        fail('Settings must be an object.')
    settings = copy.deepcopy(current or engine.DEFAULT_SETTINGS)
    if set(changes) - set(engine.DEFAULT_SETTINGS):
        fail('Unknown setting.')
    settings.update(changes)
    for field, valid in [('directions', DIRECTIONS), ('types', TYPES)]:
        values = settings.get(field)
        if not isinstance(values, list) or not values or any(not isinstance(x, str) or x not in valid for x in values):
            fail('Choose at least one valid %s option.' % field)
        settings[field] = list(dict.fromkeys(values))
    if 'select_all' in settings['types'] and 'multiple_choice' not in settings['types']:
        fail('Select all requires multiple choice.')
    if settings.get('prior') not in ('new', 'some', 'most'):
        fail('Invalid familiarity setting.')
    for field in ('starredOnly', 'shuffle', 'typo', 'oneAnswer', 'retype', 'audio', 'slow'):
        if not isinstance(settings.get(field), bool):
            fail('%s must be true or false.' % field)
    return settings


def _card_val(c, k):
    return c.get(k, [] if k in ('aliases', 'parts') else '')


def validate_cards(raw, old=None):
    if not isinstance(raw, list) or len(raw) > 5000:
        fail('Cards must be a list of at most 5000 cards.')
    old = {c['id']: c for c in (old or [])}
    result, seen, changed = [], set(), set()
    for item in raw:
        if not isinstance(item, dict):
            fail('Each card must be an object.')
        cid = item.get('id') or uuid.uuid4().hex
        if not isinstance(cid, str) or (item.get('id') and cid not in old) or cid in seen:
            fail('Invalid or duplicate card ID.')
        seen.add(cid)
        card = {'id': cid, 'term': text_field(item.get('term'), 'Term', 5000, True),
                'definition': text_field(item.get('definition'), 'Definition', 10000, True),
                'starred': item.get('starred', old.get(cid, {}).get('starred', False))}
        for field in ('explanation', 'usage', 'sentence'):
            value = item.get(field, old.get(cid, {}).get(field, ''))
            card[field] = text_field(value or '', field, 20000)
        if not isinstance(card['starred'], bool):
            fail('Starred must be true or false.')
        for field in ('aliases', 'parts'):
            vals = item.get(field, old.get(cid, {}).get(field, []))
            if not isinstance(vals, list) or len(vals) > 100:
                fail('%s must be a list of at most 100 answers.' % field)
            card[field] = [text_field(v, field, 10000, True) for v in vals]
        if cid in old and any(card.get(k) != _card_val(old[cid], k) for k in ('term', 'definition', 'explanation', 'usage', 'sentence', 'aliases', 'parts')):
            changed.add(cid)
        result.append(card)
    return result, changed | (set(old) - seen)


def col_str_to_idx(col_str):
    idx = 0
    for char in col_str.upper():
        if 'A' <= char <= 'Z':
            idx = idx * 26 + (ord(char) - ord('A') + 1)
    return idx - 1 if idx > 0 else 0


def parse_xlsx(file_bytes):
    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
    except Exception:
        fail('Invalid Excel (.xlsx) file.')
    shared_strings = []
    if 'xl/sharedStrings.xml' in zf.namelist():
        try:
            root = ET.fromstring(zf.read('xl/sharedStrings.xml'))
            for si in root.findall('.//{*}si'):
                shared_strings.append(''.join(si.itertext()))
        except Exception:
            pass
    sheet_paths = [name for name in zf.namelist() if name.startswith('xl/worksheets/sheet') and name.endswith('.xml')]
    if not sheet_paths:
        fail('No worksheet found in Excel file.')
    sheet_paths.sort()
    try:
        root = ET.fromstring(zf.read(sheet_paths[0]))
    except Exception:
        fail('Failed to parse worksheet in Excel file.')
    rows = []
    for row_elem in root.findall('.//{*}row'):
        row_data = {}
        max_col = 0
        for cell in row_elem.findall('.//{*}c'):
            r_attr = cell.attrib.get('r', '')
            match = re.match(r'([A-Za-z]+)(\d+)', r_attr)
            col_idx = col_str_to_idx(match.group(1)) if match else len(row_data)
            max_col = max(max_col, col_idx)
            t_attr = cell.attrib.get('t', '')
            val = ''
            if t_attr == 's':
                v_elem = cell.find('.//{*}v')
                if v_elem is not None and v_elem.text is not None and v_elem.text.isdigit():
                    idx = int(v_elem.text)
                    if 0 <= idx < len(shared_strings):
                        val = shared_strings[idx]
            elif t_attr == 'inlineStr':
                is_elem = cell.find('.//{*}is')
                if is_elem is not None:
                    val = ''.join(is_elem.itertext())
            else:
                v_elem = cell.find('.//{*}v')
                if v_elem is not None and v_elem.text is not None:
                    val = v_elem.text
            row_data[col_idx] = val
        if row_data or max_col > 0:
            row_list = [row_data.get(i, '') for i in range(max_col + 1)]
            rows.append(row_list)
    return rows


def parse_csv(file_bytes):
    import csv
    import io
    try:
        text = file_bytes.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = file_bytes.decode('latin-1', errors='replace')
    reader = csv.reader(io.StringIO(text))
    return [row for row in reader]


HEADER_MAP = {
    'word': 'term',
    'words': 'term',
    'term': 'term',
    'terms': 'term',
    'vocabulary': 'term',
    'vocab': 'term',
    'entry': 'term',
    'headword': 'term',

    'definition': 'definition',
    'definitions': 'definition',
    'meaning': 'definition',
    'meanings': 'definition',
    'translation': 'definition',

    'explanation': 'explanation',
    'explanations': 'explanation',
    'notes': 'explanation',
    'note': 'explanation',
    'description': 'explanation',
    'detail': 'explanation',
    'details': 'explanation',

    'usage': 'usage',
    'how it is used': 'usage',
    "how it's used": 'usage',
    'part of speech': 'usage',
    'pos': 'usage',

    'sentence': 'sentence',
    'sentences': 'sentence',
    'example sentence': 'sentence',
    'example sentences': 'sentence',
    'use in a sentence': 'sentence',
    'used in a sentence': 'sentence',
    'example': 'sentence',
    'examples': 'sentence',
    'sample sentence': 'sentence',
}


def parse_imported_rows(rows):
    if not rows:
        fail('The uploaded file is empty.')
    header_idx = -1
    col_map = {}
    for idx, row in enumerate(rows[:20]):
        temp_map = {}
        for i, cell in enumerate(row):
            norm = str(cell or '').strip().lower()
            field = HEADER_MAP.get(norm)
            if field and field not in temp_map:
                temp_map[field] = i
        if 'term' in temp_map and ('definition' in temp_map or 'explanation' in temp_map):
            if 'definition' not in temp_map and 'explanation' in temp_map:
                temp_map['definition'] = temp_map.pop('explanation')
            header_idx = idx
            col_map = temp_map
            break

    if header_idx != -1:
        data_rows = rows[header_idx + 1:]
    else:
        non_empty_rows = [r for r in rows if any(str(c or '').strip() for c in r)]
        if non_empty_rows and len(non_empty_rows[0]) >= 2:
            col_map = {'term': 0, 'definition': 1}
            data_rows = rows
        else:
            fail('Import requires column headers for Word/Term and Definition/Meaning.')

    raw_cards = []
    for row in data_rows:
        term = str(row[col_map['term']] or '').strip() if col_map['term'] < len(row) else ''
        definition = str(row[col_map['definition']] or '').strip() if col_map['definition'] < len(row) else ''
        explanation = str(row[col_map['explanation']] or '').strip() if 'explanation' in col_map and col_map['explanation'] < len(row) else ''
        usage = str(row[col_map['usage']] or '').strip() if 'usage' in col_map and col_map['usage'] < len(row) else ''
        sentence = str(row[col_map['sentence']] or '').strip() if 'sentence' in col_map and col_map['sentence'] < len(row) else ''
        if not term and not definition and not explanation and not usage and not sentence:
            continue
        if not term or not definition:
            continue
        raw_cards.append({
            'term': term,
            'definition': definition,
            'explanation': explanation,
            'usage': usage,
            'sentence': sentence
        })
    if not raw_cards:
        fail('No valid vocabulary rows were found in the file.')
    return raw_cards


def parse_multipart(body, boundary):
    import re
    fields, files = {}, {}
    parts = body.split(b'--' + boundary)
    for part in parts:
        if not part or part == b'--\r\n' or part == b'--' or part == b'--\r\n\r\n':
            continue
        if part.startswith(b'\r\n'):
            part = part[2:]
        if part.endswith(b'\r\n'):
            part = part[:-2]
        if part.endswith(b'--'):
            part = part[:-2]
        if not part:
            continue
        sep = part.find(b'\r\n\r\n')
        if sep == -1:
            sep = part.find(b'\n\n')
            if sep == -1:
                continue
            header_bytes, payload = part[:sep], part[sep+2:]
        else:
            header_bytes, payload = part[:sep], part[sep+4:]
        header_lines = header_bytes.decode('utf-8', errors='ignore').splitlines()
        disp_line = next((l for l in header_lines if l.lower().startswith('content-disposition:')), '')
        if not disp_line:
            continue
        name_match = re.search(r'name="([^"]+)"', disp_line) or re.search(r'name=([^\s;]+)', disp_line)
        filename_match = re.search(r'filename="([^"]+)"', disp_line) or re.search(r'filename=([^\s;]+)', disp_line)
        if name_match:
            name = name_match.group(1)
            if filename_match:
                files[name] = {'filename': filename_match.group(1), 'data': payload}
            else:
                fields[name] = payload.decode('utf-8', errors='ignore')
    return fields, files


def generate_sentence_hint(sentence, term):
    import re
    if not sentence or not sentence.strip():
        return None
    term_str = str(term or '').strip()
    if not term_str:
        return sentence
    pattern = re.compile(r'\b' + re.escape(term_str) + r'\b', re.IGNORECASE)
    if pattern.search(sentence):
        return pattern.sub('______', sentence)
    pattern_flex = re.compile(re.escape(term_str), re.IGNORECASE)
    if pattern_flex.search(sentence):
        return pattern_flex.sub('______', sentence)
    return 'Think of the example sentence for this word.'


def new_set(title, description, cards):
    return {'id': uuid.uuid4().hex, 'title': title, 'description': description,
            'cards': cards, 'settings': copy.deepcopy(engine.DEFAULT_SETTINGS),
            'pathStarted': False, 'states': {}, 'recent': [], 'streak': 0,
            'pending': None, 'lastAttempt': None, 'metrics': {}, 'streakGeneration': 0, 'createdAt': time.time()}


def progress(document, now):
    return engine.progress(document['cards'], document['states'], document['settings'], now, document['streak'])


def public_set(document, now):
    return {k: document[k] for k in ('id', 'title', 'description', 'cards', 'settings', 'pathStarted')} | {'progress': progress(document, now)}


def feedback(document, attempt, now):
    card = next((c for c in document['cards'] if c['id'] == attempt['plan']['cardId']), None)
    card_info = {
        'term': card.get('term', '') if card else '',
        'definition': card.get('definition', '') if card else '',
        'explanation': card.get('explanation', '') if card else '',
        'usage': card.get('usage', '') if card else '',
        'sentence': card.get('sentence', '') if card else ''
    } if card else None
    return {'attemptId': attempt['id'], 'correct': attempt['correct'], 'answer': attempt['plan']['answer'],
            'submitted': attempt['submitted'], 'overridden': attempt.get('overridden', False),
            'retypeRequired': attempt.get('retypeRequired', False), 'progress': progress(document, now),
            'cardInfo': card_info}


class Application:
    def __init__(self, db_path=None):
        self.store = Store(db_path)

    def request(self, method, path, key=None, body=None, files=None):
        body = {} if body is None else body
        if not isinstance(body, dict):
            fail('Request body must be an object.')
        if method == 'GET' and path == '/api/config':
            return {'supabaseUrl': self.store.supabase_url if self.store.is_supabase else None,
                    'publishableKey': self.store.supabase_key if self.store.is_supabase else None}
        now = time.time()
        if self.store.is_supabase:
            user_id = self.store.user_for_token(key)
            if not user_id:
                fail('Sign in to continue.', 401)
            owner = user_id
        else:
            owner = key
        with self.store.transaction(key if self.store.is_supabase else None) as conn:
            if not self.store.is_supabase and method == 'POST' and path == '/api/identity':
                key = secrets.token_urlsafe(32)
                conn.execute('INSERT INTO users VALUES(?,?)', (key, now))
                cards, _ = validate_cards([{'term': t, 'definition': d} for t, d in STARTER])
                self.store.save(conn, key, new_set('GRE essentials', 'Twenty useful words to start practicing.', cards))
                return {'key': key}
            if not self.store.is_supabase and (not key or not conn.execute('SELECT key FROM users WHERE key=?', (key,)).fetchone()):
                fail('A valid recovery key is required.', 401)
            key = owner
            if method == 'POST' and path in ('/api/sets/import', '/api/import'):
                file_info = (files or {}).get('file')
                if not file_info or not file_info.get('data'):
                    fail('An Excel (.xlsx) or CSV file is required.')
                file_bytes = file_info['data']
                filename = (file_info.get('filename') or '').lower()
                if filename.endswith('.xlsx') or file_bytes.startswith(b'PK\x03\x04'):
                    rows = parse_xlsx(file_bytes)
                else:
                    rows = parse_csv(file_bytes)
                raw_cards = parse_imported_rows(rows)
                cards, _ = validate_cards(raw_cards)
                title_val = (body.get('title') or '').strip()
                if not title_val:
                    stem = Path(file_info.get('filename', '')).stem
                    title_val = stem if stem else 'Imported Set'
                title = text_field(title_val, 'Title', 200, True)
                desc_val = (body.get('description') or '').strip()
                if not desc_val:
                    desc_val = 'Imported %d cards.' % len(cards)
                description = text_field(desc_val, 'Description', 2000)
                doc = new_set(title, description, cards)
                self.store.save(conn, key, doc)
                return public_set(doc, now)
            if path == '/api/sets':
                if method == 'GET':
                    docs = [json.loads(r['document']) for r in conn.execute('SELECT document FROM sets WHERE owner=? ORDER BY rowid', (key,))]
                    return {'sets': [{'id': d['id'], 'title': d['title'], 'description': d['description'], 'cardCount': len(d['cards']), 'progress': progress(d, now)} for d in docs]}
                if method == 'POST':
                    cards, _ = validate_cards(body.get('cards', []))
                    doc = new_set(text_field(body.get('title'), 'Title', 200, True), text_field(body.get('description', ''), 'Description', 2000), cards)
                    self.store.save(conn, key, doc)
                    return public_set(doc, now)
            segments = path.strip('/').split('/')
            if len(segments) not in (3, 4) or segments[:2] != ['api', 'sets']:
                fail('Not found.', 404)
            sid = segments[2]
            row = conn.execute('SELECT document FROM sets WHERE id=? AND owner=?', (sid, key)).fetchone()
            if not row:
                fail('Set not found.', 404)
            doc = json.loads(row['document'])
            action = segments[3] if len(segments) == 4 else ''
            result = self.set_request(conn, key, doc, method, action, body, now)
            if method != 'DELETE' and (method != 'GET' or action == 'next'):
                doc['updatedAt'] = now
                try:
                    self.store.save(conn, key, doc)
                except ConcurrentUpdateConflict:
                    fail('This set changed in another session. Reload and try again.', 409)
            return result

    def set_request(self, conn, key, doc, method, action, body, now):
        if not action:
            if method == 'GET':
                return public_set(doc, now)
            if method == 'PUT':
                cards, changed = validate_cards(body.get('cards', doc['cards']), doc['cards'])
                doc['title'] = text_field(body.get('title', doc['title']), 'Title', 200, True)
                doc['description'] = text_field(body.get('description', doc['description']), 'Description', 2000)
                doc['cards'] = cards
                doc['states'] = {k: v for k, v in doc['states'].items() if k.split(':')[0] not in changed}
                doc['recent'] = [k for k in doc['recent'] if k.split(':')[0] not in changed]
                doc['pending'] = None
                if doc.get('lastAttempt') and doc['lastAttempt']['plan']['cardId'] in changed:
                    doc['lastAttempt'] = None
                return public_set(doc, now)
            if method == 'DELETE':
                conn.execute('DELETE FROM events WHERE set_id=? AND owner=?', (doc['id'], key))
                conn.execute('DELETE FROM sets WHERE id=? AND owner=?', (doc['id'], key))
                return {'ok': True}
        if action == 'star' and method == 'POST':
            card = next((c for c in doc['cards'] if c['id'] == body.get('cardId')), None)
            if not card:
                fail('Card not found.', 404)
            if not isinstance(body.get('starred'), bool):
                fail('Starred must be true or false.')
            card['starred'] = body['starred']
            if doc['settings']['starredOnly']:
                doc['pending'] = None
            return {'ok': True}
        if (action == 'start' and method == 'POST') or (action == 'settings' and method == 'PATCH'):
            settings = validate_settings(body.get('settings', {}), doc['settings'])
            if action == 'start' and not any(not settings['starredOnly'] or c['starred'] for c in doc['cards']):
                fail('There are no cards in the selected scope.')
            if settings['types'] != doc['settings']['types']:
                doc['streak'] = 0
                doc['streakGeneration'] = doc.get('streakGeneration', 0) + 1
            doc['settings'] = settings
            if action == 'start':
                doc['pending'] = None
            return {'ok': True, 'progress': progress(doc, now)}
        if action == 'reset' and method == 'POST':
            if body.get('confirmed') is not True:
                fail('Explicit confirmation is required to reset progress.')
            doc.update(states={}, recent=[], streak=0, pending=None, lastAttempt=None, metrics={}, pathStarted=False)
            conn.execute('DELETE FROM events WHERE set_id=? AND owner=?', (doc['id'], key))
            return {'ok': True}
        if action == 'next' and method == 'GET':
            last = doc.get('lastAttempt')
            if last and last.get('retypeRequired'):
                fail('Retype the correct answer before continuing.', 409, feedback=feedback(doc, last, now))
            pending = doc.get('pending')
            if not pending or pending['expiresAt'] <= now:
                plan = engine.choose_question(doc['cards'], doc['states'], doc['settings'], doc['recent'], now)
                if plan is None:
                    doc['pending'] = None
                    return {'complete': True, 'progress': progress(doc, now)}
                pending = {'token': secrets.token_urlsafe(24), 'expiresAt': now + TOKEN_TTL, 'plan': plan, 'revealed': False, 'settings': copy.deepcopy(doc['settings'])}
                doc['pending'] = pending
                metrics = doc['metrics']
                metrics['questionsShown'] = metrics.get('questionsShown', 0) + 1
                self.store.event(conn, key, doc['id'], 'question', pending, now)
            plan = pending['plan']
            result = {k: plan[k] for k in ('cardId', 'direction', 'type', 'prompt')}
            if plan['type'] == 'written':
                card = next(c for c in doc['cards'] if c['id'] == plan['cardId'])
                hint = generate_sentence_hint(card.get('sentence', ''), card.get('term', ''))
                if hint:
                    result['hint'] = hint
            result.update(token=pending['token'], choices=plan.get('choices', []), progress=progress(doc, now))
            return result
        if action in ('answer', 'reveal') and method == 'POST':
            pending = doc.get('pending')
            if not pending or not isinstance(body.get('token'), str) or not secrets.compare_digest(body['token'], pending['token']):
                fail('This question has already been answered or is no longer active.', 409)
            if pending['expiresAt'] <= now:
                fail('This question expired. Load the next question.', 409)
            plan = pending['plan']
            if action == 'reveal':
                if plan['type'] != 'flashcard_self_assessed':
                    fail('Only flashcards can be revealed.')
                pending['revealed'] = True
                card = next(c for c in doc['cards'] if c['id'] == plan['cardId'])
                card_info = {
                    'definition': card.get('definition', ''),
                    'explanation': card.get('explanation', ''),
                    'usage': card.get('usage', ''),
                    'sentence': card.get('sentence', '')
                }
                return {'answer': plan['answer'], 'cardInfo': card_info}
            submitted = body.get('answer')
            if not isinstance(submitted, (str, list)) or (isinstance(submitted, list) and any(not isinstance(v, str) for v in submitted)):
                fail('An answer is required.')
            if len(json.dumps(submitted)) > 50000:
                fail('Answer is too long.')
            elapsed = body.get('elapsedMs', 0)
            if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
                fail('Elapsed time must be a nonnegative number.')
            card = next(c for c in doc['cards'] if c['id'] == plan['cardId'])
            question_settings = pending.get('settings', doc['settings'])
            if submitted == '__dont_know__':
                correct = False
            elif plan['type'] == 'flashcard_self_assessed':
                if not pending['revealed']:
                    fail('Reveal the flashcard before rating your answer.', 409)
                if submitted not in ('knew', 'missed'):
                    fail('Choose whether you knew the flashcard.')
                correct = submitted == 'knew'
            elif plan['type'] in ('multiple_choice', 'select_all'):
                values = submitted if isinstance(submitted, list) else [submitted]
                ids = {c['id'] for c in plan.get('choices', [])}
                if any(v not in ids for v in values):
                    fail('Choose an available answer.')
                if plan['type'] == 'multiple_choice' and len(values) != 1:
                    fail('Choose one answer.')
                correct = set(values) == set(plan.get('correctIds', []))
            else:
                canonical = card.get('parts') if plan['direction'] == 'term_to_definition' and card.get('parts') else plan['answer']
                aliases = card.get('aliases', []) if plan['direction'] == 'term_to_definition' else []
                correct = engine.grade(submitted, canonical, aliases=aliases, settings=question_settings)
            state_key = plan['cardId'] + ':' + plan['direction']
            previous = copy.deepcopy(doc['states'].get(state_key, engine.new_state(question_settings['prior'])))
            attempt = {'id': uuid.uuid4().hex, 'plan': plan, 'submitted': submitted,
                       'automaticGrade': correct, 'correct': correct, 'overridden': False,
                       'firstAttempt': True, 'elapsedMs': min(elapsed, 86400000), 'createdAt': now,
                       'beforeState': previous, 'beforeStreak': doc['streak'], 'beforeMetrics': copy.deepcopy(doc['metrics']),
                       'settings': copy.deepcopy(question_settings), 'streakGeneration': doc.get('streakGeneration', 0),
                       'retypeRequired': not correct and plan['type'] == 'written' and question_settings['retype']}
            self.apply_attempt(doc, attempt)
            doc['pathStarted'] = True
            doc['recent'] = (doc['recent'] + [state_key])[-100:]
            doc['pending'] = None
            doc['lastAttempt'] = attempt
            self.store.event(conn, key, doc['id'], 'attempt', attempt, now)
            return feedback(doc, attempt, now)
        if action in ('override', 'retype') and method == 'POST':
            attempt = doc.get('lastAttempt')
            if not attempt or body.get('attemptId') != attempt['id']:
                fail('Only the most recent answer can be changed.', 409)
            if action == 'override':
                if attempt['correct'] or attempt.get('overridden'):
                    fail('This answer is already correct.', 409)
                if doc.get('pending'):
                    fail('Override must happen before continuing.', 409)
                attempt.update(correct=True, overridden=True, retypeRequired=False)
                self.apply_attempt(doc, attempt)
                if attempt.get('streakGeneration', 0) != doc.get('streakGeneration', 0):
                    doc['streak'] = 0
                self.store.event(conn, key, doc['id'], 'override', {'attemptId': attempt['id'], 'correct': True}, now)
                return feedback(doc, attempt, now)
            if not attempt.get('retypeRequired'):
                fail('No corrective retype is required.', 409)
            answer = body.get('answer')
            if not isinstance(answer, str) or not engine.grade(answer, attempt['plan']['answer'], settings={'typo': False, 'oneAnswer': False}):
                fail('Please type the full correct answer shown.')
            attempt['retypeRequired'] = False
            self.store.event(conn, key, doc['id'], 'retype', {'attemptId': attempt['id'], 'answer': answer}, now)
            return {'ok': True}
        fail('Endpoint not found.', 404)

    @staticmethod
    def apply_attempt(doc, attempt):
        plan, correct = attempt['plan'], attempt['correct']
        state_key = plan['cardId'] + ':' + plan['direction']
        doc['states'][state_key] = engine.update_state(attempt['beforeState'], correct, plan['type'], attempt['createdAt'], attempt['settings'])
        doc['streak'] = attempt['beforeStreak'] + 1 if correct else 0
        metrics = copy.deepcopy(attempt['beforeMetrics'])
        for field, delta in [('attempts', 1), ('firstTryCorrect', int(correct)), ('totalCorrect', int(correct)),
                             ('incorrect', int(not correct)), ('overrides', int(attempt['overridden'])), ('elapsedMs', attempt['elapsedMs'])]:
            metrics[field] = metrics.get(field, 0) + delta
        metrics['longestStreak'] = max(metrics.get('longestStreak', 0), doc['streak'])
        doc['metrics'] = metrics


class Handler(BaseHTTPRequestHandler):
    server_version = 'Learn/1'

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def do_PUT(self):
        self.dispatch()

    def do_PATCH(self):
        self.dispatch()

    def do_DELETE(self):
        self.dispatch()

    def send_json(self, data, status=200):
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def dispatch(self):
        path = urlsplit(self.path).path
        try:
            if path.startswith('/api/'):
                origin = self.headers.get('Origin')
                if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                    fail('Cross-origin requests are not permitted.', 403)
                body, files = {}, {}
                ct = self.headers.get('Content-Type', '')
                if self.command != 'GET':
                    length = int(self.headers.get('Content-Length', '0'))
                    if length < 0 or length > 10_000_000:
                        fail('Request is too large.', 413)
                    raw_body = self.rfile.read(length) if length else b''
                    if ct.startswith('multipart/form-data'):
                        boundary_param = [p for p in ct.split(';') if 'boundary=' in p]
                        if not boundary_param:
                            fail('Invalid multipart request.')
                        boundary = boundary_param[0].split('boundary=')[1].strip('"\'').encode()
                        body, files = parse_multipart(raw_body, boundary)
                    elif raw_body:
                        try:
                            body = json.loads(raw_body)
                        except (ValueError, UnicodeDecodeError):
                            fail('Invalid JSON body.')
                authorization = self.headers.get('Authorization', '')
                token = authorization[7:] if authorization.startswith('Bearer ') else self.headers.get('X-User-Key')
                result = self.server.application.request(self.command, path.rstrip('/'), token, body, files=files)
                self.send_json(result)
                return
            if self.command != 'GET':
                fail('Not found.', 404)
            filename = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}.get(path)
            if not filename or not (ROOT / 'web' / filename).is_file():
                fail('Not found.', 404)
            payload = (ROOT / 'web' / filename).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', (mimetypes.guess_type(filename)[0] or 'application/octet-stream') + '; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(payload)
        except APIError as exc:
            self.send_json(exc.payload, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            import traceback
            traceback.print_exc()
            self.send_json({'error': 'An unexpected server error occurred.'}, 500)


def make_server(host='127.0.0.1', port=8000, db_path=None):
    server = ThreadingHTTPServer((host, port), Handler)
    server.application = Application(db_path)
    return server


def load_local_env():
    """Read local Supabase settings without overriding deployment environment."""
    env_file = ROOT / '.env.local'
    if not env_file.is_file():
        return
    allowed = {'SUPABASE_URL', 'SUPABASE_PUBLISHABLE_KEY', 'SUPABASE_ANON_KEY'}
    for line in env_file.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        name, value = name.strip(), value.strip()
        if name in allowed:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            os.environ.setdefault(name, value)


if __name__ == '__main__':
    load_local_env()
    server = make_server(os.environ.get('HOST', '127.0.0.1'), int(os.environ.get('PORT', '8000')))
    print('Learn is running at http://%s:%s' % server.server_address, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
