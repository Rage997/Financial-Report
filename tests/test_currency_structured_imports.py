import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from finreport.import_formats import NormalizationError, normalize_file
from finreport.models import Direction


class StructuredCurrencyImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def normalize(self, suffix, content):
        path = self.root / ('statement' + suffix)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return normalize_file(path, ai_url='', ai_model='')

    # -- JSON -----------------------------------------------------------

    def test_json_root_currency_applies_and_row_override_keeps_precision(self):
        data = {"currency": "EUR", "transactions": [
            {"date": "2025-02-13", "amount": "-12.50", "counterparty": "Bakery"},
            {"date": "2025-02-14", "amount": "0.001", "currency": "KWD", "counterparty": "Fuel"},
            {"date": "2025-02-15", "amount": 5.125, "currency": "KWD", "counterparty": "Employer"},
        ]}
        result = self.normalize('.json', json.dumps(data))
        self.assertEqual([(t.currency, t.amount, t.direction) for t in result.transactions],
                         [('EUR', Decimal('12.50'), Direction.OUT),
                          ('KWD', Decimal('0.001'), Direction.IN),
                          ('KWD', Decimal('5.125'), Direction.IN)])

    def test_json_account_currency_honors_code_and_symbol_marker(self):
        data = {"accountCurrency": "PLN", "transactions": [
            {"date": "2025-02-13", "amount": -12.50, "counterparty": "Bakery"},
            {"date": "2025-02-14", "amount": "-3.20 zł", "counterparty": "Metro"},
        ]}
        result = self.normalize('.json', json.dumps(data))
        self.assertEqual([(t.currency, t.amount, t.direction) for t in result.transactions],
                         [('PLN', Decimal('12.50'), Direction.OUT),
                          ('PLN', Decimal('3.20'), Direction.OUT)])

    def test_json_conflicting_root_declarations_fail(self):
        rows = [{"date": "2025-02-13", "amount": "-12.50", "counterparty": "Bakery"}]
        for meta in ({"currency": "EUR", "accountCurrency": "USD"},
                     {"currency": "EUR", "currency_code": "USD"},
                     {"currency": "EUR", "ccy": "EUR", "valuta": "USD"}):
            with self.subTest(meta=meta), self.assertRaises(NormalizationError):
                self.normalize('.json', json.dumps({**meta, "transactions": rows}))

    def test_json_unsupported_explicit_currency_fails(self):
        for key in ("currency", "accountCurrency", "currency_code"):
            with self.subTest(key=key), self.assertRaises(NormalizationError):
                self.normalize('.json', json.dumps(
                    {key: "AED",
                     "transactions": [{"date": "2025-02-13", "amount": "-12.50",
                                       "counterparty": "Bakery"}]}))

    def test_json_does_not_infer_currency_from_names_or_schema(self):
        data = {"$schema": "https://example.invalid/$schema/USD-v2",
                "currency_notes": "USD vouchers are not account currency",
                "currency_schema": {"type": "string"},
                "transactions": [{"date": "2025-02-13", "amount": "-12.50",
                                  "counterparty": "USD Trading & Mr.€shop"}]}
        result = self.normalize('.json', json.dumps(data))
        self.assertEqual([(t.currency, t.amount, t.counterparty) for t in result.transactions],
                         [('CHF', Decimal('12.50'), 'USD Trading & Mr.€shop')])

    def test_json_row_currency_conflicting_with_amount_marker_fails(self):
        data = {"currency": "EUR", "transactions": [
            {"date": "2025-02-13", "amount": "12.50 USD", "currency": "KWD",
             "counterparty": "Bakery"}]}
        with self.assertRaises(NormalizationError):
            self.normalize('.json', json.dumps(data))

    def test_json_bare_list_keeps_chf_default(self):
        data = [{"date": "2025-02-13", "amount": "-12.50", "counterparty": "Bakery"}]
        result = self.normalize('.json', json.dumps(data))
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('CHF', Decimal('12.50'))])

    # -- OFX/QFX --------------------------------------------------------

    def ofx_body(self, statements):
        return "<OFX>" + "".join(statements) + "</OFX>"

    def ofx_statement(self, tag, curdef, acct, posted, trnamt, name, extra=""):
        curdef_tag = f"<CURDEF>{curdef}</CURDEF>" if curdef else ""
        return (f"<{tag}>{curdef_tag}<BANKACCTFROM><ACCTID>{acct}</ACCTID></BANKACCTFROM>"
                f"<BANKTRANLIST><STMTTRN>"
                f"<DTPOSTED>{posted}</DTPOSTED><TRNAMT>{trnamt}</TRNAMT>{extra}"
                f"<NAME>{name}</NAME></STMTTRN></BANKTRANLIST></{tag}>")

    def test_ofx_statement_currencies_are_scoped_per_statement(self):
        body = self.ofx_body([
            self.ofx_statement("STMTRS", "EUR", "DE12", "20250213", "-12.50", "Bakery"),
            self.ofx_statement("CCSTMTRS", "JPY", "JP34", "20250214", "-750", "Shop"),
        ])
        result = self.normalize('.qfx', body)
        self.assertEqual([(t.currency, t.amount, t.direction, t.account)
                          for t in result.transactions],
                         [('EUR', Decimal('12.50'), Direction.OUT, 'DE12'),
                          ('JPY', Decimal('750'), Direction.OUT, 'JP34')])

    def test_ofx_sgml_unclosed_fields_keep_statement_scope(self):
        body = ("<OFX><STMTRS><CURDEF>EUR\n<BANKTRANLIST><STMTTRN>"
                "<DTPOSTED>20250213\n<TRNAMT>-12.50\n<NAME>Bakery\n"
                "</STMTTRN></BANKTRANLIST></STMTRS>"
                "<STMTRS><CURDEF>GBP\n<BANKTRANLIST><STMTTRN>"
                "<DTPOSTED>20250214\n<TRNAMT>-8.75\n<NAME>Shop\n"
                "</STMTTRN></BANKTRANLIST></STMTRS></OFX>")
        result = self.normalize('.ofx', body)
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('EUR', Decimal('12.50')), ('GBP', Decimal('8.75'))])

    def test_ofx_per_transaction_currency_overrides_statement_default(self):
        body = self.ofx_body([
            self.ofx_statement("STMTRS", "EUR", "DE12", "20250213", "-12.50", "Bakery",
                               extra="<CURRENCYCODE>USD</CURRENCYCODE>"),
        ])
        result = self.normalize('.ofx', body)
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('USD', Decimal('12.50'))])

    def test_ofx_missing_currency_defaults_to_chf_without_leaking_between_statements(self):
        body = self.ofx_body([
            self.ofx_statement("STMTRS", "EUR", "DE12", "20250213", "-12.50", "Bakery"),
            self.ofx_statement("STMTRS", None, "CH99", "20250214", "-3.25", "Shop"),
        ])
        result = self.normalize('.ofx', body)
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('EUR', Decimal('12.50')), ('CHF', Decimal('3.25'))])

    def test_ofx_unsupported_conflicting_or_incomplete_records_fail(self):
        base = self.ofx_body([
            self.ofx_statement("STMTRS", "EUR", "DE12", "20250213", "-12.50", "Bakery"),
        ])
        with self.assertRaises(NormalizationError):
            self.normalize('.ofx', base.replace("EUR", "AED"))
        with self.assertRaises(NormalizationError):
            self.normalize('.ofx', base.replace(
                "<NAME>", "<CURRENCYCODE>USD</CURRENCYCODE><CURSYM>EUR</CURSYM><NAME>"))
        with self.assertRaises(NormalizationError):
            self.normalize('.ofx', base.replace("</STMTTRN>", ""))
        with self.assertRaises(NormalizationError):
            self.normalize('.ofx', "<OFX><STMTRS><BANKTRANLIST><STMTTRN>"
                                   "<DTPOSTED>20250213</STMTTRN></BANKTRANLIST></STMTRS></OFX>")

    def test_ofx_amount_precision_follows_statement_currency(self):
        body = self.ofx_body([
            self.ofx_statement("STMTRS", "JPY", "JP34", "20250213", "-12.50", "Bakery"),
        ])
        with self.assertRaises(NormalizationError):
            self.normalize('.ofx', body)

    def test_ofx_currency_aggregate_keeps_unconverted_denomination(self):
        body = self.ofx_body([
            self.ofx_statement("STMTRS", "EUR", "DE12", "20250213", "-12.50", "Bakery",
                               extra=("<CURRENCY><CURSYM>USD</CURSYM>"
                                      "<CURDEF>USD</CURDEF><CURRATE>0.92</CURRATE></CURRENCY>")),
        ])
        result = self.normalize('.ofx', body)
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('USD', Decimal('12.50'))])

    def test_ofx_currency_aggregate_precision_follows_denomination(self):
        body = self.ofx_body([
            self.ofx_statement("STMTRS", "EUR", "DE12", "20250213", "-0.001", "Bakery",
                               extra="<CURRENCY><CURDEF>KWD</CURDEF></CURRENCY>"),
        ])
        result = self.normalize('.ofx', body)
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('KWD', Decimal('0.001'))])

    def test_ofx_origcurrency_never_relabels_converted_amount(self):
        body = self.ofx_body([
            self.ofx_statement("STMTRS", "EUR", "DE12", "20250213", "-12.50", "Bakery",
                               extra=("<ORIGCURRENCY><CURSYM>USD</CURSYM>"
                                      "<CURRATE>0.92</CURRATE></ORIGCURRENCY>")),
        ])
        result = self.normalize('.ofx', body)
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('EUR', Decimal('12.50'))])

    def test_ofx_converted_amount_without_statement_currency_fails(self):
        body = self.ofx_body([
            self.ofx_statement("STMTRS", None, "DE12", "20250213", "-12.50", "Bakery",
                               extra=("<ORIGCURRENCY><CURSYM>USD</CURSYM>"
                                      "<CURRATE>0.92</CURRATE></ORIGCURRENCY>")),
        ])
        with self.assertRaises(NormalizationError):
            self.normalize('.ofx', body)

    def test_ofx_contradictory_or_incomplete_currency_aggregates_fail(self):
        cases = [
            ("<CURRENCY><CURSYM>USD</CURSYM></CURRENCY>"
             "<ORIGCURRENCY><CURSYM>GBP</CURSYM></ORIGCURRENCY>"),
            "<CURRENCY><CURRATE>0.92</CURRATE></CURRENCY>",
            "<ORIGCURRENCY><CURSYM></CURSYM></ORIGCURRENCY>",
            "<CURRENCY><CURSYM>USD</CURSYM><CURDEF>EUR</CURDEF></CURRENCY>",
            "<CURRENCY><CURSYM>AED</CURSYM></CURRENCY>",
            ("<CURRENCY><CURSYM>USD</CURSYM></CURRENCY>"
             "<CURRENCYCODE>GBP</CURRENCYCODE>"),
            "<CURRENCY><CURSYM>USD</CURSYM></CURRENCY>" * 2,
            "<ORIGCURRENCY><CURSYM>USD</CURSYM></ORIGCURRENCY>" * 2,
        ]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(NormalizationError):
                self.normalize('.ofx', self.ofx_body([
                    self.ofx_statement("STMTRS", "EUR", "DE12", "20250213", "-12.50", "Bakery",
                                       extra=extra),
                ]))

    def test_ofx_sgml_unclosed_currency_aggregate_stays_scoped(self):
        body = ("<OFX><STMTRS><CURDEF>EUR\n<BANKTRANLIST><STMTTRN>"
                "<DTPOSTED>20250213\n<TRNAMT>-12.50\n"
                "<CURRENCY><CURSYM>USD</CURSYM><CURRATE>0.92</CURRATE>"
                "<NAME>Bakery\n</STMTTRN></BANKTRANLIST></STMTRS></OFX>")
        result = self.normalize('.ofx', body)
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('USD', Decimal('12.50'))])

    def test_ofx_orphan_record_after_closed_statement_does_not_inherit(self):
        body = ("<OFX><STMTRS><CURDEF>EUR</CURDEF>"
                "<BANKACCTFROM><ACCTID>DE12</ACCTID></BANKACCTFROM>"
                "<BANKTRANLIST><STMTTRN>"
                "<DTPOSTED>20250213</DTPOSTED><TRNAMT>-12.50</TRNAMT><NAME>Bakery</NAME>"
                "</STMTTRN></BANKTRANLIST></STMTRS>"
                "<STMTTRN><DTPOSTED>20250214</DTPOSTED><TRNAMT>-3.25</TRNAMT><NAME>Orphan</NAME>"
                "</STMTTRN></OFX>")
        result = self.normalize('.ofx', body)
        self.assertEqual([(t.currency, t.amount, t.account) for t in result.transactions],
                         [('EUR', Decimal('12.50'), 'DE12'),
                          ('CHF', Decimal('3.25'), '')])

    def test_ofx_unclosed_statement_does_not_leak_account_into_next_statement(self):
        first = self.ofx_statement("STMTRS", "EUR", "DE12", "20250213", "-12.50", "Bakery")
        first = first.removesuffix("</STMTRS>")
        second = ("<STMTRS><STMTTRN><DTPOSTED>20250214</DTPOSTED>"
                  "<TRNAMT>-3.25</TRNAMT><NAME>Shop</NAME></STMTTRN></STMTRS>")
        result = self.normalize('.ofx', self.ofx_body([first, second]))
        self.assertEqual([(t.currency, t.amount, t.account) for t in result.transactions],
                         [('EUR', Decimal('12.50'), 'DE12'),
                          ('CHF', Decimal('3.25'), '')])


    # -- QIF ------------------------------------------------------------

    def test_qif_amount_currency_is_preserved(self):
        result = self.normalize('.qif', '!Type:Bank\nD11/02/2025\nT-8.75 GBP\nPBakery\n^\n')
        self.assertEqual([(t.date, t.currency, t.amount, t.direction)
                          for t in result.transactions],
                         [(date(2025, 11, 2), 'GBP', Decimal('8.75'), Direction.OUT)])

    def test_qif_missing_currency_defaults_to_chf(self):
        result = self.normalize('.qif', '!Type:Bank\nD11/02/2025\nT-12.50\nPBakery\n^\n')
        self.assertEqual([(t.currency, t.amount, t.direction) for t in result.transactions],
                         [('CHF', Decimal('12.50'), Direction.OUT)])

    def test_qif_mixed_currencies_stay_separate(self):
        text = ('!Type:Bank\n'
                'D11/02/2025\nT-8.75 GBP\nPBakery\n^\n'
                'D11/03/2025\nT-12.50\nPShop\n^\n'
                'D11/04/2025\nT0.001 KWD\nPTax\n^\n')
        result = self.normalize('.qif', text)
        self.assertEqual([(t.currency, t.amount, t.direction) for t in result.transactions],
                         [('GBP', Decimal('8.75'), Direction.OUT),
                          ('CHF', Decimal('12.50'), Direction.OUT),
                          ('KWD', Decimal('0.001'), Direction.IN)])

    def test_qif_precision_follows_currency(self):
        with self.assertRaises(NormalizationError):
            self.normalize('.qif', '!Type:Bank\nD11/02/2025\nT0.001\nPBakery\n^\n')
        with self.assertRaises(NormalizationError):
            self.normalize('.qif', '!Type:Bank\nD11/02/2025\nT-12.50 JPY\nPBakery\n^\n')
        result = self.normalize('.qif', '!Type:Bank\nD11/02/2025\nT-750 JPY\nPBakery\n^\n')
        self.assertEqual([(t.currency, t.amount) for t in result.transactions],
                         [('JPY', Decimal('750'))])

    def test_qif_split_parent_imported_once_with_currency(self):
        result = self.normalize('.qif',
                                '!Type:Bank\nD11/02/2025\nT-12.50 GBP\nPBakery\n'
                                'SFood\n$-10.00\nSTax\n$-2.50\n^\n')
        self.assertEqual([(t.currency, t.amount, t.direction) for t in result.transactions],
                         [('GBP', Decimal('12.50'), Direction.OUT)])

    def test_qif_incomplete_record_and_unsupported_section_still_fail(self):
        with self.assertRaises(NormalizationError):
            self.normalize('.qif', '!Type:Bank\nD11/02/2025\nT12.50\nPBakery\n')
        with self.assertRaises(NormalizationError):
            self.normalize('.qif', '!Type:Invest\nD11/02/2025\nT12.50\nPTrade\n^\n')


if __name__ == '__main__':
    unittest.main()
