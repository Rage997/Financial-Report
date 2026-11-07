"""Matching semantics for authored substring rules and reviewed exact rules."""
import json
import tempfile
import unittest
from pathlib import Path

from finreport.classifier import Rule, classify, load_rules, rule_matches
from finreport.models import Category


class RuleMatchingTests(unittest.TestCase):
    def test_authored_rules_match_substrings_case_insensitively(self):
        rule = Rule(Category.EXPENSE, "Shop", ("example shop",))
        self.assertTrue(rule_matches(rule, "EXAMPLE SHOP LTD"))
        self.assertFalse(rule_matches(rule, "Other Shop"))

    def test_reviewed_rules_match_the_full_name_case_insensitively(self):
        rule = Rule(Category.INCOME, "Acme", ("acme",), exact=True)
        self.assertTrue(rule_matches(rule, "ACME"))
        self.assertTrue(rule_matches(rule, "Acme"))
        self.assertFalse(rule_matches(rule, "Acme Insurance"))
        self.assertFalse(rule_matches(rule, "Not Acme"))

    def test_first_match_wins_and_type_fallbacks_unchanged(self):
        authored = Rule(Category.TRANSFER, "Own", ("acme",))
        reviewed = Rule(Category.INCOME, "Acme", ("acme",), exact=True)
        self.assertEqual(classify("Acme", [authored, reviewed]), (Category.TRANSFER, "Own"))
        self.assertEqual(classify("Acme Insurance", [authored, reviewed]), (Category.TRANSFER, "Own"))
        self.assertEqual(classify("Acme Insurance", [reviewed], "interest"), (Category.INCOME, ""))
        self.assertEqual(classify("Acme", [reviewed], "card"), (Category.INCOME, "Acme"))

    def test_load_rules_selects_matching_mode_per_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.json"
            path.write_text(json.dumps({"rules": [
                {"category": "income", "label": "Acme", "patterns": ["Acme"]},
                {"category": "expense", "label": "Shop", "patterns": ["example shop"]},
            ]}), encoding="utf-8")
            self.assertEqual(classify("Acme Insurance", load_rules(path)), (Category.INCOME, "Acme"))
            self.assertEqual(classify("Acme Insurance", load_rules(path, exact=True)), (Category.UNCATEGORIZED, ""))
            self.assertEqual(classify("acme", load_rules(path, exact=True)), (Category.INCOME, "Acme"))


if __name__ == "__main__":
    unittest.main()
