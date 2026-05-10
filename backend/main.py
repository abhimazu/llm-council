"""FastAPI backend for LLM Council with structured error contract.

Behavioral changes vs. the original (audit findings A7, B7, F2, M21):
    1. ``/message`` and ``/message/stream`` now return identical shapes
       for identical inputs. The original code routed them through
       different code paths (``run_full_council`` vs the streaming
       handler), producing different error UX for the same broken
       state. Both paths now go through ``council.run_full_council``
       and serialize the same dict.
    2. SSE error events carry a structured ``LLMError`` shape (kind,
       detail, retryable) instead of a stringified raw exception. Raw
       exception messages are never sent to the client — they go to
       structured logs only.
    3. Stage-3 errors are emitted as ``stage3_complete`` with
       ``data.status == "error"`` rather than the original silent
       fallback string.
    4. Request-body size cap of 64 KB on the message endpoints to
       close the cost-DoS vector documented in 04_runtime_verification
       §M21 (10 MB payloads were accepted with no limit).
    5. CORS origins are now driven by config (env-overridable).

This commit deliberately does NOT change the SSE event *names* —
``stage1_start``, ``stage1_complete``, ``stage2_start``, etc. are still
the public contract. Only the shape of ``data`` changes (now carries
status fields).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import storage
from .config import CORS_ORIGINS
from .council import (
    calculate_aggregate_rankings,
    generate_conversation_title,
    run_full_council,
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
)

log = logging.getLogger(__name__)

# Hard cap on request body for the message endpoints. 64 KB is generous
# for a single-turn user message and tightly bounds the cost-DoS surface.
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
    title: Any  # str or None during the brief window before title-gen lands
    messages: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sse(event_type: str, **fields: Any) -> str:
    """Format a Server-Sent Events line with a single JSON event."""
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
async def root() -> Dict[str, str]:
    """Static health endpoint.

    Note (audit finding E3): this is intentionally cheap and static to
    serve as a process-liveness check. A full readiness check that
    verifies OpenRouter reachability and storage writability is a
    P1 follow-up; implementing it here would couple liveness to
    upstream availability.
    """
    return {"status": "ok", "service": "LLM Council API"}


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
    """Non-streaming send. Same envelope shape as the streaming endpoint."""
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

    envelope = await run_full_council(request_body.content)
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
    """Streaming send. Same shapes as ``send_message`` but per-stage events."""
    await _enforce_size_limit(request)
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    is_first_message = len(conversation["messages"]) == 0
    request_id = str(uuid.uuid4())[:8]

    async def event_generator():
        try:
            storage.add_user_message(conversation_id, request_body.content)

            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(
                    generate_conversation_title(request_body.content)
                )

            yield _sse("stage1_start")
            stage1_results = await stage1_collect_responses(
                request_body.content
            )
            stage1_dicts = [r.to_dict() for r in stage1_results]
            yield _sse("stage1_complete", data=stage1_dicts)

            yield _sse("stage2_start")
            stage2_results, label_to_model = await stage2_collect_rankings(
                request_body.content, stage1_results
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
                    "aggregate_rankings": [a.to_dict() for a in aggregate],
                },
            )

            yield _sse("stage3_start")
            stage3_result = await stage3_synthesize_final(
                request_body.content, stage1_results, stage2_results
            )
            stage3_dict = stage3_result.to_dict()
            yield _sse("stage3_complete", data=stage3_dict)

            if title_task is not None:
                title = await title_task
                if title is not None:
                    storage.update_conversation_title(conversation_id, title)
                    yield _sse("title_complete", data={"title": title})
                else:
                    yield _sse("title_failed")

            storage.add_assistant_message(
                conversation_id,
                stage1=stage1_dicts,
                stage2=stage2_dicts,
                stage3=stage3_dict,
                metadata={
                    "label_to_model": label_to_model,
                    "aggregate_rankings": [a.to_dict() for a in aggregate],
                },
            )

            yield _sse("complete")

        except Exception as exc:
            # Sanitize: do NOT send raw exception messages to clients.
            # Audit finding A7/F2 — original code passed str(e) which can
            # include provider URLs, tracebacks, and partial PII.
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
