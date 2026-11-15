import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

const COLORS = {
  open: '#00BAF2',
  awaiting_approval: '#efa928',
  waiting_payment: '#9273e8',
  waiting_timer: '#9273e8',
  waiting_customer: '#9273e8',
  resolved: '#22a06b',
  escalated: '#e35b62',
}

const LABELS = {
  open: 'Open',
  awaiting_approval: 'Approval',
  waiting_payment: 'Waiting',
  waiting_timer: 'Timer',
  waiting_customer: 'Customer',
  resolved: 'Resolved',
  escalated: 'Escalated',
}

export default function StatusChart({ cases, loading }) {
  const counts = cases.reduce((result, item) => {
    const status = item.status || 'open'
    result[status] = (result[status] || 0) + 1
    return result
  }, {})
  const data = Object.entries(counts).map(([status, count]) => ({
    status,
    label: LABELS[status] || status.replaceAll('_', ' '),
    count,
  }))

  return (
    <section className="panel chart-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">CASE MIX</p>
          <h2>Cases by status</h2>
        </div>
        <span className="chart-total">{cases.length} total</span>
      </div>
      {loading && cases.length === 0 ? (
        <div className="chart-skeleton"><span /><span /><span /><span /></div>
      ) : data.length === 0 ? (
        <div className="empty-state chart-empty">No case activity to chart.</div>
      ) : (
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height={174}>
            <BarChart data={data} margin={{ top: 8, right: 4, left: -20, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="#edf1f6" />
              <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: '#78869a', fontSize: 10 }} />
              <YAxis allowDecimals={false} axisLine={false} tickLine={false} tick={{ fill: '#98a3b2', fontSize: 10 }} />
              <Tooltip
                cursor={{ fill: '#f5f8fc' }}
                contentStyle={{ border: '1px solid #e6ebf2', borderRadius: 10, fontSize: 12 }}
                formatter={(value) => [`${value} cases`, 'Volume']}
              />
              <Bar dataKey="count" radius={[5, 5, 0, 0]} maxBarSize={38}>
                {data.map((entry) => <Cell key={entry.status} fill={COLORS[entry.status] || '#93a4b8'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  )
}
