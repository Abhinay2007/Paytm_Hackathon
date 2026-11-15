
# Paytm Hackathon - Integration Contract

## 1. Transaction

{
  "id": "TXN1001",
  "customer_id": "C1",
  "amount": 1250,
  "status": "FAILED",
  "bank_code": "BANK_TIMEOUT",
  "settle_at": null,
  "duplicate_of": null
}

## 2. Bank Status

{
  "state": "FAILED",
  "code": "BANK_TIMEOUT",
  "retryable": true,
  "eta_s": 0
}

Allowed states:
FAILED, PENDING, SETTLING, SUCCESS

## 3. Payment Link

{
  "link_id": "L1",
  "url": "<PUBLIC_CORE_URL>/pay/L1",
  "expires_at": "ISO-8601 timestamp"
}

## 4. Refund

{
  "refund_id": "R1",
  "status": "PROCESSED",
  "amount": 1500
}

## 5. Webhook

{
  "link_id": "L1",
  "txn_id": "TXN1001",
  "new_txn_id": "TXN2001",
  "status": "SUCCESS",
  "amount": 1250
}

Header:
X-Core-Secret

## 6. SSE Event

{
  "type": "step",
  "case_id": "c_17",
  "agent": "investigator",
  "kind": "llm",
  "summary": "Bank timeout, retryable (0.92)"
}

## Brain/Body Interface

Brain exposes:

handle_inbound(chat_id, text) -> case_id

decide_case(case_id, approved, by)

Brain also exposes a payment webhook resume function.

Body exposes:

send_message(chat_id, text)

send_desk_brief(case_id, text)

mark_desk_decision(case_id, text)

## Rules

- Keep these data structures consistent.
- Use ISO-8601 timestamps.
- Use JSON-compatible data.
- Do not change shared function signatures without team agreement.
