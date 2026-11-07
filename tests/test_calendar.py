"""Calendar dates and money boundaries, using the browser module without a DOM."""
import calendar
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

NODE = shutil.which('node')
MODULE = Path(__file__).resolve().parents[1] / 'static' / 'calendar.js'
RUNNER = """
const fs=require('node:fs'),vm=require('node:vm'),context={window:{}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);
const {method,args}=JSON.parse(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(context.window.calendarView[method](args)));
"""


@unittest.skipUnless(NODE, 'Node is optional for frontend calculation regressions')
class CalendarTests(unittest.TestCase):
    def invoke(self, method='monthGrid', timezone='UTC', **args):
        result = subprocess.run([NODE, '-e', RUNNER, str(MODULE)],
                                input=json.dumps(dict(method=method, args=args)), text=True,
                                capture_output=True, check=True,
                                env={**os.environ, 'TZ': timezone})
        return json.loads(result.stdout)

    def test_gregorian_month_lengths_and_monday_alignment(self):
        for year, month in [(2024, 2), (2025, 2), (1900, 2), (2000, 2),
                            (2025, 6), (1, 1), (99, 12), (9999, 12)]:
            with self.subTest(year=year, month=month):
                key = f'{year:04d}-{month:02d}'
                result = self.invoke(month=key, transactions=[])
                offset, days = calendar.monthrange(year, month)
                self.assertEqual(result['offset'], offset)
                self.assertEqual([d['date'] for d in result['days']],
                                 [f'{key}-{i:02d}' for i in range(1, days + 1)])

    def test_month_navigation_and_supported_year_boundaries(self):
        cases = [('2025-12', 1, '2026-01'), ('2025-01', -1, '2024-12'),
                 ('0001-01', -1, None), ('9999-12', 1, None),
                 ('0099-12', 1, '0100-01')]
        for month, step, expected in cases:
            with self.subTest(month=month, step=step):
                self.assertEqual(self.invoke('shiftMonth', month=month, step=step), expected)
        for invalid in ['', '0000-01', '10000-01', '2025-00', '2025-13', '2025-2']:
            with self.subTest(invalid=invalid):
                self.assertIsNone(self.invoke(month=invalid, transactions=[]))

    def test_daily_counts_refunds_precision_and_month_isolation(self):
        rows = [dict(date='2024-02-29', minor=100001, direction='out'),
                dict(date='2024-02-29', minor=1, direction='in'),
                dict(date='2024-02-29', minor=100001, direction='out'),
                dict(date='2024-02-01', minor=0, direction='in'),
                dict(date='2024-03-01', minor=999999, direction='in')]
        days = self.invoke(month='2024-02', transactions=rows)['days']
        self.assertEqual(days[-1], dict(date='2024-02-29', count=3, incoming=1,
                                       outgoing=200002, net=-200001))
        self.assertEqual(days[0]['count'], 1)
        self.assertEqual(days[1], dict(date='2024-02-02', count=0, incoming=0,
                                      outgoing=0, net=0))
        self.assertEqual(sum(d['count'] for d in days), 4)

    def test_calendar_is_timezone_independent_across_dst(self):
        baseline = self.invoke(month='2025-03', transactions=[])
        for timezone in ['America/Los_Angeles', 'Pacific/Kiritimati']:
            with self.subTest(timezone=timezone):
                self.assertEqual(self.invoke(month='2025-03', transactions=[], timezone=timezone),
                                 baseline)
