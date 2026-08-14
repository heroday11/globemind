<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { loadReportArchive, saveLocalReportArchive } from '@/utils/reportArchive'
import { AssistantDrawer } from '@/features/assistant/index.js'
import { getAuthChangedEventName, getToken } from '@/utils/auth'

const reports = ref([])
const activeFilter = ref('all')
const assistantDrawerOpen = ref(false)
const assistantDrawerKey = ref(0)
const REPORT_CONTEXT_STORAGE_KEY = 'data_assistant_report_context_v1'
const route = useRoute()
const router = useRouter()
const hasToken = ref(Boolean(getToken()))

const reportTypes = [
  { key: 'policy', label: '政策分析', icon: 'M12 22C17.523 22 22 17.523 22 12S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z', color: '#5b72df' },
  { key: 'risk', label: '风险评估', icon: 'M12 2L2 22h20L12 2z', color: '#f59e0b' },
  { key: 'trend', label: '趋势监测', icon: 'M3 17l6-6 4 4 8-8', color: '#06b6d4' },
  { key: 'event', label: '事件深度', icon: 'M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3', color: '#8b5cf6' },
]

const filters = [
  { key: 'all', label: '全部' },
  ...reportTypes.map((t) => ({ key: t.key, label: t.label })),
]

const filteredReports = computed(() => {
  if (activeFilter.value === 'all') return reports.value
  return reports.value.filter((r) => r.type === activeFilter.value)
})

const reportCenterAssistantSkill = computed(() => ({
  page: '报告中心',
  path: '/data-service/report-center',
  summary: `报告归档共 ${reports.value.length} 份，当前筛选 ${activeFilter.value === 'all' ? '全部类型' : getTypeMeta(activeFilter.value).label}。`,
  access: [
    '数据服务 > 报告中心',
    '数据助手智能体报告生成后可进入此处归档',
    '点击报告卡片可带上下文打开侧栏助手',
  ],
  sections: [
    '报告统计',
    '报告类型筛选',
    '报告卡片列表',
    '报告查看和删除操作',
  ],
  actions: [
    '按报告类型筛选',
    '查看报告并继续编辑',
    '删除报告',
    '打开数据助手新建报告',
  ],
  aiActions: [
    '解释选中报告内容',
    '继续扩写或改写报告',
    '基于归档报告生成后续研判方向',
  ],
}))

async function loadReports() {
  reports.value = await loadReportArchive()
}

function getTypeMeta(type) {
  return reportTypes.find((t) => t.key === type) || reportTypes[0]
}

function formatDate(iso) {
  if (!iso) return ''
  return iso.replace('T', ' ').slice(0, 16)
}

function goToAssistant() {
  if (!hasToken.value) {
    requestLogin()
    return
  }
  sessionStorage.removeItem(REPORT_CONTEXT_STORAGE_KEY)
  assistantDrawerKey.value += 1
  assistantDrawerOpen.value = true
}

function requestLogin() {
  window.dispatchEvent(new CustomEvent('showLoginModal'))
}

function register() {
  router.push({ path: '/register', query: { redirect: route.fullPath } })
}

function syncAuth() {
  hasToken.value = Boolean(getToken())
  if (hasToken.value) loadReports()
}

function removeReport(id) {
  reports.value = reports.value.filter((r) => r.id !== id)
  saveLocalReportArchive(reports.value)
}

function viewReport(report) {
  try {
    sessionStorage.setItem(REPORT_CONTEXT_STORAGE_KEY, JSON.stringify({
      v: 2,
      topic: report.topic,
      report,
      at: Date.now(),
    }))
  } catch {
    // The drawer can still open without persisted handoff context.
  }
  assistantDrawerKey.value += 1
  assistantDrawerOpen.value = true
}

onMounted(() => {
  if (hasToken.value) loadReports()
  window.addEventListener(getAuthChangedEventName(), syncAuth)
})

onUnmounted(() => window.removeEventListener(getAuthChangedEventName(), syncAuth))
</script>

<template>
  <div class="mr-root" data-tour="report-center">
    <section v-if="!hasToken" class="mr-auth-gate" data-tour="report-auth-gate" aria-labelledby="report-auth-title">
      <div class="mr-auth-lock" aria-hidden="true">G</div>
      <div class="mr-auth-copy">
        <p class="mr-auth-kicker">ACCOUNT REQUIRED</p>
        <h1 id="report-auth-title">登录后管理你的报告</h1>
        <p>报告中心保存数据助手生成的报告、编辑上下文和历史版本。登录后才能读取和管理属于你的内容。</p>
        <ul>
          <li>跨设备同步报告与编辑记录</li>
          <li>从报告继续调用数据助手研判</li>
          <li>保护个人项目和研究资料</li>
        </ul>
      </div>
      <div class="mr-auth-actions">
        <button type="button" class="mr-btn mr-btn--primary" @click="requestLogin">登录后进入</button>
        <button type="button" class="mr-auth-register" @click="register">没有账号？免费注册</button>
      </div>
    </section>
    <template v-else>
    <!-- Header -->
    <header class="mr-header" data-tour="report-library">
      <div class="mr-header-body">
        <div class="mr-header-overline">REPORT CENTER</div>
        <h1 class="mr-header-title">报告中心</h1>
        <p class="mr-header-desc">
          数据助手智能体生成的分析报告存档。
          <button type="button" class="mr-header-link" @click="goToAssistant">打开数据助手</button>
          新建报告。
        </p>
      </div>
      <div class="mr-header-stat" v-if="reports.length">
        <span class="mr-header-count">{{ reports.length }}</span>
        <span class="mr-header-unit">份报告</span>
      </div>
    </header>

    <!-- Filter chips -->
    <nav class="mr-filters" v-if="reports.length > 0">
      <button
        v-for="f in filters"
        :key="f.key"
        type="button"
        class="mr-chip"
        :class="{ 'is-active': activeFilter === f.key }"
        @click="activeFilter = f.key"
      >
        {{ f.label }}
      </button>
    </nav>

    <!-- Empty state -->
    <div v-if="reports.length === 0" class="mr-empty">
      <div class="mr-empty-card">
        <svg class="mr-empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M9 12h6m-3-3v6m-7 4V5a2 2 0 012-2h10a2 2 0 012 2v14l-4-3-4 3-4-3-4 3z" />
        </svg>
        <h2 class="mr-empty-title">暂无报告</h2>
        <p class="mr-empty-text">
          前往<strong>数据助手</strong> → <strong>智能体报告</strong>，选择主题与分析视角，生成您的第一份深度分析报告。
        </p>
        <div class="mr-empty-actions">
          <button type="button" class="mr-btn mr-btn--primary" @click="goToAssistant">
            <span class="mr-btn-label">打开数据助手</span>
            <svg class="mr-btn-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
          </button>
        </div>
      </div>
    </div>

    <!-- Report list -->
    <div v-else class="mr-list">
      <article
        v-for="report in filteredReports"
        :key="report.id"
        class="mr-card"
      >
        <div class="mr-card-accent" :style="{ background: getTypeMeta(report.type).color }"></div>
        <div class="mr-card-body">
          <div class="mr-card-top">
            <div class="mr-card-type">
              <svg class="mr-card-type-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path :d="getTypeMeta(report.type).icon" />
              </svg>
              <span class="mr-card-type-label" :style="{ color: getTypeMeta(report.type).color }">
                {{ getTypeMeta(report.type).label }}
              </span>
            </div>
            <span class="mr-card-date">{{ formatDate(report.createdAt) }}</span>
          </div>
          <h3 class="mr-card-topic">{{ report.topic || '未命名主题' }}</h3>
          <div class="mr-card-meta">
            <span class="mr-card-meta-item">
              <svg class="mr-card-meta-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
              {{ report.timeRange || '—' }}
            </span>
            <span class="mr-card-meta-item">
              <svg class="mr-card-meta-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4"/></svg>
              {{ report.perspective || '分析师' }}视角
            </span>
          </div>
          <p class="mr-card-preview" v-if="report.content">
            {{ report.content.slice(0, 120) }}{{ report.content.length > 120 ? '…' : '' }}
          </p>
        </div>
        <div class="mr-card-actions">
          <button type="button" class="mr-card-action" title="查看报告" @click="viewReport(report)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
          </button>
          <button type="button" class="mr-card-action mr-card-action--danger" title="删除" @click="removeReport(report.id)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
          </button>
        </div>
      </article>
    </div>
    </template>
    <AssistantDrawer
      v-if="hasToken"
      v-model="assistantDrawerOpen"
      :drawer-key="assistantDrawerKey"
      :page-skill="reportCenterAssistantSkill"
      title="报告中心数据助手"
      subtitle="读取报告归档，继续编辑、研判或生成新报告"
    />
  </div>
</template>

<style scoped>
/* ============================================
   DESIGN TOKENS
   ============================================ */
.mr-root {
  --mr-surface: #ffffff;
  --mr-border: #e8e5f0;
  --mr-border-light: #f0edf6;
  --mr-text: #1a1824;
  --mr-text-secondary: #5c5870;
  --mr-text-muted: #8b869e;
  --mr-accent-soft: rgba(91, 114, 223, 0.08);
  --mr-shadow-sm: 0 1px 2px rgba(26, 24, 36, 0.04);
  --mr-shadow: 0 1px 3px rgba(26, 24, 36, 0.04), 0 4px 16px rgba(26, 24, 36, 0.05);
  --mr-radius-sm: 8px;
  --mr-radius: 14px;
  --mr-radius-lg: 20px;
  --mr-transition: 0.22s cubic-bezier(0.33, 1, 0.68, 1);

  width: 100%;
  max-width: 860px;
  margin: 0 auto;
  padding-top: 120px;
  animation: mr-fade-up 0.5s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.mr-auth-gate {
  min-height: 480px;
  display: grid;
  grid-template-columns: 58px minmax(0, 1fr);
  align-content: center;
  gap: 8px 20px;
  padding: clamp(42px, 7vw, 72px);
  border: 1px solid rgba(37, 99, 235, .18);
  border-radius: 24px;
  background: linear-gradient(145deg, #ffffff 0%, #f7f9ff 72%, #edf3ff 100%);
  box-shadow: 0 24px 70px rgba(30, 64, 175, .12);
}

.mr-auth-lock { grid-row: 1 / span 2; width: 58px; height: 58px; display: grid; place-items: center; border-radius: 17px; color: #fff; background: linear-gradient(145deg, #1d4ed8, #0891b2); box-shadow: 0 12px 28px rgba(37, 99, 235, .24); font-size: 24px; font-weight: 900; }
.mr-auth-copy { max-width: 610px; }
.mr-auth-kicker { margin: 0 0 8px; color: #1d4ed8; font-size: 11px; font-weight: 900; letter-spacing: .16em; }
.mr-auth-copy h1 { margin: 0; color: #172033; font-size: clamp(30px, 4vw, 42px); line-height: 1.15; letter-spacing: -.025em; }
.mr-auth-copy > p { margin: 14px 0 16px; color: #526077; font-size: 15px; line-height: 1.7; }
.mr-auth-copy ul { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; color: #334155; font-size: 14px; }
.mr-auth-copy li::before { content: '✓'; margin-right: 9px; color: #0f8a72; font-weight: 900; }
.mr-auth-actions { grid-column: 2; display: flex; align-items: center; gap: 14px; margin-top: 18px; }
.mr-auth-register { border: 0; color: #3157a5; background: transparent; font-weight: 800; cursor: pointer; }

@keyframes mr-fade-up {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* ---- Header ---- */
.mr-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 28px;
  padding: 30px 34px;
  background: var(--mr-surface);
  border-radius: var(--mr-radius-lg);
  border: 1px solid var(--mr-border);
  box-shadow: var(--mr-shadow);
}

.mr-header-overline {
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: var(--uc-accent, #5b72df);
  margin-bottom: 8px;
}

.mr-header-title {
  margin: 0;
  font-size: 1.8rem;
  font-weight: 750;
  color: var(--mr-text);
  letter-spacing: -0.02em;
  line-height: 1.15;
}

.mr-header-desc {
  margin: 6px 0 0;
  font-size: 0.92rem;
  color: var(--mr-text-muted);
  font-weight: 460;
}

.mr-header-link {
  background: none;
  border: none;
  padding: 0;
  font: inherit;
  color: var(--uc-accent, #5b72df);
  font-weight: 620;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 3px;
  text-decoration-color: rgba(91, 114, 223, 0.25);
}

.mr-header-link:hover {
  text-decoration-color: var(--uc-accent, #5b72df);
}

.mr-header-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12px 22px;
  background: var(--mr-accent-soft);
  border-radius: var(--mr-radius);
  flex-shrink: 0;
}

.mr-header-count {
  font-size: 2rem;
  font-weight: 800;
  color: var(--uc-accent, #5b72df);
  letter-spacing: -0.03em;
  line-height: 1;
}

.mr-header-unit {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--mr-text-secondary);
  margin-top: 4px;
}

/* ---- Filters ---- */
.mr-filters {
  display: flex;
  gap: 8px;
  margin-bottom: 24px;
  flex-wrap: wrap;
}

.mr-chip {
  padding: 7px 16px;
  border-radius: 100px;
  border: 1px solid var(--mr-border);
  background: var(--mr-surface);
  font-size: 0.84rem;
  font-weight: 540;
  color: var(--mr-text-secondary);
  cursor: pointer;
  transition: all var(--mr-transition);
  font-family: inherit;
}

.mr-chip:hover {
  border-color: var(--uc-accent, #5b72df);
  color: var(--uc-accent, #5b72df);
}

.mr-chip.is-active {
  background: var(--uc-accent, #5b72df);
  border-color: var(--uc-accent, #5b72df);
  color: #fff;
  font-weight: 620;
}

/* ---- Empty ---- */
.mr-empty {
  animation: mr-fade-up 0.5s cubic-bezier(0.22, 1, 0.36, 1) 0.08s both;
}

.mr-empty-card {
  background: var(--mr-surface);
  border-radius: var(--mr-radius-lg);
  border: 1px solid var(--mr-border);
  box-shadow: var(--mr-shadow);
  padding: clamp(48px, 8vw, 72px) clamp(24px, 5vw, 48px);
  text-align: center;
}

.mr-empty-icon {
  width: 56px;
  height: 56px;
  color: var(--uc-accent, #5b72df);
  opacity: 0.5;
  margin-bottom: 20px;
}

.mr-empty-title {
  margin: 0 0 12px;
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--mr-text);
  letter-spacing: -0.01em;
}

.mr-empty-text {
  margin: 0 auto 28px;
  max-width: 420px;
  font-size: 0.92rem;
  line-height: 1.65;
  color: var(--mr-text-muted);
}

.mr-empty-text strong {
  color: var(--mr-text-secondary);
  font-weight: 640;
}

.mr-empty-actions {
  display: flex;
  gap: 12px;
  justify-content: center;
  flex-wrap: wrap;
}

.mr-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 11px 22px;
  border-radius: var(--mr-radius-sm);
  font-size: 0.9rem;
  font-weight: 620;
  cursor: pointer;
  font-family: inherit;
  border: none;
  transition: all 0.18s ease;
}

.mr-btn--primary {
  background: var(--uc-accent, #5b72df);
  color: #fff;
  box-shadow: 0 2px 8px rgba(91, 114, 223, 0.28);
}

.mr-btn--primary:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 14px rgba(91, 114, 223, 0.34);
}

.mr-btn--ghost {
  background: var(--mr-surface);
  color: var(--mr-text-secondary);
  border: 1px solid var(--mr-border);
}

.mr-btn--ghost:hover {
  border-color: var(--uc-accent, #5b72df);
  color: var(--uc-accent, #5b72df);
}

.mr-btn-arrow {
  width: 16px;
  height: 16px;
  transition: transform 0.2s ease;
}

.mr-btn--primary:hover .mr-btn-arrow {
  transform: translateX(2px);
}

/* ---- Report List ---- */
.mr-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.mr-card {
  display: flex;
  background: var(--mr-surface);
  border-radius: var(--mr-radius);
  border: 1px solid var(--mr-border);
  box-shadow: var(--mr-shadow-sm);
  overflow: hidden;
  transition:
    border-color var(--mr-transition),
    box-shadow var(--mr-transition);
  animation: mr-fade-up 0.5s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.mr-card:nth-child(1) { animation-delay: 0.04s; }
.mr-card:nth-child(2) { animation-delay: 0.08s; }
.mr-card:nth-child(3) { animation-delay: 0.12s; }
.mr-card:nth-child(4) { animation-delay: 0.16s; }
.mr-card:nth-child(5) { animation-delay: 0.20s; }
.mr-card:nth-child(n+6) { animation-delay: 0.24s; }

.mr-card:hover {
  border-color: rgba(91, 114, 223, 0.25);
  box-shadow:
    0 1px 3px rgba(26, 24, 36, 0.04),
    0 6px 20px rgba(26, 24, 36, 0.06);
}

.mr-card-accent {
  width: 4px;
  flex-shrink: 0;
}

.mr-card-body {
  flex: 1;
  padding: 18px 22px;
  min-width: 0;
}

.mr-card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.mr-card-type {
  display: flex;
  align-items: center;
  gap: 6px;
}

.mr-card-type-icon {
  width: 18px;
  height: 18px;
}

.mr-card-type-label {
  font-size: 0.82rem;
  font-weight: 650;
}

.mr-card-date {
  font-size: 0.78rem;
  color: var(--mr-text-muted);
  font-weight: 460;
}

.mr-card-topic {
  margin: 0 0 8px;
  font-size: 1.02rem;
  font-weight: 680;
  color: var(--mr-text);
  letter-spacing: -0.01em;
  line-height: 1.3;
}

.mr-card-meta {
  display: flex;
  gap: 16px;
  margin-bottom: 8px;
}

.mr-card-meta-item {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.8rem;
  color: var(--mr-text-muted);
  font-weight: 470;
}

.mr-card-meta-icon {
  width: 14px;
  height: 14px;
  opacity: 0.6;
}

.mr-card-preview {
  margin: 0;
  font-size: 0.85rem;
  line-height: 1.6;
  color: var(--mr-text-secondary);
}

.mr-card-actions {
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--mr-border-light);
  padding: 0 4px;
}

.mr-card-action {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  border: none;
  background: transparent;
  color: var(--mr-text-muted);
  cursor: pointer;
  transition: all 0.15s ease;
  padding: 0;
}

.mr-card-action:first-child {
  border-bottom: 1px solid var(--mr-border-light);
}

.mr-card-action svg {
  width: 17px;
  height: 17px;
}

.mr-card-action:hover {
  background: var(--mr-accent-soft);
  color: var(--uc-accent, #5b72df);
}

.mr-card-action--danger:hover {
  background: #fef2f2;
  color: #dc2626;
}

/* ---- Responsive ---- */
@media (max-width: 640px) {
  .mr-header {
    flex-direction: column;
    padding: 22px 20px;
  }

  .mr-header-stat {
    margin-top: 16px;
    flex-direction: row;
    gap: 8px;
    padding: 8px 16px;
  }

  .mr-header-count {
    font-size: 1.4rem;
  }

  .mr-card {
    flex-direction: column;
  }

  .mr-card-accent {
    width: 100%;
    height: 3px;
  }

  .mr-card-actions {
    flex-direction: row;
    border-left: none;
    border-top: 1px solid var(--mr-border-light);
  }

  .mr-card-action {
    flex: 1;
    padding: 10px;
  }

  .mr-card-action:first-child {
    border-bottom: none;
    border-right: 1px solid var(--mr-border-light);
  }
}
</style>
