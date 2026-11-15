"""Deterministic dashboard-scenario messages and Concierge precomputation."""

from collections.abc import Sequence
from typing import Any, Literal


_SCENARIO_ALIASES = {
    "pending_payment": "pending_settlement",
    "duplicate_small": "small_duplicate",
    "duplicate_large": "large_duplicate",
}
_INTENTS = {
    "failed_payment": "failed_payment",
    "pending_settlement": "pending_payment",
    "small_duplicate": "duplicate_charge",
    "large_duplicate": "duplicate_charge",
    "suspicious_refund": "refund_request",
}
_DEFAULT_AMOUNTS = {
    "failed_payment": [1250],
    "pending_settlement": [2000],
    "small_duplicate": [300, 300],
    "large_duplicate": [1500, 1500],
    "suspicious_refund": [5000],
}


def build_scenario(
    scenario: str,
    language: str,
    txn_ids: Sequence[str],
    amounts: Sequence[int],
) -> dict[str, Any]:
    canonical = _SCENARIO_ALIASES.get(scenario, scenario)
    if canonical not in _INTENTS:
        raise ValueError(f"Unsupported scenario {scenario!r}")
    if not txn_ids or len(txn_ids) != len(amounts):
        raise ValueError("Scenario requires matching transaction ids and amounts")
    if any(not isinstance(txn_id, str) or not txn_id for txn_id in txn_ids):
        raise ValueError("Transaction ids must be non-empty strings")
    if any(not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0 for amount in amounts):
        raise ValueError("Scenario amounts must be positive integer rupees")

    language_code = _language_code(language)
    amount = amounts[-1]
    txn_id = txn_ids[-1]
    first_txn_id = txn_ids[0]
    if canonical == "failed_payment":
        en = f"My payment of ₹{amount} failed. The transaction is {txn_id}; please help me recover it."
        hi = f"₹{amount} का मेरा भुगतान विफल हो गया। लेन-देन {txn_id} है; कृपया इसे वापस पाने में मदद करें।"
    elif canonical == "pending_settlement":
        en = f"My payment of ₹{amount} is still pending. The transaction is {txn_id}; could you check its status?"
        hi = f"₹{amount} का मेरा भुगतान अभी लंबित है। लेन-देन {txn_id} है; कृपया इसकी स्थिति जाँचें।"
    elif canonical in {"small_duplicate", "large_duplicate"}:
        en = (
            f"I was charged twice for ₹{amount}, in transactions {first_txn_id} and {txn_id}. "
            "Please help me with the duplicate charge."
        )
        hi = (
            f"₹{amount} का भुगतान मुझसे दो बार लिया गया, लेन-देन {first_txn_id} और {txn_id} में। "
            "कृपया दोहरे शुल्क में मदद करें।"
        )
    else:
        en = (
            f"I need a refund of ₹{amount} for transaction {txn_id}. "
            "Please redirect the refund to a different person's account."
        )
        hi = (
            f"मुझे लेन-देन {txn_id} के लिए ₹{amount} का रिफंड चाहिए। "
            "कृपया रिफंड किसी दूसरे व्यक्ति के खाते में भेजें।"
        )

    text = en if language_code == "en" else hi
    precomputed_concierge = {
        "intent": _INTENTS[canonical],
        "transaction_id": txn_id,
        "summary": text,
        "missing_information": [],
        "needs_clarification": False,
        "language": language_code,
    }
    return {
        "scenario": canonical,
        "text": text,
        "txn_id": txn_id,
        "language": language_code,
        "precomputed_concierge": precomputed_concierge,
    }


def default_scenario_amounts(scenario: str, transaction_count: int) -> list[int]:
    canonical = _SCENARIO_ALIASES.get(scenario, scenario)
    amounts = _DEFAULT_AMOUNTS.get(canonical)
    if amounts is None:
        raise ValueError(f"Unsupported scenario {scenario!r}")
    if len(amounts) != transaction_count:
        raise ValueError(
            f"Scenario {scenario!r} expects {len(amounts)} transactions, "
            f"got {transaction_count}"
        )
    return list(amounts)


def _language_code(language: str) -> Literal["en", "hi"]:
    code = language.strip().lower().split("-", maxsplit=1)[0]
    if code not in {"en", "hi"}:
        raise ValueError(f"Unsupported language {language!r}; expected 'en' or 'hi'")
    return code  # type: ignore[return-value]
