from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlmodel import Session

from core.models import (
    Customer,
    SQLModel,
    Transaction,
    engine,
    next_id,
    seed_customers,
    utc_now,
)


SCENARIOS = {
    "reset",
    "onboard",
    "failed_payment",
    "pending_payment",
    "duplicate_small",
    "duplicate_large",
    "suspicious_refund",
}


def create_transaction(
    session: Session,
    customer_id: str,
    merchant: str,
    amount: int,
    status: str,
    *,
    bank_code: str | None = None,
    created_at: datetime | None = None,
    settle_at: datetime | None = None,
    duplicate_of: str | None = None,
) -> Transaction:
    txn = Transaction(
        id=next_id(session, Transaction, "TXN", 1001),
        customer_id=customer_id,
        merchant=merchant,
        amount=amount,
        status=status,
        bank_code=bank_code,
        created_at=created_at or utc_now(),
        settle_at=settle_at,
        duplicate_of=duplicate_of,
    )
    session.add(txn)
    session.commit()
    session.refresh(txn)
    return txn


def apply_scenario(
    session: Session,
    scenario: str,
    customer_ref: str | None,
    settle_after_s: int,
    language: str | None,
) -> list[str]:
    if scenario not in SCENARIOS:
        raise HTTPException(status_code=400, detail="Unknown scenario")

    customer_id = customer_ref or "C1"
    if scenario == "reset":
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        seed_customers(session)

    customer = session.get(Customer, customer_id)
    if customer is None:
        customer = Customer(
            id=customer_id,
            name=f"Customer {customer_id}",
            language=language or "en",
        )
        session.add(customer)
    elif language is not None:
        customer.language = language
        session.add(customer)
    session.commit()

    if scenario == "reset":
        return []
    if scenario == "onboard":
        transactions = [
            create_transaction(session, customer_id, "Chai Corner", 300, "SUCCESS"),
            create_transaction(session, customer_id, "Metro Mart", 450, "SUCCESS"),
            create_transaction(session, customer_id, "Fresh Basket", 800, "SUCCESS"),
        ]
    elif scenario == "failed_payment":
        transactions = [
            create_transaction(
                session,
                customer_id,
                "Ramesh Stores",
                1250,
                "FAILED",
                bank_code="BANK_TIMEOUT",
            )
        ]
    elif scenario == "pending_payment":
        transactions = [
            create_transaction(
                session,
                customer_id,
                "Sharma Electronics",
                2000,
                "PENDING",
                settle_at=utc_now() + timedelta(seconds=settle_after_s),
            )
        ]
    elif scenario in {"duplicate_small", "duplicate_large"}:
        amount, merchant = (
            (300, "Chai Corner")
            if scenario == "duplicate_small"
            else (1500, "Metro Mart")
        )
        first_created_at = utc_now() - timedelta(seconds=40)
        first = create_transaction(
            session,
            customer_id,
            merchant,
            amount,
            "SUCCESS",
            created_at=first_created_at,
        )
        second = create_transaction(
            session,
            customer_id,
            merchant,
            amount,
            "SUCCESS",
            created_at=first_created_at + timedelta(seconds=40),
            duplicate_of=first.id,
        )
        transactions = [first, second]
    else:
        transactions = [
            create_transaction(session, customer_id, "Gold Palace", 5000, "SUCCESS")
        ]
    return [txn.id for txn in transactions]
