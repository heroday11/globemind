import type {
  AlertTriageAdminDetail,
  AlertTriageDetail,
  AlertTriageMutation,
} from '../types/index.ts'
import { sanitizeAlertTriageDetail } from '../lib/triage.ts'
import { safeFinancialApiError } from './errors.ts'


const API_BASE = (import.meta as any).env?.VITE_API_BASE_URL?.trim() || ''

function apiUrl(path: string): string {
  return `${API_BASE}/api${path}`
}

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('access_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/**
 * Read a privacy-redacted lifecycle audit. The server requires authentication
 * and never returns actor IDs or reason text on this endpoint.
 */
export async function fetchAlertTriageDetail(
  alertEventId: string,
): Promise<AlertTriageDetail> {
  const response = await fetch(
    apiUrl(`/financial/alert/triage/${encodeURIComponent(alertEventId)}`),
    { headers: { 'Content-Type': 'application/json', ...authHeaders() } },
  )
  if (!response.ok) throw safeFinancialApiError(response)
  return sanitizeAlertTriageDetail(await response.json())
}

/**
 * Submit an administrator-only lifecycle event. The terminal intentionally
 * does not infer administrator status from client state, so no page control
 * calls this function until a server-provided capability contract exists.
 */
export async function submitAdminAlertTriageEvent(
  alertEventId: string,
  mutation: AlertTriageMutation,
): Promise<AlertTriageAdminDetail> {
  const mock = (import.meta as any).env?.VITE_USE_FIN_API_MOCK
  if (String(mock || '').toLowerCase() === 'true') {
    throw new Error('Alert triage mutations are disabled in mock mode')
  }
  const response = await fetch(
    apiUrl(`/financial/alert/triage/${encodeURIComponent(alertEventId)}/events`),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(mutation),
    },
  )
  if (!response.ok) throw safeFinancialApiError(response)
  return response.json() as Promise<AlertTriageAdminDetail>
}
