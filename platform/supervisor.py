"""Brain workflow orchestration for customer payment cases."""

import hashlib
import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from platform.agents.comms import CommsAgent
from platform.agents.concierge import ConciergeAgent
from platform.agents.investigator import InvestigatorAgent
from platform.agents.resolver import ResolverAgent
from platform.db import CaseNotFoundError, Database
from platform.events import CaseEvent, EventHub
from platform.policy import PolicyDecision, check_policy

_database: Database | None = None
_event_hub: EventHub | None = None
_INITIAL_STATUS = "received"
_PROCESSING_STATUS = "processing"
_APPROVAL_STATUS = "awaiting_approval"


def _runtime() -> tuple[Database, EventHub]:
    global _database, _event_hub
    if _database is None:
        _database = Database()
    if _event_hub is None or _event_hub.database is not _database:
        _event_hub = EventHub(_database)
    return _database, _event_hub


def _publish(case_id: str, agent: str, kind: str, summary: str) -> None:
    _, events = _runtime()
    events.publish(
        CaseEvent(type="step", case_id=case_id, agent=agent, kind=kind, summary=summary)
    )


def _save(case_id: str, **updates: Any) -> dict[str, Any]:
    database, _ = _runtime()
    return database.update_case(case_id, updates)


def _fingerprint(
    proposal: Mapping[str, Any], context: Mapping[str, Any], action: str
) -> str:
    serialized = json.dumps(
        {"action": action, "proposal": proposal, "context": context},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _approval_snapshot_is_current(
    case: Mapping[str, Any], pending: Mapping[str, Any]
) -> bool:
    data = case["data"]
    proposal = pending["proposal"]
    context = pending["context"]
    return (
        data.get("resolution") == proposal
        and data.get("policy_action") == pending["action"]
        and _context_from_case(case) == context
        and _fingerprint(proposal, context, pending["action"]) == pending["fingerprint"]
    )


def _approval_expected(case: Mapping[str, Any], pending: Mapping[str, Any]) -> dict[str, Any]:
    data = case["data"]
    return {
        "status": _APPROVAL_STATUS,
        "pending_approval": dict(pending),
        "resolution": data.get("resolution"),
        "policy_action": data.get("policy_action"),
        "evidence": data.get("evidence"),
        "text": data.get("text"),
        "concierge": data.get("concierge"),
        "investigation": data.get("investigation"),
    }


def _context_from_case(case: Mapping[str, Any]) -> dict[str, Any]:
    data = case["data"]
    context: dict[str, Any] = {
        "case_id": case["case_id"],
        "customer_message": data["text"],
        "concierge": data["concierge"],
        "investigation": data["investigation"],
    }
    supplied_evidence = data.get("evidence")
    if isinstance(supplied_evidence, Mapping):
        context["supplied_evidence"] = dict(supplied_evidence)
        for key in ("transaction", "bank_status"):
            value = supplied_evidence.get(key)
            if isinstance(value, Mapping):
                context[key] = dict(value)
    return context


def _recommendation_path(
    recommendation: str,
    concierge_intent: str,
    context: Mapping[str, Any],
) -> tuple[str, str | None]:
    """Return workflow disposition and an optional real policy action."""
    if recommendation == "wait":
        return "waiting", None
    if recommendation == "request_more_information":
        return "needs_information", None
    if recommendation == "no_action":
        return "no_action", None
    if recommendation == "human_review":
        transaction = context.get("transaction")
        duplicate_evidence = (
            isinstance(transaction, Mapping) and transaction.get("duplicate_of") is not None
        )
        if concierge_intent == "duplicate_charge" or duplicate_evidence:
            return "protected_action", "resolve_duplicate_charge"
        return "human_review", None
    if recommendation == "recommend_payment_link":
        return "protected_action", "create_payment_link"
    if recommendation == "recommend_refund_review":
        return "protected_action", "refund"
    if recommendation == "check_payment_status":
        return "read_only_action", "check_payment_status"
    raise ValueError(f"Unsupported Resolver recommendation: {recommendation}")


def _is_approval_gate(action: str, decision: PolicyDecision) -> bool:
    if decision.allowed or not decision.requires_human_approval:
        return False
    if action == "refund":
        return decision.reason == "Refunds require human approval."
    if action == "resolve_duplicate_charge":
        return decision.reason == "Suspected duplicate-charge resolution requires human approval."
    return False


def _comms(context: dict[str, Any], resolution: dict[str, Any], decision: dict[str, Any]):
    pending_decision = decision.get("reason", "No payment action proposed.")
    if decision.get("approval_pending"):
        pending_decision = f"Human approval for {decision.get('action', 'protected action')}"
    messages = CommsAgent().compose(
        {
            **context,
            "resolution": resolution,
            "guardian": decision,
            "pending_decision": pending_decision,
        }
    )
    return messages.model_dump(mode="json")


def handle_inbound(chat_id: str, text: str) -> str:
    """Create and process a case for an inbound customer message."""
    database, _ = _runtime()
    case_id = f"c_{uuid4().hex[:12]}"
    database.create_case(
        case_id,
        {"chat_id": chat_id, "text": text, "status": _INITIAL_STATUS, "approval": None},
    )
    _publish(case_id, "supervisor", "workflow", "Customer message received.")
    run_case(case_id)
    return case_id


def run_case(case_id: str) -> dict[str, Any]:
    """Run a received or explicitly stale case; active and terminal cases cannot re-enter."""
    database, _ = _runtime()
    case = database.get_case(case_id)
    if case is None:
        raise CaseNotFoundError(case_id)
    data = case["data"]
    current_status = data.get("status")
    if current_status not in {_INITIAL_STATUS, "approval_stale"}:
        raise ValueError("Case is not in a restartable state")
    approval_history = list(data.get("approval_history", []))
    if isinstance(data.get("approval"), Mapping):
        approval_history.append(data["approval"])
    if not database.compare_and_set_case(
        case_id,
        {
            "status": current_status,
            "text": data["text"],
            "evidence": data.get("evidence"),
        },
        {
            "status": _PROCESSING_STATUS,
            "approval": None,
            "approval_history": approval_history,
            "pending_approval": None,
            "policy_action": None,
            "policy_decision": None,
            "messages": None,
        },
    ):
        raise ValueError("Case state changed or is not restartable")

    try:
        concierge = ConciergeAgent().interpret(case["data"]["text"])
        concierge_data = concierge.model_dump(mode="json")
        _save(case_id, concierge=concierge_data)
        _publish(case_id, "concierge", "llm", concierge.summary)

        investigation = InvestigatorAgent().investigate(
            {"customer_message": case["data"]["text"], "concierge": concierge_data}
        )
        investigation_data = investigation.model_dump(mode="json")
        _save(case_id, investigation=investigation_data)
        _publish(
            case_id,
            "investigator",
            "llm",
            f"{investigation.likely_cause} (confidence {investigation.confidence:.2f})",
        )

        # Evidence is consumed only when already supplied in the persisted case.
        # No transaction or bank data is inferred or fetched here.
        current_case = database.get_case(case_id)
        assert current_case is not None
        current_case["data"]["concierge"] = concierge_data
        current_case["data"]["investigation"] = investigation_data
        context = _context_from_case(current_case)

        resolution = ResolverAgent().resolve(context)
        resolution_data = resolution.model_dump(mode="json")
        _save(case_id, resolution=resolution_data)
        _publish(case_id, "resolver", "llm", f"{resolution.recommendation}: {resolution.rationale}")

        disposition, policy_action = _recommendation_path(
            resolution.recommendation, concierge.intent, context
        )
        guardian_data: dict[str, Any]
        final_status: str
        pending_approval: dict[str, Any] | None = None

        if policy_action is None:
            guardian_data = {
                "allowed": True,
                "reason": f"No payment action proposed; workflow disposition is {disposition}.",
                "requires_human_approval": False,
                "disposition": disposition,
                "policy_checked": False,
            }
            final_status = disposition
        else:
            decision = check_policy(policy_action, context, human_approved=False)
            guardian_data = {**decision.model_dump(mode="json"), "action": policy_action}
            if decision.allowed:
                final_status = "ready_for_body" if disposition == "protected_action" else "read_only_action"
            elif _is_approval_gate(policy_action, decision):
                final_status = _APPROVAL_STATUS
                approval_context = dict(context)
                pending_approval = {
                    "action": policy_action,
                    "proposal": resolution_data,
                    "context": approval_context,
                    "fingerprint": _fingerprint(resolution_data, approval_context, policy_action),
                }
            else:
                final_status = "blocked"
            guardian_data["disposition"] = disposition
            guardian_data["approval_pending"] = final_status == _APPROVAL_STATUS

        updates: dict[str, Any] = {
            "status": _PROCESSING_STATUS,
            "policy_action": policy_action,
            "policy_decision": guardian_data,
        }
        if pending_approval is not None:
            updates["pending_approval"] = pending_approval
        _save(case_id, **updates)
        _publish(case_id, "guardian", "policy", guardian_data["reason"])

        messages_data = _comms(context, resolution_data, guardian_data)
        _save(case_id, messages=messages_data, status=final_status)
        _publish(case_id, "comms", "llm", "Customer message and desk brief prepared.")
        return database.get_case(case_id) or {}
    except Exception as exc:
        _save(case_id, status="failed", failure_type=type(exc).__name__)
        _publish(case_id, "supervisor", "error", f"Case processing failed ({type(exc).__name__}).")
        raise


def _resume_approved_case(case_id: str, pending: Mapping[str, Any]) -> None:
    """Revalidate and continue exactly the persisted proposal that was approved."""
    database, _ = _runtime()
    case = database.get_case(case_id)
    if case is None:
        raise CaseNotFoundError(case_id)
    proposal = pending["proposal"]
    context = pending["context"]
    approval = case["data"].get("approval")
    if (
        case["data"].get("status") != "approval_processing"
        or not isinstance(approval, Mapping)
        or approval.get("approved") is not True
        or not isinstance(approval.get("by"), str)
        or not approval["by"].strip()
        or approval.get("fingerprint") != pending.get("fingerprint")
    ):
        raise ValueError("No valid persisted approval exists for this proposal")
    if not _approval_snapshot_is_current(case, pending):
        database.compare_and_set_case(
            case_id,
            {"status": "approval_processing"},
            {"status": "approval_stale"},
        )
        _publish(case_id, "guardian", "policy", "Approved proposal or evidence changed; new approval is required.")
        raise ValueError("Approved proposal or evidence changed; new approval is required")

    action = pending["action"]
    decision = check_policy(action, context, human_approved=True)
    guardian_data = {**decision.model_dump(mode="json"), "action": action}
    final_status = "ready_for_body" if decision.allowed else "blocked"
    guardian_data["approval_pending"] = False
    guardian_data["approved_by"] = approval["by"]
    messages_data = _comms(context, proposal, guardian_data)
    updates = {
        "policy_decision": guardian_data,
        "messages": messages_data,
        "status": final_status,
    }
    if not database.compare_and_set_case(
        case_id,
        {
            "status": "approval_processing",
            "pending_approval": dict(pending),
            "resolution": proposal,
            "policy_action": action,
            "evidence": case["data"].get("evidence"),
            "text": case["data"].get("text"),
            "concierge": case["data"].get("concierge"),
            "investigation": case["data"].get("investigation"),
            "approval": case["data"].get("approval"),
        },
        updates,
    ):
        database.compare_and_set_case(
            case_id,
            {"status": "approval_processing"},
            {"status": "approval_stale"},
        )
        _publish(case_id, "guardian", "policy", "Approved proposal or evidence changed; new approval is required.")
        raise ValueError("Approved proposal or evidence changed; new approval is required")
    _publish(case_id, "guardian", "policy", decision.reason)
    _publish(case_id, "comms", "llm", "Customer message and desk brief prepared.")


def decide_case(case_id: str, approved: bool, by: str) -> None:
    """Atomically record one human decision and resume or stop the case."""
    if type(approved) is not bool:
        raise TypeError("approved must be a bool")
    if not by.strip():
        raise ValueError("by must identify the human decision maker")
    database, _ = _runtime()
    case = database.get_case(case_id)
    if case is None:
        raise CaseNotFoundError(case_id)
    data = case["data"]
    if data.get("status") != _APPROVAL_STATUS:
        raise ValueError("Case is not awaiting human approval")
    pending = data.get("pending_approval")
    if not isinstance(pending, Mapping):
        raise ValueError("Case has no persisted approval proposal")

    if not _approval_snapshot_is_current(case, pending):
        if database.compare_and_set_case(
            case_id,
            {"status": _APPROVAL_STATUS, "pending_approval": dict(pending)},
            {"status": "approval_stale"},
        ):
            _publish(case_id, "guardian", "policy", "Approved proposal or evidence changed; new approval is required.")
        raise ValueError("Approval proposal or evidence changed; new approval is required")

    updates = {
        "status": "approval_processing" if approved else "rejected",
        "approval": {
            "approved": approved,
            "by": by,
            "fingerprint": pending["fingerprint"],
        },
    }
    expected = _approval_expected(case, pending)
    if not database.compare_and_set_case(case_id, expected, updates):
        current = database.get_case(case_id)
        if (
            current is not None
            and current["data"].get("status") == _APPROVAL_STATUS
            and current["data"].get("pending_approval") == dict(pending)
            and not _approval_snapshot_is_current(current, pending)
            and database.compare_and_set_case(
                case_id,
                _approval_expected(current, pending),
                {"status": "approval_stale"},
            )
        ):
            _publish(case_id, "guardian", "policy", "Approved proposal or evidence changed; new approval is required.")
            raise ValueError("Approval proposal or evidence changed; new approval is required")
        raise ValueError("Case decision was already recorded or the approval proposal changed")

    _publish(
        case_id,
        "supervisor",
        "approval",
        f"Human {'approved' if approved else 'rejected'} the proposed action ({by}).",
    )
    if approved:
        try:
            _resume_approved_case(case_id, pending)
        except Exception:
            database.compare_and_set_case(
                case_id,
                {"status": "approval_processing"},
                {"status": "failed"},
            )
            raise
