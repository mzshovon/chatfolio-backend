# The chat assistant's actual behavior — tone, formatting, contact-sharing, anti-repetition
# rules — lives in chat_prompts.toml (a sibling of this file), not here. This module only loads
# that config and exposes it as the constants the rest of the codebase imports. Tuning the
# assistant should mean editing chat_prompts.toml (or pointing LLM_PROMPTS_CONFIG_PATH at a
# different file) and restarting the service, never touching this file or rag_service.py.
import tomllib
from functools import lru_cache
from pathlib import Path

from chatfolio.config.settings import get_settings

_DEFAULT_PROMPTS_PATH = Path(__file__).with_name("chat_prompts.toml")


@lru_cache
def _load_prompts() -> dict:
    path = Path(get_settings().llm.prompts_config_path or _DEFAULT_PROMPTS_PATH)
    with path.open("rb") as f:
        return tomllib.load(f)


_prompts = _load_prompts()

CHAT_FALLBACK_RESPONSES: tuple[str, ...] = tuple(_prompts["fallback_responses"])
# First variant, used as the "say so honestly" example inside system_prompt_template.
CHAT_FALLBACK_RESPONSE = CHAT_FALLBACK_RESPONSES[0]
CHAT_SYSTEM_PROMPT_TEMPLATE: str = _prompts["system_prompt_template"]
INTENT_CLASSIFICATION_SYSTEM_PROMPT: str = _prompts["intent_classification_prompt"]
