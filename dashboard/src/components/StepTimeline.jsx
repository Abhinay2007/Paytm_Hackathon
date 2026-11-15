import { useState } from 'react'
import { ChevronDown, ChevronRight, Clock3, Braces } from 'lucide-react'
import { formatLatency, formatRelativeTime } from '../formatters'

const KIND_LABELS = {
  llm: 'Reasoning',
  tool: 'Tool call',
  system: 'System',
  skipped: 'Skipped',
}

export default function StepTimeline({ steps }) {
  const [expanded, setExpanded] = useState(null)
  const ordered = [...steps].sort((a, b) => {
    if (a.seq != null && b.seq != null) return a.seq - b.seq
    return new Date(a.created_at || a.ts || 0) - new Date(b.created_at || b.ts || 0)
  })

  return (
    <section className="panel timeline-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">AUDIT TRAIL</p>
          <h2>Step timeline</h2>
        </div>
        <span className="subtle-count">{ordered.length} events</span>
      </div>
      {ordered.length === 0 ? (
        <div className="empty-state timeline-empty">
          <Clock3 size={22} />
          <p>Agent activity will appear here.</p>
        </div>
      ) : (
        <div className="timeline-list">
          {ordered.map((step, index) => {
            const key = `${step.seq ?? step.created_at ?? step.ts ?? index}-${step.agent}`
            const isExpanded = expanded === key
            return (
              <article className="timeline-item" key={key}>
                <span className={`timeline-marker ${step.ok === false ? 'marker-failed' : ''}`} />
                <button
                  type="button"
                  className="timeline-row"
                  onClick={() => setExpanded(isExpanded ? null : key)}
                  aria-expanded={isExpanded}
                >
                  <span className={`timeline-agent agent-text-${(step.agent || '').toLowerCase()}`}>
                    {step.agent || 'Agent'}
                  </span>
                  <span className={`kind-chip kind-${step.kind || 'system'}`}>
                    {KIND_LABELS[step.kind] || step.kind || 'Event'}
                  </span>
                  <span className="timeline-summary">{step.summary || 'Step completed'}</span>
                  <span className="timeline-latency">{formatLatency(step.latency_ms)}</span>
                  <span className="timeline-time">{formatRelativeTime(step.created_at || step.ts)}</span>
                  {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                </button>
                {isExpanded && (
                  <div className="timeline-output">
                    <div className="output-label"><Braces size={14} /> Raw output</div>
                    <pre>{JSON.stringify(step.output ?? step, null, 2)}</pre>
                  </div>
                )}
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}
