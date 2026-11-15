# Paytm_Hackathon

## Paytm core simulator

Start the API from the repository root:

```bash
uvicorn core.main:app --host 0.0.0.0 --port 8001
```

The following curl requests exercise all ten core endpoints in sequence on a fresh
database. The failed-transaction scenario creates `TXN1001`; completing its `L1` link
creates `TXN1002`, which is then used for the refund.

```bash
# C10: create a failed transaction; other scenario names include onboard,
# pending_payment, duplicate_small, duplicate_large, and suspicious_refund
curl -i -X POST http://localhost:8001/admin/scenario \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"failed_payment","customer_ref":"C1","language":"en"}'

# C1: inspect a transaction (create it with C10 first)
curl -i http://localhost:8001/txns/TXN1001

# C2: list failed/pending transactions; supports comma-separated status and filters
curl -i 'http://localhost:8001/txns?status=FAILED,PENDING&min_age_s=0&customer_id=C1'

# C3: inspect a transaction's bank status
curl -i http://localhost:8001/bank/status/TXN1001

# C4: customer history
curl -i 'http://localhost:8001/customers/C1/history?limit=10'

# C5: create (or retrieve) a payment link for a FAILED transaction
curl -i -X POST http://localhost:8001/links \
  -H 'Content-Type: application/json' -d '{"txn_id":"TXN1001"}'

# C6: open the returned link URL in a browser (or inspect its HTML)
curl -i http://localhost:8001/pay/L1

# C7: complete payment for the link; returns an HTML success page
curl -i -X POST http://localhost:8001/pay/L1/complete

# C8: refund the SUCCESS transaction created by C7
curl -i -X POST http://localhost:8001/refunds \
  -H 'Content-Type: application/json' \
  -d '{"txn_id":"TXN1002","amount":150,"reason":"Customer request","idempotency_key":"refund-demo-1"}'

# C9: retrieve the refund (use the refund_id returned by C8)
curl -i http://localhost:8001/refunds/R1

```
