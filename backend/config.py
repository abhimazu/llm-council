"""Configuration for the LLM Council.

Environment variables read at startup:
    OPENROUTER_API_KEY  -- required. Server fails fast at import time if
                           missing or empty (audit finding M16). Set in
                           ``.env`` at repo root or in the process env.
    LOG_LEVEL           -- optional. One of DEBUG/INFO/WARNING/ERROR.
                           Default INFO.
    LOG_JSON            -- optional. If "1", "true", or "yes", emit
                           JSON-line logs instead of human-readable.
                           Default: human-readable in stdout.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Required configuration -- fail fast if missing
# ---------------------------------------------------------------------------

OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY") or ""

if not OPENROUTER_API_KEY:
    raise RuntimeError(
        "OPENROUTER_API_KEY is not set. Set it in a `.env` file at the "
        "repo root, or export it in the environment, before starting "
        "the server. Sign up for a key at https://openrouter.ai/."
    )


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

# Council members - list of OpenRouter model identifiers.
COUNCIL_MODELS = [
    "openai/gpt-5.1",
    "google/gemini-3-pro-preview",
    "anthropic/claude-sonnet-4.5",
    "x-ai/grok-4",
]

# Chairman model - synthesizes the final response.
#
# Audit finding B5 (issue #3 "Chairman over-influence"): if you set this
# to one of COUNCIL_MODELS, the chairman is fed Stage-1 responses tagged
# with the model name (council.py:_build_chairman_prompt) and can
# recognize itself, which has been observed to amplify self-favoritism
# already present in Stage 2. Prefer a model NOT in COUNCIL_MODELS.
CHAIRMAN_MODEL = "google/gemini-3-pro-preview"

# Title generation model — small/cheap; best-effort, never blocks the
# main response flow.
TITLE_MODEL = "google/gemini-2.5-flash"

# Routing classifier model — used by backend/router.py to decide whether a
# query needs the full council or can be answered by the chairman alone.
# Should be small/cheap; the classifier itself isn't expected to be smart,
# just consistent. ~$0.0001 per call at this size.
ROUTING_MODEL = "google/gemini-2.5-flash"


# ---------------------------------------------------------------------------
# Per-stage output caps. ``None`` = uncapped.
# ---------------------------------------------------------------------------
# Defaults are None to preserve the original uncapped behavior. The
# eval framework (proposed-changes §3) will inform the right values.
# A 150-token cap on Stage-2 truncates rankings mid-list and produces
# parse_status="partial" — observed live in 05_paid_verification.md.

STAGE1_MAX_TOKENS: Optional[int] = None
STAGE2_MAX_TOKENS: Optional[int] = None
CHAIRMAN_MAX_TOKENS: Optional[int] = None


# ---------------------------------------------------------------------------
# Endpoints / paths
# ---------------------------------------------------------------------------

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Data directory for conversation storage. Relative paths are resolved
# against the process CWD.
DATA_DIR = os.getenv("DATA_DIR", "data/conversations")


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# Comma-separated list of allowed origins. Default keeps the original
# dev-friendly setting; production deploys should override via env var.

CORS_ORIGINS = [
    o.strip() for o in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://localhost:3000",
    ).split(",")
    if o.strip()
]


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
# Configured here once at import time so every module's
# ``logging.getLogger(__name__)`` inherits a consistent formatter.

class _JsonFormatter(logging.Formatter):
    """Minimal JSON-line formatter. No external deps.

    Includes any ``extra=`` fields passed to log calls (model, kind,
    elapsed_ms, etc.) which the original print() statements lost.
    """
    _RESERVED = set(logging.LogRecord(
        "", 0, "", 0, "", None, None,
    ).__dict__.keys()) | {"message"}

    def format(self, record: logging.LogRecord) -> str:
        import json
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k in self._RESERVED or k.startswith("_"):
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _configure_logging() -> None:
    level_name = (os.getenv("LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    use_json = (os.getenv("LOG_JSON") or "").lower() in ("1", "true", "yes")

    root = logging.getLogger()
    # Avoid double-handlers on uvicorn reload
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    if use_json:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            "%Y-%m-%d %H:%M:%S",
        ))
    root.addHandler(handler)
    root.setLevel(level)


_configure_logging()
