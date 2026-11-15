import { FileSearch } from 'lucide-react'
import LoadingSkeleton from './LoadingSkeleton'
import AgentPipeline from './AgentPipeline'
import CaseHeader from './CaseHeader'
import Conversation from './Conversation'
import StepTimeline from './StepTimeline'

export default function CaseWorkspace({ caseItem, loading, onSelectCase }) {
  if (!caseItem && loading) return <LoadingSkeleton variant="case" />
  if (!caseItem) {
    return (
      <section className="panel case-empty">
        <FileSearch size={36} />
        <h2>No cases yet — run a scenario</h2>
        <p>New payment support cases will appear here as Mitra agents investigate them.</p>
      </section>
    )
  }

  return (
    <div className="case-workspace">
      <CaseHeader caseItem={caseItem} onSelectCase={onSelectCase} />
      <AgentPipeline caseItem={caseItem} />
      <StepTimeline steps={caseItem.steps || []} />
      <Conversation messages={caseItem.messages || []} />
    </div>
  )
}
