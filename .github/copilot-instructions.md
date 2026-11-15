# Project: Mitra AI — multi-agent customer-service teammate for Paytm merchants
Hackathon demo, 2-hour build, two developers. Synthetic data only, no real money.

Stack: Python 3.11, FastAPI, SQLModel (SQLite), httpx, uvicorn, python-dotenv.
Frontend: Vite + React (JavaScript), Tailwind CSS, zustand, recharts, lucide-react.

Repo layout (monorepo, run everything from the repo root):
- core/       Paytm core SIMULATOR (FastAPI, port 8001, db file core/core.db)  -> I own
- mitra/      Agent platform (FastAPI, port 8000) -> my teammate owns it, EXCEPT
              mitra/tools.py and mitra/channels/ which I own
- dashboard/  React dashboard (port 5173) -> I own
- check.py    preflight script -> I own
NEVER name any package `platform` (it shadows the Python stdlib module).

Rules:
- Simple, readable code with type hints. No over-engineering, no extra endpoints.
- Money is integer rupees. Timestamps are ISO-8601 UTC strings in JSON.
- All services allow CORS from any origin.
- Config from .env via python-dotenv: SARVAM_API_KEY, TELEGRAM_BOT_TOKEN, DESK_CHAT_ID,
  CORE_URL=http://localhost:8001, PLATFORM_URL=http://localhost:8000,
  PUBLIC_CORE_URL=http://localhost:8001, CORE_SECRET=devsecret
- Never crash on bad input; return clean JSON errors.

Shared JSON contracts (do not rename fields; additive fields are OK):
Transaction: {"id":"TXN1001","customer_id":"C1","merchant":"Ramesh Stores","amount":1250,
  "status":"FAILED","bank_code":"BANK_TIMEOUT","created_at":"...","settle_at":null,
  "duplicate_of":null,"links_issued":0,"recovered_by":null}
Bank status: {"state":"FAILED|PENDING|SETTLING|SUCCESS","code":"BANK_TIMEOUT","retryable":true,"eta_s":0}
Link: {"link_id":"L1","url":"<PUBLIC_CORE_URL>/pay/L1","expires_at":"...","links_issued":1}
Refund: {"refund_id":"R1","status":"PROCESSED","amount":1500}
Webhook (core -> platform POST /webhooks/payment, header X-Core-Secret):
  {"link_id":"L1","txn_id":"TXN1001","new_txn_id":"TXN2001","status":"SUCCESS","amount":1250}
SSE event (platform -> dashboard): {"type":"step","case_id":"c_17","agent":"investigator",
  "kind":"llm","summary":"Bank timeout, retryable (0.92)","ts":"..."}