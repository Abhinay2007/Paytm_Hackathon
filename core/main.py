import logging
import os
from datetime import timedelta
from html import escape
import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlmodel import Session, SQLModel, select

from core.models import (
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
def set_scenario(request: ScenarioRequest) -> dict[str, object]:
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
        return {
            "ok": True,
            "customer_id": request.customer_ref or "C1",
            "created": created,
        }
