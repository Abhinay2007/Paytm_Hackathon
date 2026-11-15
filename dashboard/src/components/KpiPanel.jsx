import { Activity, Clock3, IndianRupee, Shield, Timer, UsersRound } from 'lucide-react'
import { formatDuration, formatPercent, formatRupees } from '../formatters'
import LoadingSkeleton from './LoadingSkeleton'

export default function KpiPanel({ metrics, loading }) {
  if (!metrics && loading) return <LoadingSkeleton variant="kpis" />
  const items = [
    { label: 'Auto-resolution rate', value: formatPercent(metrics?.auto_resolution_rate), icon: Shield, accent: 'cyan' },
    { label: 'Escalation rate', value: formatPercent(metrics?.escalation_rate), icon: UsersRound, accent: 'amber' },
    { label: 'Median time to resolve', value: formatDuration(metrics?.median_resolution_s), icon: Clock3, accent: 'violet' },
    { label: '₹ recovered', value: formatRupees(metrics?.rupees_recovered), icon: IndianRupee, accent: 'green' },
    { label: 'Human minutes saved', value: `${metrics?.human_minutes_saved ?? '—'}`, detail: 'assumes 8 min per case', icon: Timer, accent: 'blue' },
    { label: 'Agent step success', value: formatPercent(metrics?.step_success_rate), icon: Activity, accent: 'cyan' },
  ]

  return (
    <section className="panel kpi-panel">
      <div className="panel-heading kpi-heading">
        <div>
          <p className="eyebrow">SERVICE HEALTH</p>
          <h2>Performance</h2>
        </div>
        <span className="demo-footnote">Demo data</span>
      </div>
      <div className="kpi-grid">
        {items.map(({ label, value, detail, icon: Icon, accent }) => (
          <article className="kpi-card" key={label}>
            <span className={`kpi-icon kpi-${accent}`}><Icon size={16} /></span>
            <strong>{value}</strong>
            <span className="kpi-label">{label}</span>
            {detail && <span className="kpi-detail">{detail}</span>}
          </article>
        ))}
      </div>
      <div className="kpi-secondary">
        <span>{metrics?.total_cases ?? 0} cases monitored</span>
        <span>{metrics?.auto_resolved ?? 0} auto-resolved</span>
      </div>
    </section>
  )
}
