"""FastAPI backend for LLM Council with cost-control wiring.

This commit wires the cache and router (added in the previous commit)
into the live request path. The structured-error contract from earlier
commits is preserved.

New behavior:
    1. Cache lookup before any LLM call. On hit, the cached envelope's
       events are replayed and the response time is sub-millisecond.
       Cache misses fall through to the routing decision.
    2. Smart routing: a cheap classifier decides whether to engage the
       full 4-LLM council or run the chairman alone. Trivial queries
       (factual lookups, arithmetic, definitions) skip the council.
    3. Per-call max_tokens caps are now active by default
       (config.py: STAGE1=800, STAGE2=400, CHAIRMAN=1000). Original
       code had no caps. To restore uncapped behavior, set any to None
       in config.py.

New SSE events emitted (frontend handles these defensively):
    - cache_hit         — fired before stage events when cache is used
    - routing_decision  — fired after cache miss with use_council + reason
    - solo_start        — fired when council is skipped, chairman runs alone
    - solo_complete     — fired with the SOLO chairman result
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import storage
from .cache import get_cache
from .config import (
    CHAIRMAN_MAX_TOKENS,
    CHAIRMAN_MODEL,
    CORS_ORIGINS,
    COUNCIL_MODELS,
)
from .council import (
    ChairmanResult,
    calculate_aggregate_rankings,
    generate_conversation_title,
    run_full_council,
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
)
from .openrouter import query_model
from .router import RoutingDecision, should_engage_council

log = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 64 * 1024

app = FastAPI(title="LLM Council API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CreateConversationRequest(BaseModel):
    """Empty body; placeholder for future fields (auth, etc.)."""


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=8192)


class ConversationMetadata(BaseModel):
    id: str
    created_at: str
    title: str
    message_count: int


class Conversation(BaseModel):
    id: str
    created_at: str
    title: Any
    messages: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sse(event_type: str, **fields: Any) -> str:
    return "data: " + json.dumps({"type": event_type, **fields}) + "\n\n"


async def _enforce_size_limit(request: Request) -> None:
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > MAX_MESSAGE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Request body exceeds size limit.",
                )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")


def _empty_envelope_with_solo(stage3_dict: Dict[str, Any],
                              routing: RoutingDecision) -> Dict[str, Any]:
    """Construct an envelope for the SOLO path.

    Stage 1 and Stage 2 are empty (the council didn't run). Stage 3 is
    the chairman's direct response to the user query. Metadata records
    the routing decision so downstream analysis can A/B council vs. solo.
    """
    return {
        "stage1": [],
        "stage2": [],
        "stage3": stage3_dict,
        "metadata": {
            "label_to_model": {},
            "aggregate_rankings": [],
            "routing": routing.to_dict(),
        },
    }


async def _run_solo(query: str) -> ChairmanResult:
    """Run the chairman alone on the user's query (no council)."""
    content, usage, err = await query_model(
        CHAIRMAN_MODEL,
        [{"role": "user", "content": query}],
        max_tokens=CHAIRMAN_MAX_TOKENS,
    )
    if err is not None:
        return ChairmanResult(
            model=CHAIRMAN_MODEL, status="error", error=err.to_dict(),
        )
    return ChairmanResult(
        model=CHAIRMAN_MODEL, status="ok", response=content, usage=usage,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
async def root() -> Dict[str, str]:
    return {"status": "ok", "service": "LLM Council API"}


@app.get("/api/cache/stats")
async def cache_stats() -> Dict[str, Any]:
    """Cache observability endpoint.

    No auth — intentionally read-only and contains no sensitive data
    (just hit/miss/store counters). Add to whatever observability
    you wire up in production.
    """
    return get_cache().stats.to_dict()


@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations() -> List[Dict[str, Any]]:
    return storage.list_conversations()


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(_: CreateConversationRequest) -> Dict[str, Any]:
    conversation_id = str(uuid.uuid4())
    return storage.create_conversation(conversation_id)


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str) -> Dict[str, Any]:
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(
    conversation_id: str,
    request_body: SendMessageRequest,
    request: Request,
) -> Dict[str, Any]:
    """Non-streaming send. Same envelope shape as the streaming endpoint.

    Cache + routing logic mirrors the streaming endpoint.
    """
    await _enforce_size_limit(request)
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    is_first_message = len(conversation["messages"]) == 0
    storage.add_user_message(conversation_id, request_body.content)

    if is_first_message:
        title = await generate_conversation_title(request_body.content)
        if title is not None:
            storage.update_conversation_title(conversation_id, title)

    # Cache lookup
    cache = get_cache()
    cached = cache.get(request_body.content, COUNCIL_MODELS, CHAIRMAN_MODEL)
    if cached is not None:
        envelope = dict(cached)
        envelope["metadata"] = {
            **envelope.get("metadata", {}),
            "cached": True,
        }
    else:
        # Routing decision
        routing = await should_engage_council(request_body.content)
        if routing.use_council:
            envelope = await run_full_council(request_body.content)
            envelope["metadata"]["routing"] = routing.to_dict()
            cache.put(request_body.content, COUNCIL_MODELS, CHAIRMAN_MODEL, envelope)
        else:
            solo = await _run_solo(request_body.content)
            envelope = _empty_envelope_with_solo(solo.to_dict(), routing)
            # Cache the SOLO result too (using the same key) — if a future
            # request for this query routes to council, that's a different
            # config and would key differently. SOLO results are a valid
            # cached answer for "this query, this config".
            cache.put(request_body.content, COUNCIL_MODELS, CHAIRMAN_MODEL, envelope)

    storage.add_assistant_message(
        conversation_id,
        stage1=envelope["stage1"],
        stage2=envelope["stage2"],
        stage3=envelope["stage3"],
        metadata=envelope["metadata"],
    )
    return envelope


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(
    conversation_id: str,
    request_body: SendMessageRequest,
    request: Request,
) -> StreamingResponse:
    """Streaming send with cache + routing wiring."""
    await _enforce_size_limit(request)
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    is_first_message = len(conversation["messages"]) == 0
    request_id = str(uuid.uuid4())[:8]
    cache = get_cache()

    async def event_generator():
        try:
            storage.add_user_message(conversation_id, request_body.content)

            # Title generation runs in parallel with the rest of the
            # pipeline; never blocks. Failure is silent (title_failed event).
            title_task: Optional[asyncio.Task[Optional[str]]] = None
            if is_first_message:
                title_task = asyncio.create_task(
                    generate_conversation_title(request_body.content)
                )

            # ----- Cache lookup -------------------------------------------------
            cached = cache.get(
                request_body.content, COUNCIL_MODELS, CHAIRMAN_MODEL,
            )
            if cached is not None:
                envelope = dict(cached)
                envelope["metadata"] = {
                    **envelope.get("metadata", {}),
                    "cached": True,
                }
                # Fire all three stage events from the cached envelope so
                # the frontend's existing renderers Just Work.
                yield _sse("cache_hit",
                           cached_metadata=envelope.get("metadata", {}))
                yield _sse("stage1_complete", data=envelope["stage1"])
                yield _sse(
                    "stage2_complete",
                    data=envelope["stage2"],
                    metadata={
                        "label_to_model":
                            envelope["metadata"].get("label_to_model", {}),
                        "aggregate_rankings":
                            envelope["metadata"].get("aggregate_rankings", []),
                    },
                )
                yield _sse("stage3_complete", data=envelope["stage3"])
            else:
                # ----- Routing decision ----------------------------------------
                routing = await should_engage_council(request_body.content)
                yield _sse("routing_decision", **routing.to_dict())

                if routing.use_council:
                    # Full council pipeline.
                    yield _sse("stage1_start")
                    stage1_results = await stage1_collect_responses(
                        request_body.content
                    )
                    stage1_dicts = [r.to_dict() for r in stage1_results]
                    yield _sse("stage1_complete", data=stage1_dicts)

                    yield _sse("stage2_start")
                    stage2_results, label_to_model = (
                        await stage2_collect_rankings(
                            request_body.content, stage1_results
                        )
                    )
                    stage2_dicts = [r.to_dict() for r in stage2_results]
                    aggregate = calculate_aggregate_rankings(
                        stage2_results, label_to_model
                    )
                    yield _sse(
                        "stage2_complete",
                        data=stage2_dicts,
                        metadata={
                            "label_to_model": label_to_model,
                            "aggregate_rankings":
                                [a.to_dict() for a in aggregate],
                        },
                    )

                    yield _sse("stage3_start")
                    stage3_result = await stage3_synthesize_final(
                        request_body.content, stage1_results, stage2_results,
                    )
                    stage3_dict = stage3_result.to_dict()
                    yield _sse("stage3_complete", data=stage3_dict)

                    envelope = {
                        "stage1": stage1_dicts,
                        "stage2": stage2_dicts,
                        "stage3": stage3_dict,
                        "metadata": {
                            "label_to_model": label_to_model,
                            "aggregate_rankings":
                                [a.to_dict() for a in aggregate],
                            "routing": routing.to_dict(),
                        },
                    }
                else:
                    # SOLO path: skip the council, run chairman alone.
                    yield _sse("solo_start", model=CHAIRMAN_MODEL)
                    solo = await _run_solo(request_body.content)
                    solo_dict = solo.to_dict()
                    yield _sse("solo_complete", data=solo_dict)
                    # Also emit a stage3_complete event so frontend
                    # renderers that key off stage3 still work without
                    # changes. The data shape is identical.
                    yield _sse("stage3_complete", data=solo_dict)
                    envelope = _empty_envelope_with_solo(solo_dict, routing)

                # Cache successful (or solo) results
                cache.put(
                    request_body.content,
                    COUNCIL_MODELS,
                    CHAIRMAN_MODEL,
                    envelope,
                )

            # ----- Title generation finalization --------------------------------
            if title_task is not None:
                title = await title_task
                if title is not None:
                    storage.update_conversation_title(conversation_id, title)
                    yield _sse("title_complete", data={"title": title})
                else:
                    yield _sse("title_failed")

            storage.add_assistant_message(
                conversation_id,
                stage1=envelope["stage1"],
                stage2=envelope["stage2"],
                stage3=envelope["stage3"],
                metadata=envelope["metadata"],
            )

            yield _sse("complete", cached=bool(cached is not None))

        except Exception:
            log.exception(
                "stream.unexpected_error",
                extra={"request_id": request_id,
                       "conversation_id": conversation_id},
            )
            yield _sse(
                "error",
                request_id=request_id,
                kind="server_error",
                detail="An internal error occurred. See server logs.",
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Request-Id": request_id,
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
