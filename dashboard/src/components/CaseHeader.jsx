import { ArrowUpRight, Copy, Languages } from 'lucide-react'
import { useDashboardStore } from '../store'

const STATUS_LABELS = {
  open: 'Open',
  awaiting_approval: 'Awaiting approval',
  waiting_payment: 'Waiting for payment',
  waiting_timer: 'Waiting on timer',
  waiting_customer: 'Waiting for customer',
  resolved: 'Resolved',
  escalated: 'Escalated',
}

export default function CaseHeader({ caseItem, onSelectCase }) {
  const caseMap = useDashboardStore((state) => state.cases)
  const otherCases = Object.values(caseMap)
  const copyId = async () => {
    try {
      await navigator.clipboard.writeText(caseItem.id)
    } catch {
      // Clipboard access may be unavailable in an insecure local context.
    }
  }

  return (
    <section className="panel case-header-panel">
      <div className="case-title-row">
        <div>
          <p className="eyebrow">SELECTED SUPPORT CASE</p>
          <div className="case-id-line">
            <h2>{caseItem.id}</h2>
            <button type="button" className="icon-button" onClick={copyId} aria-label="Copy case ID">
              <Copy size={14} />
            </button>
          </div>
        </div>
        <span className={`status-badge status-${caseItem.status || 'open'}`}>
          <span className="status-dot" />
          {STATUS_LABELS[caseItem.status] || caseItem.status || 'Open'}
        </span>
      </div>

      <div className="case-meta-grid">
        <div><span>Intent</span><strong>{caseItem.intent || 'Payment support'}</strong></div>
        <div>
          <span>Transaction</span>
          <strong className="mono-value">{caseItem.txn_id || '—'}</strong>
        </div>
        <div><span>Language</span><strong><Languages size={13} /> {caseItem.language || '—'}</strong></div>
        <div><span>Source</span><strong className="source-value">{caseItem.source || '—'}</strong></div>
      </div>

      {otherCases.length > 1 && (
        <label className="case-switcher">
          <span>Case</span>
          <select
            aria-label="Select case"
            value={caseItem.id}
            onChange={(event) => onSelectCase(event.target.value)}
          >
            {otherCases.map((item) => (
              <option value={item.id} key={item.id}>{item.id} · {item.intent || item.status}</option>
            ))}
          </select>
          <ArrowUpRight size={14} />
        </label>
      )}
    </section>
  )
}
