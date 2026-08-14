<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import {
  MODEL_ASSURANCE_DIMENSIONS,
  createLatestModelAssuranceRequest,
  emptyModelAssuranceDetail,
  emptyModelAssuranceLedger,
  formatAssuranceDelta,
  formatAssuranceMetric,
  formatModelSurfaceField,
  modelAssuranceApi,
  modelSurfaceCopyText,
  parseModelAssuranceManifestJson,
  reconcileModelAssuranceLedger,
} from '@/features/model-assurance/index.js'
import { getCurrentUser } from '@/utils/auth.js'

defineOptions({ name: 'ModelAssurance' })

const ledger = ref(emptyModelAssuranceLedger())
const detail = ref(emptyModelAssuranceDetail())
const selectedEvaluationId = ref('')
const ledgerError = ref(null)
const detailError = ref(null)
const surfaceError = ref(null)
const submissionError = ref(null)
const pageTitle = ref(null)

const ledgerRequest = createLatestModelAssuranceRequest()
const detailRequest = createLatestModelAssuranceRequest()
const submissionRequest = createLatestModelAssuranceRequest()
const surfaceRequest = createLatestModelAssuranceRequest()

const surfaceInventory = ref(Object.freeze({ phase: 'idle', value: null, error: '' }))
const copiedSurfaceId = ref('')
const surfaceCopyMessage = ref('')
let copyMessageTimer = null

const currentUser = ref(getCurrentUser())
const isAdmin = computed(() => String(currentUser.value?.role || '').toLowerCase() === 'admin')

const manifestText = ref('')
const preparedManifest = ref(null)
const confirmationEvaluationId = ref('')
const submitting = ref(false)
const submissionMessage = ref('')

const selectedEvaluation = computed(() => detail.value.evaluation)
const assuranceStatus = computed(() => ledger.value.status)
const evaluations = computed(() => ledger.value.evaluations)
const selectedSummary = computed(() => evaluations.value.find(
  (item) => item.evaluationId === selectedEvaluationId.value,
) || null)

const dimensionLabels = Object.freeze({
  overall: '整体',
  country: '国家',
  language: '语言',
  topic: '议题',
})

const metricRows = computed(() => {
  const evaluation = selectedEvaluation.value
  if (!evaluation) return []
  return [
    {
      key: 'overall',
      dimension: 'overall',
      value: '全部样本',
      metrics: evaluation.result.overall,
    },
    ...evaluation.result.strata.map((item) => ({
      key: `${item.dimension}:${item.value}`,
      dimension: item.dimension,
      value: item.value,
      metrics: item.metrics,
    })),
  ]
})

const coverageRows = computed(() => {
  const coverage = selectedEvaluation.value?.result.coverage
  if (!coverage) return []
  return MODEL_ASSURANCE_DIMENSIONS.map((dimension) => ({
    dimension,
    expected: coverage.expected[dimension],
    observed: coverage.observed[dimension],
    missing: coverage.missing[dimension],
    unexpected: coverage.unexpected[dimension],
  }))
})

const preparedSummary = computed(() => {
  const manifest = preparedManifest.value
  if (!manifest) return null
  return {
    evaluationId: manifest.evaluation_id,
    datasetId: manifest.dataset?.dataset_id || '缺失',
    datasetSha256: manifest.dataset?.sha256 || '缺失',
    cutoffAt: manifest.dataset?.cutoff_at || '缺失',
    modelId: manifest.model?.model_id || '缺失',
    modelVersion: manifest.model?.model_version || '缺失',
    methodVersion: manifest.model?.method_version || '缺失',
  }
})

const submissionIsSuccess = computed(() =>
  submissionMessage.value.includes('已重新读取服务端账本'),
)

const confirmationReady = computed(() => Boolean(
  preparedSummary.value
  && confirmationEvaluationId.value === preparedSummary.value.evaluationId
  && !submitting.value,
))

function requestErrorMessage(error, fallback) {
  if (error?.status === 401) return '登录状态无效，请重新登录后再试。'
  if (error?.status === 403) return '后端拒绝了该操作；只有已验证的管理员可以提交清单。'
  return String(error?.message || fallback)
}

async function focusAlert(target) {
  await nextTick()
  target.value?.focus()
}

function readyLedger(value) {
  return Object.freeze({
    phase: 'ready',
    status: value.status,
    evaluations: value.evaluations,
    error: '',
  })
}

function readyDetail(evaluation) {
  return Object.freeze({ phase: 'ready', evaluation, error: '' })
}

async function loadSurfaceInventory() {
  surfaceInventory.value = Object.freeze({ phase: 'loading', value: null, error: '' })
  copiedSurfaceId.value = ''
  surfaceCopyMessage.value = ''
  const outcome = await surfaceRequest.run((signal) =>
    modelAssuranceApi.fetchSurfaces({ signal }),
  )
  if (outcome.status === 'stale' || outcome.status === 'aborted') return
  if (outcome.status === 'error') {
    const message = outcome.error?.status === 401
      ? '登录状态无效，请重新登录后读取模型输出面清单。'
      : '模型输出面清单当前不可安全读取。'
    surfaceInventory.value = Object.freeze({ phase: 'error', value: null, error: message })
    await focusAlert(surfaceError)
    return
  }
  surfaceInventory.value = Object.freeze({ phase: 'ready', value: outcome.value, error: '' })
}

async function copySurfaceIdentity(surface) {
  const text = modelSurfaceCopyText(surface)
  if (!text) return
  try {
    const writeText = globalThis.navigator?.clipboard?.writeText
    if (typeof writeText !== 'function') throw new Error('clipboard unavailable')
    await writeText.call(globalThis.navigator.clipboard, text)
    copiedSurfaceId.value = surface.surfaceId
    surfaceCopyMessage.value = `已复制 ${surface.surfaceId} 的身份状态。`
    if (copyMessageTimer) globalThis.clearTimeout(copyMessageTimer)
    copyMessageTimer = globalThis.setTimeout(() => {
      copiedSurfaceId.value = ''
      surfaceCopyMessage.value = ''
      copyMessageTimer = null
    }, 4_000)
  } catch {
    copiedSurfaceId.value = ''
    surfaceCopyMessage.value = '浏览器未授予剪贴板权限；可直接选择卡片中的纯文本复制。'
  }
}

async function loadEvaluation(evaluationId) {
  detail.value = emptyModelAssuranceDetail('loading')
  if (!evaluationId) return
  const outcome = await detailRequest.run((signal) =>
    modelAssuranceApi.getEvaluation(evaluationId, { signal }),
  )
  if (outcome.status === 'stale' || outcome.status === 'aborted') return
  if (outcome.status === 'error') {
    detail.value = emptyModelAssuranceDetail(
      'error',
      requestErrorMessage(outcome.error, '评测详情不可用'),
    )
    await focusAlert(detailError)
    return
  }
  detail.value = readyDetail(outcome.value)
}

async function selectEvaluation(evaluationId) {
  if (evaluationId === selectedEvaluationId.value && detail.value.phase === 'ready') return
  selectedEvaluationId.value = evaluationId
  await loadEvaluation(evaluationId)
}

async function loadLedger(preferredEvaluationId = '') {
  ledger.value = emptyModelAssuranceLedger('loading')
  selectedEvaluationId.value = ''
  detailRequest.cancel()
  detail.value = emptyModelAssuranceDetail()
  const outcome = await ledgerRequest.run(async (signal) => {
    const [status, rows] = await Promise.all([
      modelAssuranceApi.fetchStatus({ signal }),
      modelAssuranceApi.listEvaluations({ limit: 100, signal }),
    ])
    return reconcileModelAssuranceLedger(status, rows)
  })
  if (outcome.status === 'stale' || outcome.status === 'aborted') return
  if (outcome.status === 'error') {
    ledger.value = emptyModelAssuranceLedger(
      'error',
      requestErrorMessage(outcome.error, '模型保障账本不可用'),
    )
    await focusAlert(ledgerError)
    return
  }
  ledger.value = readyLedger(outcome.value)
  const target = outcome.value.evaluations.some(
    (item) => item.evaluationId === preferredEvaluationId,
  )
    ? preferredEvaluationId
    : outcome.value.evaluations[0]?.evaluationId || ''
  if (target) {
    selectedEvaluationId.value = target
    await loadEvaluation(target)
  }
}

function resetPreparedSubmission() {
  preparedManifest.value = null
  confirmationEvaluationId.value = ''
}

function prepareSubmission() {
  submissionMessage.value = ''
  try {
    preparedManifest.value = parseModelAssuranceManifestJson(manifestText.value)
    confirmationEvaluationId.value = ''
  } catch (error) {
    resetPreparedSubmission()
    submissionMessage.value = requestErrorMessage(error, 'JSON 评测清单无效')
    focusAlert(submissionError)
  }
}

async function submitPreparedManifest() {
  if (!isAdmin.value || !confirmationReady.value || !preparedManifest.value) return
  submitting.value = true
  submissionMessage.value = ''
  const manifest = preparedManifest.value
  const outcome = await submissionRequest.run((signal) =>
    modelAssuranceApi.submitEvaluation(manifest, { signal }),
  )
  submitting.value = false
  if (outcome.status === 'stale' || outcome.status === 'aborted') return
  if (outcome.status === 'error') {
    submissionMessage.value = requestErrorMessage(outcome.error, '评测清单提交失败')
    await focusAlert(submissionError)
    return
  }
  const createdEvaluationId = outcome.value.manifest.evaluationId
  resetPreparedSubmission()
  manifestText.value = ''
  await nextTick()
  submissionMessage.value = `清单 ${createdEvaluationId} 已追加；正在重新读取服务端账本。`
  await loadLedger(createdEvaluationId)
  submissionMessage.value = ledger.value.phase === 'ready'
    ? `清单 ${createdEvaluationId} 已追加；已重新读取服务端账本。`
    : `清单 ${createdEvaluationId} 已追加，但账本重新读取失败；请稍后重试。`
}

function formatTimestamp(value) {
  if (!value) return '未记录'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '未记录' : parsed.toLocaleString('zh-CN')
}

function joinValues(values) {
  return Array.isArray(values) && values.length ? values.join('、') : '无'
}

function shortHash(value) {
  if (!value) return '未记录'
  return `${value.slice(0, 12)}…${value.slice(-8)}`
}

watch(manifestText, () => {
  if (preparedManifest.value) resetPreparedSubmission()
  if (submissionMessage.value) submissionMessage.value = ''
})

onMounted(async () => {
  await nextTick()
  pageTitle.value?.focus()
  await Promise.all([loadLedger(), loadSurfaceInventory()])
})

onBeforeUnmount(() => {
  ledgerRequest.cancel()
  detailRequest.cancel()
  submissionRequest.cancel()
  surfaceRequest.cancel()
  if (copyMessageTimer) globalThis.clearTimeout(copyMessageTimer)
})
</script>

<template>
  <main class="assurance-page" aria-labelledby="model-assurance-title">
    <header class="assurance-hero">
      <p class="eyebrow">MODEL ASSURANCE / MANIFEST-ONLY</p>
      <h1 id="model-assurance-title" ref="pageTitle" tabindex="-1">模型评测与发布保障</h1>
      <p class="hero-copy">
        这里展示服务端从版本化计数清单重新计算的评测、校准、漂移和回滚门禁，不运行模型，也不生成 benchmark。
      </p>
      <div class="trust-boundary" role="note">
        <strong>证据边界：manifest-only</strong>
        <span>
          “金标准”、holdout 隔离与“独立审阅”仅表示清单中的元数据声明，不表示本页面已读取数据集、复现实验或完成外部验真。
        </span>
      </div>
    </header>

    <section
      class="panel surface-panel"
      aria-labelledby="model-surface-title"
      :aria-busy="surfaceInventory.phase === 'loading'"
    >
      <div class="section-heading">
        <div>
          <p class="eyebrow">BOUNDED SURFACE INVENTORY</p>
          <h2 id="model-surface-title">模型输出面清单</h2>
        </div>
        <button
          type="button"
          class="secondary-button"
          :disabled="surfaceInventory.phase === 'loading'"
          @click="loadSurfaceInventory"
        >
          {{ surfaceInventory.phase === 'loading' ? '正在读取…' : '重新读取清单' }}
        </button>
      </div>
      <p class="surface-boundary" role="note">
        静态源码覆盖不代表运行时部署证明。当前清单不读取 provider 配置、环境变量或模型路径；缺少独立 attestation 时，模型 ID、完整版本、部署时间和变更说明保持“未知/不可用”。
      </p>
      <p v-if="surfaceInventory.phase === 'loading'" class="state-message" role="status" aria-live="polite">
        正在读取有界输出面清单；旧清单已清除。
      </p>
      <p
        v-else-if="surfaceInventory.phase === 'error'"
        ref="surfaceError"
        class="state-message state-message--error"
        role="alert"
        aria-live="assertive"
        tabindex="-1"
      >
        {{ surfaceInventory.error }} 当前不得推断任何模型部署身份。
      </p>
      <template v-else-if="surfaceInventory.phase === 'ready' && surfaceInventory.value">
        <div class="surface-summary" role="note">
          <strong>{{ surfaceInventory.value.surfaces.length }} 个有界输出面</strong>
          <span>运行时证明：{{ surfaceInventory.value.runtimeAttestationState }}</span>
          <span>完整部署声明：false</span>
        </div>
        <div class="surface-grid">
          <article
            v-for="surface in surfaceInventory.value.surfaces"
            :key="surface.surfaceId"
            class="surface-card"
          >
            <header>
              <h3><code>{{ surface.surfaceId }}</code></h3>
              <span>{{ surface.outputKind }}</span>
            </header>
            <dl>
              <div><dt>模型 ID</dt><dd>{{ formatModelSurfaceField(surface.identity.modelId) }}</dd></div>
              <div><dt>模型版本</dt><dd>{{ formatModelSurfaceField(surface.identity.modelVersion) }}</dd></div>
              <div><dt>部署时间</dt><dd>{{ formatModelSurfaceField(surface.identity.deployedAt) }}</dd></div>
              <div><dt>变更说明</dt><dd>{{ formatModelSurfaceField(surface.identity.changeNotes) }}</dd></div>
            </dl>
            <p class="surface-routes">{{ surface.routePatterns.join(' · ') }}</p>
            <pre tabindex="0">{{ modelSurfaceCopyText(surface) }}</pre>
            <button type="button" class="secondary-button" @click="copySurfaceIdentity(surface)">
              {{ copiedSurfaceId === surface.surfaceId ? '已复制' : '复制身份状态' }}
            </button>
          </article>
        </div>
        <p v-if="surfaceCopyMessage" class="copy-message" role="status" aria-live="polite">
          {{ surfaceCopyMessage }}
        </p>
      </template>
    </section>

    <section class="panel status-panel" aria-labelledby="assurance-status-title" :aria-busy="ledger.phase === 'loading'">
      <div class="section-heading">
        <div>
          <p class="eyebrow">LEDGER STATUS</p>
          <h2 id="assurance-status-title">账本状态</h2>
        </div>
        <button type="button" class="secondary-button" :disabled="ledger.phase === 'loading'" @click="loadLedger(selectedEvaluationId)">
          {{ ledger.phase === 'loading' ? '正在读取…' : '重新读取' }}
        </button>
      </div>

      <p v-if="ledger.phase === 'loading'" class="state-message" role="status" aria-live="polite">
        正在读取状态与评测列表；之前显示的数据已清除。
      </p>
      <p
        v-else-if="ledger.phase === 'error'"
        ref="ledgerError"
        class="state-message state-message--error"
        role="alert"
        aria-live="assertive"
        aria-atomic="true"
        tabindex="-1"
      >
        {{ ledger.error }} 旧状态已原子清除，当前一律按 blocked / not_observed 处理。
      </p>
      <template v-else-if="ledger.phase === 'ready' && assuranceStatus">
        <div class="status-grid" role="group" aria-label="模型保障状态摘要">
          <div><span>发布门禁</span><strong :class="`state-${assuranceStatus.releaseStatus}`">{{ assuranceStatus.releaseStatus }}</strong></div>
          <div><span>运行观测</span><strong>{{ assuranceStatus.operationalState }}</strong></div>
          <div><span>金标准状态</span><strong>{{ assuranceStatus.goldStandardState }}</strong></div>
          <div><span>评测清单</span><strong>{{ assuranceStatus.evaluationCount }}</strong></div>
          <div><span>可放行记录</span><strong>{{ assuranceStatus.eligibleCount }}</strong></div>
          <div><span>状态生成</span><strong>{{ formatTimestamp(assuranceStatus.generatedAt) }}</strong></div>
        </div>
        <div v-if="assuranceStatus.evaluationCount === 0" class="empty-state" role="status" aria-live="polite">
          <strong>blocked / not_observed</strong>
          <p>尚无服务端评测清单，也没有真实金标准数据或模型运行结果可供本页面观测。</p>
          <code v-for="code in assuranceStatus.reasonCodes" :key="code">{{ code }}</code>
        </div>
      </template>
    </section>

    <section v-if="ledger.phase === 'ready' && evaluations.length" class="ledger-layout" aria-label="评测账本与详情">
      <aside class="panel evaluation-list" aria-labelledby="evaluation-list-title">
        <p class="eyebrow">APPEND-ONLY ENTRIES</p>
        <h2 id="evaluation-list-title">评测记录</h2>
        <ul>
          <li v-for="item in evaluations" :key="item.entrySha256">
            <button
              type="button"
              :class="{ selected: selectedEvaluationId === item.evaluationId }"
              :aria-current="selectedEvaluationId === item.evaluationId ? 'true' : undefined"
              @click="selectEvaluation(item.evaluationId)"
            >
              <strong>{{ item.evaluationId }}</strong>
              <span>{{ item.modelId }} · {{ item.modelVersion }}</span>
              <span>{{ item.gateState }} · {{ item.driftState }}</span>
              <small>{{ formatTimestamp(item.storedAt) }}</small>
            </button>
          </li>
        </ul>
      </aside>

      <article class="panel detail-panel" :aria-busy="detail.phase === 'loading'">
        <p v-if="detail.phase === 'loading'" class="state-message" role="status" aria-live="polite">
          正在读取评测详情；上一条详情已清除。
        </p>
        <p
          v-else-if="detail.phase === 'error'"
          ref="detailError"
          class="state-message state-message--error"
          role="alert"
          aria-live="assertive"
          aria-atomic="true"
          tabindex="-1"
        >
          {{ detail.error }} 旧详情未被保留。
        </p>

        <template v-else-if="detail.phase === 'ready' && selectedEvaluation">
          <header class="detail-heading">
            <div>
              <p class="eyebrow">EVALUATION DETAIL</p>
              <h2>{{ selectedEvaluation.manifest.evaluationId }}</h2>
              <p>
                序号 {{ selectedEvaluation.sequence }} · entry
                <code :title="selectedEvaluation.entrySha256">{{ shortHash(selectedEvaluation.entrySha256) }}</code>
              </p>
            </div>
            <div class="gate-stack">
              <strong :class="`gate-badge gate-badge--${selectedSummary?.gateState || 'blocked'}`">
                当前门禁：{{ selectedSummary?.gateState || 'blocked' }}
              </strong>
              <small>入账时历史判定：{{ selectedEvaluation.result.gateState }}</small>
            </div>
          </header>

          <div class="detail-warning" role="note">
            <strong>{{ selectedEvaluation.result.evidenceStatus }}</strong>
            <span>
              以下数值与 result 门禁是入账时历史判定；当前门禁取自动态摘要，会在候选或任一基线 review 过期后变为 blocked。
              原始样本、标签和外部审阅证据未在浏览器中验真。
            </span>
          </div>

          <section class="detail-section" aria-labelledby="identity-title">
            <h3 id="identity-title">版本与数据身份</h3>
            <dl class="definition-grid">
              <div><dt>模型</dt><dd>{{ selectedEvaluation.manifest.model.modelId }} · {{ selectedEvaluation.manifest.model.modelVersion }}</dd></div>
              <div><dt>方法版本</dt><dd>{{ selectedEvaluation.manifest.model.methodVersion }}</dd></div>
              <div><dt>正类标签</dt><dd>{{ selectedEvaluation.manifest.model.positiveLabel }}</dd></div>
              <div><dt>分类阈值</dt><dd>{{ formatAssuranceMetric(selectedEvaluation.manifest.classificationThreshold) }}</dd></div>
              <div><dt>数据集</dt><dd>{{ selectedEvaluation.manifest.dataset.datasetId }} · {{ selectedEvaluation.manifest.dataset.datasetVersion }}</dd></div>
              <div><dt>数据 SHA-256</dt><dd><code :title="selectedEvaluation.manifest.dataset.sha256">{{ shortHash(selectedEvaluation.manifest.dataset.sha256) }}</code></dd></div>
              <div><dt>数据截止</dt><dd>{{ formatTimestamp(selectedEvaluation.manifest.dataset.cutoffAt) }}</dd></div>
              <div><dt>记录时间</dt><dd>{{ formatTimestamp(selectedEvaluation.storedAt) }}</dd></div>
            </dl>
          </section>

          <section class="detail-section" aria-labelledby="metrics-title">
            <h3 id="metrics-title">整体与分层重算指标</h3>
            <p>Precision、Recall、F1、Brier 与 ECE 均取自服务端重算结果；“未定义”不会显示为 0。</p>
            <div class="table-wrap" tabindex="0" aria-label="可水平滚动的模型指标表">
              <table>
                <caption>整体、国家、语言与议题 slice 指标</caption>
                <thead>
                  <tr>
                    <th scope="col">维度</th>
                    <th scope="col">Slice</th>
                    <th scope="col">样本</th>
                    <th scope="col">Precision</th>
                    <th scope="col">Recall</th>
                    <th scope="col">F1</th>
                    <th scope="col">Brier</th>
                    <th scope="col">ECE</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in metricRows" :key="row.key">
                    <th scope="row">{{ dimensionLabels[row.dimension] }}</th>
                    <td>{{ row.value }}</td>
                    <td>{{ row.metrics.sampleCount }}</td>
                    <td>{{ formatAssuranceMetric(row.metrics.precision) }}</td>
                    <td>{{ formatAssuranceMetric(row.metrics.recall) }}</td>
                    <td>{{ formatAssuranceMetric(row.metrics.f1) }}</td>
                    <td>{{ formatAssuranceMetric(row.metrics.brierScore) }}</td>
                    <td>{{ formatAssuranceMetric(row.metrics.expectedCalibrationError) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section class="detail-section" aria-labelledby="coverage-title">
            <div class="subsection-heading">
              <h3 id="coverage-title">Coverage</h3>
              <strong>{{ selectedEvaluation.result.coverage.state }}</strong>
            </div>
            <p>最小样本门槛：{{ selectedEvaluation.result.coverage.minimumSamplesSatisfied ? '满足' : '未满足或未声明' }}</p>
            <div class="table-wrap" tabindex="0" aria-label="可水平滚动的覆盖表">
              <table>
                <caption>国家、语言和议题覆盖差异</caption>
                <thead><tr><th scope="col">维度</th><th scope="col">声明</th><th scope="col">观测</th><th scope="col">缺失</th><th scope="col">意外</th></tr></thead>
                <tbody>
                  <tr v-for="row in coverageRows" :key="row.dimension">
                    <th scope="row">{{ dimensionLabels[row.dimension] }}</th>
                    <td>{{ joinValues(row.expected) }}</td>
                    <td>{{ joinValues(row.observed) }}</td>
                    <td>{{ joinValues(row.missing) }}</td>
                    <td>{{ joinValues(row.unexpected) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section class="detail-section assurance-grid" aria-labelledby="drift-title">
            <div>
              <h3 id="drift-title">Drift 与 baseline</h3>
              <dl>
                <div><dt>漂移状态</dt><dd>{{ selectedEvaluation.result.drift.state }}</dd></div>
                <div><dt>基线评测</dt><dd>{{ selectedEvaluation.result.drift.baselineEvaluationId || '未提供' }}</dd></div>
                <div><dt>F1 Δ</dt><dd>{{ formatAssuranceDelta(selectedEvaluation.result.drift.f1Delta) }}</dd></div>
                <div><dt>Brier Δ</dt><dd>{{ formatAssuranceDelta(selectedEvaluation.result.drift.brierDelta) }}</dd></div>
                <div><dt>ECE Δ</dt><dd>{{ formatAssuranceDelta(selectedEvaluation.result.drift.eceDelta) }}</dd></div>
                <div><dt>基线 entry</dt><dd><code :title="selectedEvaluation.manifest.baseline?.entrySha256 || ''">{{ shortHash(selectedEvaluation.manifest.baseline?.entrySha256) }}</code></dd></div>
              </dl>
            </div>
            <div>
              <h3>Rollback recommendation</h3>
              <dl>
                <div><dt>动作</dt><dd>{{ selectedEvaluation.result.rollback.action }}</dd></div>
                <div><dt>目标</dt><dd>{{ selectedEvaluation.result.rollback.targetEvaluationId || '无' }}</dd></div>
              </dl>
              <ul v-if="selectedEvaluation.result.rollback.reasonCodes.length" class="code-list">
                <li v-for="code in selectedEvaluation.result.rollback.reasonCodes" :key="code"><code>{{ code }}</code></li>
              </ul>
            </div>
          </section>

          <section class="detail-section assurance-grid" aria-labelledby="review-title">
            <div>
              <h3 id="review-title">金标准声明</h3>
              <dl>
                <div><dt>用途</dt><dd>{{ selectedEvaluation.manifest.dataset.evaluationRole }}</dd></div>
                <div><dt>声明状态</dt><dd>{{ selectedEvaluation.manifest.dataset.goldStandardStatus }}</dd></div>
                <div><dt>标注协议</dt><dd>{{ selectedEvaluation.manifest.dataset.annotationProtocolRef || '未提供' }}</dd></div>
                <div><dt>溯源引用</dt><dd>{{ selectedEvaluation.manifest.dataset.provenanceRef || '未提供' }}</dd></div>
              </dl>
              <p class="boundary-copy">这些字段不是对金标准数据的外部验真。</p>
            </div>
            <div>
              <h3>独立 review 元数据</h3>
              <dl v-if="selectedEvaluation.manifest.independentReview">
                <div><dt>决定</dt><dd>{{ selectedEvaluation.manifest.independentReview.decision }}</dd></div>
                <div><dt>组织</dt><dd>{{ selectedEvaluation.manifest.independentReview.reviewerOrganization }}</dd></div>
                <div><dt>独立声明</dt><dd>{{ selectedEvaluation.manifest.independentReview.independenceAttestation ? '已声明' : '未声明' }}</dd></div>
                <div><dt>审阅时间</dt><dd>{{ formatTimestamp(selectedEvaluation.manifest.independentReview.reviewedAt) }}</dd></div>
                <div><dt>审阅有效期</dt><dd>{{ formatTimestamp(selectedEvaluation.manifest.independentReview.validUntil) }}</dd></div>
                <div><dt>证据 SHA</dt><dd><code :title="selectedEvaluation.manifest.independentReview.evidenceSha256">{{ shortHash(selectedEvaluation.manifest.independentReview.evidenceSha256) }}</code></dd></div>
              </dl>
              <p v-else>未提供 review 元数据。</p>
              <p class="boundary-copy">本页面没有下载或验证 review evidence。</p>
            </div>
            <div>
              <h3>评测隔离声明</h3>
              <dl v-if="selectedEvaluation.manifest.evaluationIntegrity">
                <div><dt>标签来源</dt><dd>{{ selectedEvaluation.manifest.evaluationIntegrity.labelSource }}</dd></div>
                <div><dt>数据分区</dt><dd>{{ selectedEvaluation.manifest.evaluationIntegrity.partitionRole }}</dd></div>
                <div><dt>holdout 访问</dt><dd>{{ selectedEvaluation.manifest.evaluationIntegrity.holdoutAccessStatus }}</dd></div>
                <div><dt>开发集摘要数</dt><dd>{{ selectedEvaluation.manifest.evaluationIntegrity.developmentDatasetSha256s.length }}</dd></div>
                <div><dt>隔离证据 SHA</dt><dd><code :title="selectedEvaluation.manifest.evaluationIntegrity.separationEvidenceSha256">{{ shortHash(selectedEvaluation.manifest.evaluationIntegrity.separationEvidenceSha256) }}</code></dd></div>
              </dl>
              <p v-else>未提供 gold/silver 与 holdout 隔离声明。</p>
              <p class="boundary-copy">隔离字段仍是 manifest 声明；本页面不验证数据集字节或外部证据。</p>
            </div>
          </section>

          <section v-if="selectedEvaluation.manifest.thresholds" class="detail-section" aria-labelledby="threshold-title">
            <h3 id="threshold-title">显式门禁阈值</h3>
            <dl class="definition-grid">
              <div><dt>最低 Precision</dt><dd>{{ formatAssuranceMetric(selectedEvaluation.manifest.thresholds.minimumPrecision) }}</dd></div>
              <div><dt>最低 Recall</dt><dd>{{ formatAssuranceMetric(selectedEvaluation.manifest.thresholds.minimumRecall) }}</dd></div>
              <div><dt>最低 F1</dt><dd>{{ formatAssuranceMetric(selectedEvaluation.manifest.thresholds.minimumF1) }}</dd></div>
              <div><dt>最高 Brier</dt><dd>{{ formatAssuranceMetric(selectedEvaluation.manifest.thresholds.maximumBrierScore) }}</dd></div>
              <div><dt>最高 ECE</dt><dd>{{ formatAssuranceMetric(selectedEvaluation.manifest.thresholds.maximumEce) }}</dd></div>
              <div><dt>最低 slice F1</dt><dd>{{ formatAssuranceMetric(selectedEvaluation.manifest.thresholds.minimumStratumF1) }}</dd></div>
              <div><dt>总体最小样本</dt><dd>{{ selectedEvaluation.manifest.thresholds.minimumOverallSamples }}</dd></div>
              <div><dt>slice 最小样本</dt><dd>{{ selectedEvaluation.manifest.thresholds.minimumSamplesPerStratum }}</dd></div>
            </dl>
          </section>

          <section class="detail-section" aria-labelledby="reason-title">
            <h3 id="reason-title">Reason codes</h3>
            <p v-if="!selectedEvaluation.result.reasonCodes.length">无阻断原因。</p>
            <ul v-else class="code-list">
              <li v-for="code in selectedEvaluation.result.reasonCodes" :key="code"><code>{{ code }}</code></li>
            </ul>
          </section>
        </template>
      </article>
    </section>

    <section v-if="isAdmin" class="panel submission-panel" aria-labelledby="submission-title">
      <p class="eyebrow">ADMIN / APPEND-ONLY</p>
      <h2 id="submission-title">提交版本化 JSON 评测清单</h2>
      <p>
        前端不提供样例金标准、默认阈值或可编辑派生指标。请粘贴完整 JSON；后端管理员权限与 Pydantic 契约是最终边界。
      </p>
      <form @submit.prevent="prepareSubmission">
        <label for="assurance-manifest">JSON manifest</label>
        <textarea
          id="assurance-manifest"
          v-model="manifestText"
          rows="14"
          maxlength="1048576"
          spellcheck="false"
          aria-describedby="manifest-help"
          placeholder="粘贴完整评测清单；此处不会自动插入任何样例或指标。"
        ></textarea>
        <p id="manifest-help" class="field-help">
          第一步只解析严格 JSON 并显示身份摘要，不代表服务端已接受或完成评测。
        </p>
        <button type="submit" class="primary-button" :disabled="!manifestText.trim() || submitting">
          第一步：解析并核对
        </button>
      </form>

      <div v-if="preparedSummary" class="confirmation-panel" aria-labelledby="confirmation-title">
        <h3 id="confirmation-title">第二步：确认 append-only 提交</h3>
        <dl class="definition-grid">
          <div><dt>evaluation_id</dt><dd>{{ preparedSummary.evaluationId }}</dd></div>
          <div><dt>模型</dt><dd>{{ preparedSummary.modelId }} · {{ preparedSummary.modelVersion }}</dd></div>
          <div><dt>方法版本</dt><dd>{{ preparedSummary.methodVersion }}</dd></div>
          <div><dt>数据集</dt><dd>{{ preparedSummary.datasetId }}</dd></div>
          <div><dt>数据截止</dt><dd>{{ preparedSummary.cutoffAt }}</dd></div>
          <div><dt>数据 SHA</dt><dd><code :title="preparedSummary.datasetSha256">{{ shortHash(preparedSummary.datasetSha256) }}</code></dd></div>
        </dl>
        <label for="assurance-confirmation">
          输入完整 evaluation_id 以确认该 ID 一旦写入不可覆盖
        </label>
        <input
          id="assurance-confirmation"
          v-model="confirmationEvaluationId"
          type="text"
          autocomplete="off"
          :placeholder="preparedSummary.evaluationId"
        />
        <div class="confirmation-actions">
          <button type="button" class="secondary-button" :disabled="submitting" @click="resetPreparedSubmission">取消</button>
          <button type="button" class="danger-button" :disabled="!confirmationReady" @click="submitPreparedManifest">
            {{ submitting ? '正在提交…' : '第二步：确认提交' }}
          </button>
        </div>
      </div>

      <p
        v-if="submissionMessage"
        ref="submissionError"
        :class="['submission-message', { 'submission-message--success': submissionIsSuccess }]"
        :role="submissionIsSuccess ? 'status' : 'alert'"
        :aria-live="submissionIsSuccess ? 'polite' : 'assertive'"
        aria-atomic="true"
        tabindex="-1"
      >
        {{ submissionMessage }}
      </p>
    </section>

    <section v-else class="panel read-only-panel" aria-label="只读权限说明">
      <strong>当前页面为只读模式</strong>
      <p>非管理员不展示 JSON 写入入口；即使本地角色信息被修改，后端仍会以 403 拒绝未授权提交。</p>
    </section>
  </main>
</template>

<style scoped>
* {
  box-sizing: border-box;
}

.assurance-page {
  min-height: 100vh;
  padding: 108px 24px 64px;
  color: #172b46;
  background:
    radial-gradient(circle at 90% 5%, rgb(52 103 196 / 13%), transparent 30rem),
    linear-gradient(160deg, #f7faff, #edf3fa);
}

.assurance-hero,
.panel {
  width: min(100%, 1180px);
  margin-inline: auto;
  border: 1px solid #d6e1ef;
  border-radius: 20px;
  background: rgb(255 255 255 / 94%);
  box-shadow: 0 18px 50px rgb(37 66 105 / 8%);
}

.assurance-hero {
  padding: clamp(28px, 5vw, 54px);
}

.panel {
  margin-top: 22px;
  padding: clamp(22px, 4vw, 36px);
}

.eyebrow {
  margin: 0 0 10px;
  color: #245fb8;
  font: 750 0.76rem/1.4 ui-monospace, monospace;
  letter-spacing: 0.13em;
}

h1,
h2,
h3,
p {
  overflow-wrap: anywhere;
}

h1 {
  margin: 0;
  color: #102a4b;
  font-size: clamp(2.1rem, 5vw, 4rem);
  line-height: 1.08;
  letter-spacing: -0.04em;
}

h1:focus,
[tabindex='-1']:focus {
  outline: 3px solid #2367c7;
  outline-offset: 5px;
}

h2,
h3 {
  margin: 0;
  color: #122f54;
}

.hero-copy {
  max-width: 820px;
  margin: 20px 0 0;
  color: #465f7d;
  font-size: 1.05rem;
  line-height: 1.8;
}

.trust-boundary,
.detail-warning {
  display: grid;
  gap: 6px;
  margin-top: 24px;
  padding: 16px 18px;
  border: 1px solid #d7a82d;
  border-radius: 14px;
  color: #634a0b;
  background: #fff8df;
  line-height: 1.65;
}

.surface-boundary {
  margin: 20px 0 0;
  padding: 14px 16px;
  border-left: 4px solid #d7a82d;
  color: #634a0b;
  background: #fff8df;
  line-height: 1.7;
}

.surface-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 22px;
  margin-top: 20px;
  color: #526985;
}

.surface-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 18px;
}

.surface-card {
  min-width: 0;
  padding: 18px;
  border: 1px solid #d6e1ef;
  border-radius: 14px;
  background: #f8fbff;
}

.surface-card header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.surface-card header span {
  color: #60748e;
  font: 700 0.78rem/1.4 ui-monospace, monospace;
}

.surface-card dl {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}

.surface-routes {
  color: #526985;
  font: 0.82rem/1.6 ui-monospace, monospace;
}

.surface-card pre {
  overflow: auto;
  max-height: 210px;
  padding: 12px;
  border: 1px solid #dce6f2;
  border-radius: 10px;
  color: #294d78;
  background: #fff;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.surface-card .secondary-button {
  margin-top: 2px;
}

.copy-message {
  margin: 16px 0 0;
  color: #1d653d;
  font-weight: 700;
}

.section-heading,
.detail-heading,
.subsection-heading,
.confirmation-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

button,
input,
textarea {
  font: inherit;
}

button,
input[type='text'] {
  min-height: 44px;
}

button {
  border-radius: 11px;
  cursor: pointer;
  font-weight: 750;
}

button:focus-visible,
input:focus-visible,
textarea:focus-visible,
.table-wrap:focus-visible {
  outline: 3px solid #2367c7;
  outline-offset: 3px;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.primary-button,
.secondary-button,
.danger-button {
  padding: 10px 17px;
}

.primary-button {
  border: 1px solid #1f5cae;
  color: #fff;
  background: #1f5cae;
}

.secondary-button {
  border: 1px solid #afc2da;
  color: #21466f;
  background: #f7faff;
}

.danger-button {
  border: 1px solid #a63434;
  color: #fff;
  background: #a63434;
}

.state-message,
.empty-state,
.submission-message {
  margin: 22px 0 0;
  padding: 16px;
  border-radius: 12px;
  background: #edf4fc;
  line-height: 1.65;
}

.state-message--error,
.submission-message:not(.submission-message--success) {
  border: 1px solid #db8e8e;
  color: #7d2020;
  background: #fff1f1;
}

.submission-message--success {
  border: 1px solid #7dbb99;
  color: #1d653d;
  background: #ecfaf2;
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 22px;
}

.status-grid div {
  display: grid;
  gap: 6px;
  min-height: 84px;
  padding: 14px;
  border: 1px solid #dce6f2;
  border-radius: 12px;
  background: #f8fbff;
}

.status-grid span,
dt,
.field-help {
  color: #60748e;
  font-size: 0.86rem;
}

.state-blocked,
.gate-badge--blocked {
  color: #842929;
}

.state-eligible,
.gate-badge--eligible {
  color: #17623b;
}

.empty-state code {
  display: inline-block;
  margin: 4px 8px 0 0;
}

.ledger-layout {
  display: grid;
  grid-template-columns: minmax(250px, 330px) minmax(0, 1fr);
  gap: 22px;
  width: min(100%, 1180px);
  margin-inline: auto;
}

.ledger-layout > .panel {
  width: 100%;
}

.evaluation-list ul,
.code-list {
  margin: 18px 0 0;
  padding: 0;
  list-style: none;
}

.evaluation-list li + li {
  margin-top: 9px;
}

.evaluation-list button {
  display: grid;
  gap: 5px;
  width: 100%;
  min-height: 96px;
  padding: 13px;
  border: 1px solid #d6e1ef;
  color: #263f5d;
  text-align: left;
  background: #f8fbff;
}

.evaluation-list button.selected {
  border-color: #2367c7;
  box-shadow: inset 4px 0 #2367c7;
  background: #edf5ff;
}

.evaluation-list span,
.evaluation-list small {
  color: #60748e;
}

.gate-badge {
  padding: 8px 12px;
  border: 1px solid currentcolor;
  border-radius: 999px;
  font-family: ui-monospace, monospace;
}

.gate-stack {
  display: grid;
  justify-items: end;
  gap: 8px;
}

.gate-stack small {
  color: #60748e;
}

.detail-heading p {
  margin: 8px 0 0;
  color: #60748e;
}

.detail-section {
  margin-top: 28px;
  padding-top: 24px;
  border-top: 1px solid #dce6f2;
}

.detail-section > p {
  color: #526985;
  line-height: 1.65;
}

.definition-grid,
.assurance-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 24px;
}

dl {
  margin: 16px 0 0;
}

dl div {
  min-width: 0;
  padding: 10px 0;
  border-bottom: 1px solid #e2e9f2;
}

dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
  font-weight: 650;
}

code {
  color: #294d78;
  font-family: ui-monospace, monospace;
}

.table-wrap {
  overflow-x: auto;
  margin-top: 16px;
  border: 1px solid #dce6f2;
  border-radius: 12px;
}

table {
  width: 100%;
  min-width: 720px;
  border-collapse: collapse;
}

caption {
  padding: 12px;
  color: #526985;
  text-align: left;
}

th,
td {
  padding: 11px 12px;
  border-top: 1px solid #e1e8f1;
  text-align: left;
  white-space: nowrap;
}

thead th {
  color: #304d70;
  background: #f0f5fb;
}

.boundary-copy {
  color: #7a5310 !important;
  font-weight: 700;
}

.code-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.code-list li {
  padding: 7px 9px;
  border-radius: 8px;
  background: #eef3f9;
}

.submission-panel > p,
.read-only-panel p {
  max-width: 860px;
  color: #526985;
  line-height: 1.7;
}

form,
.confirmation-panel {
  display: grid;
  gap: 12px;
  margin-top: 22px;
}

label {
  color: #203f66;
  font-weight: 750;
}

textarea,
input[type='text'] {
  width: 100%;
  border: 1px solid #aebfd4;
  border-radius: 11px;
  color: #172b46;
  background: #fff;
}

textarea {
  min-height: 240px;
  padding: 14px;
  resize: vertical;
  font-family: ui-monospace, monospace;
  line-height: 1.55;
}

input[type='text'] {
  padding: 10px 12px;
}

form .primary-button {
  justify-self: start;
}

.confirmation-panel {
  padding: 20px;
  border: 1px solid #d7a82d;
  border-radius: 14px;
  background: #fff9e8;
}

.confirmation-actions {
  justify-content: flex-end;
}

@media (max-width: 880px) {
  .status-grid,
  .definition-grid,
    .assurance-grid,
    .surface-grid,
    .ledger-layout {
      grid-template-columns: 1fr;
  }

      .section-heading,
      .detail-heading {
        align-items: flex-start;
        flex-direction: column;
      }

      .gate-stack {
        justify-items: start;
      }
}

@media (max-width: 560px) {
  .assurance-page {
    padding: 88px 12px 40px;
  }

  .assurance-hero,
  .panel {
    border-radius: 14px;
  }

  .confirmation-actions {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
