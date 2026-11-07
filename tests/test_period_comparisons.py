"""Exercise the browser's pure calculations with optional Node, without a DOM/build."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

NODE = shutil.which('node')
MODULE = Path(__file__).resolve().parents[1] / 'static' / 'comparisons.js'
RUNNER = """
const fs=require('node:fs'),vm=require('node:vm'),context={window:{}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);
const {method,args}=JSON.parse(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(context.window.periodComparisons[method](args)));
"""


def txn(day, minor, category='income', direction='in'):
    return dict(date=day, minor=minor, category=category, direction=direction)


@unittest.skipUnless(NODE, 'Node is optional for frontend calculation regressions')
class PeriodComparisonTests(unittest.TestCase):
    def invoke(self, method='calculate', **args):
        result = subprocess.run([NODE, '-e', RUNNER, str(MODULE)],
                                input=json.dumps(dict(method=method, args=args)), text=True,
                                capture_output=True, check=True)
        return json.loads(result.stdout)

    def test_calendar_month_year_and_custom_ranges(self):
        cases = [
            ({'from': '2024-03-01', 'to': '2024-03-31'}, '2024-02-01', '2024-02-29'),
            ({'from': '2025-03-01', 'to': '2025-03-31'}, '2025-02-01', '2025-02-28'),
            ({'from': '2025-01-01', 'to': '2025-01-31'}, '2024-12-01', '2024-12-31'),
            ({'year': '2025'}, '2024-01-01', '2024-12-31'),
            ({'from': '2025-03-10', 'to': '2025-03-14'}, '2025-03-05', '2025-03-09'),
            ({'from': '2025-02-02', 'to': '2025-02-04'}, '2025-01-30', '2025-02-01'),
            ({'from': '1900-03-01', 'to': '1900-03-31'}, '1900-02-01', '1900-02-28'),
            ({'from': '0001-01-01', 'to': '0001-01-31'}, None, None),
        ]
        coverage = [txn('2024-02-01', 100), txn('2025-04-01', 200)]
        for filters, start, end in cases:
            with self.subTest(filters=filters):
                result = self.invoke(history=coverage, coverage=coverage, **filters)
                self.assertEqual((result['previousStart'], result['previousEnd']), (start, end))
        self.assertIsNone(self.invoke(history=[], coverage=[]))
        self.assertIsNone(self.invoke(history=coverage, coverage=coverage,
                                     **{'from': '2025-04-01', 'to': '2025-03-01'}))

    def test_missing_coverage_known_zero_and_refunds(self):
        current = txn('2025-03-01', 9000, 'expense', 'out')
        result = self.invoke(history=[current], coverage=[current],
                             **{'from': '2025-03-01', 'to': '2025-03-31'})
        self.assertIsNone(result['expense']['previous'])
        coverage = [txn('2025-02-01', 10000), current]
        result = self.invoke(history=[current], coverage=coverage,
                             **{'from': '2025-03-01', 'to': '2025-03-31'})
        self.assertEqual(result['expense'], dict(current=9000, previous=0, delta=9000, percent=None))
        history = [txn('2025-02-01', 10000, 'expense', 'out'), current,
                   txn('2025-03-02', 1000, 'expense', 'in')]
        result = self.invoke(history=history, coverage=history,
                             **{'from': '2025-03-01', 'to': '2025-03-31'})
        self.assertEqual(result['expense'], dict(current=8000, previous=10000, delta=-2000, percent=-20))

    def test_overlay_preserves_alignment_precision_and_missing_months(self):
        history = [txn('2024-01-01', 12345), txn('2024-02-01', 3, 'expense', 'out')]
        result = self.invoke('monthlyOverlay', history=history, coverage=history,
                             months=['2025-01', '2025-02', '2025-03'])
        self.assertEqual(result, dict(income=[12345, 0, None], expense=[0, 3, None]))
