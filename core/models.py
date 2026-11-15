from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from sqlmodel import Field, Session, SQLModel, create_engine, select


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class Customer(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    language: str


class Transaction(SQLModel, table=True):
    id: str = Field(primary_key=True)
    customer_id: str = Field(foreign_key="customer.id")
    merchant: str
    amount: int
    status: str
    bank_code: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    settle_at: datetime | None = None
    duplicate_of: str | None = None
    links_issued: int = 0
    recovered_by: str | None = None


class PaymentLink(SQLModel, table=True):
    id: str = Field(primary_key=True)
    txn_id: str = Field(foreign_key="transaction.id")
    amount: int
    status: str = "CREATED"
    expires_at: datetime
    paid_txn_id: str | None = None


class Refund(SQLModel, table=True):
    id: str = Field(primary_key=True)
    txn_id: str = Field(foreign_key="transaction.id")
    amount: int
    reason: str
    status: str = "PROCESSED"
    idempotency_key: str = Field(unique=True)
    created_at: datetime = Field(default_factory=utc_now)


DATABASE_PATH = Path(__file__).with_name("core.db")
engine = create_engine(
    f"sqlite:///{DATABASE_PATH}",
    connect_args={"check_same_thread": False},
)

ModelWithId = TypeVar("ModelWithId", Customer, Transaction, PaymentLink, Refund)


def next_id(session: Session, model: type[ModelWithId], prefix: str, initial: int) -> str:
    ids = session.exec(select(model.id)).all()
    numbers = [
        int(item[len(prefix) :])
        for item in ids
        if item.startswith(prefix) and item[len(prefix) :].isdigit()
    ]
    return f"{prefix}{max(numbers, default=initial - 1) + 1}"


def transaction_json(txn: Transaction) -> dict[str, object]:
    return {
        "id": txn.id,
        "customer_id": txn.customer_id,
        "merchant": txn.merchant,
        "amount": txn.amount,
        "status": txn.status,
        "bank_code": txn.bank_code,
        "created_at": as_utc(txn.created_at).isoformat(),
        "settle_at": as_utc(txn.settle_at).isoformat() if txn.settle_at else None,
        "duplicate_of": txn.duplicate_of,
        "links_issued": txn.links_issued,
        "recovered_by": txn.recovered_by,
    }


def refresh_pending(session: Session) -> None:
    now = utc_now()
    pending = session.exec(
        select(Transaction).where(Transaction.status == "PENDING")
    ).all()
    changed = False
    for txn in pending:
        if txn.settle_at and as_utc(txn.settle_at) <= now:
            txn.status = "SUCCESS"
            session.add(txn)
            changed = True
    if changed:
        session.commit()


def seed_customers(session: Session) -> None:
    if session.exec(select(Customer.id)).first() is not None:
        return
    session.add_all(
        [
            Customer(id="C1", name="Ramesh Stores", language="en"),
            Customer(id="C2", name="Sunita Devi", language="hi"),
        ]
    )
    session.commit()
