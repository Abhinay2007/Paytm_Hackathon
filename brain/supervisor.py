"""Synchronous case orchestration for the Brain."""

import asyncio
import logging
import re
import time
import traceback
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from brain.agents.comms import CommsAgent
from brain.agents.concierge import ConciergeAgent
from brain.agents.investigator import InvestigatorAgent
from brain.agents.resolver import Action, ResolverAgent, ResolverResult
from brain.db import Database
from brain.events import EventHub
from brain.executor import Executor
from brain.policy import Guardian


logger = logging.getLogger(__name__)
CASE_STATUSES = {"open", "awaiting_approval", "resolved", "escalated"}


def create_case(
    customer_id: str,
    txn_id: str,
    text: str,
    language: str | None,
    chat_id: str | int | None,
    database: Database,
    *,
    data: Mapping[str, Any] | None = None,
) -> str:
    if not isinstance(customer_id, str) or not customer_id.strip():
        raise ValueError("customer_id is required")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("customer message is required")
    case_language = _language_code(language)
    case_id = f"c_{uuid4().hex[:12]}"
    case_data = {
        "text": text,
        **dict(data or {}),
        "customer_id": customer_id,
        "txn_id": txn_id or None,
        "language": case_language,
        "chat_id": chat_id if chat_id is not None else customer_id,
    }
    database.create_case(
        case_id,
        customer_id=customer_id,
        chat_id=chat_id if chat_id is not None else customer_id,
        txn_id=txn_id or None,
        language=case_language,
        status="open",
        data=case_data,
    )
    return case_id


def run_case(case_id: str, database: Database, events: EventHub) -> dict[str, Any]:
    case = database.get_case(case_id)
    if case is None:
        return {"id": case_id, "status": "escalated", "error": "Case not found"}
    if case["status"] != "open":
        return case

    try:
        return asyncio.run(_run_case_async(case_id, database, events))
    except Exception as exc:
        _record_failure(case_id, exc, database, events)
        return database.get_case(case_id) or {
            "id": case_id,
            "status": "escalated",
            "error": type(exc).__name__,
        }


def decide_case(
    case_id: str,
    approved: bool,
    by: str,
    database: Database,
    events: EventHub,
) -> dict[str, Any]:
    if type(approved) is not bool:
        raise ValueError("approved must be a bool")
    if not isinstance(by, str) or not by.strip():
        raise ValueError("by must identify the decision maker")
    case = database.get_case(case_id)
    if case is None:
        raise LookupError(f"Case {case_id!r} does not exist")
    if case["status"] != "awaiting_approval":
        raise ValueError("Case is not awaiting approval")

    try:
        result = asyncio.run(
            _decide_case_async(case_id, approved, by, database, events)
        )
        return result
    except Exception as exc:
        _record_failure(case_id, exc, database, events)
        events.publish(
            {
                "type": "approval_decided",
                "case_id": case_id,
                "approved": approved,
                "status": "escalated",
            }
        )
        return database.get_case(case_id) or {
            "id": case_id,
            "status": "escalated",
            "error": type(exc).__name__,
        }


async def _run_case_async(
    case_id: str,
    database: Database,
    events: EventHub,
) -> dict[str, Any]:
    case = _required_case(database, case_id)
    data = case["data"]
    _publish_case_created(case, events)
    _publish_message(
        events,
        case_id,
        data["text"],
        "customer",
        case["chat_id"],
    )

    language = _language_code(case.get("language"))
    precomputed = data.get("precomputed_concierge")
    if isinstance(precomputed, Mapping):
        concierge = dict(precomputed)
        concierge.setdefault("language", language)
        concierge.setdefault("intent", "other")
        concierge.setdefault("transaction_id", case.get("txn_id"))
        concierge.setdefault("summary", data["text"])
        concierge.setdefault("missing_information", [])
        concierge.setdefault("needs_clarification", False)
        _publish_step(
            events,
            case_id,
            "concierge",
            "system",
            "Used the precomputed scenario classification.",
            True,
            concierge,
            0,
        )
    else:
        started = time.perf_counter()
        concierge_agent = ConciergeAgent()
        concierge_result = concierge_agent.interpret(data["text"], language)
        concierge = concierge_result.model_dump(mode="json")
        _publish_step(
            events,
            case_id,
            "concierge",
            "llm",
            concierge.get("summary") or f"Classified as {concierge['intent']}.",
            True,
            {**concierge, **_sarvam_step_metadata(concierge_agent)},
            _duration_ms(started),
        )
    database.update_case(case_id, {"concierge": concierge, "intent": concierge["intent"]})

    started = time.perf_counter()
    facts = await InvestigatorAgent().investigate(
        case["customer_id"],
        concierge.get("transaction_id") or case.get("txn_id"),
    )
    database.update_case(case_id, {"facts": facts})
    _publish_step(
        events,
        case_id,
        "investigator",
        "tool",
        _investigation_summary(facts),
        True,
        facts,
        _duration_ms(started),
    )

    case_type = _case_type(concierge)
    scenario = data.get("scenario")
    resolver_agent: ResolverAgent | None = None
    started = time.perf_counter()
    if isinstance(scenario, str) and scenario:
        action, reason = _scenario_action(scenario, facts)
        resolution = ResolverResult(
            action=Action(action),
            reason=reason,
            confidence=1.0,
        )
        resolver_kind = "system"
    else:
        resolver_agent = ResolverAgent()
        try:
            resolution = resolver_agent.resolve(case_type, facts)
        except Exception as exc:
            setattr(exc, "_brain_agent", "resolver")
            raise
        resolver_kind = "llm"
    resolution_data = resolution.model_dump(mode="json")
    database.update_case(
        case_id,
        {
            "resolution": resolution_data,
            "plan": {
                "action": resolution.action.value,
                "amount": facts.get("amount"),
                "reason": resolution.reason,
            },
        },
    )
    resolve_duration = _duration_ms(started)
    _publish_step(
        events,
        case_id,
        "resolver",
        resolver_kind,
        f"Selected {resolution.action.value}: {resolution.reason}",
        True,
        {
            "action": resolution.action.value,
            "reason": resolution.reason,
            "confidence": resolution.confidence,
            **(_sarvam_step_metadata(resolver_agent) if resolver_agent else {}),
        },
        resolve_duration,
    )

    started = time.perf_counter()
    decision = Guardian().evaluate(
        resolution.action.value,
        case_type,
        case["customer_id"],
        facts,
        data["text"],
    )
    database.update_case(
        case_id,
        {"verdict": decision.verdict, "verdict_reason": decision.reason},
    )
    _publish_step(
        events,
        case_id,
        "guardian",
        "policy",
        (
            f"Guardian blocked: {decision.reason}"
            if decision.verdict == "block"
            else decision.reason
        ),
        True,
        {
            "verdict": decision.verdict,
            "reason": decision.reason,
            "action": resolution.action.value,
        },
        _duration_ms(started),
    )

    customer_message = ""
    outcome: dict[str, Any]
    verified_observations: dict[str, Any] = dict(facts)
    comms_outcome = "waiting"
    if decision.verdict == "needs_approval":
        pending = {
            "action": resolution.action.value,
            "case_type": case_type,
            "facts": facts,
            "reason": resolution.reason,
        }
        plan = {
            "action": resolution.action.value,
            "amount": facts.get("amount"),
            "reason": resolution.reason,
        }
        approval = {
            "status": "pending",
            "action": _approval_label(resolution.action.value, facts),
            "reason": decision.reason,
        }
        _set_status(
            case_id,
            "awaiting_approval",
            database,
            events,
            pending_action=pending,
            approval=approval,
            plan=plan,
            verdict="needs_human",
            verdict_reason=decision.reason,
        )
        events.publish(
            {
                "type": "approval_requested",
                "case_id": case_id,
                "action": approval["action"],
                "verdict_reason": decision.reason,
            }
        )
        _publish_step(
            events,
            case_id,
            "executor",
            "system",
            "Action held for human approval.",
            True,
            {"action": resolution.action.value, "executed": False},
            0,
        )
        outcome = {"type": "awaiting_approval", "action": resolution.action.value}
        verified_observations["verified_action"] = "awaiting_approval"
        comms_outcome = "awaiting_approval"
    elif decision.verdict == "block":
        _publish_step(
            events,
            case_id,
            "executor",
            "system",
            "Blocked action was not executed.",
            True,
            {"action": resolution.action.value, "executed": False},
            0,
        )
        outcome = {"type": "blocked", "reason": decision.reason}
        _set_status(
            case_id,
            "escalated",
            database,
            events,
            outcome=outcome,
            verdict="blocked",
            verdict_reason=decision.reason,
        )
        comms_outcome = "blocked"
    else:
        started = time.perf_counter()
        execution = await Executor().execute(
            resolution.action.value,
            case_id=case_id,
            customer_id=case["customer_id"],
            facts=facts,
            policy_verdict=decision.verdict,
            reason=resolution.reason,
        )
        _publish_step(
            events,
            case_id,
            "executor",
            "tool" if execution["executed"] else "system",
            _execution_summary(execution),
            True,
            execution,
            _duration_ms(started),
        )
        outcome = {
            "type": resolution.action.value,
            "verified": execution["verified"],
            **execution["observations"],
        }
        verified_observations = {
            **facts,
            **execution["observations"],
            "verified_action": resolution.action.value,
        }
        if resolution.action.value == "create_link":
            comms_outcome = "link_created"
        elif resolution.action.value == "create_refund":
            comms_outcome = "refund_done"
        elif resolution.action.value == "wait":
            comms_outcome = "waiting"
        else:
            comms_outcome = "awaiting_approval"
        if resolution.action.value == "escalate":
            _set_status(
                case_id,
                "escalated",
                database,
                events,
                outcome=outcome,
                verdict=decision.verdict,
            )
        else:
            _set_status(
                case_id,
                "resolved",
                database,
                events,
                outcome=outcome,
                resolved_at=_utc_now(),
                verdict=decision.verdict,
            )

    started = time.perf_counter()
    comms_agent = CommsAgent()
    if decision.verdict == "block":
        customer_message = comms_agent.template(
            language, "blocked", verified_observations
        )
        comms_agent.fallback_info = {
            "fallback": True,
            "reason": "GuardianBlocked",
            "usage": {},
        }
    else:
        customer_message = comms_agent.compose(
            language,
            verified_observations,
            outcome=comms_outcome,
        )
    _publish_comms_step(
        events,
        case_id,
        language,
        customer_message,
        comms_agent,
        _duration_ms(started),
    )
    await _notify_customer(
        case_id,
        case["chat_id"],
        customer_message,
        language,
        events,
    )
    latest = _required_case(database, case_id)
    if latest["status"] == "awaiting_approval":
        return latest
    if latest["status"] in {"resolved", "escalated"}:
        return latest
    _set_status(
        case_id,
        "resolved",
        database,
        events,
        outcome=outcome,
        resolved_at=_utc_now(),
    )
    return _required_case(database, case_id)


async def _decide_case_async(
    case_id: str,
    approved: bool,
    by: str,
    database: Database,
    events: EventHub,
) -> dict[str, Any]:
    case = _required_case(database, case_id)
    data = case["data"]
    pending = data.get("pending_action")
    if not isinstance(pending, Mapping):
        raise ValueError("Case has no pending action")

    database.update_case(
        case_id,
        {
            "human_touched": True,
            "approval": {
                "status": "approved" if approved else "rejected",
                "action": data.get("approval", {}).get("action"),
                "by": by,
            },
        },
    )
    if not approved:
        _set_status(
            case_id,
            "escalated",
            database,
            events,
            outcome={"type": "rejected", "by": by},
            resolved_at=_utc_now(),
        )
        comms_agent = CommsAgent()
        message = comms_agent.compose(
            case["language"],
            _decision_observations(case),
            outcome="rejected",
        )
        _publish_comms_step(
            events, case_id, case["language"], message, comms_agent, 0
        )
        await _notify_customer(
            case_id, case["chat_id"], message, case["language"], events
        )
        events.publish(
            {
                "type": "approval_decided",
                "case_id": case_id,
                "approved": False,
                "status": "escalated",
            }
        )
        return _required_case(database, case_id)

    started = time.perf_counter()
    facts = await InvestigatorAgent().investigate(
        case["customer_id"],
        pending.get("facts", {}).get("txn_id") or case.get("txn_id"),
    )
    decision = Guardian().evaluate(
        str(pending["action"]),
        str(pending["case_type"]),
        case["customer_id"],
        facts,
        data.get("text", ""),
    )
    if decision.verdict == "block":
        raise PermissionError(decision.reason)
    execution = await Executor().execute(
        str(pending["action"]),
        case_id=case_id,
        customer_id=case["customer_id"],
        facts=facts,
        policy_verdict=decision.verdict,
        human_approved=True,
        reason=str(pending.get("reason", "")),
    )
    _publish_step(
        events,
        case_id,
        "executor",
        "tool",
        _execution_summary(execution),
        True,
        execution,
        _duration_ms(started),
    )
    observations = {
        **pending.get("facts", {}),
        **execution["observations"],
        "verified_action": str(pending["action"]),
    }
    comms_agent = CommsAgent()
    action_outcome = {
        "create_link": "link_created",
        "create_refund": "refund_done",
        "wait": "waiting",
    }.get(str(pending["action"]), "awaiting_approval")
    message = comms_agent.compose(
        case["language"], observations, outcome=action_outcome
    )
    _publish_comms_step(
        events, case_id, case["language"], message, comms_agent, 0
    )
    await _notify_customer(
        case_id, case["chat_id"], message, case["language"], events
    )
    outcome = {
        "type": str(pending["action"]),
        "verified": execution["verified"],
        "approved_by": by,
        **execution["observations"],
    }
    _set_status(
        case_id,
        "resolved",
        database,
        events,
        outcome=outcome,
        resolved_at=_utc_now(),
        verdict="approved",
    )
    events.publish(
        {
            "type": "approval_decided",
            "case_id": case_id,
            "approved": True,
            "status": "resolved",
        }
    )
    return _required_case(database, case_id)


def _decision_observations(
    case: Mapping[str, Any],
) -> dict[str, Any]:
    pending = case["data"].get("pending_action", {})
    facts = pending.get("facts", {}) if isinstance(pending, Mapping) else {}
    return {
        key: facts[key]
        for key in ("txn_id", "status", "amount", "bank_code", "retryable")
        if key in facts
    }


async def _notify_customer(
    case_id: str,
    chat_id: str | int | None,
    text: str,
    language: str,
    events: EventHub,
) -> None:
    started = time.perf_counter()
    if _is_numeric_chat_id(chat_id):
        from mitra.channels import telegram

        await telegram.send_message(chat_id, text)
    _publish_message(events, case_id, text, "assistant", chat_id)
    _publish_step(
        events,
        case_id,
        "notify",
        "tool" if _is_numeric_chat_id(chat_id) else "system",
        "Customer was notified.",
        True,
        {"channel": "telegram" if _is_numeric_chat_id(chat_id) else "web", "language": language},
        _duration_ms(started),
    )


def _publish_case_created(case: Mapping[str, Any], events: EventHub) -> None:
    data = case["data"]
    concierge = data.get("precomputed_concierge") or data.get("concierge") or {}
    events.publish(
        {
            "type": "case_created",
            "case_id": case["id"],
            "case": {
                "id": case["id"],
                "status": case["status"],
                "intent": _display_intent(concierge.get("intent") or data.get("intent")),
                "txn_id": case.get("txn_id"),
                "chat_id": case.get("chat_id"),
                "language": case["language"],
                "source": _source(case.get("chat_id")),
                "human_touched": bool(data.get("human_touched", False)),
                "created_at": case["created_at"],
            },
        }
    )


def _publish_message(
    events: EventHub,
    case_id: str,
    text: str,
    direction: str,
    chat_id: str | int | None,
) -> None:
    events.publish(
        {
            "type": "message",
            "case_id": case_id,
            "text": text,
            "direction": direction,
            "chat_id": str(chat_id) if chat_id is not None else "",
            "created_at": _utc_now(),
        }
    )


def _publish_step(
    events: EventHub,
    case_id: str,
    agent: str,
    kind: str,
    summary: str,
    ok: bool,
    payload: Mapping[str, Any],
    duration_ms: int,
) -> None:
    events.publish(
        {
            "type": "step",
            "case_id": case_id,
            "agent": agent,
            "kind": kind,
            "summary": summary,
            "ok": ok,
            "payload": dict(payload),
            "duration_ms": max(0, duration_ms),
            "created_at": _utc_now(),
        }
    )


def _publish_comms_step(
    events: EventHub,
    case_id: str,
    language: str,
    message: str,
    agent: CommsAgent,
    duration_ms: int,
) -> None:
    fallback = agent.fallback_info or {}
    is_fallback = bool(fallback.get("fallback"))
    payload: dict[str, Any] = {
        "language": language,
        "message": message,
        "fallback": is_fallback,
        "reason": fallback.get("reason"),
        "usage": fallback.get("usage", {}),
    }
    payload.update(_sarvam_step_metadata(agent))
    summary = f"Customer update prepared in {language}."
    if is_fallback:
        summary += " (template)"
    _publish_step(
        events,
        case_id,
        "comms",
        "template" if is_fallback else "llm",
        summary,
        True,
        payload,
        duration_ms,
    )


def _set_status(
    case_id: str,
    status: str,
    database: Database,
    events: EventHub,
    **updates: Any,
) -> dict[str, Any]:
    if status not in CASE_STATUSES:
        raise ValueError(f"Unsupported case status {status!r}")
    previous = _required_case(database, case_id)
    result = database.update_case(case_id, {"status": status, **updates})
    if previous["status"] != status:
        event: dict[str, Any] = {"type": "case_status", "case_id": case_id, "status": status}
        if "resolved_at" in result["data"]:
            event["resolved_at"] = result["data"]["resolved_at"]
        if "outcome" in result["data"]:
            event["outcome"] = result["data"]["outcome"]
        events.publish(event)
    return result


def _record_failure(
    case_id: str,
    exc: Exception,
    database: Database,
    events: EventHub,
) -> None:
    details = _error_details(exc)
    failed_agent = getattr(exc, "_brain_agent", "supervisor")
    try:
        _publish_step(
            events,
            case_id,
            failed_agent,
            "error",
            f"{failed_agent} failed: {details['message']}",
            False,
            details,
            0,
        )
        case = database.get_case(case_id)
        if case is not None:
            _set_status(
                case_id,
                "escalated",
                database,
                events,
                outcome={"type": "error", "exception_class": details["exception_class"]},
                last_error=details,
            )
    except Exception as persistence_error:
        logger.error(
            "Could not persist Brain failure for case %s (%s)",
            case_id,
            type(persistence_error).__name__,
        )


def _error_details(exc: Exception) -> dict[str, Any]:
    trace_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    trace = [
        _sanitize_diagnostic(line.rstrip())[:200]
        for line in "".join(trace_lines).splitlines()[-5:]
    ]
    raw_reply = getattr(exc, "raw_reply", "")
    cause = exc.__cause__
    while cause is not None:
        if not isinstance(raw_reply, str) or not raw_reply:
            raw_reply = getattr(cause, "raw_reply", "")
        cause = cause.__cause__
    http_status = getattr(exc, "http_status", None)
    finish_reason = getattr(exc, "finish_reason", None)
    cause = exc.__cause__
    while cause is not None:
        if http_status is None:
            http_status = getattr(cause, "http_status", None)
            response = getattr(cause, "response", None)
            if http_status is None and response is not None:
                http_status = getattr(response, "status_code", None)
        if finish_reason is None:
            finish_reason = getattr(cause, "finish_reason", None)
        cause = cause.__cause__
    details = {
        "exception_class": type(exc).__name__,
        "message": _sanitize_diagnostic(str(exc))[:200],
        "http_status": http_status,
        "finish_reason": finish_reason,
        "raw_reply": _sanitize_diagnostic(str(raw_reply))[:200],
        "traceback_lines": trace,
    }
    for field in (
        "usage",
        "request_max_tokens",
        "reasoning_effort",
        "reasoning_effort_sent",
        "input_length",
        "elapsed_s",
        "elapsed_ms",
        "content_length",
        "reasoning_content_length",
        "attempt",
        "sarvam_attempts",
    ):
        if hasattr(exc, field):
            details[field] = getattr(exc, field)
    return details


def _sarvam_step_metadata(agent: Any) -> dict[str, Any]:
    attempts = getattr(agent, "sarvam_attempts", [])
    if not attempts:
        return {}
    return {**attempts[-1], "sarvam_attempts": attempts}


def _sanitize_diagnostic(text: str) -> str:
    text = re.sub(
        r"(?i)(api-subscription-key|authorization|sarvam_api_key|"
        r"telegram_bot_token|core_secret)(\s*[:=]\s*|\s+)[^\s,;]+",
        r"\1\2[redacted]",
        text,
    )
    text = re.sub(r"(?i)\bbearer\s+\S+", "Bearer [redacted]", text)
    return re.sub(r"\b(?:sk|key|token)[-_][A-Za-z0-9_-]{16,}\b", "[redacted]", text)


def _required_case(database: Database, case_id: str) -> dict[str, Any]:
    case = database.get_case(case_id)
    if case is None:
        raise LookupError(f"Case {case_id!r} does not exist")
    return case


def _case_type(concierge: Mapping[str, Any]) -> str:
    intent = concierge.get("intent", "other")
    return str(getattr(intent, "value", intent)).strip().lower() or "other"


def _scenario_action(scenario: str, facts: Mapping[str, Any]) -> tuple[str, str]:
    canonical = {
        "pending_payment": "pending_settlement",
        "duplicate_small": "small_duplicate",
        "duplicate_large": "large_duplicate",
    }.get(scenario, scenario)

    if canonical == "failed_payment":
        if (
            facts.get("status") == "FAILED"
            and facts.get("retryable") is True
            and facts.get("links_issued") == 0
        ):
            return "create_link", "Verified retryable failure with no existing payment link."
        return "escalate", "Payment is not retryable or a payment link already exists."

    if canonical == "pending_settlement":
        if facts.get("bank_state") in {"PENDING", "SETTLING"}:
            return "wait", "The bank reports that settlement is still pending."
        return "escalate", "The transaction is no longer in a pending settlement state."

    if canonical in {"small_duplicate", "large_duplicate"}:
        txn_id = facts.get("txn_id")
        pairs = facts.get("duplicate_pairs", [])
        duplicate_found = facts.get("duplicate_of") is not None or (
            isinstance(pairs, list)
            and any(
                isinstance(pair, Mapping)
                and (pair.get("txn_id") == txn_id or pair.get("duplicate_of") == txn_id)
                for pair in pairs
            )
        )
        if duplicate_found and facts.get("refund_count") == 0:
            return "create_refund", "A duplicate transaction is verified and has no refund."
        return "escalate", "Duplicate evidence is missing or a refund already exists."

    if canonical == "suspicious_refund":
        return "create_refund", "Guardian must review the suspicious refund request."

    return "escalate", f"Unsupported scenario {scenario!r}."


def _investigation_summary(facts: Mapping[str, Any]) -> str:
    status = facts.get("status") or "unknown status"
    code = facts.get("bank_code")
    retryable = facts.get("retryable")
    summary = f"Transaction status: {status}"
    if code:
        summary += f"; bank code: {code}"
    if retryable is not None:
        summary += f"; retryable: {str(retryable).lower()}"
    return summary


def _execution_summary(execution: Mapping[str, Any]) -> str:
    if execution.get("executed"):
        return f"{execution['action']} completed and verified."
    return f"No write action was needed for {execution['action']}."


def _approval_label(action: str, facts: Mapping[str, Any]) -> str:
    if action == "create_refund":
        return f"Refund ₹{facts.get('amount', 0)} — duplicate charge"
    return action.replace("_", " ")


def _display_intent(intent: Any) -> str:
    if not isinstance(intent, str):
        return "payment support"
    return intent.replace("_", " ")


def _source(chat_id: Any) -> str:
    return "telegram" if _is_numeric_chat_id(chat_id) else "web"


def _is_numeric_chat_id(chat_id: Any) -> bool:
    if chat_id is None:
        return False
    value = str(chat_id)
    if value.startswith("-"):
        value = value[1:]
    return bool(value) and value.isascii() and value.isdigit()


def _language_code(language: str | None) -> str:
    code = (language or "en").strip().lower().split("-", maxsplit=1)[0]
    return code if code in {"en", "hi"} else "en"


def _duration_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
