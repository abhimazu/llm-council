"""Tests for storage.py — UUID validation, persistence shape."""
import os
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-for-import-only")

import json
import pytest

from backend import storage


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    return tmp_path


def test_validate_id_accepts_uuid4(isolated_data_dir):
    # uuid.uuid4()-shaped string
    storage.create_conversation("abcdef12-3456-4789-89ab-cdef01234567")  # noqa


def test_validate_id_rejects_traversal(isolated_data_dir):
    with pytest.raises(ValueError):
        storage.create_conversation("../etc/passwd")
    with pytest.raises(ValueError):
        storage.create_conversation("../../some/path")


def test_validate_id_rejects_empty(isolated_data_dir):
    with pytest.raises(ValueError):
        storage.create_conversation("")


def test_validate_id_rejects_non_uuid(isolated_data_dir):
    with pytest.raises(ValueError):
        storage.create_conversation("hello")
    with pytest.raises(ValueError):
        storage.create_conversation("not-a-uuid")


def test_get_returns_none_for_missing(isolated_data_dir):
    assert storage.get_conversation(
        "00000000-0000-4000-8000-000000000000",
    ) is None


def test_create_then_read_roundtrip(isolated_data_dir):
    cid = "11111111-2222-4333-8444-555555555555"
    created = storage.create_conversation(cid)
    assert created["id"] == cid
    assert created["title"] is None
    assert created["messages"] == []

    loaded = storage.get_conversation(cid)
    assert loaded == created


def test_assistant_message_persists_structured_errors(isolated_data_dir):
    """The big behavioral assertion: a chairman error is persisted as
    status=error structured data, not as a string masquerading as a
    real answer."""
    cid = "22222222-3333-4444-8555-666666666666"
    storage.create_conversation(cid)
    storage.add_user_message(cid, "what is 2+2")
    storage.add_assistant_message(
        cid,
        stage1=[
            {"model": "a/x", "status": "ok", "response": "4"},
        ],
        stage2=[],
        stage3={
            "model": "chairman/y",
            "status": "error",
            "error": {"kind": "auth", "detail": "HTTP 401", "retryable": False},
        },
    )
    conv = storage.get_conversation(cid)
    assert len(conv["messages"]) == 2
    asst = conv["messages"][1]
    assert asst["role"] == "assistant"
    assert asst["stage3"]["status"] == "error"
    assert asst["stage3"]["error"]["kind"] == "auth"
    # The misleading legacy string must not appear anywhere
    raw = json.dumps(conv)
    assert "Error: Unable to generate final synthesis" not in raw


def test_list_conversations_skips_malformed_files(isolated_data_dir):
    cid = "33333333-4444-4555-8666-777777777777"
    storage.create_conversation(cid)
    # Drop a malformed file in the data dir
    bad = isolated_data_dir / "44444444-5555-4666-8777-888888888888.json"
    bad.write_text("{not valid json")
    # Drop a non-JSON file
    (isolated_data_dir / "junk.txt").write_text("hello")
    # Drop a JSON file with non-UUID name
    (isolated_data_dir / "not-a-uuid.json").write_text(json.dumps({
        "id": "not-a-uuid", "created_at": "now", "messages": [],
    }))

    convs = storage.list_conversations()
    # Only the valid conversation should appear; malformed ones are skipped.
    assert len(convs) == 1
    assert convs[0]["id"] == cid


def test_list_conversations_uses_placeholder_for_none_title(isolated_data_dir):
    cid = "55555555-6666-4777-8888-999999999999"
    storage.create_conversation(cid)
    convs = storage.list_conversations()
    assert convs[0]["title"] == "New Conversation"
