const now = Date.now()

const firstCase = {
  id: 'c_demo_01',
  status: 'open',
  intent: 'failed payment',
  txn_id: 'TXN1001',
  chat_id: 'web-1',
  language: 'en',
  source: 'web',
  human_touched: false,
  created_at: new Date(now - 12 * 60_000).toISOString(),
  resolved_at: null,
  outcome: null,
  steps: [],
  messages: [],
  approval: null,
  plan: { action: 'recover_payment', amount: 1250 },
  verdict: 'retryable',
  verdict_reason: 'Bank timeout is transient; a payment link safely recovers the failed payment.',
}

const secondCase = {
  id: 'c_demo_02',
  status: 'awaiting_approval',
  intent: 'duplicate charge',
  txn_id: 'TXN1002',
  chat_id: 'web-1',
  language: 'hi',
  source: 'telegram',
  human_touched: false,
  created_at: new Date(now - 3 * 60_000).toISOString(),
  resolved_at: null,
  outcome: null,
  steps: [],
  messages: [],
  approval: { status: 'pending', action: 'Refund ₹1,500 — duplicate charge' },
  plan: { action: 'refund', amount: 1500, reason: 'Likely duplicate charge' },
  verdict: 'needs_human',
  verdict_reason: 'Two successful transactions of the same amount were detected 40 seconds apart.',
}

export const fixtureCases = [firstCase, secondCase]

export const fixtureDetails = {
  [firstCase.id]: firstCase,
  [secondCase.id]: secondCase,
}

export const fixtureMetrics = {
  total_cases: 128,
  auto_resolved: 104,
  auto_resolution_rate: 0.8125,
  escalation_rate: 0.094,
  median_resolution_s: 18,
  rupees_recovered: 84750,
  human_minutes_saved: 832,
  step_success_rate: 0.972,
  refunds_auto: 18,
  refunds_human: 6,
}

const stamp = (secondsAgo) => new Date(Date.now() - secondsAgo * 1000).toISOString()

function step(caseId, agent, kind, summary, seq, secondsAgo, output, ok = true) {
  return {
    type: 'step',
    case_id: caseId,
    agent,
    kind,
    summary,
    seq,
    ok,
    latency_ms: 80 + seq * 37,
    output,
    ts: stamp(secondsAgo),
  }
}

export const fixtureEvents = [
  {
    type: 'case_created',
    case_id: firstCase.id,
    status: 'open',
    intent: firstCase.intent,
    txn_id: firstCase.txn_id,
    chat_id: firstCase.chat_id,
    language: firstCase.language,
    source: firstCase.source,
    ts: stamp(35),
  },
  {
    type: 'message',
    case_id: firstCase.id,
    chat_id: firstCase.chat_id,
    direction: 'customer',
    text: 'My payment of ₹1,250 failed, but the amount is showing as debited.',
    language: 'en',
    created_at: stamp(34),
  },
  step(firstCase.id, 'watcher', 'tool', 'Flagged failed payment TXN1001 for review.', 1, 32, {
    transaction: 'TXN1001',
    status: 'FAILED',
    amount: 1250,
  }),
  step(firstCase.id, 'investigator', 'llm', 'Bank timeout detected — retryable (0.97).', 2, 30, {
    bank_code: 'BANK_TIMEOUT',
    retryable: true,
    confidence: 0.97,
  }),
  step(firstCase.id, 'resolver', 'tool', 'Prepared a secure payment link for ₹1,250.', 3, 28, {
    action: 'create_payment_link',
    amount: 1250,
    link_id: 'L1',
  }),
  step(firstCase.id, 'guardian', 'llm', 'Recovery is within policy; no approval needed.', 4, 26, {
    policy: 'automatic_recovery',
    approved: true,
  }),
  step(firstCase.id, 'executor', 'tool', 'Payment recovered successfully.', 5, 24, {
    recovered_by: 'TXN2001',
    amount: 1250,
  }),
  step(firstCase.id, 'comms', 'llm', 'Customer notified that the payment was recovered.', 6, 22, {
    channel: 'web',
    language: 'en',
  }),
  {
    type: 'message',
    case_id: firstCase.id,
    chat_id: firstCase.chat_id,
    direction: 'assistant',
    text: 'Your ₹1,250 payment has been recovered successfully.',
    language: 'en',
    created_at: stamp(21),
  },
  {
    type: 'case_status',
    case_id: firstCase.id,
    status: 'resolved',
    resolved_at: stamp(20),
    outcome: { amount_recovered: 1250 },
  },
  {
    type: 'case_created',
    case_id: secondCase.id,
    status: 'awaiting_approval',
    intent: secondCase.intent,
    txn_id: secondCase.txn_id,
    chat_id: secondCase.chat_id,
    language: secondCase.language,
    source: secondCase.source,
    ts: stamp(10),
  },
  step(secondCase.id, 'watcher', 'tool', 'Flagged two same-amount payments 40 seconds apart.', 1, 9, {
    transaction_ids: ['TXN1002', 'TXN1003'],
    amounts: [1500, 1500],
  }),
  step(secondCase.id, 'investigator', 'llm', 'Duplicate charge is likely (0.94 confidence).', 2, 8, {
    duplicate_probability: 0.94,
    interval_s: 40,
  }),
  step(secondCase.id, 'resolver', 'llm', 'Recommended a ₹1,500 refund for the second charge.', 3, 7, {
    action: 'refund',
    amount: 1500,
  }),
  step(secondCase.id, 'guardian', 'llm', 'Human approval required for this refund amount.', 4, 6, {
    policy: 'refund_requires_approval',
    approved: false,
  }),
  {
    type: 'approval_requested',
    case_id: secondCase.id,
    status: 'awaiting_approval',
    action: 'Refund ₹1,500 — duplicate charge',
    verdict_reason: secondCase.verdict_reason,
    ts: stamp(5),
  },
]

export function fixtureCaseList() {
  return fixtureCases.map((item) => structuredClone({
    id: item.id,
    status: item.status,
    intent: item.intent,
    txn_id: item.txn_id,
    chat_id: item.chat_id,
    language: item.language,
    source: item.source,
    human_touched: item.human_touched,
    created_at: item.created_at,
    resolved_at: item.resolved_at,
    outcome: item.outcome,
  }))
}

export function fixtureCaseDetail(caseId) {
  const detail = fixtureDetails[caseId]
  if (!detail) throw new Error(`Fixture case ${caseId} was not found`)
  return structuredClone(detail)
}
