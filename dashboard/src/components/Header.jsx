import { Activity, ShieldCheck } from 'lucide-react'

export default function Header({ connected }) {
  return (
    <header className="topbar">
      <div className="brand-lockup">
        <div className="brand-mark" aria-hidden="true">
          <ShieldCheck size={23} strokeWidth={2.3} />
        </div>
        <div>
          <h1>Paytm Mitra</h1>
        </div>
      </div>

      <div className="topbar-actions">
        <div className="connection-status" role="status" aria-live="polite">
          <span className={`connection-dot ${connected ? 'is-connected' : 'is-disconnected'}`} />
          <span>{connected ? 'Live' : 'Connecting'}</span>
          <Activity size={15} aria-hidden="true" />
        </div>
        <span className="demo-badge">Demo data</span>
      </div>
    </header>
  )
}
