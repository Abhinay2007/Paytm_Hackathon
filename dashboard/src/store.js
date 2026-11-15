import { create } from 'zustand'
import {
  FIXTURE_MODE,
  createScenario,
  decideCase as postDecision,
  getCase,
  getCases,
  getMetrics,
  resetDemo as postResetDemo,
  scanCases,
  sendInboundMessage,
} from './api'
import { fixtureCases } from './fixtures'

let toastTimer

function asCaseMap(items) {
  return Object.fromEntries(items.map((item) => [item.id, item]))
}

function mergeDetail(base, detail) {
  const data = detail?.case ? { ...detail.case, ...detail } : detail
  return {
    ...base,
    ...data,
    steps: data?.steps?.length ? data.steps : base?.steps || data?.steps || [],
    messages: data?.messages?.length ? data.messages : base?.messages || data?.messages || [],
  }
}

export const useDashboardStore = create((set, get) => ({
  cases: {},
  selectedCaseId: null,
  feed: [],
  metrics: null,
  connected: false,
  loading: true,
  detailLoading: false,
  fixtureReplayKey: 0,
  chatMessages: [],
  toast: null,

  setConnected: (connected) => set({ connected }),

  showToast: (message, tone = 'error') => {
    clearTimeout(toastTimer)
    set({ toast: { message, tone } })
    toastTimer = setTimeout(() => set({ toast: null }), 4200)
  },
  dismissToast: () => {
    clearTimeout(toastTimer)
    set({ toast: null })
  },

  loadInitial: async () => {
    set({ loading: true })
    try {
      const [items, metrics] = await Promise.all([getCases(), getMetrics()])
      const cases = asCaseMap(items)
      const selectedCaseId = items[0]?.id || null
      const detailIds = [...new Set([
        selectedCaseId,
        ...items.filter((item) => item.status === 'awaiting_approval').map((item) => item.id),
      ].filter(Boolean))]
      const details = await Promise.all(
        detailIds.map(async (id) => {
          try {
            return [id, await getCase(id)]
          } catch {
            return [id, null]
          }
        }),
      )
      for (const [id, detail] of details) {
        if (detail) cases[id] = mergeDetail(cases[id], detail)
      }
      const selected = cases[selectedCaseId]
      set({
        cases,
        selectedCaseId,
        metrics,
        chatMessages: selected?.messages || [],
      })
    } catch (error) {
      get().showToast(error.message || 'Could not load mission control')
    } finally {
      set({ loading: false })
    }
  },

  refreshCases: async () => {
    try {
      const items = await getCases()
      const current = get()
      const cases = Object.fromEntries(items.map((item) => {
        const old = current.cases[item.id]
        const merged = {
          ...old,
          ...item,
          steps: old?.steps || item.steps || [],
          messages: old?.messages || item.messages || [],
        }
        return [
          item.id,
          FIXTURE_MODE && old?.status ? { ...merged, status: old.status } : merged,
        ]
      }))
      const detailIds = [...new Set([
        current.selectedCaseId,
        ...items.filter((item) => item.status === 'awaiting_approval').map((item) => item.id),
      ].filter((id) => id && cases[id]))]
      const details = await Promise.all(
        detailIds.map(async (id) => {
          try {
            return [id, await getCase(id)]
          } catch {
            return [id, null]
          }
        }),
      )
      for (const [id, detail] of details) {
        if (detail) {
          const previousStatus = cases[id]?.status
          cases[id] = mergeDetail(cases[id], detail)
          if (FIXTURE_MODE && previousStatus) cases[id].status = previousStatus
        }
      }
      const selectedCaseId = cases[current.selectedCaseId]
        ? current.selectedCaseId
        : items[0]?.id || null
      set({
        cases,
        selectedCaseId,
      })
    } catch (error) {
      get().showToast(error.message || 'Could not refresh cases')
    }
  },

  selectCase: async (id) => {
    if (!id) return
    set({ selectedCaseId: id, detailLoading: true })
    try {
      const detail = await getCase(id)
      const cases = get().cases
      const selected = mergeDetail(cases[id] || { id }, detail)
      set({
        cases: { ...cases, [id]: selected },
      })
    } catch (error) {
      get().showToast(error.message || 'Could not load case details')
    } finally {
      if (get().selectedCaseId === id) set({ detailLoading: false })
    }
  },

  decide: async (id, approved) => {
    try {
      await postDecision(id, approved)
      const cases = get().cases
      const current = cases[id]
      if (current) {
        const status = approved ? 'resolved' : 'escalated'
        set({
          cases: {
            ...cases,
            [id]: {
              ...current,
              status,
              approval: { ...current.approval, status: approved ? 'approved' : 'rejected' },
            },
          },
        })
      }
      await get().refreshCases()
      if (get().selectedCaseId === id) await get().selectCase(id)
      get().showToast(approved ? 'Approval recorded' : 'Case rejected', 'success')
    } catch (error) {
      get().showToast(error.message || 'Could not submit the decision')
    }
  },

  applyEvent: (event) => {
    const type = event.type
    const caseId = event.case_id || event.case?.id || event.id
    if (type === 'step') {
      set((state) => {
        const existing = state.cases[caseId]
        const steps = existing?.steps || []
        const stepId = event.seq ?? event.id
        const nextSteps = stepId == null || !steps.some((item) => (item.seq ?? item.id) === stepId)
          ? [...steps, event]
          : steps
        return {
          feed: [event, ...state.feed].slice(0, 200),
          cases: existing
            ? { ...state.cases, [caseId]: { ...existing, steps: nextSteps } }
            : state.cases,
        }
      })
      return
    }
    if (type === 'case_status') {
      set((state) => {
        const current = state.cases[caseId]
        if (!current) return state
        const updated = {
          ...current,
          status: event.status || current.status,
          resolved_at: event.resolved_at ?? current.resolved_at,
          outcome: event.outcome ?? current.outcome,
        }
        return { cases: { ...state.cases, [caseId]: updated } }
      })
      return
    }
    if (type === 'message') {
      const message = {
        ...event,
        ...(event.message || {}),
        case_id: caseId,
      }
      set((state) => {
        const current = state.cases[caseId]
        const messages = current?.messages || []
        const alreadyAdded = messages.some(
          (item) => item.text === message.text && item.direction === message.direction,
        )
        const chatMessageExists = state.chatMessages.some(
          (item) => item.text === message.text
            && item.direction === message.direction
            && item.chat_id === message.chat_id,
        )
        return {
          chatMessages: chatMessageExists
            ? state.chatMessages
            : [...state.chatMessages, message].slice(-200),
          cases: current && !alreadyAdded
            ? {
                ...state.cases,
                [caseId]: { ...current, messages: [...messages, message] },
              }
            : state.cases,
        }
      })
      return
    }
    if (type === 'case_created') {
      const incoming = event.case || event
      if (caseId) {
        set((state) => ({
          selectedCaseId: state.selectedCaseId || caseId,
          cases: {
            ...state.cases,
            [caseId]: { ...state.cases[caseId], ...incoming, id: caseId },
          },
        }))
      }
      void get().refreshCases()
      return
    }
    if (type === 'approval_requested') {
      set((state) => {
        const current = state.cases[caseId]
        return current
          ? {
              cases: {
                ...state.cases,
                [caseId]: {
                  ...current,
                  status: 'awaiting_approval',
                  approval: {
                    ...(current.approval || {}),
                    status: 'pending',
                    action: event.action || current.approval?.action,
                  },
                  verdict_reason: event.verdict_reason || current.verdict_reason,
                },
              },
            }
          : state
      })
      void get().refreshCases()
      return
    }
    if (type === 'approval_decided') {
      set((state) => {
        const current = state.cases[caseId]
        return current
          ? {
              cases: {
                ...state.cases,
                [caseId]: {
                  ...current,
                  status: event.status || (event.approved ? 'resolved' : 'escalated'),
                  approval: { ...current.approval, status: event.approved ? 'approved' : 'rejected' },
                },
              },
            }
          : state
      })
      void get().refreshCases()
      return
    }
    if (type === 'metrics') {
      set({ metrics: event.metrics || event.data || event })
    }
  },

  scanNow: async () => {
    try {
      await scanCases()
      await get().refreshCases()
      get().showToast('Watcher scan started', 'success')
    } catch (error) {
      get().showToast(error.message || 'Could not start a scan')
    }
  },

  resetDemo: async () => {
    try {
      await postResetDemo()
      set({
        cases: FIXTURE_MODE ? asCaseMap(fixtureCases) : {},
        selectedCaseId: null,
        feed: [],
        chatMessages: [],
        fixtureReplayKey: FIXTURE_MODE ? get().fixtureReplayKey + 1 : get().fixtureReplayKey,
      })
      await get().loadInitial()
      get().showToast('Demo data reset', 'success')
    } catch (error) {
      get().showToast(error.message || 'Could not reset the demo')
    }
  },

  runScenario: async (scenario, customerRef, language) => {
    try {
      const result = await createScenario(scenario, customerRef, language)
      get().showToast(
        FIXTURE_MODE
          ? `Fixture mode: ${scenario.replaceAll('_', ' ')}`
          : `Scenario created: ${(result.created || []).join(', ') || scenario}`,
        'success',
      )
    } catch (error) {
      get().showToast(error.message || 'Could not create scenario')
    }
  },

  sendChat: async (chatId, text, language = 'en') => {
    try {
      await sendInboundMessage(chatId, text)
      const message = {
        type: 'message',
        direction: 'customer',
        text,
        chat_id: chatId,
        language,
        created_at: new Date().toISOString(),
        case_id: get().selectedCaseId,
      }
      get().applyEvent(message)
    } catch (error) {
      get().showToast(error.message || 'Could not send your message')
    }
  },
}))
