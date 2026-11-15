import { AlertCircle, CheckCircle2, X } from 'lucide-react'
import { useDashboardStore } from '../store'

export default function Toast() {
  const toast = useDashboardStore((state) => state.toast)
  const dismissToast = useDashboardStore((state) => state.dismissToast)
  if (!toast) return null
  const Icon = toast.tone === 'success' ? CheckCircle2 : AlertCircle
  return (
    <div className={`toast toast-${toast.tone}`} role="status">
      <Icon size={18} />
      <span>{toast.message}</span>
      <button type="button" aria-label="Dismiss notification" onClick={dismissToast}>
        <X size={16} />
      </button>
    </div>
  )
}
