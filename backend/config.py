"""Configuration for the LLM Council."""

import os
from dotenv import load_dotenv

load_dotenv()

# OpenRouter API key
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Council members - list of OpenRouter model identifiers
COUNCIL_MODELS = [
    "openai/gpt-5.1",
    "google/gemini-3-pro-preview",
    "anthropic/claude-sonnet-4.5",
    "x-ai/grok-4",
]

# Chairman model - synthesizes final response.
# Note (audit finding B5): if you set this to one of COUNCIL_MODELS, the
# chairman sees its own Stage-1 answer and Stage-2 ranking labelled with
# the model name, allowing self-recognition and bias. Prefer a model that
# is *not* a council member.
CHAIRMAN_MODEL = "google/gemini-3-pro-preview"

# Title generation model — small/cheap; best-effort.
TITLE_MODEL = "google/gemini-2.5-flash"

# Per-stage output caps. ``None`` = uncapped (original behavior, expensive).
# Set to integers to bound cost; the parser tolerates partial outputs and
# flags them as ``parse_status="partial"`` for downstream visibility.
# These defaults intentionally leave the council uncapped (matches the
# original code's behavior) so this commit is behavior-preserving for cost.
# Tune these after the eval framework lands.
STAGE1_MAX_TOKENS: "int | None" = None
STAGE2_MAX_TOKENS: "int | None" = None
CHAIRMAN_MAX_TOKENS: "int | None" = None

# OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Data directory for conversation storage
DATA_DIR = "data/conversations"
