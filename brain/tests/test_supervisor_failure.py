from brain.agents.base import SarvamReasoningBudgetError
from brain.db import Database
from brain.events import EventHub
from brain import supervisor


def test_resolver_failure_event_keeps_raw_reply_and_agent_label(monkeypatch, tmp_path):
    database = Database(tmp_path / "brain.sqlite3")
    events = EventHub(database)
    case_id = supervisor.create_case(
        "C1",
        "TXN1015",
        "My payment failed.",
        "en",
        "web-1",
        database,
        data={
            "precomputed_concierge": {
                "intent": "failed_payment",
                "transaction_id": "TXN1015",
                "summary": "My payment failed.",
            }
        },
    )

    async def mocked_investigate(self, customer_id, txn_id):
        return {
            "txn_id": txn_id,
            "customer_id": customer_id,
            "transaction_customer_id": customer_id,
            "status": "FAILED",
            "retryable": True,
            "amount": 1250,
        }

    def fail_resolution(self, case_type, facts):
        raise SarvamReasoningBudgetError(
            [
                {
                    "usage": {"prompt_tokens": 40, "completion_tokens": 3000},
                    "request_max_tokens": 3000,
                    "reasoning_effort": "low",
                    "reasoning_effort_sent": True,
                    "input_length": 600,
                    "elapsed_s": 90.5,
                    "elapsed_ms": 90500,
                    "content_length": 0,
                    "reasoning_content_length": 0,
                    "attempt": 2,
                }
            ]
        )

    monkeypatch.setattr(supervisor.InvestigatorAgent, "investigate", mocked_investigate)
    monkeypatch.setattr(supervisor.ResolverAgent, "resolve", fail_resolution)

    result = supervisor.run_case(case_id, database, events)
    error_step = next(
        event
        for event in database.list_events(case_id=case_id, limit=100)
        if event["type"] == "step" and event["kind"] == "error"
    )

    assert result["status"] == "escalated"
    assert error_step["agent"] == "resolver"
    assert error_step["payload"]["exception_class"] == "SarvamReasoningBudgetError"
    assert error_step["payload"]["finish_reason"] == "length"
    assert error_step["payload"]["raw_reply"] == ""
    assert error_step["payload"]["usage"] == {
        "prompt_tokens": 40,
        "completion_tokens": 3000,
    }
    assert error_step["payload"]["request_max_tokens"] == 3000
    assert error_step["payload"]["reasoning_effort"] == "low"
    assert error_step["payload"]["reasoning_effort_sent"] is True
    assert error_step["payload"]["input_length"] == 600
    assert error_step["payload"]["elapsed_s"] == 90.5
    assert error_step["payload"]["elapsed_ms"] == 90500
    assert error_step["payload"]["content_length"] == 0
    assert error_step["payload"]["attempt"] == 2
    assert error_step["payload"]["sarvam_attempts"]
    assert error_step["payload"]["traceback_lines"]
