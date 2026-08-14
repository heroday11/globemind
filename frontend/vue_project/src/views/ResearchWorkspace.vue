<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

import {
  RESEARCH_ARTIFACT_DEFAULT_FIELDS,
  RESEARCH_ARTIFACT_FORMATS,
  RESEARCH_ARTIFACT_OPTIONAL_FIELDS,
  buildSavedSearchValues,
  comparisonChangeCount,
  exportReadiness,
  normalizeVersionComparison,
  normalizeSavedSearchMonitoring,
  projectRole,
  researchWorkflowApi,
  triggerResearchArtifactDownload,
  versionedMutation,
  workflowPermissions,
  workflowStageStates,
} from '@/features/research-workflow/index.js'
import { getCurrentUser, getToken } from '@/utils/auth.js'

const projects = ref([])
const project = ref(null)
const loading = ref(false)
const error = ref('')
const notice = ref('')
const changeReason = ref('')
const currentUsername = computed(() => String(getCurrentUser()?.username || ''))
const role = computed(() => projectRole(project.value, currentUsername.value))
const permissions = computed(() => workflowPermissions(role.value))
const stages = computed(() => workflowStageStates(project.value))
const readiness = computed(() => exportReadiness(project.value))
const comparison = ref(null)
const savedSearchMonitoring = ref(normalizeSavedSearchMonitoring(null, ''))
let monitoringGeneration = 0
const artifactDownloadKey = ref('')
const artifactFormatLabels = Object.freeze({
  json: 'JSON',
  markdown: 'Markdown',
  html: 'HTML',
  csv: 'CSV 证据清单',
})
const artifactFieldLabels = Object.freeze({
  project_scope: '项目范围',
  cutoff: '数据截止及口径',
  method: '方法与模型披露',
  uncertainty: '不确定性',
  research_questions: '研究问题',
  saved_search_receipts: '检索回执（不含查询与筛选值）',
  evidence_summaries: '证据摘要（不含内部备注）',
  information_gaps: '信息缺口',
  alternative_hypotheses: '替代假设',
  judgments: '分析判断',
  human_decisions: '人工决定（不含理由正文）',
  review_outcomes: '审阅结果（不含意见正文）',
})
const selectedArtifactFields = ref([...RESEARCH_ARTIFACT_DEFAULT_FIELDS])
const compareFrom = ref(null)
const compareTo = ref(null)
const exportVersions = computed(() =>
  (project.value?.export_manifests || []).map((item) => Number(item.export_version)),
)
const changedComparisonCategories = computed(() =>
  (comparison.value?.categories || []).filter((category) => comparisonChangeCount(category) > 0),
)
const canCompareVersions = computed(
  () =>
    exportVersions.value.length >= 2 &&
    Number.isInteger(Number(compareFrom.value)) &&
    Number.isInteger(Number(compareTo.value)) &&
    Number(compareFrom.value) !== Number(compareTo.value),
)

const createForm = reactive({ title: '', description: '', countries: '', reason: '' })
const memberForm = reactive({ username: '', role: 'reviewer' })
const question = ref('')
const searchForm = reactive({
  name: '',
  query: '',
  filters: '{}',
  search_snapshot_id: '',
  query_receipt_sha256: '',
  normalized_contract_sha256: '',
  ordered_returned_ids_sha256: '',
})
const evidenceForm = reactive({
  relation: 'support',
  summary: '',
  source_id: '',
  source_title: '',
  source_url: '',
  original_anchor: '',
  article_id: '',
  evidence_snapshot_id: '',
  captured_at: '',
  content_sha256: '',
  parser_version: '',
})
const gapForm = reactive({ description: '', impact: '', resolution_plan: '' })
const hypothesisForm = reactive({ statement: '', discriminating_evidence: '' })
const judgmentForm = reactive({
  statement: '',
  supporting: '',
  opposing: '',
  gaps: '',
  hypotheses: '',
  uncertainty: '',
})
const decisionForm = reactive({ judgment_id: '', decision: 'confirm', rationale: '', modified: '' })
const reviewForm = reactive({
  review_type: 'peer_review',
  target_type: 'decision',
  target_id: '',
  outcome: 'approved',
  comment: '',
})
const exportForm = reactive({
  report_title: '',
  cutoff_at: '',
  cutoff_basis: '',
  method: '',
  uncertainty: '',
})

function idList(value) {
  return String(value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

function isoDateTime(value) {
  return value ? new Date(value).toISOString() : null
}

function setProject(next) {
  if (project.value?.id !== next.id) {
    monitoringGeneration += 1
    savedSearchMonitoring.value = normalizeSavedSearchMonitoring(null, '')
  }
  project.value = next
  comparison.value = null
  const versions = (next.export_manifests || []).map((item) => Number(item.export_version))
  compareFrom.value = versions.length >= 2 ? versions[0] : null
  compareTo.value = versions.length >= 2 ? versions.at(-1) : null
  const index = projects.value.findIndex((item) => item.id === next.id)
  const summary = {
    id: next.id,
    title: next.title,
    version: next.version,
    role: projectRole(next, currentUsername.value),
    updated_at: next.updated_at,
  }
  if (index >= 0) projects.value.splice(index, 1, summary)
  else projects.value.unshift(summary)
}

async function loadSavedSearchMonitoring(projectId) {
  const generation = ++monitoringGeneration
  try {
    const payload = await researchWorkflowApi.savedSearchMonitoring(projectId)
    if (generation !== monitoringGeneration || project.value?.id !== projectId) return
    savedSearchMonitoring.value = normalizeSavedSearchMonitoring(payload, projectId)
  } catch {
    if (generation !== monitoringGeneration || project.value?.id !== projectId) return
    savedSearchMonitoring.value = normalizeSavedSearchMonitoring(null, projectId)
  }
}

async function run(action, success) {
  error.value = ''
  notice.value = ''
  loading.value = true
  try {
    const next = await action()
    if (next?.schema_version === 'research-project-v1') setProject(next)
    notice.value = success
    changeReason.value = ''
    return next
  } catch (cause) {
    const codes = cause?.detail?.reason_codes
    error.value = codes?.length
      ? `当前仍有门禁未满足：${codes.join('、')}`
      : cause?.message || '操作失败'
    return null
  } finally {
    loading.value = false
  }
}

function mutation(values) {
  return versionedMutation(project.value, changeReason.value, values)
}

async function loadProjects() {
  if (!getToken()) {
    error.value = '请先登录后使用研究项目。'
    return
  }
  let selectedProjectId = null
  await run(async () => {
    const response = await researchWorkflowApi.listProjects()
    projects.value = response.projects || []
    if (!project.value && projects.value[0]) {
      const next = await researchWorkflowApi.getProject(projects.value[0].id)
      setProject(next)
      selectedProjectId = next.id
    }
    return null
  }, '')
  if (selectedProjectId) await loadSavedSearchMonitoring(selectedProjectId)
}

async function selectProject(id) {
  const next = await run(() => researchWorkflowApi.getProject(id), '项目已加载。')
  if (next) await loadSavedSearchMonitoring(next.id)
}

async function createProject() {
  const next = await run(
    () =>
      researchWorkflowApi.createProject({
        title: createForm.title,
        description: createForm.description,
        scope_countries: idList(createForm.countries),
        reason: createForm.reason,
      }),
    '研究项目已创建并写入持久化存储。',
  )
  if (next) {
    Object.assign(createForm, { title: '', description: '', countries: '', reason: '' })
    await loadSavedSearchMonitoring(next.id)
  }
}

async function setMember() {
  await run(
    () =>
      researchWorkflowApi.setMember(
        project.value.id,
        memberForm.username,
        mutation({ role: memberForm.role }),
      ),
    '项目角色已更新。',
  )
}

async function addQuestion() {
  const next = await run(
    () => researchWorkflowApi.addQuestion(project.value.id, mutation({ question: question.value })),
    '研究问题已记录。',
  )
  if (next) question.value = ''
}

async function addSearch() {
  let filters
  try {
    filters = JSON.parse(searchForm.filters || '{}')
  } catch {
    error.value = '检索过滤条件必须是 JSON 对象。'
    return
  }
  let values
  try {
    values = buildSavedSearchValues(searchForm, filters)
  } catch (cause) {
    error.value = cause?.message || '查询快照引用格式无效。'
    return
  }
  const linkingSnapshot = Boolean(values.search_snapshot_id)
  const next = await run(
    () =>
      researchWorkflowApi.addSavedSearch(project.value.id, mutation(values)),
    linkingSnapshot
      ? '保存检索已记录；查询快照引用已由服务端只读核验。'
      : '保存检索已记录；未提供查询快照，状态明确为 unavailable。',
  )
  if (next) await loadSavedSearchMonitoring(next.id)
}

async function addEvidence() {
  await run(
    () =>
      researchWorkflowApi.addEvidence(
        project.value.id,
        mutation({
          ...evidenceForm,
          source_url: evidenceForm.source_url || null,
          original_anchor: evidenceForm.original_anchor || null,
          article_id: evidenceForm.article_id ? Number(evidenceForm.article_id) : null,
          evidence_snapshot_id: evidenceForm.evidence_snapshot_id || null,
          captured_at: evidenceForm.captured_at || null,
          content_sha256: evidenceForm.content_sha256 || null,
          parser_version: evidenceForm.parser_version || null,
        }),
      ),
    '证据已记录；系统不会把研究员声明冒充为服务端核验。',
  )
}

async function addGap() {
  await run(
    () => researchWorkflowApi.addInformationGap(project.value.id, mutation({ ...gapForm })),
    '信息缺口已记录。',
  )
}

async function addHypothesis() {
  await run(
    () =>
      researchWorkflowApi.addAlternativeHypothesis(
        project.value.id,
        mutation({ ...hypothesisForm }),
      ),
    '替代假设已记录。',
  )
}

async function addJudgment() {
  await run(
    () =>
      researchWorkflowApi.addJudgment(
        project.value.id,
        mutation({
          statement: judgmentForm.statement,
          supporting_evidence_ids: idList(judgmentForm.supporting),
          opposing_evidence_ids: idList(judgmentForm.opposing),
          information_gap_ids: idList(judgmentForm.gaps),
          alternative_hypothesis_ids: idList(judgmentForm.hypotheses),
          uncertainty: judgmentForm.uncertainty,
        }),
      ),
    '判断及其证据引用已记录。',
  )
}

async function addDecision() {
  await run(
    () =>
      researchWorkflowApi.addDecision(
        project.value.id,
        mutation({
          judgment_id: decisionForm.judgment_id,
          decision: decisionForm.decision,
          rationale: decisionForm.rationale,
          modified_statement: decisionForm.decision === 'modify' ? decisionForm.modified : null,
        }),
      ),
    '人工确认、修改或驳回记录已保存。',
  )
}

async function addReview() {
  const reviewPayload = permissions.value.canPeerReview
    ? { ...reviewForm, review_type: 'peer_review', target_type: 'decision' }
    : { ...reviewForm, review_type: 'approval', target_type: 'decision' }
  await run(
    () => researchWorkflowApi.addReview(project.value.id, mutation(reviewPayload)),
    '审阅记录已保存。',
  )
}

async function createExport() {
  await run(
    () =>
      researchWorkflowApi.createExportManifest(
        project.value.id,
        mutation({
          report_title: exportForm.report_title,
          cutoff_at: isoDateTime(exportForm.cutoff_at),
          cutoff_basis: exportForm.cutoff_basis,
          method: exportForm.method,
          models: [],
          uncertainty: exportForm.uncertainty,
        }),
      ),
    '版本化报告清单已保存；本页面未生成正式报告。',
  )
}

async function compareVersions() {
  const payload = await run(
    () =>
      researchWorkflowApi.compareExportVersions(
        project.value.id,
        compareFrom.value,
        compareTo.value,
      ),
    '版本比较已加载；读取操作不会新增审计事件。',
  )
  if (!payload) return
  const normalized = normalizeVersionComparison(payload)
  if (!normalized.available) {
    error.value = '版本比较合同不可用。'
    return
  }
  comparison.value = normalized.comparison
}

async function downloadExportArtifact(manifest, format) {
  const key = `${manifest.export_version}:${format}`
  error.value = ''
  notice.value = ''
  artifactDownloadKey.value = key
  try {
    const artifact = await researchWorkflowApi.downloadExportArtifact(
      project.value.id,
      manifest.export_version,
      format,
      [...selectedArtifactFields.value],
    )
    triggerResearchArtifactDownload(artifact)
    notice.value =
      `manifest v${manifest.export_version} ${format.toUpperCase()} 已下载；` +
      `响应 SHA-256：${artifact.sha256}；report content SHA-256：${artifact.reportContentSha256}；` +
      `manifest SHA-256：${artifact.manifestSha256}`
  } catch (cause) {
    error.value = cause?.message || '报告 artifact 下载失败。'
  } finally {
    artifactDownloadKey.value = ''
  }
}

function artifactFormatLabel(format) {
  return artifactFormatLabels[format] || String(format).toUpperCase()
}

function comparisonCategoryLabel(categoryId) {
  return (
    {
      research_questions: '研究问题',
      saved_searches: '保存检索',
      support_evidence: '支持证据',
      opposing_evidence: '反方证据',
      background_evidence: '背景证据',
      information_gaps: '信息缺口',
      alternative_hypotheses: '替代假设',
      judgments: '判断',
      human_decisions: '人工决定',
      peer_reviews: '同行审阅',
      approvals: '批准',
      method: '方法',
      model: '模型',
      cutoff: '截止时间',
    }[categoryId] || categoryId
  )
}

function formattedDiffValue(value) {
  return JSON.stringify(value, null, 2)
}

onMounted(loadProjects)
</script>

<template>
  <main class="research-workspace">
    <header class="research-hero">
      <p class="eyebrow">V2 · 区域研究工作流</p>
      <h1>可追溯研究工作台</h1>
      <p>串联研究问题、保存检索、证据、缺口、替代假设、判断、人工决定、审阅与版本导出。</p>
      <p class="assurance" role="note">
        当前能力仅记录可审计的研究过程与导出清单，不代表真实研究员验收，也不是正式报告或决策建议。
      </p>
    </header>

    <p v-if="error" class="message error" role="alert">{{ error }}</p>
    <p v-if="notice" class="message success" aria-live="polite">{{ notice }}</p>

    <section v-if="!getToken()" class="panel auth-gate">
      <h2>需要登录</h2>
      <p>项目、版本记录和审计事件只保存在认证账户可访问的持久化空间中。</p>
      <a href="/login" class="button-link">前往登录</a>
    </section>

    <template v-else>
      <section class="panel project-picker">
        <div>
          <h2>研究项目</h2>
          <label for="research-project-select">当前项目</label>
          <select
            id="research-project-select"
            :value="project?.id || ''"
            :disabled="loading"
            @change="selectProject($event.target.value)"
          >
            <option value="" disabled>选择项目</option>
            <option v-for="item in projects" :key="item.id" :value="item.id">
              {{ item.title }} · v{{ item.version }} · {{ item.role }}
            </option>
          </select>
        </div>
        <form class="create-project" @submit.prevent="createProject">
          <h3>新建项目</h3>
          <label>标题<input v-model="createForm.title" required minlength="2" /></label>
          <label>国家代码（最多 3 个，逗号分隔）<input v-model="createForm.countries" /></label>
          <label>范围说明<textarea v-model="createForm.description" rows="2"></textarea></label>
          <label>创建原因<input v-model="createForm.reason" required minlength="2" /></label>
          <button :disabled="loading">创建并持久化</button>
        </form>
      </section>

      <template v-if="project">
        <section class="panel project-summary">
          <div>
            <p class="eyebrow">{{ role }} · 项目版本 {{ project.version }}</p>
            <h2>{{ project.title }}</h2>
            <p>{{ project.description || '未填写范围说明。' }}</p>
          </div>
          <div class="gate" :class="{ ready: readiness.ready }">
            <strong>{{ readiness.ready ? '导出门禁已满足' : '导出门禁未满足' }}</strong>
            <span v-if="!readiness.ready">{{ readiness.reasonCodes.length }} 项待完成</span>
          </div>
        </section>

        <ol class="stage-grid" aria-label="研究工作流阶段">
          <li v-for="stage in stages" :key="stage.id" :class="stage.status">
            <span>{{ stage.label }}</span
            ><strong>{{ stage.count }}</strong>
          </li>
        </ol>

        <section class="panel" aria-labelledby="saved-search-monitoring-heading">
          <h2 id="saved-search-monitoring-heading">保存检索监测边界</h2>
          <template v-if="savedSearchMonitoring.contractState === 'contract_validated'">
            <p>当前仅核验 {{ savedSearchMonitoring.itemCount }} 条保存检索的只读状态，不会重放查询。</p>
            <ul>
              <li>计划运行：未配置</li>
              <li>checkpoint：未建立</li>
              <li>差异语义：未建立</li>
              <li>仅看新增：不可用</li>
              <li>通知投递：未配置</li>
            </ul>
          </template>
          <p v-else role="status">
            监测状态契约不可用；不会据此假定后台运行、增量结果或通知已经存在。
          </p>
        </section>

        <section
          v-if="permissions.canEdit || permissions.canPeerReview"
          class="panel mutation-reason"
        >
          <label for="research-change-reason">本次变化原因</label>
          <input
            id="research-change-reason"
            v-model="changeReason"
            required
            minlength="2"
            placeholder="每次写入必须说明原因"
          />
        </section>

        <section v-if="permissions.canEdit" class="composer-grid" aria-label="项目负责人编辑区">
          <form class="panel" @submit.prevent="setMember">
            <h3>成员与 RBAC</h3>
            <label>用户名<input v-model="memberForm.username" required /></label>
            <label
              >角色<select v-model="memberForm.role">
                <option>reviewer</option>
                <option>reader</option>
              </select></label
            >
            <button :disabled="loading">保存角色</button>
          </form>

          <form class="panel" @submit.prevent="addQuestion">
            <h3>研究问题</h3>
            <label
              >问题<textarea v-model="question" required minlength="4" rows="4"></textarea>
            </label>
            <button :disabled="loading">记录问题</button>
          </form>

          <form class="panel" @submit.prevent="addSearch">
            <h3>保存检索</h3>
            <label>名称<input v-model="searchForm.name" required /></label>
            <label>检索式<textarea v-model="searchForm.query" required rows="3"></textarea></label>
            <label>过滤 JSON<textarea v-model="searchForm.filters" rows="3"></textarea></label>
            <fieldset class="snapshot-fields">
              <legend>可选：关联已捕获的查询执行收据</legend>
              <p>
                必须先在<a href="/data-service/data-search">搜索页</a>显式捕获查询快照；
                research 只读核验，不会隐式捕获、重放查询或读取正文。以下四项必须一起填写。
              </p>
              <label
                >Search snapshot ID<input
                  v-model="searchForm.search_snapshot_id"
                  placeholder="search-snap-20260809T010203000000Z-0123456789abcdef"
              /></label>
              <label
                >Query receipt SHA-256<input
                  v-model="searchForm.query_receipt_sha256"
                  pattern="[0-9a-f]{64}"
              /></label>
              <label
                >Normalized contract SHA-256<input
                  v-model="searchForm.normalized_contract_sha256"
                  pattern="[0-9a-f]{64}"
              /></label>
              <label
                >Ordered result IDs SHA-256<input
                  v-model="searchForm.ordered_returned_ids_sha256"
                  pattern="[0-9a-f]{64}"
              /></label>
            </fieldset>
            <button :disabled="loading">保存查询合同</button>
          </form>

          <form class="panel" @submit.prevent="addEvidence">
            <h3>证据项</h3>
            <label
              >关系<select v-model="evidenceForm.relation">
                <option>support</option>
                <option>opposing</option>
                <option>background</option>
              </select></label
            >
            <label
              >摘要<textarea v-model="evidenceForm.summary" required rows="3"></textarea>
            </label>
            <label>来源 ID<input v-model="evidenceForm.source_id" required /></label>
            <label>来源标题<input v-model="evidenceForm.source_title" /></label>
            <label>来源 URL<input v-model="evidenceForm.source_url" type="url" /></label>
            <label>原文锚点<input v-model="evidenceForm.original_anchor" /></label>
            <fieldset class="snapshot-fields">
              <legend>可选：关联既有证据快照</legend>
              <p>
                以下五项必须一起填写。服务端只读核验 article、snapshot 与
                hash；不会自动抓取或创建快照。
              </p>
              <label
                >Article ID<input v-model="evidenceForm.article_id" type="number" min="1"
              /></label>
              <label
                >Evidence snapshot ID<input v-model="evidenceForm.evidence_snapshot_id"
              /></label>
              <label
                >内容 SHA-256<input v-model="evidenceForm.content_sha256" pattern="[0-9a-f]{64}"
              /></label>
              <label
                >首次抓取时间<input
                  v-model="evidenceForm.captured_at"
                  placeholder="2026-08-09T08:00:00.123456Z"
              /></label>
              <label>解析器版本<input v-model="evidenceForm.parser_version" /></label>
            </fieldset>
            <button :disabled="loading">记录证据</button>
          </form>

          <form class="panel" @submit.prevent="addGap">
            <h3>信息缺口</h3>
            <label>缺口<textarea v-model="gapForm.description" required rows="3"></textarea></label>
            <label>影响<textarea v-model="gapForm.impact" required rows="2"></textarea></label>
            <label>补证计划<textarea v-model="gapForm.resolution_plan" rows="2"></textarea></label>
            <button :disabled="loading">记录缺口</button>
          </form>

          <form class="panel" @submit.prevent="addHypothesis">
            <h3>替代假设</h3>
            <label
              >假设<textarea v-model="hypothesisForm.statement" required rows="3"></textarea>
            </label>
            <label
              >区分性证据<textarea
                v-model="hypothesisForm.discriminating_evidence"
                rows="3"
              ></textarea>
            </label>
            <button :disabled="loading">记录替代假设</button>
          </form>

          <form class="panel wide" @submit.prevent="addJudgment">
            <h3>分析判断</h3>
            <label
              >判断<textarea v-model="judgmentForm.statement" required rows="3"></textarea>
            </label>
            <label
              >支持证据 ID（逗号分隔）<input v-model="judgmentForm.supporting" required
            /></label>
            <label>反方证据 ID（逗号分隔）<input v-model="judgmentForm.opposing" /></label>
            <label>信息缺口 ID（逗号分隔）<input v-model="judgmentForm.gaps" /></label>
            <label>替代假设 ID（逗号分隔）<input v-model="judgmentForm.hypotheses" /></label>
            <label
              >不确定性<textarea v-model="judgmentForm.uncertainty" required rows="3"></textarea>
            </label>
            <button :disabled="loading">记录判断</button>
          </form>

          <form class="panel" @submit.prevent="addDecision">
            <h3>人工决定</h3>
            <label>判断 ID<input v-model="decisionForm.judgment_id" required /></label>
            <label
              >决定<select v-model="decisionForm.decision">
                <option>confirm</option>
                <option>modify</option>
                <option>reject</option>
              </select></label
            >
            <label
              >理由<textarea v-model="decisionForm.rationale" required rows="3"></textarea>
            </label>
            <label v-if="decisionForm.decision === 'modify'"
              >修改后判断<textarea v-model="decisionForm.modified" required rows="3"></textarea>
            </label>
            <button :disabled="loading">记录人工决定</button>
          </form>
        </section>

        <section v-if="permissions.canEdit || permissions.canPeerReview" class="composer-grid">
          <form class="panel" @submit.prevent="addReview">
            <h3>{{ permissions.canPeerReview ? '同行审阅' : '负责人批准' }}</h3>
            <template v-if="permissions.canPeerReview">
              <input v-model="reviewForm.review_type" type="hidden" value="peer_review" />
              <input v-model="reviewForm.target_type" type="hidden" value="decision" />
            </template>
            <template v-else>
              <p><strong>记录类型：</strong>approval · decision</p>
            </template>
            <label>目标 ID<input v-model="reviewForm.target_id" required /></label>
            <label
              >结果<select v-model="reviewForm.outcome">
                <option>approved</option>
                <option>changes_requested</option>
                <option>rejected</option>
              </select></label
            >
            <label
              >审阅意见<textarea v-model="reviewForm.comment" required rows="3"></textarea>
            </label>
            <button :disabled="loading">保存审阅记录</button>
          </form>

          <form v-if="permissions.canEdit" class="panel" @submit.prevent="createExport">
            <h3>版本化导出清单</h3>
            <label>报告标题<input v-model="exportForm.report_title" required /></label>
            <label
              >数据截止时间<input v-model="exportForm.cutoff_at" type="datetime-local" required
            /></label>
            <label
              >截止口径<textarea v-model="exportForm.cutoff_basis" required rows="2"></textarea>
            </label>
            <label>方法<textarea v-model="exportForm.method" required rows="3"></textarea></label>
            <label
              >不确定性<textarea v-model="exportForm.uncertainty" required rows="3"></textarea>
            </label>
            <button :disabled="loading || !readiness.ready">创建 manifest</button>
          </form>
        </section>

        <section class="panel artifact-panel" aria-labelledby="research-artifact-heading">
          <p class="eyebrow">V1.5 · Deterministic artifacts</p>
          <h2 id="research-artifact-heading">下载已持久化报告</h2>
          <p>
            JSON、Markdown 与无脚本 HTML 均只由对应 manifest 确定性生成；CSV
            仅提供同一 manifest 的证据/引用清单。所有格式都不新增事实或无引用 AI
            文本；状态仍为 reviewed draft，researcher acceptance unavailable，且不代表
            Word、PDF、PPT 或正式报告已完成。HTML 上限 16 MiB；CSV 上限 8 MiB、5000
            条证据，超限会失败而不会静默截断。
          </p>
          <p>
            来源定位地址不等于复用许可；当前 manifest 未保存许可证据，因此每条引用的许可状态未知，
            不代表已获转载或再分发批准。字段选择只接受受控白名单；引用、草稿水印和许可边界始终保留，
            用户身份、来源内部备注、原始查询/筛选值、决定理由和审阅意见等敏感字段始终排除。
          </p>
          <fieldset class="artifact-field-selector" :disabled="Boolean(artifactDownloadKey)">
            <legend>选择补充导出字段</legend>
            <label v-for="field in RESEARCH_ARTIFACT_OPTIONAL_FIELDS" :key="field">
              <input v-model="selectedArtifactFields" type="checkbox" :value="field" />
              <span>{{ artifactFieldLabels[field] }}</span>
            </label>
          </fieldset>
          <p v-if="selectedArtifactFields.length === 0" class="empty-comparison" role="status">
            至少选择一个白名单字段后才能下载；系统不会把空选择解释为全部字段。
          </p>
          <p v-if="project.export_manifests.length === 0" class="empty-comparison">
            尚无通过工作流门禁的已持久化 manifest，因此没有可下载 artifact。
          </p>
          <ul v-else class="artifact-list">
            <li v-for="manifest in project.export_manifests" :key="manifest.manifest_id">
              <div>
                <strong>manifest v{{ manifest.export_version }} · {{ manifest.report_title }}</strong>
                <span>
                  {{ manifest.assurance.publication_status }} · researcher acceptance
                  {{ manifest.assurance.researcher_acceptance }}
                </span>
                <code>{{ manifest.integrity_sha256 }}</code>
              </div>
              <div class="artifact-actions">
                <button
                  v-for="format in RESEARCH_ARTIFACT_FORMATS"
                  :key="format"
                  type="button"
                  :disabled="Boolean(artifactDownloadKey) || selectedArtifactFields.length === 0"
                  :aria-label="`下载 manifest v${manifest.export_version} ${artifactFormatLabel(format)} artifact`"
                  @click="downloadExportArtifact(manifest, format)"
                >
                  {{
                    artifactDownloadKey === `${manifest.export_version}:${format}`
                      ? '下载中…'
                      : `下载 ${artifactFormatLabel(format)}`
                  }}
                </button>
              </div>
            </li>
          </ul>
        </section>

        <section class="panel comparison-panel" aria-labelledby="research-comparison-heading">
          <div class="comparison-heading">
            <div>
              <p class="eyebrow">V2 · Persisted manifests</p>
              <h2 id="research-comparison-heading">版本比较</h2>
              <p>
                仅项目 ACL 内的 owner、reviewer、reader
                可读取正文差异；比较读取本身不会写入审计事件。
              </p>
            </div>
            <form class="comparison-controls" @submit.prevent="compareVersions">
              <label
                >基准版本
                <select v-model.number="compareFrom" :disabled="exportVersions.length < 2">
                  <option
                    v-for="version in exportVersions"
                    :key="`from-${version}`"
                    :value="version"
                  >
                    manifest v{{ version }}
                  </option>
                </select>
              </label>
              <label
                >目标版本
                <select v-model.number="compareTo" :disabled="exportVersions.length < 2">
                  <option v-for="version in exportVersions" :key="`to-${version}`" :value="version">
                    manifest v{{ version }}
                  </option>
                </select>
              </label>
              <button :disabled="loading || !canCompareVersions">比较已持久化版本</button>
            </form>
          </div>

          <p v-if="exportVersions.length < 2" class="empty-comparison">
            至少创建两个版本化 manifest 后才能比较；系统不会虚构历史版本。
          </p>
          <template v-else-if="comparison">
            <p class="comparison-summary" aria-live="polite">
              manifest v{{ comparison.from_export.export_version }} → v{{
                comparison.to_export.export_version
              }}：新增 {{ comparison.summary.added }}、移除 {{ comparison.summary.removed }}、修改
              {{ comparison.summary.modified }}。
            </p>
            <p v-if="changedComparisonCategories.length === 0" class="empty-comparison">
              这两个 manifest 在合同覆盖字段上没有差异。
            </p>
            <details
              v-for="category in changedComparisonCategories"
              :key="category.id"
              class="comparison-category"
            >
              <summary>
                {{ comparisonCategoryLabel(category.id) }} ·
                {{ comparisonChangeCount(category) }} 项变化
              </summary>
              <div class="comparison-changes">
                <article v-for="entry in category.added" :key="`added-${entry.id}`">
                  <strong>新增 · {{ entry.id }}</strong>
                  <pre>{{ formattedDiffValue(entry.value) }}</pre>
                </article>
                <article v-for="entry in category.removed" :key="`removed-${entry.id}`">
                  <strong>移除 · {{ entry.id }}</strong>
                  <pre>{{ formattedDiffValue(entry.value) }}</pre>
                </article>
                <article v-for="entry in category.modified" :key="`modified-${entry.id}`">
                  <strong>修改 · {{ entry.id }} · {{ entry.changed_fields.join('、') }}</strong>
                  <div class="before-after">
                    <div>
                      <span>之前</span>
                      <pre>{{ formattedDiffValue(entry.before) }}</pre>
                    </div>
                    <div>
                      <span>之后</span>
                      <pre>{{ formattedDiffValue(entry.after) }}</pre>
                    </div>
                  </div>
                </article>
              </div>
            </details>
          </template>
        </section>

        <section class="panel evidence-index">
          <h2>可引用资源 ID</h2>
          <dl>
            <template v-for="item in project.evidence_items" :key="item.id">
              <dt>{{ item.relation }} · {{ item.id }}</dt>
              <dd>
                {{ item.source_id }} · {{ item.provenance_status }} · snapshot
                {{ item.snapshot_status }}
                <span v-if="item.evidence_snapshot_id"> · {{ item.evidence_snapshot_id }}</span>
              </dd>
            </template>
            <template v-for="item in project.judgments" :key="item.id">
              <dt>judgment · {{ item.id }}</dt>
              <dd>{{ item.statement }}</dd>
            </template>
            <template v-for="item in project.human_decisions" :key="item.id">
              <dt>decision · {{ item.id }}</dt>
              <dd>{{ item.decision }}</dd>
            </template>
          </dl>
        </section>
      </template>
    </template>
  </main>
</template>

<style scoped>
.research-workspace {
  max-width: 1240px;
  margin: 0 auto;
  padding: 32px 20px 72px;
  color: #17233a;
  background: #f4f7fb;
  min-height: 100vh;
}
.research-hero {
  padding: 30px;
  border-radius: 22px;
  color: #fff;
  background: linear-gradient(135deg, #102a43, #1f5f78);
}
.research-hero h1 {
  margin: 4px 0 10px;
  font-size: clamp(28px, 4vw, 46px);
}
.eyebrow {
  margin: 0;
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}
.assurance {
  margin-top: 18px;
  padding: 12px 14px;
  border: 1px solid #9ac4d4;
  border-radius: 12px;
  background: rgba(4, 24, 39, 0.35);
}
.panel {
  margin-top: 18px;
  padding: 20px;
  border: 1px solid #d4deea;
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 8px 28px rgba(16, 42, 67, 0.06);
}
.project-picker,
.project-summary {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
}
.project-summary {
  align-items: center;
}
.create-project {
  display: grid;
  gap: 10px;
}
label {
  display: grid;
  gap: 6px;
  font-size: 14px;
  font-weight: 700;
}
input,
select,
textarea {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #9fb0c3;
  border-radius: 9px;
  padding: 10px 12px;
  color: inherit;
  background: #fff;
  font: inherit;
}
input:focus,
select:focus,
textarea:focus,
button:focus-visible,
a:focus-visible {
  outline: 3px solid #f6ae2d;
  outline-offset: 2px;
}
button,
.button-link {
  min-height: 44px;
  border: 0;
  border-radius: 10px;
  padding: 10px 16px;
  color: #fff;
  background: #176b87;
  font: inherit;
  font-weight: 800;
  cursor: pointer;
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.button-link {
  display: inline-flex;
  align-items: center;
  margin-top: 10px;
  text-decoration: none;
}
.stage-grid {
  display: grid;
  grid-template-columns: repeat(9, minmax(92px, 1fr));
  gap: 8px;
  margin: 18px 0;
  padding: 0;
  list-style: none;
}
.stage-grid li {
  min-height: 74px;
  padding: 12px;
  border: 1px solid #cbd8e6;
  border-radius: 12px;
  background: #fff;
}
.stage-grid li.recorded {
  border-color: #23856d;
  background: #eefaf5;
}
.stage-grid span,
.stage-grid strong {
  display: block;
}
.stage-grid strong {
  margin-top: 8px;
  font-size: 22px;
}
.gate {
  justify-self: end;
  padding: 14px;
  border-radius: 12px;
  color: #7c2d12;
  background: #fff0e8;
}
.gate.ready {
  color: #14532d;
  background: #eaf8ef;
}
.gate span {
  display: block;
  margin-top: 4px;
}
.mutation-reason {
  position: sticky;
  z-index: 2;
  top: 8px;
  border-color: #f6ae2d;
}
.composer-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}
.composer-grid .panel {
  display: grid;
  align-content: start;
  gap: 12px;
  margin-top: 0;
}
.composer-grid .wide {
  grid-column: 1 / -1;
}
.snapshot-fields {
  display: grid;
  gap: 10px;
  margin: 0;
  border: 1px solid #b8c8d8;
  border-radius: 12px;
  padding: 12px;
}
.snapshot-fields legend {
  padding: 0 6px;
  font-weight: 800;
}
.snapshot-fields p {
  margin: 0;
  color: #52677d;
  font-size: 13px;
}
.artifact-field-selector {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin: 16px 0;
  border: 1px solid #b8c8d8;
  border-radius: 12px;
  padding: 12px;
}
.artifact-field-selector legend {
  padding: 0 6px;
  font-weight: 800;
}
.artifact-field-selector label {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  min-width: 0;
}
.artifact-field-selector input {
  flex: 0 0 auto;
  margin-top: 3px;
}
.artifact-list {
  display: grid;
  gap: 12px;
  margin: 18px 0 0;
  padding: 0;
  list-style: none;
}
.artifact-list li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 18px;
  align-items: center;
  border: 1px solid #cbd8e6;
  border-radius: 12px;
  padding: 14px;
}
.artifact-list strong,
.artifact-list span,
.artifact-list code {
  display: block;
}
.artifact-list span {
  margin-top: 4px;
  color: #52677d;
}
.artifact-list code {
  margin-top: 8px;
  overflow-wrap: anywhere;
  font-size: 12px;
}
.artifact-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.comparison-heading {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(360px, 0.8fr);
  gap: 24px;
  align-items: end;
}
.comparison-heading h2 {
  margin: 4px 0 8px;
}
.comparison-controls {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.comparison-controls button {
  grid-column: 1 / -1;
}
.comparison-summary,
.empty-comparison {
  margin: 18px 0 0;
  border-radius: 10px;
  padding: 12px 14px;
  background: #eef5fa;
}
.comparison-category {
  margin-top: 12px;
  border: 1px solid #cbd8e6;
  border-radius: 12px;
  padding: 12px;
}
.comparison-category summary {
  cursor: pointer;
  font-weight: 800;
}
.comparison-changes,
.before-after {
  display: grid;
  gap: 12px;
}
.comparison-changes {
  margin-top: 12px;
}
.comparison-changes article {
  min-width: 0;
  border-left: 4px solid #176b87;
  padding-left: 12px;
}
.before-after {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-top: 8px;
}
.before-after span {
  font-size: 12px;
  font-weight: 800;
}
.comparison-changes pre {
  max-height: 320px;
  overflow: auto;
  margin: 6px 0 0;
  border-radius: 8px;
  padding: 10px;
  color: #dcecff;
  background: #102a43;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.message {
  padding: 12px 16px;
  border-radius: 10px;
}
.error {
  color: #8f1d1d;
  background: #fff0f0;
}
.success {
  color: #14532d;
  background: #eaf8ef;
}
.evidence-index dl {
  display: grid;
  grid-template-columns: minmax(250px, 0.8fr) 1fr;
  gap: 8px 18px;
}
.evidence-index dt {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  font-weight: 800;
  overflow-wrap: anywhere;
}
.evidence-index dd {
  margin: 0;
}
@media (max-width: 900px) {
  .project-picker,
  .project-summary,
  .composer-grid,
  .comparison-heading,
  .artifact-field-selector,
  .artifact-list li {
    grid-template-columns: 1fr;
  }
  .stage-grid {
    grid-template-columns: repeat(3, 1fr);
  }
  .gate {
    justify-self: stretch;
  }
}
@media (max-width: 520px) {
  .research-workspace {
    padding: 16px 12px 52px;
  }
  .research-hero,
  .panel {
    padding: 16px;
  }
  .stage-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .evidence-index dl {
    grid-template-columns: 1fr;
  }
  .evidence-index dd {
    margin-bottom: 10px;
  }
  .comparison-controls,
  .before-after {
    grid-template-columns: 1fr;
  }
  .comparison-controls button {
    grid-column: auto;
  }
}
</style>
