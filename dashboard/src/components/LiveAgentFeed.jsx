import { Activity, CircleDashed } from 'lucide-react'
import { formatRelativeTime } from '../formatters'
import LoadingSkeleton from './LoadingSkeleton'

const AGENT_LABELS = {
  watcher: 'Watcher',
  concierge: 'Concierge',
  investigator: 'Investigator',
  resolver: 'Resolver',
  guardian: 'Guardian',
  executor: 'Executor',
  comms: 'Comms',
  human: 'Human',
}

export default function LiveAgentFeed({ events, selectedCaseId, onSelect, loading }) {
  return (
    <section className="panel feed-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">REAL-TIME OPERATIONS</p>
          <h2>Live agent feed</h2>
        </div>
        <span className="live-pill"><span /> LIVE</span>
      </div>

      <div className="feed-list" aria-live="polite">
        {loading && events.length === 0 ? (
          <LoadingSkeleton variant="feed" />
        ) : events.length === 0 ? (
          <div className="empty-state compact-empty">
            <CircleDashed size={23} />
            <p>No cases yet — run a scenario</p>
          </div>
        ) : (
          events.map((event, index) => {
            const agent = (event.agent || 'human').toLowerCase()
            const failed = event.ok === false
            return (
              <button
                type="button"
                key={event.id || `${event.case_id}-${event.seq ?? event.ts ?? event.created_at ?? event.summary ?? index}`}
                className={`feed-row ${failed ? 'feed-row-failed' : ''} ${
                  event.case_id === selectedCaseId ? 'feed-row-selected' : ''
                }`}
                onClick={() => onSelect(event.case_id)}
              >
                <span className={`agent-badge agent-${agent}`}>
                  <Activity size={12} />
                  {AGENT_LABELS[agent] || agent}
                </span>
                <span className="feed-summary" title={event.summary}>
                  {event.summary || event.kind || 'Agent event'}
                </span>
                <span className="feed-meta">
                  <span>{event.case_id || '—'}</span>
                  <time>{formatRelativeTime(event.ts || event.created_at)}</time>
                </span>
              </button>
            )
          })
        )}
      </div>
      <div className="feed-footer">
        <span className="pulse-ring" />
        Showing the latest {events.length} of 200 events
      </div>
    </section>
  )
}
