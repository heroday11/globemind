export const RESEARCH_PROJECT_SCHEMA = 'research-project-v1'
export const RESEARCH_COMPARISON_SCHEMA = 'research-version-comparison-v1'
export const SEARCH_SNAPSHOT_LINK_FIELDS = Object.freeze([
  'search_snapshot_id',
  'query_receipt_sha256',
  'normalized_contract_sha256',
  'ordered_returned_ids_sha256',
])

export const WORKFLOW_STAGES = Object.freeze([
  { id: 'question', label: '研究问题', collection: 'research_questions' },
  { id: 'search', label: '保存检索', collection: 'saved_searches' },
  { id: 'evidence', label: '证据包', collection: 'evidence_items' },
  { id: 'gap', label: '信息缺口', collection: 'information_gaps' },
  { id: 'hypothesis', label: '替代假设', collection: 'alternative_hypotheses' },
  { id: 'judgment', label: '分析判断', collection: 'judgments' },
  { id: 'decision', label: '人工决定', collection: 'human_decisions' },
  { id: 'review', label: '审阅批准', collection: 'reviews' },
  { id: 'export', label: '版本导出', collection: 'export_manifests' },
])

const EMPTY_ARRAY = Object.freeze([])
const SHA256_RE = /^[0-9a-f]{64}$/
const SEARCH_SNAPSHOT_ID_RE = /^search-snap-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{16}$/

export function buildSavedSearchValues(form, filters) {
  const result = {
    name: String(form?.name || '').trim(),
    query: String(form?.query || '').trim(),
    filters,
  }
  const reference = Object.fromEntries(
    SEARCH_SNAPSHOT_LINK_FIELDS.map((field) => [field, String(form?.[field] || '').trim()]),
  )
  const populated = SEARCH_SNAPSHOT_LINK_FIELDS.filter((field) => reference[field])
  if (populated.length === 0) return result
  if (populated.length !== SEARCH_SNAPSHOT_LINK_FIELDS.length) {
    throw new TypeError('查询快照 ID 与三个 SHA-256 必须一起填写')
  }
  if (!SEARCH_SNAPSHOT_ID_RE.test(reference.search_snapshot_id)) {
    throw new TypeError('查询快照 ID 格式无效')
  }
  for (const field of SEARCH_SNAPSHOT_LINK_FIELDS.slice(1)) {
    if (!SHA256_RE.test(reference[field])) throw new TypeError('查询快照 SHA-256 格式无效')
  }
  return { ...result, ...reference }
}

export function normalizeResearchProject(payload) {
  if (!payload || payload.schema_version !== RESEARCH_PROJECT_SCHEMA) {
    return Object.freeze({
      available: false,
      reason: 'RESEARCH_PROJECT_CONTRACT_UNAVAILABLE',
      project: null,
    })
  }
  const project = { ...payload }
  for (const stage of WORKFLOW_STAGES) {
    project[stage.collection] = Array.isArray(payload[stage.collection])
      ? payload[stage.collection]
      : EMPTY_ARRAY
  }
  project.members = Array.isArray(payload.members) ? payload.members : EMPTY_ARRAY
  project.change_history = Array.isArray(payload.change_history)
    ? payload.change_history
    : EMPTY_ARRAY
  project.audit_events = Array.isArray(payload.audit_events) ? payload.audit_events : EMPTY_ARRAY
  return Object.freeze({ available: true, reason: null, project: Object.freeze(project) })
}

export function projectRole(project, username) {
  const member = project?.members?.find?.((item) => item?.username === username)
  return ['owner', 'reviewer', 'reader'].includes(member?.role) ? member.role : null
}

export function workflowPermissions(role) {
  return Object.freeze({
    canRead: ['owner', 'reviewer', 'reader'].includes(role),
    canEdit: role === 'owner',
    canPeerReview: role === 'reviewer',
    canReadAudit: role === 'owner' || role === 'reviewer',
  })
}

export function workflowStageState(project, stage) {
  const count = Array.isArray(project?.[stage.collection]) ? project[stage.collection].length : 0
  return Object.freeze({ ...stage, count, status: count > 0 ? 'recorded' : 'missing' })
}

export function workflowStageStates(project) {
  return WORKFLOW_STAGES.map((stage) => workflowStageState(project, stage))
}

export function exportReadiness(project) {
  const reasons = []
  const required = [
    ['research_questions', 'RESEARCH_QUESTION_MISSING'],
    ['saved_searches', 'SAVED_SEARCH_MISSING'],
    ['information_gaps', 'INFORMATION_GAP_MISSING'],
    ['alternative_hypotheses', 'ALTERNATIVE_HYPOTHESIS_MISSING'],
    ['judgments', 'JUDGMENT_MISSING'],
  ]
  for (const [collection, code] of required) {
    if (!project?.[collection]?.length) reasons.push(code)
  }
  const relations = new Set((project?.evidence_items || []).map((item) => item?.relation))
  if (!relations.has('support')) reasons.push('SUPPORTING_EVIDENCE_MISSING')
  if (!relations.has('opposing')) reasons.push('OPPOSING_EVIDENCE_MISSING')
  if (!relations.has('background')) reasons.push('BACKGROUND_EVIDENCE_MISSING')

  const linkedJudgmentIds = new Set(
    (project?.judgments || [])
      .filter(
        (judgment) =>
          judgment?.supporting_evidence_ids?.length &&
          judgment?.opposing_evidence_ids?.length &&
          judgment?.information_gap_ids?.length &&
          judgment?.alternative_hypothesis_ids?.length,
      )
      .map((judgment) => judgment.id),
  )
  if (project?.judgments?.length && linkedJudgmentIds.size === 0) {
    reasons.push('JUDGMENT_ANALYTIC_LINKS_INCOMPLETE')
  }

  const latestDecisions = new Map()
  for (const decision of project?.human_decisions || []) {
    latestDecisions.set(decision?.judgment_id, decision)
  }
  const latestReview = (reviewType, targetType, targetId) =>
    [...(project?.reviews || [])]
      .reverse()
      .find(
        (review) =>
          review?.review_type === reviewType &&
          review?.target_type === targetType &&
          review?.target_id === targetId,
      )
  const approvedChain = [...latestDecisions.values()].some((decision) => {
    if (
      !['confirm', 'modify'].includes(decision?.decision) ||
      !linkedJudgmentIds.has(decision?.judgment_id)
    ) {
      return false
    }
    const peerReview = latestReview('peer_review', 'decision', decision?.id)
    const approval = latestReview('approval', 'decision', decision?.id)
    return peerReview?.outcome === 'approved' && approval?.outcome === 'approved'
  })
  if (!approvedChain) reasons.push('APPROVED_HUMAN_DECISION_CHAIN_MISSING')
  return Object.freeze({ ready: reasons.length === 0, reasonCodes: Object.freeze(reasons) })
}

export function versionedMutation(project, reason, values = {}) {
  const version = Number(project?.version)
  if (!Number.isInteger(version) || version < 1) {
    throw new TypeError('A persisted project version is required')
  }
  const normalizedReason = String(reason || '').trim()
  if (normalizedReason.length < 2) throw new TypeError('A change reason is required')
  return { ...values, expected_version: version, reason: normalizedReason }
}

export function latestResource(project, collection) {
  const rows = project?.[collection]
  return Array.isArray(rows) && rows.length ? rows.at(-1) : null
}

export function normalizeVersionComparison(payload) {
  const validSummary = ['added', 'removed', 'modified'].every(
    (field) => Number.isInteger(payload?.summary?.[field]) && payload.summary[field] >= 0,
  )
  const validEndpoints = [payload?.from_export, payload?.to_export].every(
    (endpoint) => Number.isInteger(endpoint?.export_version) && endpoint.export_version >= 1,
  )
  const validCategories =
    Array.isArray(payload?.categories) &&
    payload.categories.every(
      (category) =>
        typeof category?.id === 'string' &&
        category.id.length > 0 &&
        Array.isArray(category.added) &&
        Array.isArray(category.removed) &&
        Array.isArray(category.modified),
    )
  if (
    !payload ||
    payload.schema_version !== RESEARCH_COMPARISON_SCHEMA ||
    typeof payload.project_id !== 'string' ||
    payload.project_id.length === 0 ||
    !validSummary ||
    !validEndpoints ||
    !validCategories ||
    payload.access?.content_visibility !== 'project-acl' ||
    payload.access?.source_manifests_persisted !== true
  ) {
    return Object.freeze({
      available: false,
      reason: 'RESEARCH_VERSION_COMPARISON_UNAVAILABLE',
      comparison: null,
    })
  }
  const categories = payload.categories.map((category) =>
    Object.freeze({
      id: category.id,
      added: category.added,
      removed: category.removed,
      modified: category.modified,
    }),
  )
  return Object.freeze({
    available: true,
    reason: null,
    comparison: Object.freeze({ ...payload, categories: Object.freeze(categories) }),
  })
}

export function comparisonChangeCount(category) {
  return (
    (category?.added?.length || 0) +
    (category?.removed?.length || 0) +
    (category?.modified?.length || 0)
  )
}
