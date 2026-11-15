import { useEffect } from 'react'
import { API_URL, FIXTURE_MODE } from './api'
import { fixtureEvents } from './fixtures'
import { useDashboardStore } from './store'

const EVENT_TYPES = [
  'case_created',
  'case_status',
  'step',
  'message',
  'approval_requested',
  'approval_decided',
  'metrics',
]

export function useStream() {
  const setConnected = useDashboardStore((state) => state.setConnected)
  const applyEvent = useDashboardStore((state) => state.applyEvent)
  const showToast = useDashboardStore((state) => state.showToast)
  const fixtureReplayKey = useDashboardStore((state) => state.fixtureReplayKey)

  useEffect(() => {
    if (FIXTURE_MODE) {
      setConnected(true)
      let index = 0
      let timer
      const replay = () => {
        if (index >= fixtureEvents.length) return
        applyEvent(fixtureEvents[index])
        index += 1
        timer = window.setTimeout(replay, 1200)
      }
      timer = window.setTimeout(replay, 1200)
      return () => {
        window.clearTimeout(timer)
        setConnected(false)
      }
    }

    const stream = new EventSource(`${API_URL}/api/stream`)
    const receive = (message, eventName = 'message') => {
      try {
        const payload = JSON.parse(message.data)
        applyEvent({ ...payload, type: payload.type || eventName })
      } catch {
        showToast('Received an unreadable event from the platform')
      }
    }

    stream.onopen = () => setConnected(true)
    stream.onerror = () => setConnected(false)
    stream.onmessage = (event) => receive(event)
    for (const eventName of EVENT_TYPES) {
      stream.addEventListener(eventName, (event) => receive(event, eventName))
    }

    return () => {
      stream.close()
      setConnected(false)
    }
  }, [applyEvent, fixtureReplayKey, setConnected, showToast])
}
