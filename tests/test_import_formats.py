import json
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from finreport.import_formats import NormalizationError, normalize_file, parse_source_amount
from finreport.models import Direction


class ImportFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def normalize(self, suffix, content, *, model=False):
        path = self.root / ('statement' + suffix)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return normalize_file(path, ai_url='http://llama:8080' if model else '', ai_model='test')

    def test_signed_values_duplicates_and_explicit_accounts_survive(self):
        name = 'Long counterparty ' + 'x' * 100
        result = self.normalize('.csv', 'date;amount;counterparty;currency;account\n'
                                f'2025-02-13;1000.00;Employer;CHF;Account A\n'
                                f'2025-02-14;-12,50;{name};CHF;Account A\n'
                                f'2025-02-14;-12,50;{name};CHF;Account A\n'
                                f'2025-02-15;2.50;{name};CHF;Account A\n')
        self.assertEqual([t.amount for t in result.transactions], list(map(Decimal, ['1000', '12.50', '12.50', '2.50'])))
        self.assertEqual([t.direction for t in result.transactions], [Direction.IN, Direction.OUT, Direction.OUT, Direction.IN])
        self.assertEqual(result.transactions[1].counterparty, name)
        self.assertEqual([t.account for t in result.transactions], ['Account A'] * 4)
        self.assertEqual([p['row'] for p in result.provenance], [2, 3, 4, 5])

    def test_explicit_outgoing_magnitudes_and_conflicting_incoming(self):
        result = self.normalize('.csv', 'date;amount;direction;counterparty\n2025-02-13;12.50;out;Bakery\n')
        self.assertEqual((result.transactions[0].amount, result.transactions[0].direction), (Decimal('12.50'), Direction.OUT))
        with self.assertRaises(NormalizationError):
            self.normalize('.csv', 'date;amount;direction;counterparty\n2025-02-13;-12.50;in;Bakery\n')

    def test_split_columns_include_refunds_and_zero_filled_other_column(self):
        result = self.normalize('.csv', 'date;debit;credit;counterparty\n'
                                '2025-02-13;0;1000;Employer\n2025-02-14;12.50;0;Bakery\n'
                                '2025-02-15;-2.50;0;Bakery\n')
        self.assertEqual([t.direction for t in result.transactions], [Direction.IN, Direction.OUT, Direction.IN])
        self.assertEqual([t.amount for t in result.transactions], list(map(Decimal, ['1000', '12.50', '2.50'])))

    def test_currency_precision_and_magnitude_are_not_silently_changed(self):
        for text, expected in [('CHF -12,50', '-12.50'), ('1.553,80', '1553.80'),
                               ('1’553.80', '1553.80'), ('−12.50', '-12.50'),
                               ('(12.50)', '-12.50'), ('12.5000', '12.50'),
                               ('EUR 12.50', '12.50')]:
            with self.subTest(text=text):
                self.assertEqual(parse_source_amount(text, context='source'), Decimal(expected))
        for value in ['₪12.50', '12,3456', '12.501', 'NaN', '1e1000000', '1e-1000000']:
            with self.subTest(value=value), self.assertRaises(NormalizationError):
                parse_source_amount(value, context='source')

    def test_utf16_preamble_and_multiline_cell_provenance(self):
        text = 'sep=;\nBank statement\ndate;amount;counterparty\n2025-02-13;1.00;"A\nB"\n2025-02-14;-2.00;C\n'
        result = self.normalize('.csv', text.encode('utf-16'))
        self.assertEqual([p['row'] for p in result.provenance], [4, 6])
        self.assertEqual(result.transactions[0].counterparty, 'A\nB')

    def test_headerless_inference_retains_first_transaction(self):
        with patch('finreport.import_formats._model_ready'), patch('finreport.import_formats._ai_chat', return_value={
            'columns': {'date': 0, 'amount': 1, 'counterparty': 2}
        }):
            result = self.normalize('.csv', '2025-02-13;1000.00;Employer\n2025-02-14;-12.50;Bakery\n', model=True)
        self.assertEqual([(t.date, t.amount) for t in result.transactions], [(date(2025, 2, 13), Decimal('1000')), (date(2025, 2, 14), Decimal('12.50'))])
        self.assertEqual([p['row'] for p in result.provenance], [1, 2])

    def test_json_does_not_round_fractional_cents_or_overwrite_duplicate_keys(self):
        for value in ['1.0000000000000000001', '1e1000000', '1e-1000000']:
            with self.subTest(value=value), self.assertRaises(NormalizationError):
                self.normalize('.json', '[{"date":"2025-02-13","amount":' + value + ',"counterparty":"A"}]')
        with self.assertRaises(NormalizationError):
            self.normalize('.json', '[{"date":"2025-02-13","amount":12,"amount":24,"counterparty":"A"}]')

    def test_json_cannot_discard_first_row_or_another_transaction_array(self):
        cases = [
            [['2025-02-13', '1000', 'Employer'], ['2025-02-14', '-12.50', 'Bakery']],
            {'transactions': [{'date': '2025-02-13', 'amount': 1000, 'counterparty': 'Employer'}],
             'entries': [{'date': '2025-02-14', 'amount': -12.50, 'counterparty': 'Bakery'}]},
        ]
        for data in cases:
            with self.subTest(data=data), patch('finreport.import_formats._model_ready'), patch(
                    'finreport.import_formats._ai_chat', return_value={
                        'columns': {'date': 0, 'amount': 1, 'counterparty': 2}
                    }), self.assertRaises(NormalizationError):
                self.normalize('.json', json.dumps(data), model=True)

    def test_json_limits_sparse_width_and_dense_cell_expansion(self):
        sparse = [
            {'date': '2025-02-13', 'amount': '-12.50', 'counterparty': 'Bakery',
             f'extra_{index}': 'unused'}
            for index in range(201)
        ]
        wide_table = [
            ['date', 'amount', 'counterparty'] + [f'extra_{index}' for index in range(201)],
            ['2025-02-13', '-12.50', 'Bakery'] + ['unused'] * 201,
        ]
        for data in (sparse, wide_table):
            with self.subTest(shape=type(data[0]).__name__), self.assertRaises(NormalizationError):
                self.normalize('.json', json.dumps(data))
        records = [
            {'date': '2025-02-13', 'amount': '-12.50', 'counterparty': 'Bakery'},
            {'date': '2025-02-14', 'amount': '-12.50', 'counterparty': 'Bakery'},
        ]
        for data, budget in ((records, 6),
                             ([list(records[0])] + [list(row.values()) for row in records], 9)):
            with self.subTest(shape=type(data[0]).__name__), patch(
                    'finreport.import_formats.MAX_JSON_CELLS', budget):
                result = self.normalize('.json', json.dumps(data))
                self.assertEqual(sum(t.amount for t in result.transactions), Decimal('25'))
                with self.assertRaises(NormalizationError):
                    self.normalize('.json', json.dumps(data + [data[-1]]))

    def test_currency_outside_transaction_rows_cannot_be_ignored(self):
        cases = [
            ('.csv', 'EUR statement\ndate;amount;counterparty\n2025-02-13;-12.50;Bakery\n'),
            ('.json', '{"currency":"EUR","transactions":[{"date":"2025-02-13","amount":-12.50,"counterparty":"Bakery"}]}'),
        ]
        for suffix, text in cases:
            with self.subTest(suffix=suffix):
                result = self.normalize(suffix, text)
                self.assertEqual([(t.currency, t.amount, t.direction) for t in result.transactions],
                                 [('EUR', Decimal('12.50'), Direction.OUT)])
        from openpyxl import Workbook
        book = Workbook()
        book.active.append(['EUR statement'])
        book.active.append(['date', 'amount', 'counterparty'])
        book.active.append(['2025-02-13', -12.50, 'Bakery'])
        path = self.root / 'foreign.xlsx'
        book.save(path)
        result = normalize_file(path, ai_url='', ai_model='')
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('EUR', Decimal('12.50'))])

    def test_currency_characters_in_identities_and_schema_metadata_are_not_money(self):
        names = ["QA $hop", "$5 Store", "USD Trading", "Mr.€shop"]
        for name in names:
            row = {"date": "2025-02-13", "amount": "-12.50", "currency": "CHF", "counterparty": name}
            for suffix, content in (
                (".csv", "date;amount;currency;counterparty\n2025-02-13;-12.50;CHF;" + name + "\n"),
                (".json", json.dumps({"$schema": "https://example.invalid/$schema/USD-v2", "transactions": [row]})),
            ):
                with self.subTest(name=name, suffix=suffix):
                    result = self.normalize(suffix, content)
                    self.assertEqual([(t.counterparty, t.amount, t.direction) for t in result.transactions],
                                     [(name, Decimal("12.50"), Direction.OUT)])
        from openpyxl import Workbook
        book = Workbook()
        book.active.append(["date", "amount", "currency", "counterparty"])
        for name in names:
            book.active.append(["2025-02-13", -12.50, "CHF", name])
        path = self.root / "merchants.xlsx"
        book.save(path)
        result = normalize_file(path, ai_url="", ai_model="")
        self.assertEqual([t.counterparty for t in result.transactions], names)

    def test_amount_markers_cannot_override_a_conflicting_row_currency(self):
        for amount in ("$12.50", "-€12.50", "12.50£", "USD12.50", "12.50EUR"):
            with self.subTest(amount=amount), self.assertRaises(NormalizationError):
                self.normalize(".csv", "date;amount;currency;counterparty\n"
                               f"2025-02-13;{amount};CHF;QA Shop\n")

    def test_ai_cannot_turn_foreign_currency_symbols_into_chf(self):
        for symbol in ('€', '$', '£', '₹'):
            text = f'Booked 2025-02-13 for Bakery with amount -12.50 {symbol}'
            with self.subTest(symbol=symbol), patch('finreport.import_formats._model_ready'), patch(
                    'finreport.import_formats._ai_chat', return_value={
                        'rows': [self.extracted(1, text)]
                    }), self.assertRaises(NormalizationError):
                self.normalize('.txt', text, model=True)

    def test_ofx_sgml_and_xml_share_dates_signs_and_currency_guards(self):
        for closed in (False, True):
            def field(key, value):
                return f'<{key}>{value}' + (f'</{key}>' if closed else '\n')
            body = '<OFX><STMTRS>' + field('CURDEF', 'CHF') + '<BANKTRANLIST><STMTTRN>'
            body += field('DTPOSTED', '20250213120000.000[0:GMT]') + field('TRNAMT', '-12.50')
            body += field('NAME', 'Bakery &amp; Cafe') + '</STMTTRN></BANKTRANLIST></STMTRS></OFX>'
            result = self.normalize('.qfx' if closed else '.ofx', body)
            self.assertEqual((result.transactions[0].date, result.transactions[0].amount, result.transactions[0].direction), (date(2025, 2, 13), Decimal('12.50'), Direction.OUT))
            self.assertEqual(result.transactions[0].counterparty, 'Bakery & Cafe')
            foreign = self.normalize('.ofx', body.replace('CHF', 'EUR'))
            self.assertEqual([(t.currency, t.amount) for t in foreign.transactions],
                             [('EUR', Decimal('12.50'))])
            with self.assertRaises(NormalizationError):
                self.normalize('.ofx', body.replace('</STMTTRN>', ''))

    def test_ofx_corrections_reject_the_entire_statement(self):
        for closed in (False, True):
            def field(key, value):
                return f'<{key}>{value}' + (f'</{key}>' if closed else '\n')

            def transaction(identifier, amount, correction=''):
                return ('<STMTTRN>' + field('DTPOSTED', '20250213')
                        + field('TRNAMT', amount) + field('NAME', 'Bakery')
                        + field('FITID', identifier) + correction + '</STMTTRN>')

            start = '<OFX><STMTRS>' + field('CURDEF', 'CHF') + '<BANKTRANLIST>'
            end = '</BANKTRANLIST></STMTRS></OFX>'
            original = transaction('original', '-10.00')
            ordinary = start + original + transaction('second', '-12.00') + end
            result = self.normalize('.ofx', ordinary)
            self.assertEqual([t.amount for t in result.transactions],
                             [Decimal('10'), Decimal('12')])
            for action in ('REPLACE', 'DELETE'):
                correction = field('CORRECTFITID', 'original') + field('CORRECTACTION', action)
                source = start + original + transaction('corrected', '-12.00', correction) + end
                with self.subTest(closed=closed, action=action), self.assertRaises(NormalizationError):
                    self.normalize('.qfx' if closed else '.ofx', source)

    def test_qif_uses_month_first_dates_and_imports_split_parent_once(self):
        result = self.normalize('.qif', '!Type:Bank\nD11/02/2025\nT-12.50\nPBakery\nSFood\n$-10.00\nSTax\n$-2.50\n^\n')
        self.assertEqual([(t.date, t.amount, t.direction) for t in result.transactions], [(date(2025, 11, 2), Decimal('12.50'), Direction.OUT)])
        with self.assertRaises(NormalizationError):
            self.normalize('.qif', '!Type:Bank\nD11/02/2025\nT12.50\nPBakery\n')

    def test_workbook_all_sheets_and_uncached_formulas(self):
        from openpyxl import Workbook
        path = self.root / 'book.xlsx'
        book = Workbook()
        for index, (name, amount) in enumerate([('Payments', -12.5), ('Refunds', 2.5)]):
            sheet = book.active if index == 0 else book.create_sheet()
            sheet.title = name
            sheet.append(['date', 'amount', 'counterparty', 'currency'])
            sheet.append([datetime(2025, 2, 13 + index), amount, 'Bakery', 'CHF'])
        book.save(path)
        result = normalize_file(path, ai_url='', ai_model='')
        self.assertEqual([t.amount for t in result.transactions], [Decimal('12.50'), Decimal('2.50')])
        self.assertEqual([p['sheet'] for p in result.provenance], ['Payments', 'Refunds'])
        book['Refunds']['B2'] = '=1+1.5'
        book.save(path)
        with self.assertRaises(NormalizationError):
            normalize_file(path, ai_url='', ai_model='')

    def test_unrecognized_transaction_sheet_cannot_disappear(self):
        from openpyxl import Workbook
        path = self.root / 'book.xlsx'
        book = Workbook()
        book.active.append(['date', 'amount', 'counterparty'])
        book.active.append(['2025-02-13', 1000, 'Employer'])
        sheet = book.create_sheet('Unfamiliar')
        sheet.append(['When happened', 'Cash change', 'Party'])
        sheet.append(['2025-02-14', -12.50, 'Bakery'])
        book.save(path)
        with self.assertRaises(NormalizationError):
            normalize_file(path, ai_url='', ai_model='')

    @staticmethod
    def extracted(line, text, *, amount='-12.50', date_='2025-02-13', direction='out'):
        return {'line': line, 'kind': 'transaction', 'date': date_, 'amount': amount,
                'direction': direction, 'counterparty': 'Bakery', 'currency': 'CHF', 'evidence': text}

    def test_ai_cannot_change_source_values_or_silently_drop_records(self):
        text = 'Booked 2025-02-13 for Bakery with CHF amount -12.50'
        valid = self.extracted(1, text)
        invalid = [[], [dict(valid, amount='-2.50')], [dict(valid, amount='12.50', direction='in')],
                   [dict(valid, date='2025-02-14')], [dict(valid, amount='13', direction='in')],
                   [dict(valid, kind='not-transaction')], [dict(valid, line=2)]]
        for rows in invalid:
            with self.subTest(rows=rows), patch('finreport.import_formats._model_ready'), patch(
                    'finreport.import_formats._ai_chat', return_value={'rows': rows}), self.assertRaises(NormalizationError):
                self.normalize('.txt', text, model=True)

    def test_unicode_and_accounting_minus_cannot_be_lost_in_evidence(self):
        for sign in ['−12.50', '(12.50)']:
            text = f'{sign} booked 2025-02-13 for Bakery'
            row = self.extracted(1, text, amount='12.50', direction='in')
            with self.subTest(sign=sign), patch('finreport.import_formats._model_ready'), patch(
                    'finreport.import_formats._ai_chat', return_value={'rows': [row]}), self.assertRaises(NormalizationError):
                self.normalize('.txt', text, model=True)

    def test_ai_keeps_identical_occurrences_and_multiline_record_boundaries(self):
        text = 'Booked 2025-02-13 for Bakery with CHF amount -12.50'
        rows = [self.extracted(1, text), self.extracted(2, text)]
        with patch('finreport.import_formats._model_ready'), patch('finreport.import_formats._ai_chat', return_value={'rows': rows}):
            result = self.normalize('.txt', text + '\n' + text, model=True)
        self.assertEqual([p['row'] for p in result.provenance], [1, 2])
        self.assertEqual(sum(t.amount for t in result.transactions), Decimal('25'))
        multiline = '2025-02-13\nBakery\nCHF -12.50'
        with patch('finreport.import_formats._model_ready'), patch('finreport.import_formats._ai_chat', return_value={'rows': [self.extracted(1, multiline)]}):
            result = self.normalize('.txt', multiline, model=True)
        self.assertEqual((result.transactions[0].amount, result.transactions[0].direction), (Decimal('12.50'), Direction.OUT))

    def test_text_cannot_take_running_balance_as_transaction_amount(self):
        with self.assertRaises(NormalizationError):
            self.normalize('.txt', 'CHF\n2025-02-13  Bakery  -12.50  987.50\n')

    def test_ai_rejects_balances_and_ambiguous_amount_roles(self):
        cases = [
            ('amount -12.50 balance 987.50', '987.50', 'in'),
            ('amount -12.50 balance: CHF 987.50', '987.50', 'in'),
            ('balance 987.50', '987.50', 'in'),
            ('-12.50 987.50', '987.50', 'in'),
            ('-12.50 987.50', '-12.50', 'out'),
            ('12.50 -987.50', '-987.50', 'out'),
            ('CHF 12.50 987.50', '12.50', 'in'),
            ('amount -12.50 closing balance -987.50', '-987.50', 'out'),
        ]
        for suffix, amount, direction in cases:
            text = f'2025-02-13 Bakery {suffix}'
            row = self.extracted(1, text, amount=amount, direction=direction)
            with self.subTest(source=text), patch('finreport.import_formats._model_ready'), patch(
                    'finreport.import_formats._ai_chat', return_value={'rows': [row]}), \
                    self.assertRaises(NormalizationError):
                self.normalize('.txt', text, model=True)

    def test_ai_keeps_the_transaction_when_other_amounts_are_explicit_balances(self):
        for detail, amount, direction in (
                ('amount -12.50 running balance 987.50', '-12.50', 'out'),
                ('credit 12.50 balance: -987.50', '12.50', 'in')):
            text = f'2025-02-13 Bakery {detail}'
            row = self.extracted(1, text, amount=amount, direction=direction)
            with self.subTest(source=text), patch('finreport.import_formats._model_ready'), patch(
                    'finreport.import_formats._ai_chat', return_value={'rows': [row]}):
                result = self.normalize('.txt', text, model=True)
            self.assertEqual([(t.counterparty, t.amount, t.direction) for t in result.transactions],
                             [('Bakery', Decimal('12.50'), Direction(direction))])

    def test_txt_table_cannot_discard_a_transaction_before_its_header(self):
        table = 'Date\tAmount\tPayee\n2025-02-14\t-10.00\tGrocery\n'
        for first in ('2025-02-13\t-25.00\tBakery', '2025-02-13 Bakery -25.00'):
            with self.subTest(first=first), self.assertRaises(NormalizationError):
                self.normalize('.txt', first + '\n' + table)
        result = self.normalize('.txt', 'Opening balance 2025-02-01 100.00\n' + table)
        self.assertEqual([(t.counterparty, t.amount) for t in result.transactions],
                         [('Grocery', Decimal('10'))])

    def test_dated_identifier_description_is_not_a_header(self):
        result = self.normalize('.txt', '2025-02-13 IBAN transfer -25.00\n'
                                '2025-02-14 Grocery -10.00\n')
        self.assertEqual([(t.counterparty, t.amount) for t in result.transactions],
                         [('IBAN transfer', Decimal('25')), ('Grocery', Decimal('10'))])
        # Ambiguous header-like descriptions must fail, not publish only Grocery.
        for merchant in ('Page 2 Books', 'Opening balance supplies'):
            with self.subTest(merchant=merchant), self.assertRaises(NormalizationError):
                self.normalize('.txt', f'2025-02-13 {merchant} -25.00\n'
                               '2025-02-14 Grocery -10.00\n')

    def test_balance_line_cannot_bypass_validation_after_initial_text_rows(self):
        text = '2025-02-13 Bakery -1.00\n' * 10 + '2025-02-14 Bakery balance 987.50\n'
        with self.assertRaises(NormalizationError):
            self.normalize('.txt', text)

    def test_statement_default_applies_before_split_amount_precision_checks(self):
        result = self.normalize('.csv', 'Statement in KWD\ndate;debit;credit;counterparty\n'
                                '2025-02-13;0;5.125;Employer\n'
                                '2025-02-14;0,001;0;Bakery\n')
        self.assertEqual([(t.currency, t.amount, t.direction) for t in result.transactions],
                         [('KWD', Decimal('5.125'), Direction.IN),
                          ('KWD', Decimal('0.001'), Direction.OUT)])

    def test_currency_headers_control_precision_and_reject_conflicting_cells(self):
        result = self.normalize('.csv', 'date;amount (JPY);counterparty\n'
                                '2025-02-13;-750;Bakery\n')
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('JPY', Decimal('750'))])
        for header, amount, currency in [('amount (JPY)', '-750.50', 'JPY'),
                                         ('amount (USD)', '-12.50', 'EUR')]:
            with self.subTest(header=header), self.assertRaises(NormalizationError):
                self.normalize('.csv', f'date;{header};currency;counterparty\n'
                               f'2025-02-13;{amount};{currency};Bakery\n')

    def test_amount_symbols_require_a_compatible_explicit_currency(self):
        self.assertEqual(parse_source_amount('$12.50', context='source', currency='USD'),
                         Decimal('12.50'))
        self.assertEqual(parse_source_amount('12.50 zł', context='source'), Decimal('12.50'))
        with self.assertRaises(NormalizationError):
            parse_source_amount('$12.50', context='source')
        with self.assertRaises(NormalizationError):
            parse_source_amount('1,234', context='source', currency='KWD')
        self.assertEqual(parse_source_amount('0,001', context='source', currency='KWD'),
                         Decimal('0.001'))

    def test_unsupported_statement_currency_cannot_become_chf(self):
        with self.assertRaises(NormalizationError):
            self.normalize('.csv', 'Currency: AED\ndate;amount;counterparty\n'
                           '2025-02-13;-12.50;Bakery\n')

    def test_invalid_first_currency_amount_is_not_discarded_as_a_header(self):
        with patch('finreport.import_formats._model_ready'), patch(
                'finreport.import_formats._ai_chat', return_value={
                    'columns': {'date': 0, 'amount': 1, 'currency': 2, 'counterparty': 3}
                }), self.assertRaises(NormalizationError):
            self.normalize('.csv', '2025-02-13;0.50;JPY;Bakery\n'
                           '2025-02-14;100;JPY;Bakery\n', model=True)

    def test_value_dates_do_not_hide_currency_columns(self):
        result = self.normalize('.csv', 'date;valuta;amount;Div.;counterparty\n'
                                '2025-02-13;2025-02-14;-12.50;EUR;Bakery\n')
        self.assertEqual([(t.date, t.currency, t.amount) for t in result.transactions],
                         [(date(2025, 2, 13), 'EUR', Decimal('12.50'))])
        with self.assertRaises(NormalizationError):
            self.normalize('.csv', 'date;currency;amount;Div.;counterparty\n'
                           '2025-02-13;CHF;-12.50;EUR;Bakery\n')


if __name__ == '__main__':
    unittest.main()
