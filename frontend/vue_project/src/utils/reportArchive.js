import { API_PREFIX } from '@/config/api'
import { getToken } from '@/utils/auth'

export const REPORT_STORAGE_KEY = 'data_assistant_report_history_v1'
const REPORT_WORKSPACE_NAME = 'report'

function safeJson(raw, fallback) {
  try {
    const parsed = JSON.parse(raw)
    return parsed ?? fallback
  } catch {
    return fallback
  }
}

function titleFromFileName(name) {
  return String(name || '')
    .replace(/\.md$/i, '')
    .replace(/^\d{4}-\d{2}-\d{2}T?\d{0,2}-?\d{0,2}-?\d{0,2}-?/, '')
    .replace(/[-_]+/g, ' ')
    .trim() || '未命名报告'
}

export function normalizeReportRecord(record) {
  if (!record || typeof record !== 'object') return null
  const topic = String(record.topic || record.title || titleFromFileName(record.fileName) || '').trim()
  const createdAt = record.createdAt || record.time || record.modified || new Date().toISOString()
  const fileName = record.fileName || ''
  return {
    ...record,
    id: record.id || record.filePath || fileName || `report-${createdAt}-${topic}`,
    type: record.type || 'risk',
    topic: topic || '未命名报告',
    title: topic || '未命名报告',
    timeRange: record.timeRange || '7d',
    perspective: record.perspective || 'analyst',
    template: record.template || 'standard',
    content: record.content || '',
    createdAt,
    time: createdAt,
    status: record.status || 'done',
    workspace: record.workspace || REPORT_WORKSPACE_NAME,
    fileName,
    filePath: record.filePath || (fileName ? `${REPORT_WORKSPACE_NAME}/${fileName}` : ''),
  }
}

export function loadLocalReportArchive() {
  const rows = safeJson(localStorage.getItem(REPORT_STORAGE_KEY), [])
  return Array.isArray(rows) ? rows.map(normalizeReportRecord).filter(Boolean) : []
}

export function saveLocalReportArchive(rows) {
  const normalized = (Array.isArray(rows) ? rows : []).map(normalizeReportRecord).filter(Boolean)
  localStorage.setItem(REPORT_STORAGE_KEY, JSON.stringify(normalized))
  return normalized
}

async function scanReportWorkspace() {
  if (!getToken()) return []
  try {
    const res = await fetch(`${API_PREFIX}/workspaces/${encodeURIComponent(REPORT_WORKSPACE_NAME)}/files`, {
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${getToken()}`,
      },
      credentials: 'same-origin',
    })
    if (!res.ok) return []
    const json = await res.json()
    const files = Array.isArray(json?.data) ? json.data : []
    return files
      .filter((file) => !file.is_dir && /\.md$/i.test(file.name || ''))
      .map((file) => normalizeReportRecord({
        id: `${REPORT_WORKSPACE_NAME}/${file.name}`,
        topic: titleFromFileName(file.name),
        title: titleFromFileName(file.name),
        createdAt: file.modified,
        time: file.modified,
        workspace: REPORT_WORKSPACE_NAME,
        fileName: file.name,
        filePath: `${REPORT_WORKSPACE_NAME}/${file.name}`,
        status: 'done',
      }))
      .filter(Boolean)
  } catch {
    return []
  }
}

export async function loadReportArchive() {
  const localRows = loadLocalReportArchive()
  const scannedRows = await scanReportWorkspace()
  const byKey = new Map()
  for (const row of [...scannedRows, ...localRows]) {
    byKey.set(row.filePath || row.id, { ...(byKey.get(row.filePath || row.id) || {}), ...row })
  }
  const merged = [...byKey.values()].sort((a, b) => String(b.createdAt || '').localeCompare(String(a.createdAt || '')))
  return saveLocalReportArchive(merged)
}
