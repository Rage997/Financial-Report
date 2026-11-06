"""Tier selection, .env loading and precedence for the local AI settings."""
import os
import tempfile
import unittest
from pathlib import Path

from finreport.settings import ai_config, load_environment

AI_VARS = (
    "FINREPORT_AI_TIER",
    "FINREPORT_AI_MODEL_LOW",
    "FINREPORT_AI_MODEL_MEDIUM",
    "FINREPORT_AI_MODEL_HIGH",
    "FINREPORT_AI_URL",
)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self._saved = {name: os.environ.get(name) for name in AI_VARS}
        for name in AI_VARS:
            os.environ.pop(name, None)

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def set_env(self, **values):
        for name, value in values.items():
            os.environ[name] = value

    def write_env_file(self, root: Path, lines: str) -> None:
        (root / ".env").write_text(lines, encoding="utf-8")

    def test_tier_selection_uses_its_own_configured_model(self):
        models = {"low": "custom/small:tag", "medium": "custom/middle:tag", "high": "custom/large:tag"}
        for tier, model in models.items():
            os.environ["FINREPORT_AI_MODEL_" + tier.upper()] = model
        for tier, model in models.items():
            with self.subTest(tier=tier):
                self.set_env(FINREPORT_AI_TIER=tier)
                self.assertEqual(ai_config().model, model)

    def test_invalid_tier_is_rejected(self):
        for bad in ("", "tiny", "LOW", "medium extra"):
            with self.subTest(tier=bad):
                self.set_env(FINREPORT_AI_TIER=bad)
                with self.assertRaises(ValueError):
                    ai_config()

    def test_invalid_model_name_is_rejected(self):
        for bad in ("", "two words", " leading", "trailing ", "--help"):
            with self.subTest(value=bad):
                self.set_env(FINREPORT_AI_MODEL_LOW=bad)
                with self.assertRaises(ValueError):
                    ai_config()

    def test_env_file_never_overrides_process_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_env_file(root, "FINREPORT_AI_TIER=high\nFINREPORT_AI_MODEL_LOW=file-model:tag\n")
            self.set_env(FINREPORT_AI_TIER="low", FINREPORT_AI_MODEL_LOW="shell-model:tag")
            load_environment(root)
            config = ai_config()
            self.assertEqual(config.tier, "low")
            self.assertEqual(config.model, "shell-model:tag")

    def test_env_file_values_apply_when_process_environment_is_unset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_env_file(
                root,
                "FINREPORT_AI_TIER=medium\n"
                "FINREPORT_AI_MODEL_MEDIUM=custom/from-dotenv\n",
            )
            load_environment(root)
            config = ai_config()
            self.assertEqual(config.tier, "medium")
            self.assertEqual(config.model, "custom/from-dotenv")


if __name__ == "__main__":
    unittest.main()
