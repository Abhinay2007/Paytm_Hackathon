import pytest

from platform.db import CaseNotFoundError, Database


def test_database_url_is_read_from_environment_and_cases_can_be_updated(monkeypatch, tmp_path):
    path = tmp_path / "brain.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    database = Database()

    created = database.create_case("c_17", {"status": "new", "chat_id": "chat-1"})
    updated = database.update_case("c_17", {"status": "investigating"})

    assert created["case_id"] == "c_17"
    assert updated["data"] == {"status": "investigating", "chat_id": "chat-1"}
    assert database.get_case("c_17")["data"]["status"] == "investigating"
    assert database.get_case("missing") is None


def test_database_rejects_non_sqlite_urls():
    with pytest.raises(ValueError, match="sqlite"):
        Database("postgresql://example/db")


def test_database_update_missing_case_raises(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'missing.sqlite3'}")

    with pytest.raises(CaseNotFoundError):
        database.update_case("missing", {"status": "open"})


def test_database_case_data_must_be_json_compatible(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'invalid.sqlite3'}")

    with pytest.raises(TypeError):
        database.create_case("invalid", {"unsupported": object()})


def test_compare_and_set_case_applies_only_matching_expected_state(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'cas.sqlite3'}")
    database.create_case("c_1", {"status": "awaiting_approval", "approval": None})

    assert database.compare_and_set_case(
        "c_1",
        {"status": "awaiting_approval", "approval": None},
        {"status": "approval_processing", "approval": {"approved": True}},
    )
    assert not database.compare_and_set_case(
        "c_1",
        {"status": "awaiting_approval"},
        {"status": "rejected"},
    )
    assert database.get_case("c_1")["data"]["status"] == "approval_processing"
