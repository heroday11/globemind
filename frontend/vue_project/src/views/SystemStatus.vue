<script setup>
import { computed, onMounted } from 'vue'

import {
  formatFreshnessCutoff,
  freshnessStateLabel,
  useFeatureFreshness,
} from '@/features/operations/index.js'

defineOptions({ name: 'SystemStatus' })

const { report, refresh } = useFeatureFreshness()

const services = computed(() => Object.values(report.value.features || {}))
const freshnessObjectives = computed(() => report.value.objectives?.freshness || [])
const workflowObjectives = computed(() => report.value.objectives?.workflows || [])
const isLoading = computed(() => report.value.phase === 'idle')
const isUnavailable = computed(() => report.value.phase === 'error')
const overallLabel = computed(() => {
  if (isLoading.value) return '正在核验'
  if (isUnavailable.value) return '状态服务不可用'
  if (report.value.researchMode === 'current') return '当前资料模式'
  return '历史资料模式'
})

function measurementLabel(value) {
  return {
    active: '已测量',
    partial: '部分测量',
    observed: '已有持久观测',
    not_observed: '尚无观测样本',
    unavailable: '不可测量',
    not_instrumented: '尚未接入测量',
    not_available: '能力尚未上线',
  }[value] || '状态未知'
}

function percentText(value) {
  const normalized = nonnegativeNumber(value)
  if (normalized === null || normalized > 1) return '无法确认'
  return `${(normalized * 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}%`
}

function workflowObservedText(item) {
  const observed = item?.observed
  if (!observed) return '暂无可验证样本'
  const count = nonnegativeNumber(observed.sample_count)
  const p95 = nonnegativeNumber(observed.p95_ms)
  if (count === null || p95 === null) return '观测契约不可用'
  return `${count.toLocaleString('zh-CN')} 个样本；成功率 ${percentText(observed.success_rate)}；P95 ${p95.toLocaleString('zh-CN')} 毫秒`
}

function thresholdAssessmentLabel(value) {
  return {
    within: '在内部时限内（非 SLO）',
    exceeded: '超过内部时限',
    unknown: '无法判断',
  }[value] || '无法判断'
}

function nonnegativeNumber(value) {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null
}

function objectiveText(item) {
  const threshold = item?.threshold
  const value = nonnegativeNumber(threshold?.value)
  if (value === null) return '内部时限未登记；SLO 未批准'
  return `不超过 ${value} 小时（内部门禁，未批准）`
}

function observedText(item) {
  const observed = item?.observed
  const value = nonnegativeNumber(observed?.value)
  if (value === null) return '暂无权威观测'
  return `${value.toLocaleString('zh-CN')} 小时`
}

function maintenanceEvidenceLabel(value) {
  return {
    available: '账本已核验',
    not_configured: '账本未配置',
    unavailable: '账本不可核验',
    not_available: '时间线未上线',
  }[value] || '状态未知'
}

function maintenanceTypeLabel(value) {
  return value === 'incident' ? '服务事件' : '计划维护'
}

function maintenanceStateLabel(value) {
  return {
    completed: '已完成',
    cancelled: '已取消',
    resolved: '已解决',
  }[value] || '状态未知'
}

function maintenanceFeatureLabel(value) {
  return services.value.find((service) => service.featureId === value)?.label || '公开能力未确认'
}

function disclosureAvailabilityLabel(value) {
  return value === 'available' ? '可用' : value === 'unavailable' ? '不可用' : '未知'
}

function reloadStatus() {
  void refresh({ force: true })
}

onMounted(() => {
  document.querySelector('#system-status-title')?.focus()
  void refresh()
})
</script>

<template>
  <main class="status-page" aria-labelledby="system-status-title">
    <section class="status-shell">
      <header class="status-hero">
        <p class="eyebrow">PUBLIC STATUS / V1.0</p>
        <h1 id="system-status-title" tabindex="-1">数据与服务状态</h1>
        <p class="status-pill" :class="{ 'is-current': report.researchMode === 'current' }">
          {{ overallLabel }}
        </p>
        <p class="summary">
          本页只公开研究使用所需的数据截止、延迟、内部门禁和正式目标缺口。内部依赖、文件系统与运行控制细节不在公共接口披露。
        </p>
        <p v-if="report.generatedAt" class="verified-at">
          状态生成时间：{{ formatFreshnessCutoff(report.generatedAt) }}
        </p>
        <p v-if="isUnavailable" class="status-error" role="alert">{{ report.error }}</p>
        <button type="button" class="reload-button" :disabled="isLoading" @click="reloadStatus">
          {{ isLoading ? '核验中…' : '重新核验' }}
        </button>
      </header>

      <section class="status-section" aria-labelledby="data-status-heading">
        <div class="section-heading">
          <div>
            <p class="section-kicker">DATA FRESHNESS</p>
            <h2 id="data-status-heading">业务数据新鲜度</h2>
          </div>
          <span>{{ services.length }} 项公开能力</span>
        </div>
        <div v-if="services.length" class="service-grid">
          <article v-for="service in services" :key="service.featureId" class="service-card">
            <div class="service-title-row">
              <h3>{{ service.label }}</h3>
              <span class="state-badge" :data-state="service.state">
                {{ freshnessStateLabel(service.state) }}
              </span>
            </div>
            <dl>
              <div>
                <dt>数据截止</dt>
                <dd>{{ service.cutoff ? formatFreshnessCutoff(service.cutoff) : '无法确认' }}</dd>
              </div>
              <div>
                <dt>当前延迟</dt>
                <dd>{{ service.lagHours === null ? '无法确认' : `${service.lagHours} 小时` }}</dd>
              </div>
              <div>
                <dt>内部更新时限</dt>
                <dd>{{ service.slaHours === null ? '尚未登记' : `${service.slaHours} 小时` }}</dd>
              </div>
              <div>
                <dt>研究用途</dt>
                <dd>{{ service.researchUse === 'current' ? '可作当前资料使用' : service.researchUse === 'unavailable' ? '当前不可用' : '仅作历史资料使用' }}</dd>
              </div>
            </dl>
            <p>{{ service.detail || '状态说明不可用。' }}</p>
          </article>
        </div>
        <p v-else class="empty-state">尚未取得可验证的数据状态，所有结果应按历史资料处理。</p>
      </section>

      <section class="status-section" aria-labelledby="slo-heading">
        <div class="section-heading">
          <div>
            <p class="section-kicker">SERVICE LEVEL OBJECTIVES</p>
            <h2 id="slo-heading">服务目标与测量缺口</h2>
          </div>
        </div>
        <div class="table-wrap" tabindex="0" aria-label="可水平滚动的数据新鲜度目标表">
          <table>
            <thead>
              <tr>
                <th scope="col">指标</th>
                <th scope="col">内部更新时限</th>
                <th scope="col">观测</th>
                <th scope="col">结论</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in freshnessObjectives" :key="item.id">
                <th scope="row">{{ item.label }}</th>
                <td>{{ objectiveText(item) }}</td>
                <td>{{ observedText(item) }}</td>
                <td>{{ thresholdAssessmentLabel(item.threshold_assessment) }}</td>
              </tr>
              <tr v-if="!freshnessObjectives.length">
                <td colspan="4">尚无可验证的新鲜度目标。</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="gap-grid">
          <article v-for="item in workflowObjectives" :key="item.id" class="gap-card">
            <p>{{ measurementLabel(item.measurement_status) }}</p>
            <h3>{{ item.label }}</h3>
            <strong>{{ item.indicator }}</strong>
            <span>{{ workflowObservedText(item) }}</span>
            <span>{{ item.reason }}</span>
          </article>
        </div>
        <p class="honesty-note">
          内部更新时限用于触发历史模式或离线降级，不是经批准 SLO。
          “已有观测”不是达标。检索、导出和报告在形成经批准目标与误差预算前，合规结论固定为不可计算，不宣称具备正式 SLO。
        </p>
      </section>

      <section
        v-if="report.degradationDisclosure?.status === 'action_required'"
        class="status-section"
        aria-labelledby="response-heading"
      >
        <div class="section-heading">
          <div>
            <p class="section-kicker">DEGRADATION RESPONSE</p>
            <h2 id="response-heading">降级处置公开状态</h2>
          </div>
        </div>
        <p class="honesty-note">{{ report.degradationDisclosure.reason }}</p>
        <dl class="maintenance-governance">
          <div>
            <dt>事件负责人</dt>
            <dd>{{ disclosureAvailabilityLabel(report.degradationDisclosure.incidentOwner.availability) }}</dd>
          </div>
          <div>
            <dt>恢复预计</dt>
            <dd>{{ disclosureAvailabilityLabel(report.degradationDisclosure.recoveryEstimate.availability) }}</dd>
          </div>
          <div>
            <dt>最近状态更新</dt>
            <dd>{{ disclosureAvailabilityLabel(report.degradationDisclosure.lastStatusUpdate.availability) }}</dd>
          </div>
        </dl>
        <p class="honesty-note">
          不使用状态生成时间或数据截止时间推测恢复预计；工作流违约状态在目标未批准时保持未知。
        </p>
      </section>

      <section class="status-section" aria-labelledby="incident-heading">
        <div class="section-heading">
          <div>
            <p class="section-kicker">INCIDENT HISTORY</p>
            <h2 id="incident-heading">事件与维护记录</h2>
          </div>
        </div>
        <p class="maintenance-evidence">
          {{ maintenanceEvidenceLabel(report.incidentHistory?.status) }}：
          {{ report.incidentHistory?.reason || '事件证据状态未知；当前页面不能证明历史可用性。' }}
        </p>
        <ol v-if="report.incidentHistory?.events?.length" class="maintenance-list">
          <li v-for="event in report.incidentHistory.events" :key="event.id">
            <div class="service-title-row">
              <h3>{{ event.title }}</h3>
              <span class="state-badge">{{ maintenanceStateLabel(event.status) }}</span>
            </div>
            <p>{{ maintenanceTypeLabel(event.type) }} · {{ formatFreshnessCutoff(event.started_at) }}—{{ formatFreshnessCutoff(event.ended_at) }}</p>
            <p>{{ event.summary }}</p>
            <p>影响范围：{{ event.affected_features.map(maintenanceFeatureLabel).join('、') }}</p>
          </li>
        </ol>
        <p v-else-if="report.incidentHistory?.status === 'available'" class="honesty-note">
          已核验账本中暂无已发布记录；这不等于历史无事件。
        </p>
        <dl class="maintenance-governance">
          <div>
            <dt>正式事件 owner</dt>
            <dd>{{ report.incidentHistory?.owner?.status === 'not_configured' ? '未配置' : '状态未知' }}</dd>
          </div>
          <div>
            <dt>保留策略</dt>
            <dd>{{ report.incidentHistory?.retention?.status === 'not_approved' ? '未批准' : '状态未知' }}</dd>
          </div>
          <div>
            <dt>状态订阅</dt>
            <dd>{{ report.incidentHistory?.subscription?.status === 'not_configured' ? '未配置' : '状态未知' }}</dd>
          </div>
        </dl>
      </section>

      <nav class="status-links" aria-label="相关公开页面">
        <router-link to="/data-service/help-docs">帮助文档</router-link>
        <router-link to="/methodology">方法说明</router-link>
        <router-link to="/sources">来源与许可</router-link>
        <router-link to="/corrections">报告问题</router-link>
      </nav>
    </section>
  </main>
</template>

<style scoped>
.status-page {
  min-height: 100vh;
  padding: 112px 22px 70px;
  box-sizing: border-box;
  color: #183153;
  background:
    radial-gradient(circle at 84% 6%, rgba(38, 124, 178, 0.14), transparent 31rem),
    linear-gradient(160deg, #f6faff, #edf3f9);
}

.status-shell { width: min(100%, 1080px); margin: 0 auto; }
.status-hero, .status-section {
  border: 1px solid #dbe6f1;
  border-radius: 22px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 18px 52px rgba(42, 70, 111, 0.08);
}
.status-hero { padding: clamp(28px, 6vw, 58px); }
.eyebrow, .section-kicker {
  margin: 0 0 10px;
  color: #2766ca;
  font: 750 0.76rem/1.4 ui-monospace, monospace;
  letter-spacing: 0.13em;
}
h1 { margin: 0; color: #102b50; font-size: clamp(2.2rem, 6vw, 4.2rem); line-height: 1.08; }
h1:focus { outline: 3px solid #3678dd; outline-offset: 8px; }
.status-pill {
  display: inline-flex;
  margin: 22px 0 0;
  padding: 8px 12px;
  border-radius: 999px;
  color: #7a4d00;
  background: #fff0c8;
  font-weight: 760;
}
.status-pill.is-current { color: #0c664b; background: #dff7ed; }
.summary { max-width: 800px; margin: 22px 0 0; color: #506782; font-size: 1.05rem; line-height: 1.8; }
.verified-at, .status-error { margin: 14px 0 0; color: #64778f; }
.status-error { color: #a23030; }
.reload-button {
  min-width: 112px;
  min-height: 46px;
  margin-top: 20px;
  border: 0;
  border-radius: 12px;
  color: #fff;
  background: #245fae;
  font-weight: 750;
  cursor: pointer;
}
.reload-button:focus-visible, .status-links a:focus-visible, .table-wrap:focus-visible {
  outline: 3px solid rgba(36, 95, 174, 0.42);
  outline-offset: 3px;
}
.reload-button:disabled { cursor: wait; opacity: 0.65; }
.status-section { margin-top: 22px; padding: clamp(24px, 4vw, 38px); }
.section-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; }
.section-heading h2 { margin: 0; color: #17385f; font-size: clamp(1.45rem, 3vw, 2rem); }
.section-heading > span { color: #667b95; }
.service-grid, .gap-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-top: 24px; }
.service-card, .gap-card { padding: 20px; border: 1px solid #dce6f0; border-radius: 16px; background: #f9fbfe; }
.service-title-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.service-title-row h3, .gap-card h3 { margin: 0; color: #1a3a60; font-size: 1.05rem; }
.state-badge { flex: 0 0 auto; padding: 4px 8px; border-radius: 999px; color: #714900; background: #fff0c5; font-size: 0.76rem; font-weight: 750; }
.state-badge[data-state='live'] { color: #0a6047; background: #dcf6ec; }
.state-badge[data-state='offline'] { color: #9b2828; background: #ffe4e4; }
dl { margin: 18px 0 0; }
dl > div { display: grid; grid-template-columns: 88px 1fr; gap: 8px; padding: 8px 0; border-top: 1px solid #e7edf4; }
dt { color: #6b7d93; } dd { margin: 0; color: #263f5e; font-weight: 650; }
.service-card > p, .status-section > p, .gap-card span { color: #5c7089; line-height: 1.7; }
.table-wrap { margin-top: 24px; overflow-x: auto; border: 1px solid #dce6f0; border-radius: 14px; }
table { width: 100%; min-width: 660px; border-collapse: collapse; text-align: left; }
th, td { padding: 13px 15px; border-bottom: 1px solid #e4ebf3; }
thead th { color: #294967; background: #f2f6fb; }
tbody tr:last-child > * { border-bottom: 0; }
.gap-card > p { display: inline-flex; margin: 0 0 10px; padding: 4px 8px; border-radius: 999px; color: #855500; background: #fff1ca; font-size: 0.76rem; font-weight: 750; }
.gap-card strong, .gap-card span { display: block; margin-top: 10px; }
.gap-card strong { color: #2b4667; }
.honesty-note { padding: 16px 18px; border-left: 4px solid #d9961f; background: #fff8e7; }
.empty-state { padding: 18px; border-radius: 14px; background: #fff7e3; }
.status-links { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 22px; }
.status-links a { display: inline-flex; align-items: center; min-height: 44px; padding: 0 14px; border: 1px solid #ccd9e7; border-radius: 12px; color: #22599d; background: #fff; text-decoration: none; font-weight: 700; }

@media (max-width: 860px) {
  .service-grid, .gap-grid { grid-template-columns: 1fr; }
  .section-heading { align-items: flex-start; flex-direction: column; }
}

@media (max-width: 560px) {
  .status-page { padding: 92px 12px 46px; }
  .status-hero, .status-section { border-radius: 16px; }
  dl > div { grid-template-columns: 1fr; gap: 3px; }
  .status-links { display: grid; grid-template-columns: 1fr; }
}
</style>
