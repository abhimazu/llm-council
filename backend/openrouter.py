"""OpenRouter API client with typed errors, retries, and structured logging.

This module replaces the original openrouter.py which used a single broad
``except Exception: print(...); return None`` pattern (audit findings A1-A6).

Key behavioral changes vs. the original:
    1. ``query_model`` now returns a 3-tuple ``(content, usage, error)`` —
       exactly one of ``content`` or ``error`` is non-None. Callers branch
       explicitly instead of inferring failure from a None return.
    2. Retryable errors (rate_limit / timeout / 5xx / network) are retried
       once with exponential backoff. Auth and payload errors are not
       retried.
    3. ``max_tokens`` is now configurable per-call. The original code sent
       no cap, allowing flagship reasoning models to run extended reasoning
       at high cost (audit finding C3).
    4. Per-call structured logs include model, kind, latency, prompt/
       completion tokens, and OpenRouter's reported cost (audit findings
       E1, E2, M8). The ``usage`` object — which the original code
       discarded — is now returned to callers.
    5. ``query_models_parallel`` uses ``return_exceptions=True`` so a
       genuine asyncio failure cannot kill the whole council (audit
       finding A2).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import OPENROUTER_API_KEY, OPENROUTER_API_URL
from .errors import (
    LLMError,
    LLMErrorKind,
    RETRYABLE_KINDS,
    classify_http_status,
)

log = logging.getLogger(__name__)


# Maximum number of retry attempts for retryable errors. ``1`` means
# "one extra attempt after the first failure" (so up to 2 total).
DEFAULT_MAX_RETRIES = 1

# Default per-call timeout. Lowered from the original 120s — at 120s a
# stuck call holds a connection open for two minutes (audit finding C4).
DEFAULT_TIMEOUT_S = 30.0

# Soft cap on output tokens. Original code had no cap; this prevents a
# single chatty (or reasoning-mode) model from blowing up cost. Callers
# can override per call. ``None`` means "no cap", restoring original
# behavior.
DEFAULT_MAX_TOKENS: Optional[int] = None


def _safe_detail(exc: BaseException) -> str:
    """Produce a short, safe-to-log detail string from an exception.

    We deliberately do NOT include ``str(exc)`` verbatim — exception
    messages from httpx can include the full URL (with provider host)
    and sometimes partial response bodies. This helper truncates and
    classifies.
    """
    msg = str(exc).splitlines()[0] if str(exc) else ""
    return f"{type(exc).__name__}: {msg[:120]}"


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter. attempt is 1-indexed."""
    base = 2 ** attempt
    return base + random.uniform(0, 0.5)


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = DEFAULT_TIMEOUT_S,
    max_tokens: Optional[int] = DEFAULT_MAX_TOKENS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[LLMError]]:
    """Query a single model via OpenRouter.

    Returns:
        A 3-tuple ``(content, usage, error)``. Exactly one of:
            - ``content`` is a non-None string and ``error`` is None
              (success), OR
            - ``content`` is None and ``error`` is a non-None LLMError
              (failure, possibly after exhausted retries).
        ``usage`` is the OpenRouter ``usage`` dict on success (containing
        prompt_tokens, completion_tokens, cost, etc.) or None on failure.

    Why a tuple and not a single discriminated union: callers in
    ``council.py`` need to access usage for telemetry independent of
    whether they succeeded — but in practice usage is None on failure,
    so this shape is fine and avoids importing dataclasses everywhere.
    """
    if not OPENROUTER_API_KEY:
        # Defensive: config.py should have raised at startup, but be
        # explicit if someone constructed an instance without it.
        return None, None, LLMError(
            kind=LLMErrorKind.AUTH,
            model=model,
            detail="OPENROUTER_API_KEY is not set",
            retryable=False,
            attempt=1,
        )

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {"model": model, "messages": messages}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    last_error: Optional[LLMError] = None
    for attempt in range(1, max_retries + 2):  # 1 initial + max_retries
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    OPENROUTER_API_URL,
                    headers=headers,
                    json=payload,
                )
            elapsed_ms = int((time.monotonic() - t0) * 1000)

            if response.status_code != 200:
                kind = classify_http_status(response.status_code)
                err = LLMError(
                    kind=kind,
                    model=model,
                    detail=f"HTTP {response.status_code}",
                    retryable=kind in RETRYABLE_KINDS,
                    upstream_status=response.status_code,
                    attempt=attempt,
                )
                log.warning(
                    "llm.call.failed",
                    extra={
                        "model": model,
                        "kind": kind.value,
                        "status": response.status_code,
                        "elapsed_ms": elapsed_ms,
                        "attempt": attempt,
                    },
                )
                last_error = err
                if err.retryable and attempt <= max_retries:
                    await asyncio.sleep(_backoff_seconds(attempt))
                    continue
                return None, None, err

            try:
                data = response.json()
                message = data["choices"][0]["message"]
                content = message.get("content") or ""
                usage = data.get("usage", {}) or {}
            except (KeyError, IndexError, ValueError) as parse_exc:
                err = LLMError(
                    kind=LLMErrorKind.PARSE,
                    model=model,
                    detail=_safe_detail(parse_exc),
                    retryable=False,
                    upstream_status=200,
                    attempt=attempt,
                )
                log.warning(
                    "llm.call.parse_error",
                    extra={"model": model, "elapsed_ms": elapsed_ms,
                           "attempt": attempt},
                )
                return None, None, err

            log.info(
                "llm.call.ok",
                extra={
                    "model": model,
                    "elapsed_ms": elapsed_ms,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "cost_usd": usage.get("cost"),
                    "finish_reason": data["choices"][0].get("finish_reason"),
                    "attempt": attempt,
                },
            )
            return content, usage, None

        except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            err = LLMError(
                kind=LLMErrorKind.TIMEOUT,
                model=model,
                detail=_safe_detail(exc),
                retryable=True,
                attempt=attempt,
            )
            log.warning("llm.call.timeout",
                        extra={"model": model, "elapsed_ms": elapsed_ms,
                               "attempt": attempt})
            last_error = err
            if attempt <= max_retries:
                await asyncio.sleep(_backoff_seconds(attempt))
                continue
            return None, None, err

        except httpx.RequestError as exc:
            # transport-level: DNS, connection refused, TLS, etc.
            err = LLMError(
                kind=LLMErrorKind.NETWORK,
                model=model,
                detail=_safe_detail(exc),
                retryable=True,
                attempt=attempt,
            )
            log.warning("llm.call.network",
                        extra={"model": model, "attempt": attempt})
            last_error = err
            if attempt <= max_retries:
                await asyncio.sleep(_backoff_seconds(attempt))
                continue
            return None, None, err

        except asyncio.CancelledError:
            # Don't retry on cancellation; propagate the cancel intent.
            return None, None, LLMError(
                kind=LLMErrorKind.CANCELLED,
                model=model,
                detail="task cancelled",
                retryable=False,
                attempt=attempt,
            )

        except Exception as exc:  # pylint: disable=broad-except
            err = LLMError(
                kind=LLMErrorKind.UNKNOWN,
                model=model,
                detail=_safe_detail(exc),
                retryable=False,
                attempt=attempt,
            )
            log.exception("llm.call.unknown",
                          extra={"model": model, "attempt": attempt})
            return None, None, err

    # Should be unreachable; if all retries exhausted last_error is set.
    assert last_error is not None
    return None, None, last_error


async def query_models_parallel(
    models: List[str],
    messages: List[Dict[str, str]],
    timeout: float = DEFAULT_TIMEOUT_S,
    max_tokens: Optional[int] = DEFAULT_MAX_TOKENS,
) -> Dict[str, Tuple[Optional[str], Optional[Dict[str, Any]], Optional[LLMError]]]:
    """Query multiple models in parallel; preserve per-model results.

    Uses ``return_exceptions=True`` so a non-LLMError exception in one
    coroutine cannot kill the whole gather. We then convert any unexpected
    exception into an UNKNOWN ``LLMError`` for that model — this is
    defense-in-depth; ``query_model`` itself catches everything, but we
    do not want a future change to that contract to silently break the
    council (audit finding A2).
    """
    coros = [
        query_model(m, messages, timeout=timeout, max_tokens=max_tokens)
        for m in models
    ]
    raw = await asyncio.gather(*coros, return_exceptions=True)
    out: Dict[str, Tuple[Optional[str], Optional[Dict[str, Any]],
                         Optional[LLMError]]] = {}
    for model, result in zip(models, raw):
        if isinstance(result, BaseException):
            out[model] = (None, None, LLMError(
                kind=LLMErrorKind.UNKNOWN,
                model=model,
                detail=_safe_detail(result),
                retryable=False,
            ))
        else:
            out[model] = result
    return out


__all__ = [
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_MAX_TOKENS",
    "query_model",
    "query_models_parallel",
]
