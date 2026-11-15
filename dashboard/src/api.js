import {
  fixtureCaseDetail,
  fixtureCaseList,
  fixtureMetrics,
} from './fixtures'

export const API_URL = (import.meta.env.VITE_API || 'http://localhost:8000').replace(/\/+$/, '')
export const CORE_URL = (import.meta.env.VITE_CORE || 'http://localhost:8001').replace(/\/+$/, '')
export const FIXTURE_MODE = import.meta.env.VITE_FIXTURE === '1'

async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  })
  if (!response.ok) {
    const body = await response.text()
    throw new Error(body || `Request failed with status ${response.status}`)
  }
  if (response.status === 204) return null
  const contentType = response.headers.get('content-type') || ''
  return contentType.includes('application/json') ? response.json() : null
}

export async function getCases(status = '') {
  if (FIXTURE_MODE) return fixtureCaseList()
  const query = new URLSearchParams()
  if (status) query.set('status', status)
  return request(`${API_URL}/api/cases?${query}`)
}

export async function getCase(caseId) {
  if (FIXTURE_MODE) return fixtureCaseDetail(caseId)
  return request(`${API_URL}/api/cases/${encodeURIComponent(caseId)}`)
}

export async function getMetrics() {
  if (FIXTURE_MODE) return fixtureMetrics
  return request(`${API_URL}/api/metrics`)
}

export async function decideCase(caseId, approved) {
  if (FIXTURE_MODE) return { ok: true, case_id: caseId, approved }
  return request(`${API_URL}/api/cases/${encodeURIComponent(caseId)}/decision`, {
    method: 'POST',
    body: JSON.stringify({ approved, by: 'dashboard' }),
  })
}

export async function scanCases() {
  if (FIXTURE_MODE) return { ok: true }
  return request(`${API_URL}/api/watcher/scan`, { method: 'POST' })
}

export async function resetDemo() {
  if (FIXTURE_MODE) return { ok: true }
  return request(`${API_URL}/api/demo/reset`, { method: 'POST' })
}

export async function sendInboundMessage(chatId, text) {
  if (FIXTURE_MODE) return { ok: true }
  return request(`${API_URL}/ingress/message`, {
    method: 'POST',
    body: JSON.stringify({ chat_id: chatId, text }),
  })
}

export async function createScenario(scenario, customerRef, language) {
  if (FIXTURE_MODE) return { ok: true, customer_id: customerRef, created: [] }
  return request(`${CORE_URL}/admin/scenario`, {
    method: 'POST',
    body: JSON.stringify({
      scenario,
      customer_ref: customerRef,
      language,
    }),
  })
}
