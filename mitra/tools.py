import asyncio
import os
from typing import Any
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

load_dotenv()

CORE_URL = os.getenv("CORE_URL", "http://localhost:8001").rstrip("/")


class ToolError(Exception):
    """Raised when a core simulator request fails."""


async def _request_json(method: str, path: str, **kwargs: Any) -> Any:
    try:
        async with httpx.AsyncClient(base_url=CORE_URL, timeout=10.0) as client:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        raise ToolError(
            f"Core API returned HTTP {exc.response.status_code}: {detail}"
        ) from exc
    except httpx.TimeoutException as exc:
        raise ToolError("Core API request timed out") from exc
    except httpx.HTTPError as exc:
        raise ToolError(f"Core API request failed: {exc}") from exc
    except ValueError as exc:
        raise ToolError("Core API returned invalid JSON") from exc


async def get_txn(txn_id: str) -> dict[str, Any]:
    return await _request_json("GET", f"/txns/{quote(txn_id, safe='')}")


async def bank_status(txn_id: str) -> dict[str, Any]:
    return await _request_json("GET", f"/bank/status/{quote(txn_id, safe='')}")


async def history(customer_id: str, limit: int = 10) -> dict[str, Any]:
    return await _request_json(
        "GET",
        f"/customers/{quote(customer_id, safe='')}/history",
        params={"limit": limit},
    )


async def list_txns(
    status: str = "FAILED,PENDING",
    min_age_s: int = 0,
    customer_id: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"status": status, "min_age_s": min_age_s}
    if customer_id is not None:
        params["customer_id"] = customer_id
    return await _request_json("GET", "/txns", params=params)


async def create_link(txn_id: str) -> dict[str, Any]:
    return await _request_json("POST", "/links", json={"txn_id": txn_id})


async def create_refund(
    txn_id: str,
    amount: int,
    reason: str,
    idempotency_key: str,
) -> dict[str, Any]:
    return await _request_json(
        "POST",
        "/refunds",
        json={
            "txn_id": txn_id,
            "amount": amount,
            "reason": reason,
            "idempotency_key": idempotency_key,
        },
    )


async def get_refund(refund_id: str) -> dict[str, Any]:
    return await _request_json("GET", f"/refunds/{quote(refund_id, safe='')}")


ALLOWED = {
    "watcher": {"list_txns"},
    "investigator": {"get_txn", "bank_status", "history"},
    "guardian": {"get_txn", "history"},
    "executor": {
        "get_txn",
        "bank_status",
        "history",
        "create_link",
        "create_refund",
        "get_refund",
    },
}
WRITE_TOOLS = {"create_link", "create_refund"}

_TOOLS = {
    "get_txn": get_txn,
    "bank_status": bank_status,
    "history": history,
    "list_txns": list_txns,
    "create_link": create_link,
    "create_refund": create_refund,
    "get_refund": get_refund,
}


async def call_tool(
    agent: str,
    name: str,
    verdict: str | None = None,
    **kwargs: Any,
) -> dict[str, Any] | list[dict[str, Any]]:
    if agent not in ALLOWED or name not in ALLOWED[agent]:
        raise PermissionError(f"Agent {agent!r} cannot call {name!r}")
    if name in WRITE_TOOLS and verdict not in {"approve", "human_approved"}:
        raise PermissionError(f"Tool {name!r} requires an approved verdict")
    return await _TOOLS[name](**kwargs)


async def _demo() -> None:
    print(await list_txns())


if __name__ == "__main__":
    asyncio.run(_demo())
