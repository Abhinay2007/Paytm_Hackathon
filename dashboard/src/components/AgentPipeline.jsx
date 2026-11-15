import PipelineNodeIcon from './PipelineNodeIcon'

const PIPELINE = [
  ['concierge', 'Concierge'],
  ['investigator', 'Investigator'],
  ['resolver', 'Resolver'],
  ['guardian', 'Guardian'],
  ['executor', 'Executor'],
  ['comms', 'Comms'],
]

function nodeFor(agent, steps, caseStatus) {
  const matching = steps.filter((step) => (step.agent || '').toLowerCase() === agent)
  const latest = matching.at(-1)
  if (!latest) return { state: 'idle', summary: 'Waiting for handoff' }
  if (latest.skipped || latest.status === 'skipped' || latest.kind === 'skipped') {
    return { state: 'skipped', summary: latest.summary || 'Skipped by policy' }
  }
  if (latest.ok === false) return { state: 'failed', summary: latest.summary || 'Step failed' }
  if (latest.status === 'active' || latest.active) {
    return { state: 'active', summary: latest.summary || 'Working…' }
  }
  const isLast = steps.at(-1) === latest
  if (isLast && caseStatus === 'open' && latest.ok == null) {
    return { state: 'active', summary: latest.summary || 'Working…' }
  }
  return { state: 'done', summary: latest.summary || 'Completed' }
}

export default function AgentPipeline({ caseItem }) {
  const steps = caseItem.steps || []
  return (
    <section className="panel pipeline-panel">
      <div className="panel-heading pipeline-heading">
        <div>
          <p className="eyebrow">AUTONOMOUS WORKFLOW</p>
          <h2>Agent pipeline</h2>
        </div>
        <span className="pipeline-count">{steps.length} steps</span>
      </div>
      <div className="pipeline-strip">
        {PIPELINE.map(([agent, label], index) => {
          const node = nodeFor(agent, steps, caseItem.status)
          return (
            <div className="pipeline-node-wrap" key={agent}>
              <div className={`pipeline-node pipeline-${node.state}`}>
                <span className="pipeline-icon"><PipelineNodeIcon state={node.state} /></span>
                <span className="pipeline-label">{label}</span>
                <span className="pipeline-summary" title={node.summary}>{node.summary}</span>
              </div>
              {index < PIPELINE.length - 1 && (
                <span className={`pipeline-connector ${node.state === 'done' ? 'connector-done' : ''}`} />
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}
