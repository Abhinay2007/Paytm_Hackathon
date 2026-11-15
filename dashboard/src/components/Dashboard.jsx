import { lazy, Suspense, useEffect } from 'react'
import { useDashboardStore } from '../store'
import { useStream } from '../useStream'
import CaseWorkspace from './CaseWorkspace'
import DemoControls from './DemoControls'
import Header from './Header'
import HumanDesk from './HumanDesk'
import KpiPanel from './KpiPanel'
import LiveAgentFeed from './LiveAgentFeed'
import Toast from './Toast'

const StatusChart = lazy(() => import('./StatusChart'))

export default function Dashboard() {
  const loadInitial = useDashboardStore((state) => state.loadInitial)
  const scanNow = useDashboardStore((state) => state.scanNow)
  const resetDemo = useDashboardStore((state) => state.resetDemo)
  const cases = useDashboardStore((state) => state.cases)
  const selectedCaseId = useDashboardStore((state) => state.selectedCaseId)
  const feed = useDashboardStore((state) => state.feed)
  const metrics = useDashboardStore((state) => state.metrics)
  const loading = useDashboardStore((state) => state.loading)
  const connected = useDashboardStore((state) => state.connected)
  const selectCase = useDashboardStore((state) => state.selectCase)
  const decide = useDashboardStore((state) => state.decide)

  useStream()
  useEffect(() => {
    void loadInitial()
  }, [loadInitial])

  return (
    <main className="app-shell min-h-screen">
      <Header connected={connected} onScan={scanNow} onReset={resetDemo} />
      <div className="dashboard-grid">
        <aside className="left-rail">
          <LiveAgentFeed
            events={feed}
            selectedCaseId={selectedCaseId}
            onSelect={selectCase}
            loading={loading}
          />
          <DemoControls />
        </aside>

        <section className="center-stage">
          <CaseWorkspace
            caseItem={cases[selectedCaseId]}
            loading={loading}
            onSelectCase={selectCase}
          />
        </section>

        <aside className="right-rail">
          <HumanDesk cases={Object.values(cases)} onDecision={decide} />
          <KpiPanel metrics={metrics} loading={loading} />
          <Suspense fallback={
            <section className="panel chart-panel">
              <div className="panel-heading"><h2>Cases by status</h2></div>
              <div className="chart-skeleton"><span /><span /><span /><span /></div>
            </section>
          }>
            <StatusChart cases={Object.values(cases)} loading={loading} />
          </Suspense>
        </aside>
      </div>
      <Toast />
    </main>
  )
}
