"""Environment loading and local AI model tier selection.

The app loads ``.env`` from the project root without overriding variables
already present in the process environment. Docker Compose performs the same
tier selection from the same variable names and defaults, so a native run and
``docker compose up`` agree; the process environment wins in both cases.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

#: Model tiers, smallest to largest.
TIERS = ("low", "medium", "high")

#: Default Ollama tags per tier. Sizes are weight download sizes from the
#: Ollama registry manifests, NOT RAM caps: inference needs extra memory.
#:   low    qwen2.5:1.5b                986,048,512 bytes (~0.99 GB)
#:   medium qwen2.5:7b-instruct-q3_K_M  3,808,391,584 bytes (~3.81 GB)
#:   high   qwen2.5:14b-instruct-q3_K_L 7,924,768,608 bytes (~7.92 GB)
DEFAULT_MODELS = {
    "low": "qwen2.5:1.5b",
    "medium": "qwen2.5:7b-instruct-q3_K_M",
    "high": "qwen2.5:14b-instruct-q3_K_L",
}

DEFAULT_TIER = "low"

TIER_ENV = "FINREPORT_AI_TIER"
URL_ENV = "FINREPORT_AI_URL"


@dataclass(frozen=True)
class AIConfig:
    """Resolved local AI configuration for the selected tier."""

    tier: str
    model: str
    url: str
    models: dict[str, str]


def load_environment(root: Path) -> None:
    """Load ``root/.env`` into the process environment.

    Existing process environment variables always win. A missing ``.env``
    (for example inside the Compose image, where Compose interpolation
    already applied the same values) is a no-op.
    """
    env_path = Path(root) / ".env"
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path, override=False)


def _tier_model_env(tier: str) -> str:
    return f"FINREPORT_AI_MODEL_{tier.upper()}"


def ai_config() -> AIConfig:
    """Resolve the AI configuration from the current process environment.

    Only the selected tier's model is used (and only that tier is downloaded
    by the Compose model initializer). Raises ValueError for an unknown tier
    or a malformed model name.
    """
    tier = os.environ.get(TIER_ENV, DEFAULT_TIER)
    if tier not in TIERS:
        raise ValueError(f"{TIER_ENV} must be one of: {', '.join(TIERS)}")
    models = {}
    for name in TIERS:
        env_name = _tier_model_env(name)
        value = os.environ.get(env_name, DEFAULT_MODELS[name])
        if not value or value.startswith("-") or any(ch.isspace() for ch in value):
            raise ValueError(f"{env_name} must be a single Ollama model name")
        models[name] = value
    url = os.environ.get(URL_ENV, "").strip()
    return AIConfig(tier=tier, model=models[tier], url=url, models=models)
