import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from platform import supervisor
from platform.agents import base
from platform.db import Database
from platform.events import EventHub

EVENT_FIELDS = {"type", "case_id", "agent", "kind", "summary"}


def install_test_runtime(monkeypatch, tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'supervisor.sqlite3'}")
    event_hub = EventHub(database)
    monkeypatch.setattr(supervisor, "_database", database)
    monkeypatch.setattr(supervisor, "_event_hub", event_hub)
    return database, event_hub


def install_agent_responses(monkeypatch, *, recommendation="request_more_information"):
    calls = []

    def fake_chat(system, user):
        calls.append((system, user))
        if "You are the Concierge" in system:
            lowered = user.lower()
            intent = (
                "refund_request" if "refund" in lowered else
                "duplicate_charge" if "duplicate" in lowered or "charged twice" in lowered else
                "payment_status"
            )
            return json.dumps({
                "intent": intent,
                "transaction_id": None,
                "summary": "Customer asks about a payment.",
                "missing_information": ["transaction_id"],
                "needs_clarification": True,
            })
        if "You are the Investigator" in system:
            return json.dumps({
                "likely_cause": "Unknown",
                "confirmed_facts": [],
                "hypotheses": [],
                "confidence": 0.2,
                "retryable": None,
                "recommended_next_step": "Request transaction details.",
            })
        if "You are the Resolver" in system:
            return json.dumps({
                "recommendation": recommendation,
                "rationale": "Based on the supplied case context.",
                "requires_human_approval": recommendation in {
                    "recommend_refund_review", "human_review"
                },
                "needs_more_information": recommendation == "request_more_information",
                "evidence_gaps": ["transaction_id"] if recommendation == "request_more_information" else [],
            })
        if "You are Comms" in system:
            return json.dumps({
                "customer_message": "Please share the transaction reference so we can review this.",
                "desk_brief": "Payment query; evidence and pending decision are summarized.",
                "language": "English",
            })
        raise AssertionError("Unexpected agent prompt")

    monkeypatch.setattr(base.llm, "chat", fake_chat)
    return calls


def create_case(database, *, text="Payment question", evidence=None):
    case_id = "c_test"
    data = {"chat_id": "chat-1", "text": text, "status": "received", "approval": None}
    if evidence is not None:
        data["evidence"] = evidence
    database.create_case(case_id, data)
    return case_id


def test_handle_inbound_persists_distinct_disposition_and_exact_event_schema(monkeypatch, tmp_path):
    database, event_hub = install_test_runtime(monkeypatch, tmp_path)
    calls = install_agent_responses(monkeypatch)

    case_id = supervisor.handle_inbound("chat-1", "Where is my payment?")

    case = database.get_case(case_id)
    assert case["data"]["status"] == "needs_information"
    assert case["data"]["policy_decision"]["disposition"] == "needs_information"
    assert case["data"]["policy_decision"]["policy_checked"] is False
    assert len(calls) == 4
    assert [event.agent for event in event_hub.history(case_id)] == [
        "supervisor", "concierge", "investigator", "resolver", "guardian", "comms"
    ]
    assert all(set(event.model_dump()) == EVENT_FIELDS for event in event_hub.history(case_id))


def test_non_action_recommendations_remain_distinct_and_skip_policy(
    monkeypatch, tmp_path
):
    cases = [
        ("wait", "waiting"),
        ("request_more_information", "needs_information"),
        ("no_action", "no_action"),
        ("human_review", "human_review"),
    ]
    for index, (recommendation, expected_status) in enumerate(cases):
        scoped = pytest.MonkeyPatch()
        try:
            database, _ = install_test_runtime(scoped, tmp_path / str(index))
            install_agent_responses(scoped, recommendation=recommendation)
            scoped.setattr(
                supervisor,
                "check_policy",
                lambda *args, **kwargs: pytest.fail("No policy action should be checked"),
            )
            case_id = create_case(database)

            result = supervisor.run_case(case_id)

            assert result["data"]["status"] == expected_status
            assert result["data"]["policy_action"] is None
            assert result["data"]["policy_decision"]["disposition"] == expected_status
        finally:
            scoped.undo()


def test_direct_run_case_cannot_supply_approval_authority(monkeypatch, tmp_path):
    database, _ = install_test_runtime(monkeypatch, tmp_path)
    install_agent_responses(monkeypatch, recommendation="recommend_refund_review")
    case_id = create_case(database, text="I need a refund")
    supervisor.run_case(case_id)
    assert database.get_case(case_id)["data"]["status"] == "awaiting_approval"

    with pytest.raises(TypeError):
        supervisor.run_case(case_id, human_approved=True, approved_action="refund")
    assert database.get_case(case_id)["data"]["status"] == "awaiting_approval"


def test_refund_requires_recorded_approval_and_records_approver(monkeypatch, tmp_path):
    database, event_hub = install_test_runtime(monkeypatch, tmp_path)
    install_agent_responses(monkeypatch, recommendation="recommend_refund_review")
    case_id = create_case(database, text="I need a refund")
    supervisor.run_case(case_id)

    pending = database.get_case(case_id)["data"]
    assert pending["status"] == "awaiting_approval"
    assert pending["policy_decision"]["allowed"] is False
    assert pending["pending_approval"]["action"] == "refund"
    assert "approval_pending" in pending["policy_decision"]
    with pytest.raises(ValueError, match="persisted approval"):
        supervisor._resume_approved_case(case_id, pending["pending_approval"])
    assert database.get_case(case_id)["data"]["status"] == "awaiting_approval"

    supervisor.decide_case(case_id, True, "desk-agent-7")

    updated = database.get_case(case_id)["data"]
    assert updated["approval"]["approved"] is True
    assert updated["approval"]["by"] == "desk-agent-7"
    assert updated["approval"]["fingerprint"] == updated["pending_approval"]["fingerprint"]
    assert updated["policy_decision"]["allowed"] is True
    assert updated["policy_decision"]["approved_by"] == "desk-agent-7"
    assert updated["status"] == "ready_for_body"
    assert all(set(event.model_dump()) == EVENT_FIELDS for event in event_hub.history(case_id))


def test_missing_duplicate_evidence_never_authorizes_resolution(monkeypatch, tmp_path):
    database, _ = install_test_runtime(monkeypatch, tmp_path)
    install_agent_responses(monkeypatch, recommendation="human_review")
    case_id = create_case(database, text="I was charged twice")

    result = supervisor.run_case(case_id)

    data = result["data"]
    assert data["status"] == "blocked"
    assert data["policy_action"] == "resolve_duplicate_charge"
    assert data["policy_decision"]["allowed"] is False
    assert "Duplicate-charge evidence is missing" in data["policy_decision"]["reason"]
    assert data["pending_approval"] is None
    assert "transaction" not in data


def test_valid_duplicate_evidence_requires_human_approval(monkeypatch, tmp_path):
    database, _ = install_test_runtime(monkeypatch, tmp_path)
    install_agent_responses(monkeypatch, recommendation="human_review")
    case_id = create_case(
        database,
        text="I was charged twice",
        evidence={"transaction": {"id": "TXN2002", "duplicate_of": "TXN1001"}},
    )

    supervisor.run_case(case_id)
    assert database.get_case(case_id)["data"]["status"] == "awaiting_approval"
    supervisor.decide_case(case_id, True, "reviewer-1")

    result = database.get_case(case_id)["data"]
    assert result["status"] == "ready_for_body"
    assert result["policy_decision"]["allowed"] is True
    assert result["approval"]["by"] == "reviewer-1"


def test_rejection_stops_case_without_rerunning_agents(monkeypatch, tmp_path):
    database, event_hub = install_test_runtime(monkeypatch, tmp_path)
    calls = install_agent_responses(monkeypatch, recommendation="recommend_refund_review")
    case_id = create_case(database, text="I need a refund")
    supervisor.run_case(case_id)
    call_count = len(calls)

    supervisor.decide_case(case_id, False, "reviewer-2")

    data = database.get_case(case_id)["data"]
    assert data["status"] == "rejected"
    assert data["approval"] == {
        "approved": False,
        "by": "reviewer-2",
        "fingerprint": data["pending_approval"]["fingerprint"],
    }
    assert len(calls) == call_count
    assert event_hub.history(case_id)[-1].kind == "approval"


def test_repeated_approval_is_rejected(monkeypatch, tmp_path):
    database, _ = install_test_runtime(monkeypatch, tmp_path)
    install_agent_responses(monkeypatch, recommendation="recommend_refund_review")
    case_id = create_case(database, text="I need a refund")
    supervisor.run_case(case_id)
    supervisor.decide_case(case_id, True, "reviewer-1")

    with pytest.raises(ValueError, match="not awaiting"):
        supervisor.decide_case(case_id, True, "reviewer-1")


def test_concurrent_approve_and_reject_have_one_winner(monkeypatch, tmp_path):
    database, event_hub = install_test_runtime(monkeypatch, tmp_path)
    install_agent_responses(monkeypatch, recommendation="recommend_refund_review")
    case_id = create_case(database, text="I need a refund")
    supervisor.run_case(case_id)

    def decide(value, by):
        try:
            supervisor.decide_case(case_id, value, by)
            return "accepted"
        except ValueError:
            return "rejected-race"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda args: decide(*args), [(True, "approver"), (False, "rejector")]))

    assert results.count("accepted") == 1
    assert results.count("rejected-race") == 1
    data = database.get_case(case_id)["data"]
    assert data["status"] in {"ready_for_body", "rejected"}
    assert data["approval"]["by"] in {"approver", "rejector"}
    approval_events = [event for event in event_hub.history(case_id) if event.kind == "approval"]
    assert len(approval_events) == 1


def test_changed_proposal_or_evidence_cannot_reuse_approval(monkeypatch, tmp_path):
    database, _ = install_test_runtime(monkeypatch, tmp_path)
    install_agent_responses(monkeypatch, recommendation="recommend_refund_review")
    case_id = create_case(database, text="I need a refund")
    supervisor.run_case(case_id)
    database.update_case(case_id, {"evidence": {"changed": True}})

    with pytest.raises(ValueError, match="changed"):
        supervisor.decide_case(case_id, True, "reviewer-1")

    data = database.get_case(case_id)["data"]
    assert data["status"] == "approval_stale"
    assert data["approval"] is None

    supervisor.run_case(case_id)
    renewed = database.get_case(case_id)["data"]
    assert renewed["status"] == "awaiting_approval"
    assert renewed["pending_approval"]["fingerprint"] != data["pending_approval"]["fingerprint"]
    supervisor.decide_case(case_id, True, "reviewer-2")
    assert database.get_case(case_id)["data"]["status"] == "ready_for_body"


@pytest.mark.parametrize("status", ["needs_information", "rejected", "read_only_action"])
def test_run_case_rejects_invalid_reentry(monkeypatch, tmp_path, status):
    database, _ = install_test_runtime(monkeypatch, tmp_path)
    calls = install_agent_responses(monkeypatch)
    case_id = create_case(database)
    if status == "needs_information":
        supervisor.run_case(case_id)
    elif status == "rejected":
        install_agent_responses(monkeypatch, recommendation="recommend_refund_review")
        database.update_case(case_id, {"text": "refund request"})
        supervisor.run_case(case_id)
        supervisor.decide_case(case_id, False, "reviewer")
    else:
        # Only a policy-authorized read-only proposal is needed to reach a final state.
        monkeypatch.setattr(
            base.llm,
            "chat",
            lambda system, user: json.dumps({
                "recommendation": "check_payment_status",
                "rationale": "Check current status.",
                "requires_human_approval": False,
                "needs_more_information": False,
                "evidence_gaps": [],
            }) if "You are the Resolver" in system else (
                json.dumps({
                    "intent": "payment_status", "transaction_id": None,
                    "summary": "Status query", "missing_information": [],
                    "needs_clarification": False,
                }) if "You are the Concierge" in system else (
                    json.dumps({
                        "likely_cause": "Unknown", "confirmed_facts": [], "hypotheses": [],
                        "confidence": 0.2, "retryable": None,
                        "recommended_next_step": "Read status.",
                    }) if "You are the Investigator" in system else json.dumps({
                        "customer_message": "We are checking.", "desk_brief": "Status query.",
                        "language": "English",
                    })
                )
            ),
        )
        supervisor.run_case(case_id)

    call_count = len(calls)
    with pytest.raises(ValueError, match="restartable"):
        supervisor.run_case(case_id)
    assert len(calls) == call_count
    assert database.get_case(case_id)["data"]["status"] == status
