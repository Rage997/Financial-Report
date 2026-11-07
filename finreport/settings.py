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

#: Default GGUF filenames per tier; the llama.cpp server aliases each model
#: to its filename. Sizes are weight download sizes, NOT RAM caps: inference
#: needs extra memory.
#:   low    qwen2.5-1.5b-instruct-q4_k_m.gguf  1,117,320,736 bytes (~1.12 GB)
#:   medium Qwen2.5-7B-Instruct-Q3_K_M.gguf    3,808,391,872 bytes (~3.81 GB)
#:   high   Qwen2.5-14B-Instruct-Q3_K_L.gguf   7,924,768,896 bytes (~7.92 GB)
DEFAULT_MODELS = {
    "low": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
    "medium": "Qwen2.5-7B-Instruct-Q3_K_M.gguf",
    "high": "Qwen2.5-14B-Instruct-Q3_K_L.gguf",
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


#: Characters allowed in a model filename. The value is used as a file name
#: inside the model volume and on the llama.cpp command line, so path
#: separators and shell metacharacters are rejected outright.
_FILENAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _validate_model_filename(env_name: str, value: str) -> str:
    """Reject empty values, paths, option-like names and non-GGUF names."""
    if (
        not value
        or value.startswith("-")
        or value in (".", "..")
        or not value.endswith(".gguf")
        or any(ch not in _FILENAME_CHARS for ch in value)
    ):
        raise ValueError(f"{env_name} must be a single GGUF filename such as model.gguf")
    return value


def ai_config() -> AIConfig:
    """Resolve the AI configuration from the current process environment.

    Only the selected tier's model is used (and only that tier is downloaded
    by the Compose model initializer). Raises ValueError for an unknown tier
    or an unsafe model filename.
    """
    tier = os.environ.get(TIER_ENV, DEFAULT_TIER)
    if tier not in TIERS:
        raise ValueError(f"{TIER_ENV} must be one of: {', '.join(TIERS)}")
    models = {}
    for name in TIERS:
        env_name = _tier_model_env(name)
        value = os.environ.get(env_name, DEFAULT_MODELS[name])
        models[name] = _validate_model_filename(env_name, value)
    url = os.environ.get(URL_ENV, "").strip()
    return AIConfig(tier=tier, model=models[tier], url=url, models=models)
