import { Activity, RotateCcw, ScanSearch, ShieldCheck } from 'lucide-react'

export default function Header({ connected, onScan, onReset }) {
  return (
    <header className="topbar">
      <div className="brand-lockup">
        <div className="brand-mark" aria-hidden="true">
          <ShieldCheck size={23} strokeWidth={2.3} />
        </div>
        <div>
          <p className="brand-eyebrow">PAYTM · MITRA AI</p>
          <h1>Mission Control</h1>
        </div>
      </div>

      <div className="topbar-actions">
        <div className="connection-status" role="status" aria-live="polite">
          <span className={`connection-dot ${connected ? 'is-connected' : 'is-disconnected'}`} />
          <span>{connected ? 'Live stream connected' : 'Connecting to platform'}</span>
          <Activity size={15} aria-hidden="true" />
        </div>
        <button className="button button-secondary" type="button" onClick={onReset}>
          <RotateCcw size={16} />
          Reset demo
        </button>
        <button className="button button-primary" type="button" onClick={onScan}>
          <ScanSearch size={16} />
          Scan now
        </button>
      </div>
    </header>
  )
}
