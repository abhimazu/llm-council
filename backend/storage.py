"""JSON-based storage for conversations.

Behavioral changes vs. the original (audit findings A5, G1):
    1. Assistant messages now carry the structured stage results from
       ``council.run_full_council``: each stage's per-member entries
       have an explicit ``status`` field, and the chairman result has
       ``status`` plus either ``response`` or ``error``. The original
       silently persisted the chairman fallback string as a normal
       response, indistinguishable from a real reply.
    2. Conversation titles can now be ``None`` (when title-gen fails)
       to distinguish "title generation failed" from "title not yet
       generated" / the default "New Conversation". The list endpoint
       coerces ``None`` to a placeholder for UI safety.
    3. ``conversation_id`` is sanitized to a UUID-only character set
       before being interpolated into the file path. The HTTP layer is
       not currently exploitable via path traversal (FastAPI routing
       blocks it; doc 04 §G3), but this closes the defense-in-depth
       gap so any future caller is also safe.
    4. Reads tolerate the *old* persisted shape (assistant messages
       without status fields) so users with existing conversations on
       disk are not broken.

Concurrency note (audit findings D1, D2):
    The race-condition / file-corruption issue under multi-worker
    deployments is **not fixed in this commit**. That fix lives in
    proposed-changes §5 P1-1 (move to SQLite) and is intentionally
    a separate commit so the migration is reviewable in isolation.
    This commit is a no-behavior-change refactor as far as the file
    semantics go.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DATA_DIR

log = logging.getLogger(__name__)

# Conversation IDs are uuid4 hex with dashes. Validate at the storage
# boundary before any os.path.join — this is defense-in-depth even
# though FastAPI's path routing currently blocks traversal at HTTP.
_UUID_RE = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-"
                      r"[a-f0-9]{4}-[a-f0-9]{12}$")


def _validate_id(conversation_id: str) -> None:
    if not isinstance(conversation_id, str) or not _UUID_RE.match(
        conversation_id
    ):
        raise ValueError(
            f"invalid conversation_id (must be a UUID4 string)"
        )


def ensure_data_dir() -> None:
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)


def get_conversation_path(conversation_id: str) -> str:
    _validate_id(conversation_id)
    return os.path.join(DATA_DIR, f"{conversation_id}.json")


def create_conversation(conversation_id: str) -> Dict[str, Any]:
    """Create a new conversation with a deterministic default shape."""
    _validate_id(conversation_id)
    ensure_data_dir()
    conversation: Dict[str, Any] = {
        "id": conversation_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "title": None,  # explicit None until title-gen runs
        "messages": [],
    }
    path = get_conversation_path(conversation_id)
    with open(path, "w") as f:
        json.dump(conversation, f, indent=2)
    return conversation


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """Load a conversation; return None if not found.

    Tolerates older persisted shapes (assistant messages without an
    explicit ``status`` field). The reader does not mutate older
    documents on read; migration to the new shape happens implicitly on
    the next write to that conversation.
    """
    path = get_conversation_path(conversation_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        # Race-condition note (D1/D2): a concurrent writer mid-rewrite
        # can leave the file in an unparseable state. We surface this
        # as None rather than raising because the API layer needs to
        # respond to the user; the operator should see this in logs
        # and migrate to SQLite (P1-1).
        log.warning(
            "storage.read_failed",
            extra={"conversation_id": conversation_id, "err": repr(exc)[:120]},
        )
        return None


def save_conversation(conversation: Dict[str, Any]) -> None:
    ensure_data_dir()
    cid = conversation["id"]
    _validate_id(cid)
    path = get_conversation_path(cid)
    with open(path, "w") as f:
        json.dump(conversation, f, indent=2, default=str)


def list_conversations() -> List[Dict[str, Any]]:
    """List all conversations (metadata only).

    Original code's O(N) per call full-file-read remains; switching to
    SQLite (P1-1) makes this O(log N) and avoids the corruption-on-read
    risk from D1/D2.
    """
    ensure_data_dir()
    conversations: List[Dict[str, Any]] = []
    for filename in os.listdir(DATA_DIR):
        if not filename.endswith(".json"):
            continue
        # Skip files that don't match the UUID pattern; protects against
        # someone dropping a malformed file in the data dir.
        if not _UUID_RE.match(filename[:-5]):
            continue
        path = os.path.join(DATA_DIR, filename)
        try:
            with open(path, "r") as f:
                data = json.load(f)
            conversations.append({
                "id": data["id"],
                "created_at": data["created_at"],
                "title": data.get("title") or "New Conversation",
                "message_count": len(data.get("messages", [])),
            })
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            log.warning(
                "storage.list_skip",
                extra={"file": filename, "err": repr(exc)[:120]},
            )
            continue
    conversations.sort(key=lambda x: x["created_at"], reverse=True)
    return conversations


def add_user_message(conversation_id: str, content: str) -> None:
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")
    conversation["messages"].append({"role": "user", "content": content})
    save_conversation(conversation)


def add_assistant_message(
    conversation_id: str,
    stage1: List[Dict[str, Any]],
    stage2: List[Dict[str, Any]],
    stage3: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Add an assistant message with all 3 stages to a conversation.

    All three stage payloads are expected to be the dict form produced
    by ``council.CouncilMemberResult.to_dict()`` etc. — i.e. carrying a
    ``status`` field per entry. Persisting the dataclass dict form
    directly keeps storage agnostic to council module internals.
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")
    msg: Dict[str, Any] = {
        "role": "assistant",
        "stage1": stage1,
        "stage2": stage2,
        "stage3": stage3,
    }
    if metadata is not None:
        msg["metadata"] = metadata
    conversation["messages"].append(msg)
    save_conversation(conversation)


def update_conversation_title(
    conversation_id: str, title: Optional[str],
) -> None:
    """Update the title of a conversation.

    Accepts ``None`` explicitly so a failed title-generation round
    doesn't have to invent a placeholder string. The list endpoint
    renders ``None`` titles as the placeholder.
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")
    conversation["title"] = title
    save_conversation(conversation)


__all__ = [
    "ensure_data_dir",
    "get_conversation_path",
    "create_conversation",
    "get_conversation",
    "save_conversation",
    "list_conversations",
    "add_user_message",
    "add_assistant_message",
    "update_conversation_title",
]
