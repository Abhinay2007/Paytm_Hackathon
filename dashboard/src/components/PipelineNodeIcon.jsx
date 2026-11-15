import { Check, Circle, Minus, X } from 'lucide-react'

export default function PipelineNodeIcon({ state }) {
  if (state === 'done') return <Check size={13} strokeWidth={3} />
  if (state === 'failed') return <X size={13} strokeWidth={3} />
  if (state === 'skipped') return <Minus size={13} />
  return <Circle size={10} />
}
