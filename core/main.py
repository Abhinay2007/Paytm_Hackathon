import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from html import escape
from collections.abc import Mapping
from typing import Any
import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, SQLModel, select

from brain import llm as brain_llm
from brain import runtime as brain_runtime
from brain.events import encode_sse
from core.models import (
    Customer,
    PaymentLink,
    Refund,
    Transaction,
    as_utc,
    engine,
    next_id,
    refresh_pending,
    seed_customers,
    transaction_json,
    utc_now,
)
from core.scenarios import apply_scenario, create_transaction

load_dotenv()

logger = logging.getLogger(__name__)
PUBLIC_CORE_URL = os.getenv("PUBLIC_CORE_URL", "http://localhost:8001").rstrip("/")
PLATFORM_URL = os.getenv("PLATFORM_URL", "http://localhost:8000").rstrip("/")
CORE_SECRET = os.getenv("CORE_SECRET", "devsecret")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class LinkRequest(BaseModel):
    txn_id: str


class RefundRequest(BaseModel):
    txn_id: str
    amount: int
    reason: str
    idempotency_key: str


class ScenarioRequest(BaseModel):
    scenario: str
    customer_ref: str | None = None
    settle_after_s: int = 90
    language: str | None = None


class DecisionRequest(BaseModel):
    approved: bool
    by: str = "dashboard"


@app.on_event("startup")
def startup() -> None:
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_customers(session)


@app.get("/txns/{txn_id}")
def get_transaction(txn_id: str) -> dict[str, object]:
    with Session(engine) as session:
        refresh_pending(session)
        txn = session.get(Transaction, txn_id)
        if txn is None:
            raise HTTPException(status_code=404, detail="Transaction not found")
        return transaction_json(txn)


@app.get("/txns")
def list_transactions(
    status: str = "FAILED,PENDING",
    min_age_s: int = Query(0, ge=0),
    customer_id: str | None = None,
) -> list[dict[str, object]]:
    with Session(engine) as session:
        refresh_pending(session)
        statuses = {value.strip().upper() for value in status.split(",") if value.strip()}
        cutoff = utc_now() - timedelta(seconds=min_age_s)
        transactions = session.exec(select(Transaction)).all()
        filtered = [
            txn
            for txn in transactions
            if txn.status in statuses
            and txn.recovered_by is None
            and as_utc(txn.created_at) <= cutoff
            and (customer_id is None or txn.customer_id == customer_id)
        ]
        filtered.sort(key=lambda txn: as_utc(txn.created_at))
        return [transaction_json(txn) for txn in filtered[:50]]


@app.get("/bank/status/{txn_id}")
def get_bank_status(txn_id: str) -> dict[str, object]:
    with Session(engine) as session:
        refresh_pending(session)
        retryable_codes = {"BANK_TIMEOUT", "NETWORK_ERROR", "BANK_BUSY"}
        txn = session.get(Transaction, txn_id)
        if txn is None:
            raise HTTPException(status_code=404, detail="Transaction not found")
        if txn.status == "FAILED":
            return {
                "state": "FAILED",
                "code": txn.bank_code,
                "retryable": txn.bank_code in retryable_codes,
                "eta_s": 0,
            }
        if txn.status == "PENDING":
            remaining = (
                max(0, int((as_utc(txn.settle_at) - utc_now()).total_seconds()))
                if txn.settle_at
                else 0
            )
            return {
                "state": "SETTLING",
                "code": "SETTLEMENT_IN_PROGRESS",
                "retryable": False,
                "eta_s": remaining,
            }
        return {"state": "SUCCESS", "code": "OK", "retryable": False, "eta_s": 0}


@app.get("/customers/{customer_id}/history")
def customer_history(
    customer_id: str,
    limit: int = Query(10, ge=0),
) -> dict[str, object]:
    with Session(engine) as session:
        refresh_pending(session)
        customer = session.get(Customer, customer_id)
        transactions = session.exec(
            select(Transaction).where(Transaction.customer_id == customer_id)
        ).all()
        transactions.sort(key=lambda txn: as_utc(txn.created_at), reverse=True)
        history = transactions[:limit]
        transaction_ids = [txn.id for txn in transactions]
        refunds = (
            session.exec(select(Refund).where(Refund.txn_id.in_(transaction_ids))).all()
            if transaction_ids
            else []
        )
        return {
            "customer_id": customer_id,
            "name": customer.name if customer else None,
            "language": customer.language if customer else None,
            "transactions": [transaction_json(txn) for txn in history],
            "refund_count": len(refunds),
            "duplicate_pairs": [
                {"txn_id": txn.id, "duplicate_of": txn.duplicate_of}
                for txn in transactions
                if txn.duplicate_of is not None
            ],
        }


def link_json(link: PaymentLink, links_issued: int) -> dict[str, object]:
    return {
        "link_id": link.id,
        "url": f"{PUBLIC_CORE_URL}/pay/{link.id}",
        "expires_at": as_utc(link.expires_at).isoformat(),
        "links_issued": links_issued,
    }


@app.post("/links")
def create_payment_link(request: LinkRequest) -> dict[str, object]:
    with Session(engine) as session:
        txn = session.get(Transaction, request.txn_id)
        if txn is None or txn.status != "FAILED" or txn.recovered_by is not None:
            raise HTTPException(status_code=409, detail="Transaction cannot be recovered")
        now = utc_now()
        existing = session.exec(
            select(PaymentLink).where(
                PaymentLink.txn_id == txn.id,
                PaymentLink.status == "CREATED",
            )
        ).all()
        for link in existing:
            if as_utc(link.expires_at) > now:
                return link_json(link, txn.links_issued)
            link.status = "EXPIRED"
            session.add(link)
        link = PaymentLink(
            id=next_id(session, PaymentLink, "L", 1),
            txn_id=txn.id,
            amount=txn.amount,
            status="CREATED",
            expires_at=now + timedelta(minutes=30),
        )
        txn.links_issued += 1
        session.add(link)
        session.add(txn)
        session.commit()
        session.refresh(link)
        session.refresh(txn)
        return link_json(link, txn.links_issued)


def html_page(message: str, *, form: str = "") -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Paytm</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f2f6fb;
font-family:Arial,sans-serif;color:#002E6E}}.card{{width:min(380px,calc(100% - 40px));
box-sizing:border-box;padding:32px;text-align:center;background:white;border-radius:16px;
box-shadow:0 8px 30px #002e6e1a}}h1{{font-size:23px}}button{{width:100%;padding:15px;
border:0;border-radius:8px;background:#00BAF2;color:white;font-size:18px;font-weight:bold;
cursor:pointer}}</style>
<main class="card"><h1>{message}</h1>{form}</main></html>"""
    )


@app.get("/pay/{link_id}", response_class=HTMLResponse)
def view_payment_link(link_id: str) -> HTMLResponse:
    with Session(engine) as session:
        link = session.get(PaymentLink, link_id)
        if link is None:
            raise HTTPException(status_code=404, detail="Payment link not found")
        if link.status == "PAID":
            return html_page("Already paid ✅")
        if link.status == "EXPIRED" or as_utc(link.expires_at) <= utc_now():
            if link.status == "CREATED":
                link.status = "EXPIRED"
                session.add(link)
                session.commit()
            return html_page("Link expired")
        txn = session.get(Transaction, link.txn_id)
        if txn is None:
            raise HTTPException(status_code=404, detail="Transaction not found")
        message = f"Pay ₹{link.amount} to {escape(txn.merchant)}"
        form = (
            f'<form method="post" action="/pay/{escape(link.id, quote=True)}/complete">'
            '<button type="submit">Pay</button></form>'
        )
        return html_page(message, form=form)


def refund_json(refund: Refund) -> dict[str, object]:
    return {
        "refund_id": refund.id,
        "status": refund.status,
        "amount": refund.amount,
    }


async def notify_platform(payload: dict[str, object]) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{PLATFORM_URL}/webhooks/payment",
                json=payload,
                headers={"X-Core-Secret": CORE_SECRET},
            )
            response.raise_for_status()
    except Exception:
        logger.exception("Could not deliver payment webhook")


@app.post("/pay/{link_id}/complete", response_class=HTMLResponse)
def complete_payment(link_id: str, background_tasks: BackgroundTasks) -> HTMLResponse:
    with Session(engine) as session:
        link = session.get(PaymentLink, link_id)
        if link is None:
            raise HTTPException(status_code=404, detail="Payment link not found")
        if link.status == "PAID":
            return html_page(f"Payment successful ✅ ₹{link.amount}")
        if link.status != "CREATED":
            return html_page("Link expired")
        if as_utc(link.expires_at) <= utc_now():
            link.status = "EXPIRED"
            session.add(link)
            session.commit()
            return html_page("Link expired")
        original = session.get(Transaction, link.txn_id)
        if original is None:
            raise HTTPException(status_code=404, detail="Transaction not found")
        paid = create_transaction(
            session,
            original.customer_id,
            original.merchant,
            original.amount,
            "SUCCESS",
        )
        link.status = "PAID"
        link.paid_txn_id = paid.id
        original.recovered_by = paid.id
        session.add(link)
        session.add(original)
        session.commit()
        background_tasks.add_task(
            notify_platform,
            {
                "link_id": link.id,
                "txn_id": original.id,
                "new_txn_id": paid.id,
                "status": "SUCCESS",
                "amount": paid.amount,
            },
        )
        return html_page(f"Payment successful ✅ ₹{link.amount}")


@app.post("/refunds")
def create_refund(request: RefundRequest) -> dict[str, object]:
    with Session(engine) as session:
        existing = session.exec(
            select(Refund).where(Refund.idempotency_key == request.idempotency_key)
        ).first()
        if existing is not None:
            return refund_json(existing)
        txn = session.get(Transaction, request.txn_id)
        if (
            txn is None
            or txn.status != "SUCCESS"
            or request.amount <= 0
            or request.amount > txn.amount
        ):
            raise HTTPException(status_code=400, detail="Invalid refund request")
        refund = Refund(
            id=next_id(session, Refund, "R", 1),
            txn_id=txn.id,
            amount=request.amount,
            reason=request.reason,
            status="PROCESSED",
            idempotency_key=request.idempotency_key,
        )
        session.add(refund)
        session.commit()
        session.refresh(refund)
        return refund_json(refund)


@app.get("/refunds/{refund_id}")
def get_refund(refund_id: str) -> dict[str, object]:
    with Session(engine) as session:
        refund = session.get(Refund, refund_id)
        if refund is None:
            raise HTTPException(status_code=404, detail="Refund not found")
        return {
            "refund_id": refund.id,
            "txn_id": refund.txn_id,
            "status": refund.status,
            "amount": refund.amount,
            "reason": refund.reason,
        }


@app.post("/admin/scenario")
async def set_scenario(request: ScenarioRequest) -> dict[str, object]:
    if request.settle_after_s < 0:
        raise HTTPException(status_code=400, detail="settle_after_s must not be negative")
    with Session(engine) as session:
        created = apply_scenario(
            session,
            request.scenario,
            request.customer_ref,
            request.settle_after_s,
            request.language,
        )
        customer_id = request.customer_ref or "C1"
        customer = session.get(Customer, customer_id)
        language = request.language or (customer.language if customer else None) or "en"
    if created and request.scenario in {
        "failed_payment",
        "pending_payment",
        "duplicate_small",
        "duplicate_large",
        "suspicious_refund",
    }:
        asyncio.create_task(
            _start_scenario_background(
                customer_id,
                request.scenario,
                created,
                language,
            )
        )
    return {
        "ok": True,
        "customer_id": customer_id,
        "created": created,
    }


async def _start_scenario_background(
    customer_id: str,
    scenario: str,
    created_txn_ids: list[str],
    language: str,
) -> None:
    try:
        await asyncio.to_thread(
            brain_runtime.start_scenario,
            customer_id,
            scenario,
            created_txn_ids,
            language,
        )
    except Exception as exc:
        logger.error(
            "Brain scenario handoff failed for %s (%s)",
            customer_id,
            type(exc).__name__,
        )


@app.get("/api/cases")
def list_brain_cases(status: str | None = None) -> list[dict[str, Any]]:
    cases = brain_runtime._database.list_cases()
    if status:
        accepted = {item.strip() for item in status.split(",") if item.strip()}
        cases = [case for case in cases if case["status"] in accepted]
    return [_dashboard_case(case) for case in cases]


@app.get("/api/cases/{case_id}")
def get_brain_case(case_id: str) -> dict[str, Any]:
    case = brain_runtime._database.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    result = _dashboard_case(case)
    events = brain_runtime._database.list_events(case_id=case_id, limit=1000)
    result["steps"] = [event for event in events if event["type"] == "step"]
    result["messages"] = [event for event in events if event["type"] == "message"]
    return result


@app.post("/api/cases/{case_id}/decision")
async def decide_brain_case(
    case_id: str,
    request: DecisionRequest,
) -> dict[str, Any]:
    try:
        case = await asyncio.to_thread(
            brain_runtime.decide_case,
            case_id,
            request.approved,
            request.by,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "ok": case.get("status") in {"resolved", "escalated"},
        "case_id": case_id,
        "approved": request.approved,
        "status": case.get("status"),
    }


@app.get("/api/metrics")
def get_brain_metrics() -> dict[str, int | float]:
    database = brain_runtime._database
    cases = database.list_cases(limit=100000)
    events = database.list_events(limit=100000)
    total_cases = len(cases)
    resolved = [case for case in cases if case["status"] == "resolved"]
    auto_resolved = sum(not bool(case["data"].get("human_touched")) for case in resolved)
    escalated = sum(case["status"] == "escalated" for case in cases)
    durations = [
        duration
        for case in resolved
        if (duration := _resolution_seconds(case)) is not None
    ]
    durations.sort()
    middle = len(durations) // 2
    median_resolution_s: float = (
        float(durations[middle])
        if len(durations) % 2
        else (durations[middle - 1] + durations[middle]) / 2
        if durations
        else 0.0
    )
    successful_steps = sum(
        event.get("ok") is True for event in events if event["type"] == "step"
    )
    total_steps = sum(event["type"] == "step" for event in events)
    refunds = [
        case
        for case in resolved
        if case["data"].get("outcome", {}).get("type") == "create_refund"
    ]
    return {
        "total_cases": total_cases,
        "auto_resolved": auto_resolved,
        "auto_resolution_rate": auto_resolved / total_cases if total_cases else 0.0,
        "escalation_rate": escalated / total_cases if total_cases else 0.0,
        "median_resolution_s": median_resolution_s,
        "rupees_recovered": sum(
            int(case["data"]["outcome"].get("refund_amount", 0))
            for case in refunds
            if case["data"].get("outcome", {}).get("verified") is True
        ),
        "human_minutes_saved": auto_resolved * 8,
        "step_success_rate": successful_steps / total_steps if total_steps else 0.0,
        "refunds_auto": sum(not bool(case["data"].get("human_touched")) for case in refunds),
        "refunds_human": sum(bool(case["data"].get("human_touched")) for case in refunds),
    }


@app.get("/api/debug/llm")
def get_llm_debug_metadata() -> dict[str, list[dict[str, Any]]]:
    return {"calls": brain_llm.recent_call_metadata(20)}


@app.get("/api/stream")
async def stream_brain_events() -> StreamingResponse:
    event_hub = brain_runtime._events
    queue, replay = event_hub.subscribe_with_replay(limit=100)

    async def stream():
        try:
            for event in replay:
                yield encode_sse(event)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                else:
                    yield encode_sse(event)
        finally:
            event_hub.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _dashboard_case(case: Mapping[str, Any]) -> dict[str, Any]:
    data = case["data"]
    concierge = data.get("concierge") or data.get("precomputed_concierge") or {}
    intent = concierge.get("intent") or data.get("intent") or "payment support"
    if not isinstance(intent, str):
        intent = str(getattr(intent, "value", intent))
    return {
        "id": case["id"],
        "status": case["status"],
        "intent": intent.replace("_", " "),
        "txn_id": case.get("txn_id"),
        "chat_id": case.get("chat_id"),
        "language": case["language"],
        "source": "telegram" if _is_numeric_chat_id(case.get("chat_id")) else "web",
        "human_touched": bool(data.get("human_touched", False)),
        "created_at": case["created_at"],
        "resolved_at": data.get("resolved_at"),
        "outcome": data.get("outcome"),
        "approval": data.get("approval"),
        "plan": data.get("plan"),
        "verdict": data.get("verdict"),
        "verdict_reason": data.get("verdict_reason"),
    }


def _resolution_seconds(case: Mapping[str, Any]) -> float | None:
    ended = case["data"].get("resolved_at") or case["updated_at"]
    try:
        start = datetime.fromisoformat(str(case["created_at"]))
        finish = datetime.fromisoformat(str(ended))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if finish.tzinfo is None:
            finish = finish.replace(tzinfo=timezone.utc)
        return max(0.0, (finish - start).total_seconds())
    except (TypeError, ValueError):
        return None


def _is_numeric_chat_id(chat_id: Any) -> bool:
    if chat_id is None:
        return False
    value = str(chat_id)
    if value.startswith("-"):
        value = value[1:]
    return bool(value) and value.isascii() and value.isdigit()
