import io
import json
import tempfile
import zipfile
import unittest
from pathlib import Path
from server import (
    parse_csv,
    parse_xlsx,
    parse_imported_rows,
    generate_sentence_hint,
    validate_cards,
    Application
)


class TestImportAndFields(unittest.TestCase):
    def test_parse_csv(self):
        csv_data = b"Word,Definition,Explanation,Usage,Sentence\nabate,become less intense,Used for storms,Formal,The storm abated after midnight."
        rows = parse_csv(csv_data)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], ['Word', 'Definition', 'Explanation', 'Usage', 'Sentence'])
        self.assertEqual(rows[1], ['abate', 'become less intense', 'Used for storms', 'Formal', 'The storm abated after midnight.'])

    def test_parse_imported_rows(self):
        rows = [
            ['Term', 'Meaning', 'Explanation', 'Usage', 'Sentence'],
            ['anomalous', 'deviating from normal', 'Adjective form', 'Academic', 'Her anomalous result surprised everyone.'],
            ['  ', 'invalid row without term', '', '', '']
        ]
        cards = parse_imported_rows(rows)
        self.assertEqual(len(cards), 1)
        c = cards[0]
        self.assertEqual(c['term'], 'anomalous')
        self.assertEqual(c['definition'], 'deviating from normal')
        self.assertEqual(c['explanation'], 'Adjective form')
        self.assertEqual(c['usage'], 'Academic')
        self.assertEqual(c['sentence'], 'Her anomalous result surprised everyone.')

    def test_parse_imported_rows_with_title_banner(self):
        rows = [
            ['Magoosh GRE Vocabulary Study Sheet', '', '', ''],
            ['261 distinct entries extracted...', '', '', ''],
            ['Word', 'Explanation', 'Definition', 'Use in a Sentence'],
            ['Aberration', 'A deviation', 'The act of wandering', 'Aberrations in climate']
        ]
        cards = parse_imported_rows(rows)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['term'], 'Aberration')
        self.assertEqual(cards[0]['definition'], 'The act of wandering')
        self.assertEqual(cards[0]['explanation'], 'A deviation')
        self.assertEqual(cards[0]['sentence'], 'Aberrations in climate')

    def test_parse_xlsx_in_memory(self):
        sheet_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row>'
            '</sheetData>'
            '</worksheet>'
        )
        shared_strings_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="4" uniqueCount="4">'
            '<si><t>Word</t></si>'
            '<si><t>Definition</t></si>'
            '<si><t>lucid</t></si>'
            '<si><t>easy to understand</t></si>'
            '</sst>'
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('xl/worksheets/sheet1.xml', sheet_xml)
            zf.writestr('xl/sharedStrings.xml', shared_strings_xml)
        
        rows = parse_xlsx(buf.getvalue())
        self.assertEqual(rows, [['Word', 'Definition'], ['lucid', 'easy to understand']])

    def test_generate_sentence_hint(self):
        sentence = "Her anomalous behavior was noted by all."
        hint = generate_sentence_hint(sentence, "anomalous")
        self.assertEqual(hint, "Her ______ behavior was noted by all.")

        hint_punct = generate_sentence_hint("They tried to abate, but failed.", "abate")
        self.assertEqual(hint_punct, "They tried to ______, but failed.")

        self.assertEqual(generate_sentence_hint("No matching word here.", "abate"), 'Think of the example sentence for this word.')

    def test_validate_cards_field_change_invalidates_history(self):
        old_cards = [{
            'id': 'card1',
            'term': 'abate',
            'definition': 'lessen',
            'explanation': 'old exp',
            'usage': '',
            'sentence': ''
        }]
        new_cards = [{
            'id': 'card1',
            'term': 'abate',
            'definition': 'lessen',
            'explanation': 'updated exp',
            'usage': '',
            'sentence': ''
        }]
        cards, changed = validate_cards(new_cards, old_cards)
        self.assertIn('card1', changed)

    def test_api_import_and_study_flow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'test.db'
            app = Application(db_path)
            res = app.request('POST', '/api/identity')
            key = res['key']

            boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
            csv_content = (
                "Word,Definition,Explanation,Usage,Sentence\r\n"
                "cacophony,harsh sounds,Noun,Formal,The cacophony of traffic woke me up."
            ).encode('utf-8')
            body = (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="title"\r\n\r\n'
                "GRE Vocab Import\r\n"
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="vocab.csv"\r\n'
                "Content-Type: text/csv\r\n\r\n"
            ).encode('utf-8') + csv_content + f"\r\n--{boundary}--\r\n".encode('utf-8')

            from server import parse_multipart
            fields, files = parse_multipart(body, boundary.encode('utf-8'))
            
            imported_set = app.request('POST', '/api/sets/import', key=key, body=fields, files=files)
            self.assertEqual(imported_set['title'], 'GRE Vocab Import')
            self.assertEqual(len(imported_set['cards']), 1)
            card = imported_set['cards'][0]
            self.assertEqual(card['explanation'], 'Noun')
            self.assertEqual(card['sentence'], 'The cacophony of traffic woke me up.')

            sid = imported_set['id']
            app.request('POST', f'/api/sets/{sid}/start', key=key, body={'settings': {'types': ['flashcard_self_assessed', 'written']}})

            q = app.request('GET', f'/api/sets/{sid}/next', key=key)
            token = q['token']
            if q['type'] == 'written':
                self.assertIn('hint', q)
                self.assertEqual(q['hint'], 'The _________ of traffic woke me up.')

            if q['type'] == 'flashcard_self_assessed':
                rev = app.request('POST', f'/api/sets/{sid}/reveal', key=key, body={'token': token})
                self.assertIn('cardInfo', rev)
                self.assertEqual(rev['cardInfo']['explanation'], 'Noun')
                self.assertEqual(rev['cardInfo']['sentence'], 'The cacophony of traffic woke me up.')


if __name__ == '__main__':
    unittest.main()
