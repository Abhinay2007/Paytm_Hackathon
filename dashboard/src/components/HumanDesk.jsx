import { Check, ClipboardCheck, X } from 'lucide-react'
import { useState } from 'react'
import { formatRupees } from '../formatters'

function proposedAction(item) {
  const action = item.approval?.action || item.plan?.action
  const amount = item.plan?.amount
  if (typeof action === 'string' && action.toLowerCase().includes('refund')) {
    return item.approval?.action || `Refund ${formatRupees(amount)} — ${item.intent || 'support case'}`
  }
  return item.approval?.action || (action ? String(action).replaceAll('_', ' ') : 'Review recommended action')
}

export default function HumanDesk({ cases, onDecision }) {
  const [pendingId, setPendingId] = useState(null)
  const waiting = cases.filter((item) => item.status === 'awaiting_approval')

  const decide = async (caseId, approved) => {
    setPendingId(caseId)
    await onDecision(caseId, approved)
    setPendingId(null)
  }

  return (
    <section className="panel human-desk-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">HUMAN-IN-THE-LOOP</p>
          <h2>Human desk</h2>
        </div>
        <span className={`desk-count ${waiting.length ? 'has-pending' : ''}`}>
          {waiting.length}
        </span>
      </div>

      {waiting.length === 0 ? (
        <div className="desk-empty">
          <ClipboardCheck size={22} />
          <div><strong>All clear</strong><span>No cases are waiting for approval.</span></div>
        </div>
      ) : (
        <div className="approval-list">
          {waiting.map((item) => (
            <article className="approval-card" key={item.id}>
              <div className="approval-title-line">
                <span className="approval-icon"><ClipboardCheck size={16} /></span>
                <div><strong>{item.id}</strong><span>{item.intent || 'Support case'}</span></div>
              </div>
              <p className="proposed-action">{proposedAction(item)}</p>
              <p className="approval-reason">{item.verdict_reason || item.approval?.reason || 'Agent requested human review.'}</p>
              <div className="approval-buttons">
                <button
                  type="button"
                  className="button button-approve"
                  disabled={pendingId === item.id}
                  onClick={() => decide(item.id, true)}
                >
                  <Check size={15} /> Approve
                </button>
                <button
                  type="button"
                  className="button button-reject"
                  disabled={pendingId === item.id}
                  onClick={() => decide(item.id, false)}
                >
                  <X size={15} /> Reject
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
