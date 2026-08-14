// ============================================================
// API 层 — 后端接口调用（生产使用真实接口，显式 mock/开发环境可回退）
//
// 切换方式：设置环境变量 VITE_USE_FIN_API_MOCK=true 强制使用 mock
//          VITE_API_BASE_URL 指向后端地址（默认同源 /api）
// ============================================================
import type {
  DashboardData,
  AlertData,
  AlertRule,
  AlertReport,
  FinancialTrust,
  AlertRulesData,
} from '../types'
import {
  sanitizeFinancialAlertData,
  sanitizeFinancialDashboard,
} from '../lib/trust'
import { safeFinancialApiError } from './errors'
import {
  mockOhlcSeries,
  rollingMa,
  mockIndices,
  mockWatchlist,
  mockAlertRules,
} from '../lib/mockData'

// --------------- 配置 ---------------

const API_BASE = (import.meta as any).env?.VITE_API_BASE_URL?.trim() || ''

function apiUrl(path: string): string {
  return `${API_BASE}/api${path}`
}

/** 是否使用 mock 模式（默认关闭；设置 VITE_USE_FIN_API_MOCK=true 可强制使用 mock） */
function useMock(): boolean {
  const raw = (import.meta as any).env?.VITE_USE_FIN_API_MOCK
  if (raw === undefined || raw === null || raw === '') return false
  return String(raw).toLowerCase() === 'true'
}

function allowMockFallback(): boolean {
  const raw = (import.meta as any).env?.VITE_ALLOW_FIN_API_FALLBACK
  if (raw !== undefined && raw !== null && raw !== '') {
    return String(raw).toLowerCase() === 'true'
  }
  return false
}

// --------------- 通用 fetch ---------------

async function get<T>(path: string): Promise<T> {
  const res = await fetch(apiUrl(path), {
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
  })
  if (!res.ok) {
    throw safeFinancialApiError(res)
  }
  return res.json()
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(apiUrl(path), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    throw safeFinancialApiError(res)
  }
  return res.json()
}

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('access_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// ============================================================
// Dashboard API
// ============================================================

/**
 * 获取 Dashboard 全部数据。
 * 生产环境：GET /api/financial/dashboard
 * 显式 mock 模式或开发兜底：返回本地测试数据
 */
export async function fetchDashboard(): Promise<DashboardData> {
  if (useMock()) {
    return mockDashboard('mock')
  }
  try {
    return sanitizeFinancialDashboard(await get<DashboardData>('/financial/dashboard'))
  } catch (e: any) {
    if (!allowMockFallback()) throw e
    return mockDashboard('mock-fallback', 'live API unavailable')
  }
}

/**
 * 获取 K 线数据（子集）
 * GET /api/financial/candlestick?symbol=CL=F&interval=1h&count=220
 */
export async function fetchCandlestick(
  symbol: string,
  interval: string,
  count: number,
): Promise<{ bars: DashboardData['bars']; ma20: DashboardData['ma20']; ma50: DashboardData['ma50']; ma200: DashboardData['ma200'] }> {
  if (useMock()) {
    const bars = mockOhlcSeries(count)
    return {
      bars,
      ma20: rollingMa(bars, 20),
      ma50: rollingMa(bars, 50),
      ma200: rollingMa(bars, 200),
    }
  }
  return get(`/financial/candlestick?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(interval)}&count=${count}`)
}

/**
 * 获取指数摘要卡片
 * GET /api/financial/indices
 */
export async function fetchIndices(): Promise<DashboardData['indices']> {
  if (useMock()) return mockIndices()
  try {
    return await get('/financial/indices')
  } catch (e) {
    if (!allowMockFallback()) throw e
    return mockIndices()
  }
}

/**
 * 获取自选监视列表
 * GET /api/financial/watchlist
 */
export async function fetchWatchlist(): Promise<DashboardData['watchlist']> {
  if (useMock()) return mockWatchlist()
  try {
    return await get('/financial/watchlist')
  } catch (e) {
    if (!allowMockFallback()) throw e
    return mockWatchlist()
  }
}

// ============================================================
// Alert API
// ============================================================

/**
 * 获取预警规则列表（实时监控面板）
 * GET /api/financial/alert/rules
 */
export async function fetchAlertRules(): Promise<AlertRule[]> {
  if (useMock()) return mockAlertRules()
  try {
    const data = sanitizeFinancialAlertData({
      ...(await get<AlertRulesData>('/financial/alert/rules')),
      history: [],
    })
    return data.rules
  } catch (e) {
    if (!allowMockFallback()) throw e
    return mockAlertRules()
  }
}

/**
 * 获取告警历史记录
 * GET /api/financial/alert/history?limit=50
 */
export async function fetchAlertHistory(limit = 50): Promise<AlertReport[]> {
  if (useMock()) return []
  try {
    return await get<AlertReport[]>(`/financial/alert/history?limit=${limit}`)
  } catch (e) {
    if (!allowMockFallback()) throw e
    return []
  }
}

function mockDashboard(mode: 'mock' | 'mock-fallback', detail = 'front-end generated mock data'): DashboardData {
  const bars = mockOhlcSeries(220, 64)
  const fallbackStatus: 'mock' | 'degraded' = mode === 'mock' ? 'mock' : 'degraded'
  const watchlist = mockWatchlist().map((row) => ({
    ...row,
    metric_id: row.symbol,
    status: fallbackStatus,
  }))
  const indices = mockIndices().map((item, index) => ({
    ...item,
    metric_id: ['wsi', 'diplomacy', 'energy', 'supply', 'tech', 'society'][index] ? `IDX-${['WSI', 'DIPLOMACY', 'ENERGY', 'SUPPLY', 'TECH', 'SOCIETY'][index]}` : undefined,
  }))
  const baseTimes = bars.slice(-24).map((bar) => bar.time)
  const series = [
    {
      id: 'IDX-WSI',
      kind: 'index' as const,
      label: '世界状态综合指数',
      unit: '指数',
      source: '前端模拟',
      cadence: '1h',
      status: mode === 'mock' ? 'mock' as const : 'degraded' as const,
      category: 'politics' as const,
      region: '全球',
      description: '本地回退数据。',
      points: bars.slice(-24).map((bar) => ({ time: bar.time, value: bar.close })),
      latest: bars[bars.length - 1]?.close || 0,
      change_pct: ((bars[bars.length - 1]?.close || 0) - (bars[bars.length - 24]?.close || 1)) / Math.max(bars[bars.length - 24]?.close || 1, 1) * 100,
    },
    ...watchlist.map((row, rowIndex) => ({
      id: row.symbol,
      kind: 'metric' as const,
      label: row.label,
      unit: '指数',
      source: row.source,
      cadence: row.cadence,
      status: row.status,
      category: row.category,
      region: row.region,
      description: row.description || '本地回退数据。',
      points: baseTimes.map((time, pointIndex) => ({
        time,
        value: row.price * (0.96 + ((pointIndex + rowIndex) % 9) * 0.01),
      })),
      latest: row.price,
      change_pct: row.change_pct,
    })),
  ]
  return {
    mode,
    cache: 'miss',
    last_updated: new Date().toISOString(),
    bars,
    ma20: rollingMa(bars, 20),
    ma50: rollingMa(bars, 50),
    ma200: rollingMa(bars, 200),
    indices,
    watchlist,
    series,
    default_metric_id: 'IDX-WSI',
    sources: [
      {
        id: mode,
        name: mode === 'mock' ? 'Front-end mock' : 'Live API fallback',
        status: mode === 'mock' ? 'mock' : 'degraded',
        records: 0,
        detail,
        cadence: 'local',
        last_updated: new Date().toISOString(),
      },
    ],
    alert_rules: mockAlertRules(),
    trust: mockTrust(mode),
    trust_status: 'mock',
    freshness_status: 'mock',
    data_as_of: new Date().toISOString(),
    model_version: 'frontend-mock-v1',
    method_version: 'frontend-mock-v1',
    schema_version: 'financial-trust-v1',
    snapshot_id: `frontend-${mode}`,
  }
}

function mockTrust(mode: 'mock' | 'mock-fallback'): FinancialTrust {
  return {
    schema_version: 'financial-trust-v1',
    snapshot_id: `frontend-${mode}`,
    trust_status: 'mock',
    freshness_status: 'mock',
    computability: 'not_computable',
    computable: false,
    data_as_of: new Date().toISOString(),
    evaluated_at: new Date().toISOString(),
    coverage_ratio: 0,
    minimum_coverage_ratio: 0.5,
    usable_sources: 0,
    source_total: 0,
    usable_source_ids: [],
    unavailable_source_ids: [],
    source_status: { mock: 1 },
    model_version: 'frontend-mock-v1',
    method_version: 'frontend-mock-v1',
    unavailable_reasons: [
      {
        code: mode === 'mock' ? 'EXPLICIT_MOCK_MODE' : 'LIVE_API_UNAVAILABLE',
        message: mode === 'mock' ? 'Explicit mock mode is enabled.' : 'Live API is unavailable.',
      },
    ],
    alerts_enabled: mode === 'mock',
  }
}

/**
 * 获取预警页完整数据
 * GET /api/financial/alert/data
 */
export async function fetchAlertData(): Promise<AlertData> {
  if (useMock()) {
    return {
      rules: mockAlertRules(),
      history: [],
      trust: mockTrust('mock'),
      trust_status: 'mock',
      freshness_status: 'mock',
      model_version: 'frontend-mock-v1',
      method_version: 'frontend-mock-v1',
      schema_version: 'financial-trust-v1',
      snapshot_id: 'frontend-mock',
      unavailable_reasons: mockTrust('mock').unavailable_reasons,
    }
  }
  return sanitizeFinancialAlertData(await get<AlertData>('/financial/alert/data'))
}

export {
  fetchAlertTriageDetail,
  submitAdminAlertTriageEvent,
} from './triage.ts'

/**
 * 创建预警规则
 * POST /api/financial/alert/rules
 */
export async function createAlertRule(data: Partial<AlertRule>): Promise<AlertRule> {
  return post<AlertRule>('/financial/alert/rules', data)
}

/**
 * 更新预警规则
 * PUT /api/financial/alert/rules/{id}
 */
export async function updateAlertRule(id: string, data: Partial<AlertRule>): Promise<AlertRule> {
  if (useMock()) return { ...mockAlertRules()[0], ...data, id }
  const res = await fetch(apiUrl(`/financial/alert/rules/${id}`), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(`PUT alert rule ${id} failed`)
  return res.json()
}

/**
 * 删除预警规则
 * DELETE /api/financial/alert/rules/{id}
 */
export async function deleteAlertRule(id: string): Promise<void> {
  if (useMock()) return
  const res = await fetch(apiUrl(`/financial/alert/rules/${id}`), {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!res.ok) throw new Error(`DELETE alert rule ${id} failed`)
}
