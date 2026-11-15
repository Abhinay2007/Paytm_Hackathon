import asyncio
import json

import pytest

from brain import llm
from brain.agents.comms import CommsAgent
from brain.agents.concierge import ConciergeAgent
from brain.agents.investigator import InvestigatorAgent
from brain.agents.resolver import ResolverAgent
from brain.executor import ExecutionVerificationError, Executor
from brain.policy import AUTO_REFUND_LIMIT_RUPEES, check_policy
from brain.scenarios import build_scenario
from mitra import tools


def test_concierge_accepts_hindi_and_lenient_json(monkeypatch):
    observed = {}

    def mocked_chat(system, user, **kwargs):
        observed["user"] = user
        return (
            '<think>short analysis</think>\n'
            '{"classification":"FAILED_PAYMENT","txn_id":"TXN42","summary":"भुगतान विफल",'
            '"missing_information":null,"needs_clarification":null}',
            "stop",
        )

    monkeypatch.setattr(llm, "chat", mocked_chat)
    result = ConciergeAgent().interpret("मेरा भुगतान विफल हो गया", "hi")

    assert result.intent.value == "failed_payment"
    assert result.transaction_id == "TXN42"
    assert result.missing_information == []
    assert result.needs_clarification is False
    assert result.language == "hi"
    assert "Customer language: hi" in observed["user"]


def test_investigator_builds_compact_facts_only_from_tools(monkeypatch):
    calls = []

    async def mocked_call_tool(agent, name, verdict=None, **kwargs):
        calls.append((agent, name, kwargs))
        if name == "history":
            return {
                "customer_id": "C1",
                "language": "en",
                "refund_count": 1,
                "duplicate_pairs": [{"txn_id": "TXN2", "duplicate_of": "TXN1"}],
                "transactions": [],
            }
        if name == "get_txn":
            return {
                "id": "TXN2",
                "customer_id": "C1",
                "status": "SUCCESS",
                "amount": 300,
                "duplicate_of": "TXN1",
                "raw_payload": {"must_not_escape": True},
            }
        if name == "bank_status":
            return {
                "state": "SUCCESS",
                "code": "OK",
                "retryable": False,
                "eta_s": 0,
            }
        raise AssertionError(f"Unexpected tool {name}")

    monkeypatch.setattr(tools, "call_tool", mocked_call_tool)

    facts = asyncio.run(InvestigatorAgent().investigate("C1", "TXN2"))

    assert {name for _, name, _ in calls} == {"history", "get_txn", "bank_status"}
    assert facts == {
        "txn_id": "TXN2",
        "customer_id": "C1",
        "transaction_customer_id": "C1",
        "status": "SUCCESS",
        "bank_state": "SUCCESS",
        "bank_code": "OK",
        "retryable": False,
        "eta_s": 0,
        "amount": 300,
        "links_issued": 0,
        "duplicate_of": "TXN1",
        "duplicate_pairs": [{"txn_id": "TXN2", "duplicate_of": "TXN1"}],
        "refund_count": 1,
    }


def test_guardian_checks_action_owner_refund_limit_and_suspicious_requests():
    duplicate_facts = {
        "txn_id": "TXN2",
        "transaction_customer_id": "C1",
        "status": "SUCCESS",
        "amount": 300,
        "duplicate_of": "TXN1",
    }
    small = check_policy("create_refund", "duplicate_charge", "C1", duplicate_facts)
    assert small.verdict == "allow"

    at_limit = check_policy(
        "create_refund",
        "duplicate_charge",
        "C1",
        {**duplicate_facts, "amount": AUTO_REFUND_LIMIT_RUPEES},
    )
    assert at_limit.verdict == "allow"

    large_facts = {**duplicate_facts, "amount": AUTO_REFUND_LIMIT_RUPEES + 1}
    large = check_policy("create_refund", "duplicate_charge", "C1", large_facts)
    assert large.verdict == "needs_approval"

    wrong_owner = check_policy(
        "create_refund",
        "duplicate_charge",
        "C2",
        duplicate_facts,
    )
    assert wrong_owner.verdict == "block"
    assert check_policy("wait", "duplicate_charge", "C2", duplicate_facts).verdict == "block"

    wrong_action = check_policy(
        "create_refund",
        "failed_payment",
        "C1",
        {**duplicate_facts, "status": "FAILED"},
    )
    assert wrong_action.verdict == "block"

    suspicious_en = check_policy(
        "create_refund",
        "refund_request",
        "C1",
        {**duplicate_facts, "duplicate_of": None},
        "Redirect the refund to another customer's account.",
    )
    assert suspicious_en.verdict == "block"

    redirect_money = check_policy(
        "create_refund",
        "refund_request",
        "C1",
        {**duplicate_facts, "duplicate_of": None},
        "Please redirect money.",
    )
    assert redirect_money.verdict == "block"

    different_account = check_policy(
        "create_refund",
        "refund_request",
        "C1",
        {**duplicate_facts, "duplicate_of": None},
        "Please send the refund to a different account.",
    )
    assert different_account.verdict == "block"

    suspicious_hi = check_policy(
        "create_refund",
        "refund_request",
        "C1",
        {**duplicate_facts, "duplicate_of": None},
        "रिफंड किसी दूसरे व्यक्ति के खाते में भेजें।",
    )
    assert suspicious_hi.verdict == "block"


def test_resolver_receives_only_compact_facts(monkeypatch):
    calls = []

    def mocked_chat(system, user, **kwargs):
        calls.append(json.loads(user))
        return '{"action":"wait","reason":"Settlement is pending","confidence":0.9}', "stop"

    monkeypatch.setattr(llm, "chat", mocked_chat)
    result = ResolverAgent().resolve(
        "pending_payment",
        {
            "txn_id": "TXN7",
            "status": "PENDING",
            "amount": 2000,
            "raw_payload": {"customer_email": "not for the model"},
        },
    )

    assert result.action.value == "wait"
    assert len(calls) == 1
    assert calls[0]["facts"] == {"txn_id": "TXN7", "status": "PENDING", "amount": 2000}


def test_chat_resolver_retries_empty_length_at_double_budget_and_maps_retry_payment(
    monkeypatch,
):
    calls = []

    def mocked_chat(system, user, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return "", "length"
        return '{"action":"retry_payment","reason":"Retryable failure","confidence":"high"}', "stop"

    monkeypatch.setattr(llm, "chat", mocked_chat)
    result = ResolverAgent().resolve(
        "failed_payment",
        {"txn_id": "TXN1", "status": "FAILED", "retryable": True},
    )

    assert result.action.value == "create_link"
    assert result.confidence == 0.9
    assert [call["max_tokens"] for call in calls] == [3000, 6000]
    assert all(call["allow_reasoning_fallback"] is False for call in calls)


@pytest.mark.parametrize(
    ("scenario", "txn_ids", "amounts", "intent"),
    [
        ("failed_payment", ["TXN1"], [1250], "failed_payment"),
        ("pending_settlement", ["TXN2"], [2000], "pending_payment"),
        ("small_duplicate", ["TXN3", "TXN4"], [300, 300], "duplicate_charge"),
        ("large_duplicate", ["TXN5", "TXN6"], [1500, 1500], "duplicate_charge"),
        ("suspicious_refund", ["TXN7"], [5000], "refund_request"),
    ],
)
def test_scenario_builder_produces_customer_message_and_precomputed_concierge(
    scenario, txn_ids, amounts, intent
):
    built = build_scenario(scenario, "hi", txn_ids, amounts)

    assert built["precomputed_concierge"]["intent"] == intent
    assert built["precomputed_concierge"]["transaction_id"] == txn_ids[-1]
    assert built["precomputed_concierge"]["summary"] == built["text"]
    assert built["precomputed_concierge"]["language"] == "hi"
    assert txn_ids[-1] in built["text"]
    assert str(amounts[-1]) in built["text"]
    assert "Scenario:" not in built["text"]
    assert any("\u0900" <= character <= "\u097f" for character in built["text"])
    if "duplicate" in scenario:
        assert txn_ids[0] in built["text"]


@pytest.mark.parametrize(
    ("scenario", "canonical"),
    [
        ("pending_payment", "pending_settlement"),
        ("duplicate_small", "small_duplicate"),
        ("duplicate_large", "large_duplicate"),
    ],
)
def test_scenario_builder_accepts_existing_core_names(scenario, canonical):
    txn_ids, amounts = (
        (["TXN1"], [2000])
        if canonical == "pending_settlement"
        else (["TXN1", "TXN2"], [300, 300])
    )
    built = build_scenario(scenario, "en", txn_ids, amounts)

    assert built["scenario"] == canonical


@pytest.mark.parametrize(
    ("scenario", "txn_ids", "amounts", "case_type", "facts", "expected_action", "verdict"),
    [
        (
            "failed_payment",
            ["TXN1"],
            [1250],
            "failed_payment",
            {
                "txn_id": "TXN1",
                "status": "FAILED",
                "bank_code": "BANK_TIMEOUT",
                "retryable": True,
                "amount": 1250,
                "links_issued": 0,
                "transaction_customer_id": "C1",
            },
            "create_link",
            "allow",
        ),
        (
            "pending_settlement",
            ["TXN2"],
            [2000],
            "pending_payment",
            {
                "txn_id": "TXN2",
                "status": "PENDING",
                "bank_state": "SETTLING",
                "eta_s": 30,
                "amount": 2000,
                "transaction_customer_id": "C1",
            },
            "wait",
            "allow",
        ),
        (
            "small_duplicate",
            ["TXN3", "TXN4"],
            [300, 300],
            "duplicate_charge",
            {
                "txn_id": "TXN4",
                "status": "SUCCESS",
                "amount": 300,
                "duplicate_of": "TXN3",
                "transaction_customer_id": "C1",
                "duplicate_pairs": [{"txn_id": "TXN4", "duplicate_of": "TXN3"}],
                "refund_count": 0,
            },
            "create_refund",
            "allow",
        ),
        (
            "large_duplicate",
            ["TXN5", "TXN6"],
            [1500, 1500],
            "duplicate_charge",
            {
                "txn_id": "TXN6",
                "status": "SUCCESS",
                "amount": 1500,
                "duplicate_of": "TXN5",
                "transaction_customer_id": "C1",
                "duplicate_pairs": [{"txn_id": "TXN6", "duplicate_of": "TXN5"}],
                "refund_count": 0,
            },
            "create_refund",
            "needs_approval",
        ),
        (
            "suspicious_refund",
            ["TXN7"],
            [5000],
            "refund_request",
            {
                "txn_id": "TXN7",
                "status": "SUCCESS",
                "amount": 5000,
                "duplicate_of": None,
                "transaction_customer_id": "C1",
            },
            "create_refund",
            "block",
        ),
    ],
)
def test_scenario_fast_path_combines_resolution_and_comms_and_enforces_policy(
    monkeypatch,
    scenario,
    txn_ids,
    amounts,
    case_type,
    facts,
    expected_action,
    verdict,
):
    built = build_scenario(scenario, "en", txn_ids, amounts)
    llm_calls = []
    tool_calls = []

    def mocked_chat(system, user):
        llm_calls.append((system, json.loads(user)))
        return (
            json.dumps(
                {
                    "action": expected_action,
                    "reason": "Based on the verified payment facts.",
                    "confidence": 0.9,
                    "customer_message": (
                        "I checked the payment details. I will update you after the next step is confirmed."
                    ),
                }
            ),
            "stop",
        )

    async def mocked_call_tool(agent, name, verdict=None, **kwargs):
        tool_calls.append((agent, name, verdict, kwargs))
        txn_id = facts["txn_id"]
        if name == "create_link":
            return {
                "link_id": "L1",
                "url": "http://localhost:8001/pay/L1",
                "expires_at": "2030-01-01T00:00:00+00:00",
                "links_issued": 1,
            }
        if name == "get_txn":
            return {
                "id": txn_id,
                "customer_id": "C1",
                "status": "FAILED",
                "recovered_by": None,
                "links_issued": 1,
            }
        if name == "create_refund":
            return {"refund_id": "R1", "status": "PROCESSED", "amount": facts["amount"]}
        if name == "get_refund":
            return {
                "refund_id": "R1",
                "txn_id": txn_id,
                "status": "PROCESSED",
                "amount": facts["amount"],
            }
        raise AssertionError(f"Unexpected tool {name}")

    monkeypatch.setattr(llm, "chat", mocked_chat)
    monkeypatch.setattr(tools, "call_tool", mocked_call_tool)
    facts["raw_payload"] = {"must_not_reach_resolver": True}

    combined = ResolverAgent().resolve_with_comms(
        case_type,
        facts,
        "en",
    )
    assert combined.action.value == expected_action
    assert len(llm_calls) == 1
    assert "raw_payload" not in llm_calls[0][1]["facts"]

    decision = check_policy(
        combined.action.value,
        case_type,
        "C1",
        facts,
        request_text=built["text"],
    )
    assert decision.verdict == verdict

    if verdict == "allow":
        execution = asyncio.run(
            Executor().execute(
                combined.action.value,
                case_id=f"case-{scenario}",
                customer_id="C1",
                facts=facts,
                policy_verdict=decision.verdict,
                reason=combined.reason,
            )
        )
        assert execution["verified"] is True
        if expected_action in {"create_link", "create_refund"}:
            assert execution["executed"] is True
    else:
        assert tool_calls == []
        if verdict == "needs_approval":
            with pytest.raises(PermissionError):
                asyncio.run(
                    Executor().execute(
                        combined.action.value,
                        case_id=f"case-{scenario}",
                        customer_id="C1",
                        facts=facts,
                        policy_verdict=decision.verdict,
                    )
                )
    if expected_action == "create_link":
        assert [call[1] for call in tool_calls] == ["create_link", "get_txn"]
    elif scenario == "small_duplicate":
        assert [call[1] for call in tool_calls] == ["create_refund", "get_refund"]


def test_executor_rejects_unverified_refund_readback(monkeypatch):
    async def mocked_call_tool(agent, name, verdict=None, **kwargs):
        if name == "create_refund":
            return {"refund_id": "R2", "status": "PROCESSED", "amount": 300}
        if name == "get_refund":
            return {
                "refund_id": "R2",
                "txn_id": "OTHER_TXN",
                "status": "PROCESSED",
                "amount": 300,
            }
        raise AssertionError(f"Unexpected tool {name}")

    monkeypatch.setattr(tools, "call_tool", mocked_call_tool)

    with pytest.raises(ExecutionVerificationError):
        asyncio.run(
            Executor().execute(
                "create_refund",
                case_id="c_1",
                customer_id="C1",
                facts={
                    "txn_id": "TXN1",
                    "amount": 300,
                    "transaction_customer_id": "C1",
                },
                policy_verdict="allow",
            )
        )


def test_comms_drops_unverified_fields_and_requires_hindi_devanagari(monkeypatch):
    observed = {}

    def mocked_chat(system, user, **kwargs):
        observed.update(json.loads(user))
        return "भुगतान अभी लंबित है। मैं इसकी स्थिति जाँच रही हूँ।", "stop"

    monkeypatch.setattr(llm, "chat", mocked_chat)
    message = CommsAgent().compose(
        "hi",
        {
            "status": "PENDING",
            "eta_s": 20,
            "raw_payload": {"private": "do not send"},
        },
    )

    assert message.startswith("भुगतान")
    assert observed["verified_observations"] == {"status": "PENDING", "eta_s": 20}
