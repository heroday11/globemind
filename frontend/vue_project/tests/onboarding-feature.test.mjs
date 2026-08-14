import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { buildHomeCapabilityCards } from '../src/features/operations/home-capabilities.js'
import { normalizeFeatureFreshness } from '../src/features/operations/freshness.js'

async function source(path) {
  return readFile(new URL(path, import.meta.url), 'utf8')
}

test('guided tour owns cross-route steps and real spotlight targets', async () => {
  const guide = await source('../src/components/NewUserGuide.vue')
  const appNav = await source('../src/components/appNav.vue')
  const home = await source('../src/views/appHome.vue')
  const homeCapabilities = await source('../src/features/operations/home-capabilities.js')
  const search = await source('../src/views/DataService/data-search.vue')
  const story = await source('../src/views/StoryGraphView.vue')
  const sentiment = await source('../src/views/sentimentAnalysis.vue')
  const groundHome = await source('../src/views/GroundNewsHome.vue')
  const groundDesk = await source('../src/views/GroundNewsDesk.vue')
  const knowledge = await source('../src/views/AcademicData.vue')
  const financial = await source('../src/views/FinancialTerminal.vue')
  const assistant = await source('../src/features/assistant/AssistantExperience.vue')
  const report = await source('../src/views/DataService/report-center.vue')

  assert.match(guide, /globemind_new_user_guide_v3/)
  assert.match(guide, /await router\.push\(nextRoute\)/)
  assert.match(guide, /scrollIntoView/)
  assert.match(guide, /transitionState/)
  assert.match(guide, /tour-route-status/)
  assert.match(guide, /教程目录/)
  assert.match(guide, /120; attempt/)
  assert.match(guide, /tour-spotlight/)
  assert.match(guide, /tour-dimmer--cutout/)
  assert.match(guide, /backdrop-filter: none/)
  assert.match(guide, /addEventListener\(['"]openGlobeMindGuide['"], openGuide\)/)
  assert.doesNotMatch(guide, /setTimeout\(openGuide,\s*1100\)/)
  assert.doesNotMatch(guide, /autoOpenTimer/)
  assert.match(appNav, /data-tour="global-search"/)
  assert.match(home, /data-tour="home-capabilities"/)
  assert.match(homeCapabilities, /截止时间、覆盖与可计算状态以页内门禁为准/)
  assert.match(homeCapabilities, /不代表实时或完整覆盖/)
  assert.match(homeCapabilities, /来源许可和实际可用性以数据目录登记为准/)
  assert.doesNotMatch(home, /近300万|100\+|60\+ 语种|200\+|智能决策/)
  assert.match(groundHome, /data-tour="ground-news-edition"/)
  assert.match(groundDesk, /data-tour="ground-desk-analysis"/)
  assert.match(search, /data-tour="search-form"/)
  assert.match(search, /data-tour="search-result-tools"/)
  assert.match(story, /data-tour="story-canvas"/)
  assert.match(sentiment, /data-tour="sentiment-chart"/)
  assert.match(knowledge, /data-tour="knowledge-skills"/)
  assert.match(financial, /data-tour="financial-terminal"/)
  assert.match(assistant, /data-tour="assistant-auth-gate"/)
  assert.match(report, /data-tour="report-auth-gate"/)
  assert.doesNotMatch(guide, /先判断今天|最新快讯回答|本周多源/)
  assert.doesNotMatch(assistant, /今日研判队列|>Live<\/span>/)
})

test('logged-out users receive persistent and page-specific access explanations', async () => {
  const app = await source('../src/App.vue')
  const guest = await source('../src/components/GuestAccessBanner.vue')
  const assistant = await source('../src/features/assistant/AssistantExperience.vue')
  const report = await source('../src/views/DataService/report-center.vue')

  assert.match(app, /<GuestAccessBanner \/>/)
  assert.match(guest, /当前为访客模式/)
  assert.match(guest, /收藏仅临时保存在此浏览器/)
  assert.match(guest, /跨设备同步、数据助手、报告和个人配置需要登录/)
  assert.match(guest, /role="region"/)
  assert.match(guest, /role="status" aria-live="polite" aria-atomic="true"/)
  assert.match(guest, /正在打开登录窗口/)
  assert.match(guest, /globemind:guest-access-banner:dismissed:v1/)
  assert.match(guest, /sessionStorage\.setItem/)
  assert.match(assistant, /免费注册/)
  assert.match(report, /登录后管理你的报告/)
  assert.match(report, /v-if="!hasToken"/)
})

test('user center keeps a replay entry for the tutorial', async () => {
  const userCenter = await source('../src/views/UserCenter.vue')
  assert.match(userCenter, /data-tour="account-guide-entry"/)
  assert.match(userCenter, /重看新手教程/)
  assert.match(userCenter, /openGlobeMindGuide/)
})

function moduleEvidence(moduleId, scopeId, scopeLabel, cutoffMetric, cutoffStatus = 'available') {
  return {
    schemaVersion: 'globemind.home-module-evidence.v1',
    moduleId,
    scope: { id: scopeId, label: scopeLabel },
    cutoffMetric,
    cutoffStatus,
    method: {
      id: 'business-freshness-health-projection',
      version: 'v1',
      status: 'configured',
    },
    evidenceStatus: cutoffStatus === 'available' ? 'contract_validated' : 'unavailable',
  }
}

function readyHomeReport(overrides = {}) {
  return {
    phase: 'ready',
    schemaVersion: 'globemind.public-status.v1',
    generatedAt: '2026-08-10T03:55:00.000Z',
    receivedAt: '2026-08-10T03:55:01.000Z',
    features: {
      search: {
        featureId: 'search',
        state: 'live',
        researchUse: 'current',
        cutoff: '2026-08-10T03:00:00.000Z',
        moduleEvidence: moduleEvidence(
          'home-data-search',
          'public-news-event-search',
          '公开新闻与事件检索结果',
          'latest_news_at',
        ),
      },
      'ground-news': {
        featureId: 'ground-news',
        state: 'stale',
        researchUse: 'historical',
        cutoff: '2026-08-08T03:00:00.000Z',
        moduleEvidence: moduleEvidence(
          'home-ground-news',
          'public-ground-news-story-sources',
          '公开事件卡、报道与来源构成',
          'latest_story_source_at',
        ),
      },
      'opinion-analysis': {
        featureId: 'opinion-analysis',
        state: 'offline',
        researchUse: 'unavailable',
        cutoff: '',
        moduleEvidence: moduleEvidence(
          'home-opinion-analysis',
          'public-opinion-analysis-scores',
          '公开涉华舆情聚合结果',
          'latest_score_date',
          'unknown',
        ),
      },
    },
    ...overrides,
  }
}

test('home cards bind stable module ids to bounded status evidence', () => {
  const cards = buildHomeCapabilityCards(readyHomeReport(), {
    now: new Date('2026-08-10T04:00:00Z'),
  })

  assert.deepEqual(cards.map((card) => card.moduleId), [
    'home-opinion-analysis',
    'home-ground-news',
    'home-data-search',
    'home-agent-data-connections',
  ])
  assert.equal(cards[0].disclosure.state, 'unknown')
  assert.equal(cards[0].disclosure.cutoff, '无法确认')
  assert.equal(cards[0].disclosure.freshness, '状态无法确认')
  assert.equal(cards[0].disclosure.statusSource, '/api/status · opinion-analysis')
  assert.equal(cards[1].disclosure.state, 'projection_bound')
  assert.equal(cards[1].disclosure.scope, '公开事件卡、报道与来源构成')
  assert.match(cards[1].disclosure.cutoff, /2026/)
  assert.equal(cards[1].disclosure.freshness, '历史快照')
  assert.equal(cards[1].disclosure.method, '业务新鲜度投影 v1')
  assert.equal(
    cards[1].disclosure.evidence,
    '状态契约已校验；不验证数据真实性或完整覆盖',
  )
  assert.equal(cards[3].disclosure.state, 'not_configured')
  assert.equal(cards[3].disclosure.cutoff, '无法确认')
  assert.equal(cards[3].disclosure.method, '方法未配置')
  assert.equal(cards[3].disclosure.evidence, '证据未配置')
})

test('home disclosures fail closed on stale generation or malformed scope and method', () => {
  const stale = buildHomeCapabilityCards(readyHomeReport(), {
    now: new Date('2026-08-10T04:20:01Z'),
  })
  assert.equal(stale[1].disclosure.state, 'unknown')
  assert.equal(stale[1].disclosure.cutoff, '无法确认')

  const malformedReport = readyHomeReport()
  malformedReport.features.search.moduleEvidence = {
    ...malformedReport.features.search.moduleEvidence,
    scope: { id: 'all-data-worldwide', label: '全部资料' },
    method: { id: 'marketing-live-v9', version: 'v9', status: 'trusted' },
  }
  const malformed = buildHomeCapabilityCards(malformedReport, {
    now: new Date('2026-08-10T04:00:00Z'),
  })
  assert.equal(malformed[2].disclosure.state, 'unknown')
  assert.equal(malformed[2].disclosure.cutoff, '无法确认')
  assert.equal(JSON.stringify(malformed).includes('all-data-worldwide'), false)
  assert.equal(JSON.stringify(malformed).includes('marketing-live-v9'), false)
})

test('public status normalization accepts only the feature-specific home evidence contract', () => {
  const check = {
    feature_id: 'search',
    label: '新闻与事件检索',
    status: 'up',
    research_use: 'current',
    detail: '数据在当前更新时限内。',
    metrics: {
      freshness_status: 'live',
      latest_news_at: '2026-08-10T03:00:00Z',
      freshness_lag_hours: 1,
      freshness_sla_hours: 48,
    },
    module_evidence: {
      schema_version: 'globemind.home-module-evidence.v1',
      module_id: 'home-data-search',
      scope: {
        id: 'public-news-event-search',
        label: '公开新闻与事件检索结果',
      },
      cutoff_metric: 'latest_news_at',
      cutoff_status: 'available',
      method: {
        id: 'business-freshness-health-projection',
        version: 'v1',
        status: 'configured',
      },
      evidence_status: 'contract_validated',
    },
  }
  const normalized = normalizeFeatureFreshness(check, 'search', {
    evaluatedAt: new Date('2026-08-10T04:00:00Z'),
  })
  assert.equal(normalized.moduleEvidence.moduleId, 'home-data-search')
  assert.equal(normalized.moduleEvidence.scope.id, 'public-news-event-search')

  const malformed = structuredClone(check)
  malformed.module_evidence.cutoff_metric = 'latest_everything_at'
  malformed.module_evidence.method.status = 'trusted'
  assert.equal(normalizeFeatureFreshness(malformed, 'search', {
    evaluatedAt: new Date('2026-08-10T04:00:00Z'),
  }).moduleEvidence, null)
})

test('home disclosures never reflect transport error bodies or coerce missing values to zero', () => {
  const cards = buildHomeCapabilityCards({
    phase: 'error',
    error: 'postgresql' + '://user:secret@private/status?token=secret',
    generatedAt: null,
    features: null,
  }, { now: new Date('2026-08-10T04:00:00Z') })

  const serialized = JSON.stringify(cards)
  assert.equal(serialized.includes('postgresql'), false)
  assert.equal(serialized.includes('secret'), false)
  assert.equal(serialized.includes('0 小时'), false)
  assert.equal(cards.every((card) => card.disclosure.cutoff === '无法确认'), true)
})

test('home renders every visible card disclosure from the controlled module model', async () => {
  const home = await source('../src/views/appHome.vue')

  assert.match(home, /buildHomeCapabilityCards/)
  assert.match(home, /useFeatureFreshness/)
  assert.match(home, /:data-module-id="card\.moduleId"/)
  assert.match(home, /:aria-describedby="`\$\{card\.moduleId\}-disclosure`"/)
  assert.match(home, /<dt>范围<\/dt>/)
  assert.match(home, /<dt>状态生成<\/dt>/)
  assert.match(home, /<dt>数据截止<\/dt>/)
  assert.match(home, /<dt>新鲜度<\/dt>/)
  assert.match(home, /<dt>方法 \/ 证据<\/dt>/)
  assert.match(home, /<dt>状态来源<\/dt>/)
  assert.match(home, /window\.setInterval/)
  assert.match(home, /linear-gradient\(90deg,[\s\S]*url\('\/imgs\/home\/home-cards\.webp'\)/)
  assert.match(home, /background-size:\s*100% 100%,\s*100% 400%/)
  assert.doesNotMatch(home, /report\.error/)
})
