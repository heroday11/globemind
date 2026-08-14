import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import test from 'node:test'

import {
  AssistantApiError,
  buildAssistantAuthHeaders,
  createAssistantApi,
} from '../src/features/assistant/api.js'
import {
  normalizeAssistantMessageDto,
  normalizeAssistantProviderDto,
  normalizeAssistantSessionDto,
  normalizeAssistantWorkspaceDto,
  normalizeBriefingScheduleDto,
} from '../src/features/assistant/dto.js'
import {
  createRequestAbortScope,
  displayFolderName,
  formatConversationTime,
  isAssistantAbortError,
  parseSseDataBlocks,
  pickSearchQueryFromPayload,
  safeParseJson,
} from '../src/features/assistant/state.js'
import {
  BRIEFING_CADENCE_OPTIONS,
  BRIEFING_ASSURANCE_SCHEMA,
  BRIEFING_STATUS_LABELS,
  briefingAssuranceLabel,
  briefingCadenceLabel,
  briefingStatusLabel,
  buildAssistantDashboardTrend,
  buildBriefingPeriodicTasks,
  buildBriefingScheduleContext,
  buildBriefingSchedulePayload,
  calculateBriefingStats,
  calculateDashboardBarHeight,
  createBriefingScheduleForm,
  dashboardDateKey,
  normalizeBriefingAssurance,
} from '../src/features/assistant/briefing/model.js'
import { createBriefingScheduleService } from '../src/features/assistant/briefing/service.js'
import { createAssistantWorkspaceController } from '../src/features/assistant/workspace/controller.js'
import { triggerBrowserDownload } from '../src/features/assistant/workspace/download.js'
import {
  TEXT_PREVIEW_EXTENSIONS,
  isTextPreviewFile,
  normalizeAssistantWorkspaceDto as normalizeWorkspaceDtoFromFeature,
  selectedWorkspaceFilePaths,
  toggleAllWorkspaceFiles,
  toggleWorkspaceFileSelection,
  workspaceArchiveFilename,
  workspaceChildPath,
  workspaceDownloadFilename,
  workspaceFilePath,
  workspaceFilePreviewFromError,
  workspaceFilePreviewFromResponse,
  workspaceParentPath,
} from '../src/features/assistant/workspace/model.js'
import { createAssistantWorkspaceService } from '../src/features/assistant/workspace/service.js'
import {
  chatStreamEventError,
  createChatStreamMessage,
  createChatStreamState,
  finalizeChatStreamState,
  reduceChatStreamEvent,
} from '../src/features/assistant/chat/reducer.js'
import {
  ChatStreamError,
  createChatStreamController,
  isChatStreamAbortError,
} from '../src/features/assistant/chat/stream.js'
import {
  REPORT_STORAGE_KEY,
  REPORT_WORKSPACE_NAME,
  buildConversationReportContext,
  buildPptMasterRequestMarkdown,
  buildReportFilename,
  buildReportPrompt,
  calculateReportEditorStats,
  createReportConfig,
  deriveReportTopic,
  loadReportHistory,
  normalizeReportRecord,
  sanitizeReportFilename,
  saveReportHistory,
} from '../src/features/assistant/reports/model.js'
import { createAssistantReportArchiveService } from '../src/features/assistant/reports/service.js'
import {
  ReportStreamError,
  createReportStreamController,
  reportStreamEventError,
} from '../src/features/assistant/reports/stream.js'
import {
  directoryLoadErrorText,
  formatDirectoryCount,
  formatDirectoryTimestamp,
  memberPresenceLabel,
  siteDirectoryStatus,
} from '../src/features/assistant/directory/model.js'

function jsonResponse(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => 'application/json' },
    async json() {
      return payload
    },
    async text() {
      return JSON.stringify(payload)
    },
  }
}

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial))
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null
    },
    setItem(key, value) {
      values.set(key, String(value))
    },
  }
}

function concatBytes(...parts) {
  const result = new Uint8Array(parts.reduce((total, part) => total + part.length, 0))
  let offset = 0
  for (const part of parts) {
    result.set(part, offset)
    offset += part.length
  }
  return result
}

test('assistant site and member directory keeps unknown values and catalog state honest', () => {
  assert.equal(formatDirectoryCount(null), '—')
  assert.equal(formatDirectoryCount(''), '—')
  assert.equal(formatDirectoryCount(-1), '—')
  assert.equal(formatDirectoryCount(12.5), '—')
  assert.equal(formatDirectoryCount('1234'), '1,234')
  assert.deepEqual(siteDirectoryStatus('active'), {
    label: '配置为启用',
    tone: 'configured',
  })
  assert.deepEqual(siteDirectoryStatus('unexpected'), {
    label: '未登记状态',
    tone: 'unknown',
  })
  assert.equal(formatDirectoryTimestamp(null), '—')
  assert.equal(formatDirectoryTimestamp('2026-04-30 15:00'), '2026-04-30 15:00')
  assert.equal(formatDirectoryTimestamp('not-a-time'), '—')
  assert.equal(memberPresenceLabel(true), '目录标记：在线')
  assert.equal(memberPresenceLabel(undefined), '目录标记：未知')
  assert.equal(
    directoryLoadErrorText({ status: 500, message: 'secret-canary' }),
    '站点与成员目录加载失败。',
  )
})

test('assistant API facade owns auth, encoded routes, bodies, and stream transport', async () => {
  const calls = []
  const fetchImpl = async (url, options) => {
    calls.push({ url, options })
    return jsonResponse({ ok: true, data: { request: calls.length } })
  }
  const api = createAssistantApi({
    fetchImpl,
    apiPrefix: '/api',
    getAccessToken: () => 'token-1',
  })

  await api.saveSchedule('daily/1', { title: 'Daily' })
  await api.createWorkspace({ name: 'report' })
  await api.listWorkspaceFiles('research space', 'folder/a')
  await api.downloadWorkspaceFile('research space', 'folder/a report.md')
  await api.downloadWorkspaceZip('research space', ['folder/a.md', 'root.md'])
  const formData = new FormData()
  formData.append('files', new Blob(['hello']), 'hello.txt')
  await api.uploadWorkspaceFiles('research space', formData)
  await api.listSessionMessages('session/1')
  const stream = await api.openAssistantStream({ message: 'hello' }, { signal: { aborted: false } })

  assert.equal(calls[0].url, '/api/assistant/schedules/daily%2F1')
  assert.equal(calls[0].options.method, 'PUT')
  assert.deepEqual(JSON.parse(calls[0].options.body), { title: 'Daily' })
  assert.equal(calls[0].options.headers.Authorization, 'Bearer token-1')
  assert.equal(calls[0].options.credentials, 'same-origin')
  assert.equal(calls[1].url, '/api/workspaces')
  assert.equal(
    calls[2].url,
    '/api/workspaces/research%20space/files?subpath=folder%2Fa',
  )
  assert.equal(
    calls[3].url,
    '/api/workspaces/research%20space/files/folder%2Fa%20report.md/download',
  )
  assert.equal(calls[4].url, '/api/workspaces/research%20space/download-zip')
  assert.deepEqual(JSON.parse(calls[4].options.body), {
    filenames: ['folder/a.md', 'root.md'],
  })
  assert.equal(calls[5].url, '/api/workspaces/research%20space/upload')
  assert.equal(calls[5].options.method, 'POST')
  assert.equal(calls[5].options.body, formData)
  assert.equal(calls[5].options.headers['Content-Type'], undefined)
  assert.equal(calls[6].url, '/api/assistant/sessions/session%2F1/messages')
  assert.equal(calls[7].url, '/api/assistant/cc/stream')
  assert.equal(calls[7].options.headers.Accept, 'text/event-stream')
  assert.equal(stream.status, 200)
  assert.deepEqual(buildAssistantAuthHeaders('', { Accept: 'application/json' }), {
    Accept: 'application/json',
  })
})

test('assistant API maps HTTP failures to a stable typed error', async () => {
  const api = createAssistantApi({
    fetchImpl: async () => jsonResponse({ detail: 'session missing', code: 'not_found' }, 404),
    apiPrefix: '/api',
    getAccessToken: () => '',
  })

  await assert.rejects(
    api.listSessionMessages(99),
    (error) =>
      error instanceof AssistantApiError &&
      error.status === 404 &&
      error.message === 'session missing' &&
      error.payload.code === 'not_found',
  )
})

test('provider, workspace, session, message, and schedule DTOs are deterministic', () => {
  assert.deepEqual(normalizeAssistantProviderDto({ provider: 'OpenAI', model_name: 'gpt-x' }), {
    id: 'openai',
    label: 'OpenAI',
    model: 'gpt-x',
    status: 'configured',
  })
  assert.deepEqual(normalizeAssistantWorkspaceDto({ name: 'report', files_count: '3' }), {
    name: 'report',
    files_count: '3',
    desc: '',
    pinned: false,
    file_count: 3,
  })
  const session = normalizeAssistantSessionDto(
    { id: 7, title: '', updated_at: '2026-07-11T08:00:00Z', provider: 'hermes' },
    123,
  )
  assert.equal(session.id, '7')
  assert.equal(session.title, '会话')
  assert.equal(session.provider_info.id, 'hermes')
  assert.equal(session.at, Date.parse('2026-07-11T08:00:00Z'))
  assert.deepEqual(normalizeAssistantMessageDto({ id: 2, role: 'other', content: null }), {
    id: 2,
    role: 'user',
    content: '',
    extra: {},
  })
  assert.deepEqual(
    normalizeBriefingScheduleDto({ id: 4, topic: 'Risk', enabled: 0, run_count: '2' }),
    {
      id: '4',
      topic: 'Risk',
      enabled: null,
      run_count: 2,
      title: 'Risk',
      cadence: 'daily',
      timezone: 'Asia/Shanghai',
      recent_runs: [],
      last_assurance: {
        schema_version: null,
        status: 'unavailable',
        publication_eligibility: 'blocked_assurance_unavailable',
        source_count: null,
      },
    },
  )
})

test('briefing cadence, status, and form normalization contracts stay stable', () => {
  assert.deepEqual(
    BRIEFING_CADENCE_OPTIONS.map(({ value, label }) => [value, label]),
    [
      ['daily', '每天'],
      ['every_6_hours', '每 6 小时'],
      ['every_12_hours', '每 12 小时'],
      ['weekly', '每周'],
      ['custom_hours', '自定义小时'],
      ['manual', '仅手动'],
    ],
  )
  assert.deepEqual(BRIEFING_STATUS_LABELS, {
    idle: '待运行',
    running: '生成中',
    done: '已生成',
    failed: '失败',
    unknown: '状态未知',
  })
  assert.equal(briefingCadenceLabel('weekly'), '每周')
  assert.equal(briefingCadenceLabel('unknown'), '周期未知')
  assert.equal(briefingStatusLabel('running'), '生成中')
  assert.equal(briefingStatusLabel('unknown'), '状态未知')
  assert.equal(briefingStatusLabel('done'), '已生成 · 可信状态不可用')

  assert.deepEqual(createBriefingScheduleForm(), {
    title: '',
    topic: '',
    prompt: '',
    cadence: 'daily',
    timezone: 'Asia/Shanghai',
    time_of_day: '08:30',
    day_of_week: 0,
    interval_hours: 24,
    enabled: true,
    report_type: 'brief',
    time_range: '24h',
    perspective: '综合研判',
    include_sources: true,
    include_charts: false,
  })
  assert.deepEqual(
    createBriefingScheduleForm({
      id: 3,
      topic: 'Risk',
      day_of_week: '5',
      interval_hours: '12',
      enabled: false,
      include_sources: false,
      include_charts: 1,
    }),
    {
      title: 'Risk',
      topic: 'Risk',
      prompt: '',
      cadence: 'daily',
      timezone: 'Asia/Shanghai',
      time_of_day: '08:30',
      day_of_week: 5,
      interval_hours: 12,
      enabled: false,
      report_type: 'brief',
      time_range: '24h',
      perspective: '综合研判',
      include_sources: false,
      include_charts: true,
    },
  )
})

test('briefing assurance is fail-closed and never presented as fact checked', () => {
  const assurance = {
    schema_version: BRIEFING_ASSURANCE_SCHEMA,
    status: 'review_required',
    publication_eligibility: 'blocked_pending_human_review',
    source_count: 2,
    source_inventory_sha256: 'a'.repeat(64),
    model_output_sha256: 'b'.repeat(64),
    write_time_saved_draft_sha256: 'c'.repeat(64),
    substantive_blocks_total: 3,
    substantive_blocks_cited: 2,
    substantive_blocks_explicit_unknown: 1,
    substantive_blocks_uncited: 0,
    substantive_block_source_citation_rate: '0.666667',
    substantive_block_disposition_rate: '1.000000',
    checks: {
      source_identifier_boundary: 'passed',
      substantive_block_disposition: 'passed',
      source_citation_rate: 'measured_not_targeted',
      source_truth: 'not_verified',
      semantic_entailment: 'not_verified',
      fact_check: 'not_performed',
      human_review: 'required',
      integrity_on_read: 'not_verified',
      report_storage: 'local_mutable_file',
      metadata_storage: 'local_mutable_json',
      append_only_audit_chain: 'unavailable',
    },
  }
  assert.deepEqual(normalizeBriefingAssurance(assurance), {
    schema_version: BRIEFING_ASSURANCE_SCHEMA,
    status: 'review_required',
    publication_eligibility: 'blocked_pending_human_review',
    source_count: 2,
  })
  assert.equal(
    briefingStatusLabel('done', assurance),
    '已生成 · 待人工审阅',
  )
  assert.equal(
    briefingAssuranceLabel({ last_status: 'done', last_assurance: assurance }),
    '仅检查引用标记与来源边界（2 条）；事实、语义和当前文件完整性未核验，待人工审阅',
  )
  const tampered = structuredClone(assurance)
  tampered.checks.fact_check = 'passed'
  assert.equal(normalizeBriefingAssurance(tampered).status, 'unavailable')
  assert.equal(
    briefingAssuranceLabel({ last_status: 'done', last_assurance: tampered }),
    '可信状态不可用；不得视为已核验',
  )
})

test('briefing payload and bounded context preserve the backend contract', () => {
  const favoriteItems = Array.from({ length: 26 }, (_, index) => ({ id: index + 1 }))
  const skills = Array.from({ length: 32 }, (_, index) => `skill-${index + 1}`)
  const databaseCards = Array.from({ length: 22 }, (_, index) => ({ id: index + 1 }))
  const context = buildBriefingScheduleContext({
    pinnedWorkspaceName: 'report',
    favoriteFolderName: '风险观察',
    favoriteItems,
    skills,
    databaseCards,
  })
  assert.equal(context.favorite_context.items.length, 24)
  assert.equal(context.knowledge_context.skills.length, 30)
  assert.equal(context.knowledge_context.database_cards.length, 20)
  assert.equal(favoriteItems.length, 26)
  assert.equal(skills.length, 32)
  assert.equal(databaseCards.length, 22)

  const payload = buildBriefingSchedulePayload(
    {
      title: '   ',
      topic: '  Supply risk  ',
      prompt: '  Focus on evidence  ',
      cadence: 'weekly',
      timezone: '',
      time_of_day: '',
      day_of_week: '0',
      interval_hours: 0,
      enabled: 0,
      report_type: '',
      time_range: '',
      perspective: '',
      include_sources: 0,
      include_charts: 1,
    },
    context,
  )
  assert.deepEqual(payload, {
    title: 'Supply risk',
    topic: 'Supply risk',
    prompt: 'Focus on evidence',
    cadence: 'weekly',
    timezone: 'Asia/Shanghai',
    time_of_day: '08:30',
    day_of_week: 0,
    interval_hours: 24,
    enabled: false,
    report_type: 'brief',
    time_range: '24h',
    perspective: '综合研判',
    include_sources: false,
    include_charts: true,
    pinned_workspace: 'report',
    favorite_context: context.favorite_context,
    knowledge_context: context.knowledge_context,
  })
  assert.deepEqual(buildBriefingScheduleContext(), {
    pinned_workspace: '',
    favorite_context: null,
    knowledge_context: null,
  })
})

test('briefing task stats and seven-day dashboard trend are deterministic', () => {
  const schedules = [
    {
      id: 'one',
      title: 'Daily risk',
      cadence: 'daily',
      last_status: 'running',
      next_run_at: 'next',
      run_count: '2',
      recent_runs: [{}, {}],
      enabled: true,
    },
    {
      id: 'two',
      topic: 'Weekly risk',
      cadence: 'weekly',
      last_status: 'done',
      updated_at: 'updated',
      run_count: 3,
      recent_runs: [],
      enabled: false,
    },
  ]
  assert.deepEqual(calculateBriefingStats(schedules), {
    total: 2,
    enabled: 1,
    generated: 5,
    running: true,
  })
  const tasks = buildBriefingPeriodicTasks(schedules, (value) => `fmt:${value}`)
  assert.deepEqual(
    tasks.map(({ id, title, at, stats, cadence, status }) => ({
      id,
      title,
      at,
      stats,
      cadence,
      status,
    })),
    [
      {
        id: 'one',
        title: 'Daily risk',
        at: 'fmt:next',
        stats: '2 · 2 · 1',
        cadence: '每天',
        status: '生成中',
      },
      {
        id: 'two',
        title: 'Weekly risk',
        at: 'fmt:updated',
        stats: '3 · 0 · 0',
        cadence: '每周',
        status: '已生成 · 可信状态不可用',
      },
    ],
  )

  const localDate = (day) => new Date(2026, 6, day, 12, 0, 0)
  const trend = buildAssistantDashboardTrend({
    now: localDate(11),
    conversations: [{ at: localDate(5) }, { updated_at: localDate(11) }],
    reports: [{ createdAt: localDate(6) }, { time: localDate(11) }],
    schedules: [{ last_run_at: localDate(7) }, { updated_at: localDate(8) }],
  })
  assert.equal(dashboardDateKey(localDate(5)), '2026-07-05')
  assert.deepEqual(
    trend.points.map(({ key, label, value }) => ({ key, label, value })),
    [
      { key: '2026-07-05', label: '7/5', value: 1 },
      { key: '2026-07-06', label: '7/6', value: 1 },
      { key: '2026-07-07', label: '7/7', value: 1 },
      { key: '2026-07-08', label: '7/8', value: 1 },
      { key: '2026-07-09', label: '7/9', value: 0 },
      { key: '2026-07-10', label: '7/10', value: 0 },
      { key: '2026-07-11', label: '7/11', value: 2 },
    ],
  )
  assert.equal(trend.total, 6)
  assert.equal(trend.max, 2)
  assert.equal(calculateDashboardBarHeight(0, 10), '8%')
  assert.equal(calculateDashboardBarHeight(1, 10), '14%')
  assert.equal(calculateDashboardBarHeight(10, 10), '100%')
})

test('briefing schedule service owns use-case success and normalization rules', async () => {
  const calls = []
  const service = createBriefingScheduleService({
    async listSchedules() {
      calls.push(['list'])
      return { ok: true, data: [{ id: 5, topic: 'Risk', run_count: '2' }] }
    },
    async saveSchedule(id, payload) {
      calls.push(['save', id, payload])
      return { ok: true, data: { id: id || 6, ...payload } }
    },
    async runSchedule(id) {
      calls.push(['run', id])
      return { ok: true, data: { schedule: { id }, file: { file_name: 'risk.md' } } }
    },
    async deleteSchedule(id) {
      calls.push(['remove', id])
      return { ok: true }
    },
  })

  const rows = await service.list()
  assert.equal(rows[0].id, '5')
  assert.equal(rows[0].title, 'Risk')
  assert.equal(rows[0].run_count, 2)
  assert.deepEqual(await service.save('5', { title: 'Daily' }), { id: '5', title: 'Daily' })
  assert.deepEqual(await service.run('5'), {
    schedule: { id: '5' },
    file: { file_name: 'risk.md' },
  })
  assert.deepEqual(await service.remove('5'), { ok: true })
  assert.deepEqual(calls, [
    ['list'],
    ['save', '5', { title: 'Daily' }],
    ['run', '5'],
    ['remove', '5'],
  ])

  const failing = createBriefingScheduleService({
    async listSchedules() {
      return { ok: false, detail: 'schedule unavailable' }
    },
  })
  await assert.rejects(failing.list(), /读取定时任务失败/)
})

test('workspace paths, selection, preview, and download names preserve UI rules', () => {
  assert.deepEqual(
    normalizeWorkspaceDtoFromFeature({ name: 'reports', description: 'Saved', files_count: '4' }),
    {
      name: 'reports',
      description: 'Saved',
      files_count: '4',
      desc: 'Saved',
      pinned: false,
      file_count: 4,
    },
  )
  assert.equal(workspaceChildPath('', 'folder'), 'folder')
  assert.equal(workspaceChildPath('one/two', 'three'), 'one/two/three')
  assert.equal(workspaceParentPath('one/two/three'), 'one/two')
  assert.equal(workspaceParentPath('one'), '')
  assert.equal(workspaceParentPath(''), '')
  assert.equal(workspaceFilePath('folder', { name: 'report.md' }), 'folder/report.md')
  assert.equal(workspaceFilePath('', 'report.md'), 'report.md')

  const selected = new Set(['one.md', 'two.md'])
  assert.deepEqual([...toggleWorkspaceFileSelection(selected, 'one.md')], ['two.md'])
  assert.deepEqual([...selected], ['one.md', 'two.md'])
  assert.deepEqual([...toggleWorkspaceFileSelection(selected, 'three.md')], [
    'one.md',
    'two.md',
    'three.md',
  ])
  const files = [{ name: 'one.md' }, { name: 'folder', is_dir: true }]
  assert.deepEqual([...toggleAllWorkspaceFiles(new Set(), files)], ['one.md', 'folder'])
  assert.deepEqual([...toggleAllWorkspaceFiles(new Set(['one.md', 'other']), files)], [])
  assert.deepEqual(selectedWorkspaceFilePaths(selected, 'nested'), [
    'nested/one.md',
    'nested/two.md',
  ])

  assert.ok(TEXT_PREVIEW_EXTENSIONS.includes('.toml'))
  assert.equal(isTextPreviewFile('REPORT.MD'), true)
  assert.equal(isTextPreviewFile('archive.tar.gz'), false)
  assert.equal(isTextPreviewFile('README'), false)
  assert.equal(workspaceDownloadFilename('folder/report.md'), 'report.md')
  assert.equal(workspaceDownloadFilename(''), 'download')
  assert.equal(workspaceArchiveFilename('research space'), 'research space.zip')
  assert.deepEqual(
    workspaceFilePreviewFromResponse({ ok: true, data: { content: '# report' } }),
    { content: '# report', originalContent: '# report' },
  )
  assert.deepEqual(workspaceFilePreviewFromResponse({ ok: false, error: 'denied' }), {
    content: '// 无法读取文件: denied',
    originalContent: '// 无法读取文件: denied',
  })
  assert.deepEqual(workspaceFilePreviewFromError(new Error('offline')), {
    content: '// 读取失败: offline',
    originalContent: '// 读取失败: offline',
  })
})

test('workspace service preserves pin rollback, file payloads, and blob download contracts', async () => {
  const calls = []
  const singleBlob = { kind: 'single' }
  const zipBlob = { kind: 'zip' }
  const service = createAssistantWorkspaceService({
    async listWorkspaces() {
      calls.push(['list'])
      return { ok: true, data: [{ name: 'alpha', files_count: '2' }] }
    },
    async createWorkspace(payload) {
      calls.push(['create', payload])
      return { ok: true, data: payload }
    },
    async updateWorkspace(name, payload) {
      calls.push(['pin', name, payload])
      return { ok: true }
    },
    async deleteWorkspace(name) {
      calls.push(['remove', name])
      return { ok: true }
    },
    async listWorkspaceFiles(name, subpath) {
      calls.push(['files', name, subpath])
      return { ok: true, data: [{ name: 'report.md' }] }
    },
    async readWorkspaceFile(name, filename) {
      calls.push(['read', name, filename])
      return { ok: true, data: { content: 'text' } }
    },
    async updateWorkspaceFile(name, filename, payload) {
      calls.push(['update-file', name, filename, payload])
      return { ok: true }
    },
    async createWorkspaceFile(name, payload) {
      calls.push(['create-file', name, payload])
      return { ok: true }
    },
    async uploadWorkspaceFiles(name, formData) {
      calls.push(['upload', name, formData])
      return { ok: true, data: [{ name: 'source.txt', size: 5 }] }
    },
    async downloadWorkspaceFile(name, filename) {
      calls.push(['download-file', name, filename])
      return { async blob() { return singleBlob } }
    },
    async downloadWorkspaceZip(name, filenames) {
      calls.push(['download-zip', name, filenames])
      return { async blob() { return zipBlob } }
    },
  })

  assert.equal((await service.list())[0].file_count, 2)
  assert.equal((await service.create('beta', 'Beta files')).name, 'beta')
  const workspaces = [
    { name: 'alpha', pinned: true },
    { name: 'beta', pinned: false },
  ]
  await service.togglePinned(workspaces, workspaces[1])
  assert.deepEqual(workspaces.map(({ name, pinned }) => ({ name, pinned })), [
    { name: 'beta', pinned: true },
    { name: 'alpha', pinned: false },
  ])
  await service.remove('alpha')
  assert.deepEqual(await service.listFiles('beta', 'nested/reports'), [{ name: 'report.md' }])
  assert.deepEqual(await service.readFile('beta', 'nested/report.md'), {
    ok: true,
    data: { content: 'text' },
  })
  await service.updateFile('beta', 'nested/report.md', 'updated')
  await service.createFile('beta', 'nested/new.md')
  const uploadFormData = await service.uploadFiles('beta', [new Blob(['hello'])])
  assert.deepEqual(uploadFormData, [{ name: 'source.txt', size: 5 }])
  assert.deepEqual(await service.downloadSelection('beta', ['nested/report.md']), {
    blob: singleBlob,
    filename: 'report.md',
  })
  assert.deepEqual(
    await service.downloadSelection('beta', ['nested/report.md', 'nested/new.md']),
    { blob: zipBlob, filename: 'beta.zip' },
  )
  assert.deepEqual(
    calls.find(([kind]) => kind === 'update-file'),
    [
      'update-file',
      'beta',
      'nested/report.md',
      { filename: 'nested/report.md', content: 'updated' },
    ],
  )
  assert.deepEqual(calls.find(([kind]) => kind === 'create-file'), [
    'create-file',
    'beta',
    { filename: 'nested/new.md', content: '' },
  ])
  assert.equal(calls.find(([kind]) => kind === 'upload')?.[1], 'beta')
  assert.equal(calls.find(([kind]) => kind === 'upload')?.[2] instanceof FormData, true)
  assert.deepEqual(calls.find(([kind]) => kind === 'download-zip'), [
    'download-zip',
    'beta',
    ['nested/report.md', 'nested/new.md'],
  ])

  const original = [
    { name: 'alpha', pinned: true },
    { name: 'beta', pinned: false },
  ]
  const failing = createAssistantWorkspaceService({
    async updateWorkspace() {
      return { ok: false, error: 'pin rejected' }
    },
  })
  await assert.rejects(failing.togglePinned(original, original[1]), /pin rejected/)
  assert.deepEqual(original.map(({ name, pinned }) => ({ name, pinned })), [
    { name: 'alpha', pinned: true },
    { name: 'beta', pinned: false },
  ])
})

test('workspace controller keeps refs, dialogs, and service orchestration compatible', async () => {
  const ref = (value) => ({ value })
  const state = {
    workspaces: ref([]),
    workspaceLoading: ref(false),
    workspaceError: ref(''),
    currentWorkspace: ref(null),
    currentFilePath: ref(''),
    workspaceFiles: ref([]),
    selectedFileNames: ref(new Set()),
    filesLoading: ref(false),
    createDialogVisible: ref(true),
    newWorkspaceName: ref(' beta '),
    newWorkspaceDescription: ref(' Beta files '),
    creatingWorkspace: ref(false),
    createFileDialogVisible: ref(true),
    newFileName: ref(' note.md '),
    creatingFile: ref(false),
    uploadInputEl: ref({ clicked: false, click() { this.clicked = true } }),
    uploadingFiles: ref(false),
    filePreviewVisible: ref(false),
    filePreviewTitle: ref(''),
    filePreviewFullPath: ref(''),
    filePreviewContent: ref(''),
    fileOriginalContent: ref(''),
    filePreviewLoading: ref(false),
    fileSaving: ref(false),
  }
  const calls = []
  const downloads = []
  const service = {
    async list() {
      calls.push(['list'])
      return [{ name: 'alpha', pinned: false }]
    },
    async create(name, desc) {
      calls.push(['create', name, desc])
      return { name, desc, pinned: false }
    },
    async togglePinned(workspaces, workspace) {
      calls.push(['pin', workspace.name])
      workspaces.forEach((item) => { item.pinned = false })
      workspace.pinned = true
    },
    async remove(name) {
      calls.push(['remove', name])
    },
    async listFiles(name, subpath) {
      calls.push(['files', name, subpath])
      return [{ name: 'report.md', is_dir: false }, { name: 'nested', is_dir: true }]
    },
    async readFile(name, filename) {
      calls.push(['read', name, filename])
      return { ok: true, data: { content: '# report' } }
    },
    async updateFile(name, filename, content) {
      calls.push(['update', name, filename, content])
    },
    async createFile(name, filename) {
      calls.push(['create-file', name, filename])
    },
    async uploadFiles(name, files) {
      calls.push(['upload-files', name, files.map((file) => file.name)])
      return [{ name: 'source.txt' }]
    },
    async downloadFile(name, filename) {
      calls.push(['download-file', name, filename])
      return { blob: { name: filename }, filename: 'report.md' }
    },
    async downloadSelection(name, filenames) {
      calls.push(['download-selection', name, filenames])
      return { blob: { names: filenames }, filename: `${name}.zip` }
    },
  }
  const controller = createAssistantWorkspaceController({
    state,
    service,
    hasToken: () => true,
    showError(message) {
      assert.fail(`unexpected workspace error: ${message}`)
    },
    errorText: (error, fallback) => error?.message || fallback,
    confirmDeleteWorkspace: async () => true,
    confirmCloseDirtyPreview: async () => true,
    focusCreateFileInput() {
      calls.push(['focus'])
    },
    triggerDownload(blob, filename) {
      downloads.push({ blob, filename })
    },
  })

  await controller.fetchWorkspaces()
  assert.equal(state.workspaces.value[0].name, 'alpha')
  await controller.createWorkspace()
  assert.deepEqual(calls.find(([kind]) => kind === 'create'), [
    'create',
    'beta',
    'Beta files',
  ])
  const beta = state.workspaces.value[0]
  await controller.togglePinWorkspace(beta)
  assert.equal(beta.pinned, true)
  await controller.enterWorkspace(beta)
  assert.equal(state.currentFilePath.value, '')
  assert.equal(state.workspaceFiles.value.length, 2)
  controller.enterWorkspaceFolder('nested')
  await Promise.resolve()
  assert.equal(state.currentFilePath.value, 'nested')
  assert.equal(controller.fileFullPath({ name: 'report.md' }), 'nested/report.md')
  controller.toggleFileSelection('report.md')
  assert.deepEqual([...state.selectedFileNames.value], ['report.md'])
  await controller.openFilePreview({ name: 'report.md', is_dir: false })
  assert.equal(state.filePreviewContent.value, '# report')
  state.filePreviewContent.value = '# updated'
  await controller.saveFileContent()
  assert.deepEqual(calls.find(([kind]) => kind === 'update'), [
    'update',
    'beta',
    'nested/report.md',
    '# updated',
  ])
  await controller.downloadSelected()
  assert.equal(downloads[0].filename, 'beta.zip')
  await controller.closeFilePreview()
  assert.equal(state.filePreviewVisible.value, false)
  controller.openCreateFileDialog()
  assert.equal(state.newFileName.value, '')
  assert.deepEqual(calls.at(-1), ['focus'])
  state.currentFilePath.value = ''
  controller.openUploadDialog()
  assert.equal(state.uploadInputEl.value.clicked, true)
  const uploadTarget = { files: [{ name: 'source.txt' }], value: 'selected' }
  await controller.uploadWorkspaceFiles({ target: uploadTarget })
  assert.deepEqual(calls.find(([kind]) => kind === 'upload-files'), [
    'upload-files',
    'beta',
    ['source.txt'],
  ])
  assert.equal(uploadTarget.value, '')
  assert.equal(state.uploadingFiles.value, false)
  await controller.deleteWorkspace(beta)
  assert.equal(state.workspaces.value.some((workspace) => workspace.name === 'beta'), false)
})

test('browser download adapter preserves object URL lifecycle and fallback filename', () => {
  const events = []
  const anchor = {
    href: '',
    download: '',
    click() {
      events.push('click')
    },
    remove() {
      events.push('remove')
    },
  }
  const urlApi = {
    createObjectURL(blob) {
      events.push(['create', blob])
      return 'blob:workspace'
    },
    revokeObjectURL(url) {
      events.push(['revoke', url])
    },
  }
  const documentRef = {
    createElement(tag) {
      events.push(['element', tag])
      return anchor
    },
    body: {
      appendChild(value) {
        assert.equal(value, anchor)
        events.push('append')
      },
    },
  }
  const blob = { size: 3 }
  triggerBrowserDownload(blob, '', { urlApi, documentRef })
  assert.equal(anchor.href, 'blob:workspace')
  assert.equal(anchor.download, 'download')
  assert.deepEqual(events, [
    ['create', blob],
    ['element', 'a'],
    'append',
    'click',
    'remove',
    ['revoke', 'blob:workspace'],
  ])
})

test('chat stream reducer keeps context, tools, sources, done, and error deterministic', () => {
  const initial = createChatStreamState({
    message: createChatStreamMessage({ sources: [{ key: 'existing' }] }),
    newsHits: [{ id: 'old-news' }],
    clusterHits: [{ id: 'old-cluster' }],
  })
  const initialSnapshot = JSON.stringify(initial)
  const adapters = {
    normalizeWebSourceGroups: (groups) => groups.map((group) => ({
      key: `web-${group.query}`,
      items: group.results,
    })),
    sourceGroupsFromToolResult: (tool, result) => [{
      key: `${tool}-result`,
      items: result.news || [],
    }],
    mergeSourceGroups: (existing, incoming) => [...existing, ...incoming],
  }

  let reduction = reduceChatStreamEvent(initial, {
    step: 'context',
    session_id: 91,
    news_hits: [{ id: 'news-1' }],
    cluster_hits: [{ id: 'cluster-1' }],
    web_sources: [{ query: 'risk', results: [{ id: 'web-1' }] }],
  }, adapters)
  let state = reduction.state
  assert.deepEqual(reduction.effects, {
    contextUpdated: true,
    pageActionPhase: '',
    sessionId: 91,
    shouldScroll: false,
  })
  assert.deepEqual(state.newsHits, [{ id: 'news-1' }])
  assert.deepEqual(state.clusterHits, [{ id: 'cluster-1' }])
  assert.deepEqual(state.message.sources.map((group) => group.key), ['existing', 'web-risk'])

  state = reduceChatStreamEvent(state, {
    step: 'text_delta',
    text: '跨块报告',
  }, adapters).state
  state = reduceChatStreamEvent(state, {
    step: 'thinking_delta',
    text: '核验来源',
  }, adapters).state
  reduction = reduceChatStreamEvent(state, {
    step: 'tool_executing',
    tool: 'news_search',
    input: { query: 'risk' },
    invoke: { query_preview: 'risk' },
  }, adapters)
  state = reduction.state
  assert.equal(reduction.effects.pageActionPhase, 'executing')
  assert.equal(reduction.effects.shouldScroll, true)
  assert.equal(state.message.toolCalls[0].type, 'tool_executing')

  reduction = reduceChatStreamEvent(state, {
    step: 'tool_finished',
    tool: 'news_search',
    result: { news: [{ id: 'news-2' }] },
  }, adapters)
  state = reduction.state
  assert.equal(reduction.effects.pageActionPhase, 'finished')
  assert.equal(state.message.toolCalls.length, 1)
  assert.equal(state.message.toolCalls[0].type, 'tool_finished')
  assert.deepEqual(state.message.toolCalls[0].result, { news: [{ id: 'news-2' }] })
  assert.deepEqual(state.message.sources.map((group) => group.key), [
    'existing',
    'web-risk',
    'news_search-result',
  ])

  state = reduceChatStreamEvent(state, {
    step: 'done',
    reply: '最终答复',
    finish_reason: 'length',
    truncated: true,
  }, adapters).state
  assert.equal(state.fullReply, '最终答复')
  assert.equal(state.message.storedFullReply, '最终答复')
  assert.equal(state.message.text, '最终答复')
  assert.equal(state.message.finishReason, 'length')
  assert.equal(state.message.truncated, true)
  state = reduceChatStreamEvent(state, {
    step: 'error',
    detail: { code: 'source_failed' },
  }, adapters).state
  assert.equal(state.error, '{"code":"source_failed"}')
  const finalized = finalizeChatStreamState(state)
  assert.equal(finalized.message.text, '最终答复\n\n【错误】{"code":"source_failed"}')
  assert.equal(finalized.message.storedFullReply, finalized.message.text)
  assert.equal(chatStreamEventError({ msg: 'fallback error' }), 'fallback error')
  assert.equal(JSON.stringify(initial), initialSnapshot)
})

test('chat stream transport preserves headers, UTF-8 tail frames, and event order', async () => {
  const encoder = new TextEncoder()
  const chinese = encoder.encode('报告')
  const chunks = [
    concatBytes(
      encoder.encode([
        'data: {"step":"context","session_id":91,"news_hits":[],"cluster_hits":[]}\n\n',
        'data: {"step":"thinking_delta","text":"核验"}\n\n',
        'data: {"step":"tool_executing","tool":"news_search"}\n\n',
        'data: {"step":"tool_finished","tool":"news_search","result":{}}\n\n',
        'data: {"step":"text_delta","text":"跨块',
      ].join('')),
      chinese.slice(0, 2),
    ),
    concatBytes(
      chinese.slice(2),
      encoder.encode([
        '"}\n\n',
        'data: {"step":"error","detail":{"code":"partial"}}\n\n',
        'data: {"step":"done","reply":"最终答复"}',
      ].join('')),
    ),
  ]
  let cancelled = 0
  let decoderFlushes = 0
  let requestOptions
  class TrackingTextDecoder {
    constructor() {
      this.decoder = new TextDecoder()
    }
    decode(value, options) {
      if (value === undefined) decoderFlushes += 1
      return this.decoder.decode(value, options)
    }
  }
  const response = {
    headers: {
      get(name) {
        return name === 'X-Assistant-Session-Id' ? '91' : null
      },
    },
    body: {
      getReader() {
        let index = 0
        return {
          async read() {
            if (index >= chunks.length) return { done: true }
            return { done: false, value: chunks[index++] }
          },
          async cancel() {
            cancelled += 1
          },
        }
      },
    },
  }
  const controller = createChatStreamController({
    async openAssistantStream(body, options) {
      assert.deepEqual(body, { message: 'question' })
      requestOptions = options
      return response
    },
  }, {
    TextDecoderImpl: TrackingTextDecoder,
  })
  const events = []
  let headerSessionId = ''
  const result = await controller.run({
    requestBody: { message: 'question' },
    onResponse(value) {
      headerSessionId = value.headers.get('X-Assistant-Session-Id')
    },
    onEvent(event) {
      events.push(event)
    },
  })
  assert.equal(headerSessionId, '91')
  assert.equal(Boolean(requestOptions.signal), true)
  assert.equal(result.eventCount, 7)
  assert.deepEqual(events.map((event) => event.step), [
    'context',
    'thinking_delta',
    'tool_executing',
    'tool_finished',
    'text_delta',
    'error',
    'done',
  ])
  assert.equal(events[4].text, '跨块报告')
  assert.equal(events[6].reply, '最终答复')
  assert.equal(decoderFlushes, 1)
  assert.equal(cancelled, 1)
  assert.equal(controller.isRunning, false)
})

test('chat stream transport is take-latest and supports explicit abort cleanup', async () => {
  const encoder = new TextEncoder()
  let callCount = 0
  const signals = []
  const oldEvents = []
  const latestEvents = []
  const controller = createChatStreamController({
    async openAssistantStream(_body, { signal }) {
      callCount += 1
      signals.push(signal)
      if (callCount === 1) {
        return {
          headers: { get: () => null },
          body: {
            getReader: () => ({
              read() {
                return new Promise((resolve) => {
                  signal.addEventListener('abort', () => resolve({
                    done: false,
                    value: encoder.encode('data: {"step":"text_delta","text":"stale"}\n\n'),
                  }), { once: true })
                })
              },
              async cancel() {},
            }),
          },
        }
      }
      let sent = false
      return {
        headers: { get: () => null },
        body: {
          getReader: () => ({
            async read() {
              if (sent) return { done: true }
              sent = true
              return {
                done: false,
                value: encoder.encode('data: {"step":"done","reply":"latest"}\n\n'),
              }
            },
            async cancel() {},
          }),
        },
      }
    },
  })
  const first = controller.run({
    requestBody: { id: 1 },
    onEvent: (event) => oldEvents.push(event),
  })
  const firstRejected = assert.rejects(
    first,
    (error) => isChatStreamAbortError(error),
  )
  await Promise.resolve()
  const second = controller.run({
    requestBody: { id: 2 },
    onEvent: (event) => latestEvents.push(event),
  })
  await firstRejected
  assert.deepEqual(await second, { eventCount: 1 })
  assert.equal(signals[0].aborted, true)
  assert.deepEqual(oldEvents, [])
  assert.equal(latestEvents[0].reply, 'latest')

  let pendingSignal
  const pendingController = createChatStreamController({
    async openAssistantStream(_body, { signal }) {
      pendingSignal = signal
      return {
        headers: { get: () => null },
        body: {
          getReader: () => ({
            read() {
              return new Promise((resolve) => {
                signal.addEventListener('abort', () => resolve({ done: true }), { once: true })
              })
            },
            async cancel() {},
          }),
        },
      }
    },
  })
  const pending = pendingController.run({ requestBody: {} })
  const pendingRejected = assert.rejects(
    pending,
    (error) => error instanceof ChatStreamError && error.code === 'aborted',
  )
  await Promise.resolve()
  assert.equal(pendingController.abort(), true)
  await pendingRejected
  assert.equal(pendingSignal.aborted, true)
  pendingController.dispose()
  await assert.rejects(
    pendingController.run({ requestBody: {} }),
    (error) => error instanceof ChatStreamError && error.code === 'disposed',
  )
})

test('report model preserves config, history, prompt, and filename contracts', () => {
  const config = createReportConfig({
    type: 'risk',
    inputMode: 'conversation',
    topic: '',
    customPrompt: '  verify primary sources  ',
    format: 'docx',
  })
  assert.equal(config.type, 'risk')
  assert.equal(config.includeCharts, true)
  assert.equal(
    deriveReportTopic({
      config,
      conversations: [{ id: 7, title: 'Supply chain risk' }],
      currentConversationId: '7',
      messages: [{ role: 'user', text: 'fallback question' }],
    }),
    'verify primary sources',
  )
  config.customPrompt = ''
  assert.equal(
    deriveReportTopic({
      config,
      conversations: [{ id: 7, title: 'Supply chain risk' }],
      currentConversationId: '7',
      messages: [],
    }),
    'Supply chain risk',
  )

  const conversationContext = buildConversationReportContext({
    activeConversationTitle: 'Supply chain risk',
    messages: [
      { role: 'user', text: 'What changed?' },
      {
        role: 'assistant',
        text: 'Costs increased.',
        toolCalls: [{ name: 'news_search' }],
        sources: [{ title: 'News' }],
      },
    ],
    toolName: (name) => (name === 'news_search' ? '新闻检索' : name),
  })
  assert.match(conversationContext, /当前会话：Supply chain risk/)
  assert.match(conversationContext, /工具：新闻检索/)
  assert.match(conversationContext, /来源组：News/)

  const prompt = buildReportPrompt({
    config,
    topic: 'Supply chain risk',
    skills: ['source-check'],
    databases: ['events'],
    pinnedFavoriteFolder: '风险观察',
    favoriteItems: [{ title: 'Factory outage', source: 'official' }],
    reportPanelTopic: 'Global supply',
    conversationContext,
  })
  assert.match(prompt, /报告类型：风险评估报告/)
  assert.match(prompt, /已启用 Skill：source-check/)
  assert.match(prompt, /会话材料摘要：/)
  assert.match(prompt, /固定收藏材料：/)

  assert.equal(sanitizeReportFilename(' risk / watch?# '), 'risk-watch')
  assert.equal(
    buildReportFilename('risk / watch', '2026-07-11T08:09:10.123Z', '.DOCX'),
    '2026-07-11T08-09-10-risk-watch.docx',
  )
  assert.deepEqual(calculateReportEditorStats('one\ntwo'), { chars: 7, lines: 2 })

  const storage = memoryStorage()
  saveReportHistory([
    { topic: 'Risk', content: '# Risk', fileName: 'risk.md', createdAt: '2026-07-11' },
    null,
  ], storage)
  const stored = JSON.parse(storage.getItem(REPORT_STORAGE_KEY))
  assert.equal(stored.length, 1)
  assert.equal(stored[0].workspace, REPORT_WORKSPACE_NAME)
  assert.equal(stored[0].filePath, 'report/risk.md')
  assert.equal(loadReportHistory(storage)[0].status, 'done')
  assert.deepEqual(loadReportHistory(memoryStorage({ [REPORT_STORAGE_KEY]: '{bad' })), [])
  assert.equal(
    normalizeReportRecord({ title: '', createdAt: '2026-07-11' }).topic,
    '未命名主题',
  )

  const pptRequest = buildPptMasterRequestMarkdown({
    topic: 'Risk',
    createdAt: '2026-07-11',
    wordFilename: 'risk.docx',
    content: '# Risk',
    formatDateTime: (value) => `fmt:${value}`,
  })
  assert.match(pptRequest, /Word 源稿：report\/risk\.docx/)
  assert.match(pptRequest, /创建时间：fmt:2026-07-11/)
  assert.match(pptRequest, /# Risk/)
})

test('report archive service keeps workspace races and export filenames deterministic', async () => {
  const calls = []
  let workspaces = []
  let exportCount = 0
  const api = {
    async listWorkspaces() {
      calls.push(['list'])
      return { ok: true, data: [] }
    },
    async createWorkspace(payload) {
      calls.push(['create-workspace', payload])
      return { ok: true, data: { ...payload, files_count: 0 } }
    },
    async exportWorkspaceDocx(name, payload) {
      exportCount += 1
      calls.push(['docx', name, payload])
      if (exportCount === 1) return { ok: false, error: '文件已存在' }
      return { ok: true }
    },
    async createWorkspaceFile(name, payload) {
      calls.push(['file', name, payload])
      return { ok: true }
    },
  }
  const service = createAssistantReportArchiveService(api, {
    hasToken: () => true,
    getWorkspaces: () => workspaces,
    setWorkspaces: (rows) => { workspaces = rows },
    formatDateTime: (value) => `fmt:${value}`,
    now: () => 42,
  })
  const createdAt = '2026-07-11T08:09:10.123Z'
  const collisionSafeName = await service.saveReport('# Risk', 'Risk', createdAt, 'docx')
  assert.equal(collisionSafeName, '2026-07-11T08-09-10-Risk-42.docx')
  assert.equal(workspaces[0].name, REPORT_WORKSPACE_NAME)
  assert.deepEqual(calls.find(([kind]) => kind === 'create-workspace'), [
    'create-workspace',
    { name: 'report', desc: '智能体报告固定存放目录' },
  ])
  assert.equal(calls.filter(([kind]) => kind === 'docx').length, 2)

  const pack = await service.savePptMasterPackage('# Risk', 'Risk', createdAt)
  assert.equal(pack.wordFilename, '2026-07-11T08-09-10-Risk.docx')
  assert.equal(pack.pptRequestFileName, '2026-07-11T08-09-10-Risk-ppt-master任务.md')
  const taskPayload = calls.find(([kind]) => kind === 'file')[2]
  assert.match(taskPayload.content, /Word 源稿：report\/2026-07-11T08-09-10-Risk\.docx/)
  assert.match(taskPayload.content, /创建时间：fmt:2026-07-11T08:09:10\.123Z/)

  let listCount = 0
  const racing = createAssistantReportArchiveService({
    async listWorkspaces() {
      listCount += 1
      return listCount === 1
        ? { ok: true, data: [] }
        : { ok: true, data: [{ name: 'report' }] }
    },
    async createWorkspace() {
      const error = new Error('同名工作区')
      error.payload = { error: '同名工作区' }
      throw error
    },
  })
  assert.equal((await racing.ensureWorkspace()).name, 'report')
  assert.equal(listCount, 2)
})

test('report stream controller parses tail frames and maps stream errors', async () => {
  const encoder = new TextEncoder()
  const chinese = encoder.encode('报告')
  const chunks = [
    concatBytes(
      encoder.encode('data: {"step":"context"}\n\ndata: {"step":"text_delta","text":"first"}\n\ndata: {"step":"text_delta","text":" tail '),
      chinese.slice(0, 2),
    ),
    concatBytes(
      chinese.slice(2),
      encoder.encode('"}\n\ndata: {"step":"done","reply":"final report"}'),
    ),
  ]
  let cancelled = 0
  let decoderFlushes = 0
  let options
  class TrackingTextDecoder {
    constructor() {
      this.decoder = new TextDecoder()
    }
    decode(value, nextOptions) {
      if (value === undefined) decoderFlushes += 1
      return this.decoder.decode(value, nextOptions)
    }
  }
  const controller = createReportStreamController({
    async openAssistantStream(body, nextOptions) {
      assert.deepEqual(body, { message: 'report' })
      options = nextOptions
      let index = 0
      return {
        body: {
          getReader() {
            return {
              async read() {
                if (index >= chunks.length) return { done: true }
                return { done: false, value: chunks[index++] }
              },
              async cancel() {
                cancelled += 1
              },
            }
          },
        },
      }
    },
  }, {
    TextDecoderImpl: TrackingTextDecoder,
  })
  const events = []
  const result = await controller.run({
    requestBody: { message: 'report' },
    onEvent: (event) => events.push(event),
  })
  assert.equal(result, 'final report')
  assert.deepEqual(events.map((event) => event.step), ['context', 'text_delta', 'text_delta', 'done'])
  assert.equal(events[2].text, ' tail 报告')
  assert.equal(options.fallbackMessage, '报告生成失败')
  assert.equal(cancelled, 1)
  assert.equal(decoderFlushes, 1)
  assert.equal(controller.isRunning, false)
  assert.equal(reportStreamEventError({ detail: { code: 'bad' } }), '{"code":"bad"}')

  const failing = createReportStreamController({
    async openAssistantStream() {
      const payload = encoder.encode('data: {"step":"error","detail":"source failed"}\n\n')
      let sent = false
      return {
        body: {
          getReader: () => ({
            async read() {
              if (sent) return { done: true }
              sent = true
              return { done: false, value: payload }
            },
            async cancel() {},
          }),
        },
      }
    },
  })
  await assert.rejects(
    failing.run({ requestBody: {} }),
    (error) => error instanceof ReportStreamError && error.message === 'source failed',
  )
})

test('report stream controller is take-latest and aborts pending work on dispose', async () => {
  const encoder = new TextEncoder()
  let callCount = 0
  const signals = []
  const controller = createReportStreamController({
    async openAssistantStream(_body, { signal }) {
      callCount += 1
      signals.push(signal)
      if (callCount === 1) {
        return {
          body: {
            getReader: () => ({
              read() {
                if (signal.aborted) return Promise.resolve({ done: true })
                return new Promise((resolve) => {
                  signal.addEventListener('abort', () => resolve({ done: true }), { once: true })
                })
              },
              async cancel() {},
            }),
          },
        }
      }
      let sent = false
      return {
        body: {
          getReader: () => ({
            async read() {
              if (sent) return { done: true }
              sent = true
              return {
                done: false,
                value: encoder.encode('data: {"step":"done","reply":"latest"}\n\n'),
              }
            },
            async cancel() {},
          }),
        },
      }
    },
  })
  const first = controller.run({ requestBody: { id: 1 } })
  const firstRejected = assert.rejects(
    first,
    (error) => error instanceof ReportStreamError && error.code === 'aborted',
  )
  await Promise.resolve()
  const second = controller.run({ requestBody: { id: 2 } })
  await firstRejected
  assert.equal(await second, 'latest')
  assert.equal(signals[0].aborted, true)

  let pendingSignal
  const pendingController = createReportStreamController({
    async openAssistantStream(_body, { signal }) {
      pendingSignal = signal
      return {
        body: {
          getReader: () => ({
            read() {
              return new Promise((resolve) => {
                signal.addEventListener('abort', () => resolve({ done: true }), { once: true })
              })
            },
            async cancel() {},
          }),
        },
      }
    },
  })
  const pending = pendingController.run({ requestBody: {} })
  const pendingRejected = assert.rejects(
    pending,
    (error) => error instanceof ReportStreamError && error.code === 'aborted',
  )
  await Promise.resolve()
  pendingController.dispose()
  await pendingRejected
  assert.equal(pendingSignal.aborted, true)
  await assert.rejects(
    pendingController.run({ requestBody: {} }),
    (error) => error instanceof ReportStreamError && error.code === 'disposed',
  )
})

test('SSE parsing, abort scope, errors, and formatting stay pure', () => {
  const parsed = parseSseDataBlocks(
    'event: message\ndata: {"step":"text_delta",\ndata: "text":"ok"}\n\npartial',
  )
  assert.deepEqual(parsed.events, [{ step: 'text_delta', text: 'ok' }])
  assert.equal(parsed.rest, 'partial')

  let timeoutCallback
  let cleared = false
  class FakeAbortController {
    constructor() {
      this.signal = { aborted: false }
    }
    abort() {
      this.signal.aborted = true
    }
  }
  const scope = createRequestAbortScope(100, {
    AbortControllerImpl: FakeAbortController,
    setTimeoutImpl(callback) {
      timeoutCallback = callback
      return 7
    },
    clearTimeoutImpl(id) {
      assert.equal(id, 7)
      cleared = true
    },
  })
  timeoutCallback()
  assert.equal(scope.signal.aborted, true)
  assert.equal(isAssistantAbortError(null, scope.signal), true)
  scope.dispose()
  assert.equal(cleared, true)

  assert.deepEqual(safeParseJson('{"ok":true}', {}), { ok: true })
  assert.equal(displayFolderName('新闻分析主题'), '默认工作夹')
  assert.equal(formatConversationTime(1000, 31000), '刚刚')
  assert.equal(
    pickSearchQueryFromPayload({ result: { params: { search_query: ['global', 'risk'] } } }),
    'global risk',
  )
})

async function walkFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    const path = `${directory}/${entry.name}`
    if (entry.isDirectory()) files.push(...(await walkFiles(path)))
    else files.push(path)
  }
  return files
}

test('drawer and route compatibility contracts are preserved through public API', async () => {
  const projectRoot = new URL('../', import.meta.url)
  const drawer = await readFile(new URL('../src/features/assistant/AssistantDrawer.vue', import.meta.url), 'utf8')
  const experience = await readFile(
    new URL('../src/features/assistant/AssistantExperience.vue', import.meta.url),
    'utf8',
  )
  const publicEntry = await readFile(
    new URL('../src/features/assistant/index.js', import.meta.url),
    'utf8',
  )
  const routeView = await readFile(new URL('../src/views/DataAssistant.vue', import.meta.url), 'utf8')

  for (const prop of ['modelValue', 'drawerKey', 'title', 'subtitle', 'pageSkill']) {
    assert.match(drawer, new RegExp(`\\b${prop}:`))
  }
  assert.match(drawer, /defineEmits\(\['update:modelValue', 'page-action', 'layout-change'\]\)/)
  assert.match(drawer, /import DataAssistant from ['"]\.\/AssistantExperience\.vue['"]/)
  assert.match(drawer, /<DataAssistant[\s\S]*embedded[\s\S]*@page-action="forwardPageAction"/)
  assert.match(routeView, /import\(['"]@\/features\/assistant\/index\.js['"]\)/)
  assert.match(routeView, /<AssistantExperience/)
  assert.match(routeView, /<Suspense>/)
  assert.match(routeView, /assistant-loading-shell/)
  assert.match(experience, /from ['"]\.\/briefing\/model\.js['"]/)
  assert.match(experience, /from ['"]\.\/briefing\/service\.js['"]/)
  assert.doesNotMatch(experience, /const briefingCadenceOptions = \[/)
  assert.match(experience, /from ['"]\.\/workspace\/controller\.js['"]/)
  assert.match(experience, /from ['"]\.\/workspace\/service\.js['"]/)
  assert.doesNotMatch(experience, /const TEXT_PREVIEW_EXTS = \[/)
  assert.match(experience, /from ['"]\.\/chat\/reducer\.js['"]/)
  assert.match(experience, /from ['"]\.\/chat\/stream\.js['"]/)
  assert.doesNotMatch(experience, /new TextDecoder\(/)
  assert.doesNotMatch(experience, /parseSseDataBlocks\(/)
  assert.match(experience, /const turnMessages = messages\.value/)
  assert.match(experience, /cacheMessages\(cacheSessionId, turnMessages\)/)
  assert.doesNotMatch(experience, /cacheMessages\(sid, messages\.value\)/)
  assert.match(experience, /from ['"]\.\/reports\/model\.js['"]/)
  assert.match(experience, /from ['"]\.\/reports\/service\.js['"]/)
  assert.match(experience, /from ['"]\.\/reports\/stream\.js['"]/)
  assert.doesNotMatch(experience, /const reportTypes = \[/)
  assert.doesNotMatch(experience, /async function ensureReportWorkspace/)
  assert.match(publicEntry, /from ['"]\.\/briefing\/model\.js['"]/)
  assert.match(publicEntry, /createBriefingScheduleService/)
  assert.match(publicEntry, /from ['"]\.\/workspace\/model\.js['"]/)
  assert.match(publicEntry, /createAssistantWorkspaceService/)
  assert.match(publicEntry, /from ['"]\.\/chat\/reducer\.js['"]/)
  assert.match(publicEntry, /createChatStreamController/)
  assert.match(publicEntry, /from ['"]\.\/reports\/model\.js['"]/)
  assert.match(publicEntry, /createAssistantReportArchiveService/)
  assert.match(publicEntry, /createReportStreamController/)

  const oldDrawer = new URL('../src/components/AssistantDrawer.vue', import.meta.url)
  await assert.rejects(readFile(oldDrawer, 'utf8'), (error) => error?.code === 'ENOENT')

  const sourceFiles = (await walkFiles(new URL('../src', import.meta.url).pathname)).filter((path) =>
    /\.(vue|js)$/.test(path),
  )
  let publicImports = 0
  for (const path of sourceFiles) {
    const source = await readFile(path, 'utf8')
    for (const match of source.matchAll(/(?:from\s+|import\()['"]@\/features\/assistant\/([^'"]+)['"]/g)) {
      publicImports += 1
      assert.equal(match[1], 'index.js', `deep assistant import in ${path}`)
    }
    assert.doesNotMatch(source, /@\/components\/AssistantDrawer\.vue/, path)
  }
  assert.equal(publicImports, 8)
  assert.ok(projectRoot)
})

test('assistant mobile shell cannot widen the viewport', async () => {
  const styles = await readFile(
    new URL('../src/features/assistant/components/style.css', import.meta.url),
    'utf8',
  )
  const mobileStyles = styles.slice(styles.lastIndexOf('@media (max-width: 720px)'))

  assert.match(mobileStyles, /\.yisight-shell\s*\{[^}]*max-width:\s*100vw;/s)
  for (const selector of ['ys-body', 'ys-content', 'ys-main']) {
    assert.match(
      mobileStyles,
      new RegExp(`\\.${selector}\\s*\\{[^}]*max-width:\\s*100%;[^}]*min-width:\\s*0;`, 's'),
    )
  }
  assert.match(
    mobileStyles,
    /\.ys-chat-drawer:not\(\.ys-chat-drawer--open\)\s*\{[^}]*display:\s*none;/s,
  )
  for (const selector of ['ys-chat-topbar', 'ys-chat-scroll', 'ys-chat-composer-wrap']) {
    assert.match(
      mobileStyles,
      new RegExp(`\\.${selector}\\s*\\{[^}]*min-width:\\s*0;[^}]*max-width:\\s*100%;`, 's'),
    )
  }
  assert.match(
    mobileStyles,
    /\.yisight-shell \.ys-chat-stage \.ys-chat-welcome-title\s*\{[^}]*overflow-wrap:\s*anywhere;[^}]*word-break:\s*normal;[^}]*text-wrap:\s*wrap;/s,
  )
})
