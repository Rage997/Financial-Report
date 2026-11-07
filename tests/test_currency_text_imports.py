"""Currency grounding for text/PDF imports.

Deterministic lines, AI extraction and statement defaults must all keep the
source currency: explicit row markers and amount decorations win, a statement
declaration applies to unmarked amounts, and unsupported, ambiguous,
conflicting or AI-invented denominations fail instead of being relabeled.
"""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from finreport.import_formats import NormalizationError, normalize_file
from finreport.models import Direction


class TextCurrencyImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def normalize(self, suffix, content, *, model=False):
        path = self.root / ('statement' + suffix)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return normalize_file(path, ai_url='http://llama:8080' if model else '', ai_model='test')

    @staticmethod
    def extracted(line, text, *, amount='-19.50', date_='2025-03-01', direction='out',
                  counterparty='Paid SynthiMart', currency='EUR', evidence=None):
        return {'line': line, 'kind': 'transaction', 'date': date_, 'amount': amount,
                'direction': direction, 'counterparty': counterparty, 'currency': currency,
                'evidence': evidence if evidence is not None else text}

    # --- deterministic lines ---

    def test_row_marker_and_statement_default_keep_native_currency(self):
        result = self.normalize('.txt', 'Currency: EUR\n2025-03-01 Paid SynthiMart -19.50 EUR\n')
        self.assertEqual([(t.currency, t.amount, t.direction) for t in result.transactions],
                         [('EUR', Decimal('19.50'), Direction.OUT)])
        self.assertEqual(result.transactions[0].counterparty, 'Paid SynthiMart')

    def test_statement_default_applies_to_unmarked_amounts(self):
        result = self.normalize('.txt', 'Currency: EUR\n2025-03-01 Paid SynthiMart -19.50\n')
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('EUR', Decimal('19.50'))])

    def test_statement_currency_changes_apply_only_to_following_records(self):
        result = self.normalize('.txt', 'Currency: EUR\n2025-03-01 Bakery -12.50\n'
                                        'Currency: KWD\n2025-03-02 Bakery -0.001\n')
        self.assertEqual([(t.currency, t.amount, t.direction) for t in result.transactions],
                         [('EUR', Decimal('12.50'), Direction.OUT),
                          ('KWD', Decimal('0.001'), Direction.OUT)])

    def test_ai_statement_currency_changes_do_not_leak_between_records(self):
        first = '2025-03-01 Bakery -12.50 balance 987.50'
        second = '2025-03-02 Bakery -0.001 balance 9.999'
        rows = [
            self.extracted(2, first, counterparty='Bakery', amount='-12.50', currency=''),
            self.extracted(4, second, counterparty='Bakery', amount='-0.001',
                           date_='2025-03-02', currency=''),
        ]
        with patch('finreport.import_formats._model_ready'), \
                patch('finreport.import_formats._ai_chat', return_value={'rows': rows}):
            result = self.normalize('.txt', f'Currency: EUR\n{first}\nCurrency: KWD\n{second}', model=True)
        self.assertEqual([(t.currency, t.amount, t.direction) for t in result.transactions],
                         [('EUR', Decimal('12.50'), Direction.OUT),
                          ('KWD', Decimal('0.001'), Direction.OUT)])

    def test_row_currency_overrides_statement_default(self):
        result = self.normalize('.txt', 'Currency: EUR\n2025-03-01 Bank -1.00 JPY\n')
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('JPY', Decimal('1.00'))])

    def test_mixed_currencies_and_minor_units_are_preserved(self):
        result = self.normalize('.txt', '2025-03-01 SynthiMart -19.50 EUR\n'
                                        '2025-03-02 SynthiMart -750 JPY\n'
                                        '2025-03-03 Bank 0,001 KWD\n')
        self.assertEqual([(t.currency, t.amount, t.direction) for t in result.transactions],
                         [('EUR', Decimal('19.50'), Direction.OUT),
                          ('JPY', Decimal('750'), Direction.OUT),
                          ('KWD', Decimal('0.001'), Direction.IN)])

    def test_three_digit_precision_and_ambiguous_grouping_fail(self):
        result = self.normalize('.txt', 'Currency: KWD\n2025-03-01 Bank -1,234.567 KWD\n')
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('KWD', Decimal('1234.567'))])
        with self.assertRaises(NormalizationError):
            self.normalize('.txt', 'Currency: KWD\n2025-03-01 Bank -1,234 KWD\n')
        with self.assertRaises(NormalizationError):
            self.normalize('.txt', 'Currency: EUR\n2025-03-01 Bank -1.234 EUR\n')

    def test_unsupported_or_conflicting_declarations_fail(self):
        with self.assertRaises(NormalizationError):
            self.normalize('.txt', 'Currency: AED\n2025-03-01 Bank -1.00 AED\n')
        with self.assertRaises(NormalizationError):
            self.normalize('.txt', 'Currency: EUR\nCurrency: USD\n2025-03-01 Bank -1.00\n')

    def test_ambiguous_symbol_fails_without_a_compatible_default(self):
        with self.assertRaises(NormalizationError):
            self.normalize('.txt', '2025-03-01 Bank $12.50\n')

    def test_ambiguous_symbol_resolved_by_statement_default(self):
        result = self.normalize('.txt', 'Currency: USD\n2025-03-01 Bank $12.50\n')
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('USD', Decimal('12.50'))])

    def test_missing_currency_assumes_chf(self):
        result = self.normalize('.txt', '2025-03-01 Bank -1.00\n')
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('CHF', Decimal('1.00'))])

    def test_name_currency_without_denomination_evidence_does_not_relabel(self):
        result = self.normalize('.txt', 'Currency: EUR\n2025-03-01 USD Corp -19.50\n')
        self.assertEqual([(t.currency, t.amount, t.counterparty) for t in result.transactions],
                         [('EUR', Decimal('19.50'), 'USD Corp')])

    def test_decorated_amount_forms_keep_sign_and_currency(self):
        result = self.normalize('.txt', '2025-03-01 Bakery -12.50EUR\n'
                                        '2025-03-02 Shop -€12.50\n'
                                        '2025-03-03 Shop CHF -12.50\n')
        self.assertEqual([(t.currency, t.amount, t.direction) for t in result.transactions],
                         [('EUR', Decimal('12.50'), Direction.OUT),
                          ('EUR', Decimal('12.50'), Direction.OUT),
                          ('CHF', Decimal('12.50'), Direction.OUT)])

    def test_decorated_statement_amounts_keep_the_outgoing_sign(self):
        for currency, raw, amount in (
                ('USD', '-$12.50', '12.50'), ('JPY', '-¥750', '750'),
                ('CHF', '-CHF 12.50', '12.50'), ('USD', 'USD $-12.50', '12.50'),
                ('USD', '(USD $12.50)', '12.50')):
            with self.subTest(raw=raw):
                result = self.normalize('.txt', f'Currency: {currency}\n2025-03-01 Bakery {raw}\n')
                self.assertEqual([(t.currency, t.amount, t.direction) for t in result.transactions],
                                 [(currency, Decimal(amount), Direction.OUT)])

    def test_ai_cannot_drop_a_sign_before_currency_decorations(self):
        for currency, raw in (('USD', '-$12.50'), ('JPY', '-¥750'), ('CHF', '-CHF 12.50')):
            evidence = f'2025-03-01 Bakery {raw}'
            row = self.extracted(2, evidence, counterparty='Bakery', currency=currency,
                                 amount='750' if currency == 'JPY' else '12.50', direction='in')
            with self.subTest(raw=raw), patch('finreport.import_formats._model_ready'), \
                    patch('finreport.import_formats._ai_chat', return_value={'rows': [row]}), \
                    self.assertRaises(NormalizationError):
                self.normalize('.txt', f'Currency: {currency}\n{evidence} balance 987.50', model=True)

    def test_ai_cannot_reverse_positive_sign_after_currency_decorations(self):
        evidence = '2025-03-01 Bakery USD $+12.50'
        row = self.extracted(2, evidence, counterparty='Bakery', currency='USD',
                             amount='12.50', direction='out')
        with patch('finreport.import_formats._model_ready'), \
                patch('finreport.import_formats._ai_chat', return_value={'rows': [row]}), \
                self.assertRaises(NormalizationError):
            self.normalize('.txt', f'Currency: USD\n{evidence} balance 987.50', model=True)

    # --- AI extraction (deterministic mocked model content) ---

    def test_ai_extraction_grounds_currency_in_source(self):
        text = 'Currency: EUR\n2025-03-01 Paid SynthiMart -19.50 EUR balance 100.00'
        row = self.extracted(2, '2025-03-01 Paid SynthiMart -19.50 EUR')
        with patch('finreport.import_formats._model_ready'), \
                patch('finreport.import_formats._ai_chat', return_value={'rows': [row]}):
            result = self.normalize('.txt', text, model=True)
        self.assertEqual([(t.currency, t.amount, t.direction) for t in result.transactions],
                         [('EUR', Decimal('19.50'), Direction.OUT)])

    def test_ai_cannot_substitute_another_currency_for_a_stated_one(self):
        text = 'Currency: EUR\n2025-03-01 Paid SynthiMart -19.50 EUR balance 100.00'
        for currency in ('USD', 'CHF'):
            row = self.extracted(2, '2025-03-01 Paid SynthiMart -19.50 EUR', currency=currency)
            with self.subTest(currency=currency), \
                    patch('finreport.import_formats._model_ready'), \
                    patch('finreport.import_formats._ai_chat', return_value={'rows': [row]}), \
                    self.assertRaises(NormalizationError):
                self.normalize('.txt', text, model=True)

    def test_ai_must_report_a_stated_currency(self):
        text = 'Currency: EUR\n2025-03-01 Paid SynthiMart -19.50 EUR balance 100.00'
        row = self.extracted(2, '2025-03-01 Paid SynthiMart -19.50 EUR', currency='')
        with patch('finreport.import_formats._model_ready'), \
                patch('finreport.import_formats._ai_chat', return_value={'rows': [row]}), \
                self.assertRaises(NormalizationError):
            self.normalize('.txt', text, model=True)

    def test_ai_evidence_must_include_the_currency_marker(self):
        text = 'Currency: EUR\n2025-03-01 Paid SynthiMart -19.50 EUR balance 100.00'
        row = self.extracted(2, '2025-03-01 Paid SynthiMart -19.50 EUR',
                             evidence='2025-03-01 Paid SynthiMart -19.50')
        with patch('finreport.import_formats._model_ready'), \
                patch('finreport.import_formats._ai_chat', return_value={'rows': [row]}), \
                self.assertRaises(NormalizationError):
            self.normalize('.txt', text, model=True)

    def test_ai_cannot_relabel_money_from_a_merchant_name(self):
        text = '2025-03-01 USD Corp -19.50 balance 100.00'
        row = self.extracted(1, '2025-03-01 USD Corp -19.50',
                             counterparty='USD Corp', currency='USD')
        with patch('finreport.import_formats._model_ready'), \
                patch('finreport.import_formats._ai_chat', return_value={'rows': [row]}), \
                self.assertRaises(NormalizationError):
            self.normalize('.txt', text, model=True)
        row = self.extracted(1, '2025-03-01 USD Corp -19.50',
                             counterparty='USD Corp', currency='')
        with patch('finreport.import_formats._model_ready'), \
                patch('finreport.import_formats._ai_chat', return_value={'rows': [row]}):
            result = self.normalize('.txt', text, model=True)
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('CHF', Decimal('19.50'))])

    def test_ai_uses_statement_default_for_unmarked_amounts(self):
        text = 'Currency: EUR\n2025-03-01 Paid SynthiMart -19.50 balance 100.00'
        row = self.extracted(2, '2025-03-01 Paid SynthiMart -19.50', currency='')
        with patch('finreport.import_formats._model_ready'), \
                patch('finreport.import_formats._ai_chat', return_value={'rows': [row]}):
            result = self.normalize('.txt', text, model=True)
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('EUR', Decimal('19.50'))])

    def test_ai_cannot_invent_incoming_on_a_decorated_outgoing_amount(self):
        text = '2025-03-01 Bakery -€12.50 balance 987.50'
        for amount in ('-€12.50', '12.50', '+12.50'):
            row = self.extracted(1, '2025-03-01 Bakery -€12.50', counterparty='Bakery',
                                 amount=amount, direction='in')
            with self.subTest(amount=amount), \
                    patch('finreport.import_formats._model_ready'), \
                    patch('finreport.import_formats._ai_chat', return_value={'rows': [row]}), \
                    self.assertRaises(NormalizationError):
                self.normalize('.txt', text, model=True)
        row = self.extracted(1, '2025-03-01 Bakery -€12.50', counterparty='Bakery',
                             amount='-€12.50')
        with patch('finreport.import_formats._model_ready'), \
                patch('finreport.import_formats._ai_chat', return_value={'rows': [row]}):
            result = self.normalize('.txt', text, model=True)
        self.assertEqual([(t.currency, t.amount, t.direction) for t in result.transactions],
                         [('EUR', Decimal('12.50'), Direction.OUT)])

    def test_ai_keeps_three_digit_minor_units(self):
        text = 'Currency: KWD\n2025-03-01 Bank -1,234.567 KWD balance 99.00'
        row = self.extracted(2, '2025-03-01 Bank -1,234.567 KWD', counterparty='Bank',
                             amount='-1,234.567', currency='KWD')
        with patch('finreport.import_formats._model_ready'), \
                patch('finreport.import_formats._ai_chat', return_value={'rows': [row]}):
            result = self.normalize('.txt', text, model=True)
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('KWD', Decimal('1234.567'))])

    def test_ai_ambiguous_symbol_fails_without_a_compatible_default(self):
        text = '2025-03-01 Bakery $12.50 balance 987.50'
        row = self.extracted(1, '2025-03-01 Bakery $12.50', counterparty='Bakery',
                             amount='12.50', direction='in', currency='USD')
        with patch('finreport.import_formats._model_ready'), \
                patch('finreport.import_formats._ai_chat', return_value={'rows': [row]}), \
                self.assertRaises(NormalizationError):
            self.normalize('.txt', text, model=True)

    def test_ai_ambiguous_symbol_resolved_by_statement_default(self):
        text = 'Currency: USD\n2025-03-01 Bakery $12.50 balance 987.50'
        row = self.extracted(2, '2025-03-01 Bakery $12.50', counterparty='Bakery',
                             amount='12.50', direction='in', currency='USD')
        with patch('finreport.import_formats._model_ready'), \
                patch('finreport.import_formats._ai_chat', return_value={'rows': [row]}):
            result = self.normalize('.txt', text, model=True)
        self.assertEqual([(t.currency, t.amount, t.direction) for t in result.transactions],
                         [('USD', Decimal('12.50'), Direction.IN)])


    # --- entry points ---

    def test_txt_table_preamble_default_applies(self):
        result = self.normalize('.txt', 'Currency: EUR\nDate\tAmount\tPayee\n'
                                        '2025-03-01\t-19.50\tSynthiMart\n')
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('EUR', Decimal('19.50'))])

    def test_pdf_pages_keep_currency_and_page_provenance(self):
        with patch('finreport.import_formats._pdf_page_texts', return_value=[
                'Currency: EUR\n2025-03-01 Paid SynthiMart -19.50 EUR',
                '2025-03-02 Paid SynthiMart -750 JPY']):
            result = self.normalize('.pdf', b'')
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('EUR', Decimal('19.50')), ('JPY', Decimal('750'))])
        self.assertEqual([p['page'] for p in result.provenance], [1, 2])


if __name__ == '__main__':
    unittest.main()
