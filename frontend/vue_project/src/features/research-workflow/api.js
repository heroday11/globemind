import { API_PREFIX } from '../../config/api.js'
import { getToken } from '../../utils/auth.js'

export const RESEARCH_ARTIFACT_SCHEMA = 'research-export-artifact-v3'
export const RESEARCH_FIELD_SELECTION_SCHEMA = 'research-export-field-selection-v1'
export const RESEARCH_ARTIFACT_FORMATS = Object.freeze(['json', 'markdown', 'html', 'csv'])
export const RESEARCH_ARTIFACT_OPTIONAL_FIELDS = Object.freeze([
  'project_scope',
  'cutoff',
  'method',
  'uncertainty',
  'research_questions',
  'saved_search_receipts',
  'evidence_summaries',
  'information_gaps',
  'alternative_hypotheses',
  'judgments',
  'human_decisions',
  'review_outcomes',
])
export const RESEARCH_ARTIFACT_DEFAULT_FIELDS = Object.freeze([
  ...RESEARCH_ARTIFACT_OPTIONAL_FIELDS,
])
const RESEARCH_ARTIFACT_FORMAT_CONTRACTS = Object.freeze({
  json: Object.freeze({
    extension: '.json',
    mediaType: 'application/json',
    maximumBytes: 64 * 1024 * 1024,
  }),
  markdown: Object.freeze({
    extension: '.md',
    mediaType: 'text/markdown',
    maximumBytes: 64 * 1024 * 1024,
  }),
  html: Object.freeze({
    extension: '.html',
    mediaType: 'text/html',
    maximumBytes: 16 * 1024 * 1024,
  }),
  csv: Object.freeze({
    extension: '.csv',
    mediaType: 'text/csv',
    maximumBytes: 8 * 1024 * 1024,
  }),
})
const SHA256_RE = /^[0-9a-f]{64}$/
const SAFE_ARTIFACT_DISPOSITION =
  /^attachment; filename="([A-Za-z0-9][A-Za-z0-9._-]{0,159})"$/
const ARTIFACT_CSP =
  "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; sandbox"

export function normalizeResearchArtifactFields(fields = RESEARCH_ARTIFACT_DEFAULT_FIELDS) {
  if (!Array.isArray(fields) || fields.length === 0) {
    throw new ResearchWorkflowApiError('报告 artifact 字段选择无效', {
      code: 'ARTIFACT_FIELDS_INVALID',
    })
  }
  if (fields.length > RESEARCH_ARTIFACT_OPTIONAL_FIELDS.length) {
    throw new ResearchWorkflowApiError('报告 artifact 字段选择无效', {
      code: 'ARTIFACT_FIELDS_INVALID',
    })
  }
  const selected = new Set()
  for (const field of fields) {
    if (
      typeof field !== 'string'
      || !RESEARCH_ARTIFACT_OPTIONAL_FIELDS.includes(field)
      || selected.has(field)
    ) {
      throw new ResearchWorkflowApiError('报告 artifact 字段选择无效', {
        code: 'ARTIFACT_FIELDS_INVALID',
      })
    }
    selected.add(field)
  }
  return Object.freeze(
    RESEARCH_ARTIFACT_OPTIONAL_FIELDS.filter((field) => selected.has(field)),
  )
}

async function sha256ArtifactBlob(blob) {
  const subtle = globalThis.crypto?.subtle
  if (typeof blob?.arrayBuffer !== 'function' || typeof subtle?.digest !== 'function') {
    throw new Error('Web Crypto SHA-256 is unavailable')
  }
  const bytes = await blob.arrayBuffer()
  if (!(bytes instanceof ArrayBuffer) || bytes.byteLength !== blob.size) {
    throw new Error('Artifact blob bytes are unavailable')
  }
  const digest = new Uint8Array(await subtle.digest('SHA-256', bytes))
  if (digest.byteLength !== 32) throw new Error('Web Crypto returned an invalid SHA-256 digest')
  return Array.from(digest, (value) => value.toString(16).padStart(2, '0')).join('')
}

function sha256Equal(left, right) {
  if (!SHA256_RE.test(left) || !SHA256_RE.test(right)) return false
  let difference = 0
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index)
  }
  return difference === 0
}

export class ResearchWorkflowApiError extends Error {
  constructor(message, { status = 0, code = 'REQUEST_FAILED', detail = null, cause } = {}) {
    super(message, { cause })
    this.name = 'ResearchWorkflowApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }
}

async function responsePayload(response) {
  const contentType = String(response.headers?.get?.('content-type') || '')
    .toLowerCase()
    .split(';', 1)[0]
    .trim()
  if (contentType === 'application/json') return response.json().catch(() => ({}))
  return {}
}

function errorMetadata(payload, fallback) {
  const detail = payload?.detail
  if (detail && typeof detail === 'object') {
    const code = /^[A-Z][A-Z0-9_]{1,63}$/.test(String(detail.code || ''))
      ? String(detail.code)
      : 'HTTP_ERROR'
    const safeDetail = { code }
    for (const field of ['expected_version', 'current_version']) {
      if (Number.isSafeInteger(detail[field]) && detail[field] >= 0) {
        safeDetail[field] = detail[field]
      }
    }
    return {
      message: fallback,
      code,
      detail: safeDetail,
    }
  }
  return {
    message: fallback,
    code: 'HTTP_ERROR',
    detail: null,
  }
}

export function createResearchWorkflowApi({
  fetchImpl = globalThis.fetch,
  apiPrefix = API_PREFIX,
  getAccessToken = getToken,
  computeArtifactSha256 = sha256ArtifactBlob,
} = {}) {
  if (typeof fetchImpl !== 'function') throw new TypeError('Research workflow API requires fetch')
  if (typeof computeArtifactSha256 !== 'function') {
    throw new TypeError('Research workflow API requires an artifact SHA-256 adapter')
  }

  async function request(
    path,
    { method = 'GET', body, signal, fallback = '研究工作流请求失败' } = {},
  ) {
    const token = getAccessToken()
    if (!token) {
      throw new ResearchWorkflowApiError('请先登录后使用研究工作流', {
        status: 401,
        code: 'AUTH_REQUIRED',
      })
    }
    const headers = {
      Accept: 'application/json',
      Authorization: `Bearer ${token}`,
    }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    let response
    try {
      response = await fetchImpl(`${apiPrefix}${path}`, {
        method,
        headers,
        credentials: 'same-origin',
        body: body === undefined ? undefined : JSON.stringify(body),
        signal,
      })
    } catch (error) {
      if (error?.name === 'AbortError') throw error
      throw new ResearchWorkflowApiError(fallback, {
        code: 'NETWORK_ERROR',
      })
    }
    const payload = await responsePayload(response)
    if (!response.ok) {
      const metadata = errorMetadata(payload, fallback)
      throw new ResearchWorkflowApiError(metadata.message, {
        status: response.status,
        code: metadata.code,
        detail: metadata.detail,
      })
    }
    return payload
  }

  async function requestArtifact(
    projectId,
    exportVersion,
    format,
    fields = RESEARCH_ARTIFACT_DEFAULT_FIELDS,
    signal,
  ) {
    const formatContract = RESEARCH_ARTIFACT_FORMAT_CONTRACTS[format]
    if (!formatContract) {
      throw new ResearchWorkflowApiError('不支持的报告 artifact 格式', {
        code: 'ARTIFACT_FORMAT_UNSUPPORTED',
      })
    }
    const selectedFields = normalizeResearchArtifactFields(fields)
    const version = Number(exportVersion)
    if (!Number.isInteger(version) || version < 1) {
      throw new ResearchWorkflowApiError('报告 manifest 版本无效', {
        code: 'ARTIFACT_VERSION_INVALID',
      })
    }
    const token = getAccessToken()
    if (!token) {
      throw new ResearchWorkflowApiError('请先登录后下载报告 artifact', {
        status: 401,
        code: 'AUTH_REQUIRED',
      })
    }
    const query = new URLSearchParams({ format })
    for (const field of selectedFields) query.append('fields', field)
    const path = `${projectPath(projectId, `/exports/${version}/artifact`)}?${query}`
    let response
    try {
      response = await fetchImpl(`${apiPrefix}${path}`, {
        method: 'GET',
        headers: {
          Accept: formatContract.mediaType,
          Authorization: `Bearer ${token}`,
        },
        credentials: 'same-origin',
        signal,
      })
    } catch (error) {
      if (error?.name === 'AbortError') throw error
      throw new ResearchWorkflowApiError('下载报告 artifact 失败', {
        code: 'NETWORK_ERROR',
      })
    }
    if (!response.ok) {
      const payload = await responsePayload(response)
      const metadata = errorMetadata(payload, '下载报告 artifact 失败')
      throw new ResearchWorkflowApiError(metadata.message, {
        status: response.status,
        code: metadata.code,
        detail: metadata.detail,
      })
    }
    const schemaVersion = String(response.headers?.get?.('x-research-artifact-schema') || '')
    const responseFormat = String(response.headers?.get?.('x-research-artifact-format') || '')
    const sha256 = String(response.headers?.get?.('x-research-artifact-sha256') || '')
    const etag = String(response.headers?.get?.('etag') || '')
    const reportContentSha256 = String(
      response.headers?.get?.('x-research-report-content-sha256') || '',
    )
    const manifestSha256 = String(response.headers?.get?.('x-research-manifest-sha256') || '')
    const publicationStatus = String(
      response.headers?.get?.('x-research-publication-status') || '',
    )
    const researcherAcceptance = String(
      response.headers?.get?.('x-researcher-acceptance') || '',
    )
    const distributionStatus = String(
      response.headers?.get?.('x-research-distribution-status') || '',
    )
    const fieldSelectionSchema = String(
      response.headers?.get?.('x-research-field-selection-schema') || '',
    )
    const responseFields = String(response.headers?.get?.('x-research-export-fields') || '')
    const sourceLicenseStatus = String(
      response.headers?.get?.('x-research-source-license-status') || '',
    )
    const cacheControl = String(response.headers?.get?.('cache-control') || '')
    const contentTypeOptions = String(response.headers?.get?.('x-content-type-options') || '')
    const vary = String(response.headers?.get?.('vary') || '')
    const contentSecurityPolicy = String(
      response.headers?.get?.('content-security-policy') || '',
    )
    const disposition = String(response.headers?.get?.('content-disposition') || '')
    const contentType = String(response.headers?.get?.('content-type') || '').toLowerCase()
    const mediaType = contentType.split(';', 1)[0].trim()
    const filenameMatch = SAFE_ARTIFACT_DISPOSITION.exec(disposition)
    if (
      schemaVersion !== RESEARCH_ARTIFACT_SCHEMA ||
      responseFormat !== format ||
      !SHA256_RE.test(sha256) ||
      etag !== `"sha256-${sha256}"` ||
      !SHA256_RE.test(reportContentSha256) ||
      !SHA256_RE.test(manifestSha256) ||
      !filenameMatch ||
      !filenameMatch[1].startsWith('research-reviewed-draft-') ||
      !filenameMatch[1].endsWith(formatContract.extension) ||
      !new RegExp(`-fields-[0-9a-f]{12}\\${formatContract.extension}$`).test(filenameMatch[1]) ||
      publicationStatus !== 'reviewed_draft' ||
      researcherAcceptance !== 'unavailable' ||
      distributionStatus !== 'not_for_publication' ||
      fieldSelectionSchema !== RESEARCH_FIELD_SELECTION_SCHEMA ||
      responseFields !== selectedFields.join(',') ||
      sourceLicenseStatus !== 'unknown' ||
      cacheControl !== 'private, no-store' ||
      contentTypeOptions !== 'nosniff' ||
      vary !== 'Authorization' ||
      (format === 'html' && contentSecurityPolicy !== ARTIFACT_CSP) ||
      mediaType !== formatContract.mediaType ||
      typeof response.blob !== 'function'
    ) {
      throw new ResearchWorkflowApiError('报告 artifact 响应合同不可用', {
        status: response.status,
        code: 'ARTIFACT_RESPONSE_INVALID',
      })
    }
    const blob = await response.blob()
    if (!Number.isSafeInteger(blob?.size) || blob.size <= 0) {
      throw new ResearchWorkflowApiError('报告 artifact 响应大小合同不可用', {
        status: response.status,
        code: 'ARTIFACT_RESPONSE_INVALID',
      })
    }
    if (blob.size > formatContract.maximumBytes) {
      throw new ResearchWorkflowApiError('报告 artifact 响应超出大小边界', {
        status: response.status,
        code: 'ARTIFACT_RESPONSE_TOO_LARGE',
      })
    }
    let computedSha256
    try {
      computedSha256 = await computeArtifactSha256(blob)
    } catch (cause) {
      throw new ResearchWorkflowApiError('报告 artifact 正文完整性无法验证', {
        status: response.status,
        code: 'ARTIFACT_INTEGRITY_UNAVAILABLE',
        cause,
      })
    }
    if (typeof computedSha256 !== 'string' || !sha256Equal(computedSha256, sha256)) {
      throw new ResearchWorkflowApiError('报告 artifact 正文 SHA-256 不匹配', {
        status: response.status,
        code: 'ARTIFACT_INTEGRITY_MISMATCH',
      })
    }
    return Object.freeze({
      schemaVersion,
      format,
      filename: filenameMatch[1],
      sha256,
      reportContentSha256,
      manifestSha256,
      publicationStatus,
      researcherAcceptance,
      distributionStatus,
      selectedFields,
      sourceLicenseStatus,
      blob,
    })
  }

  const projectPath = (projectId, suffix = '') =>
    `/research/projects/${encodeURIComponent(projectId)}${suffix}`

  return Object.freeze({
    storageStatus(signal) {
      return request('/research/storage-status', { signal, fallback: '研究存储状态不可用' })
    },
    listProjects(signal) {
      return request('/research/projects', { signal, fallback: '读取研究项目失败' })
    },
    createProject(payload) {
      return request('/research/projects', {
        method: 'POST',
        body: payload,
        fallback: '创建研究项目失败',
      })
    },
    getProject(projectId, signal) {
      return request(projectPath(projectId), { signal, fallback: '读取研究项目失败' })
    },
    savedSearchMonitoring(projectId, signal) {
      return request(projectPath(projectId, '/saved-search-monitoring'), {
        signal,
        fallback: '保存检索监测状态不可用',
      })
    },
    getAudit(projectId, signal) {
      return request(projectPath(projectId, '/audit'), { signal, fallback: '读取脱敏审计记录失败' })
    },
    setMember(projectId, username, payload) {
      return request(projectPath(projectId, `/members/${encodeURIComponent(username)}`), {
        method: 'PUT',
        body: payload,
        fallback: '更新项目成员失败',
      })
    },
    addQuestion(projectId, payload) {
      return request(projectPath(projectId, '/questions'), { method: 'POST', body: payload })
    },
    addSavedSearch(projectId, payload) {
      return request(projectPath(projectId, '/saved-searches'), { method: 'POST', body: payload })
    },
    addEvidence(projectId, payload) {
      return request(projectPath(projectId, '/evidence'), { method: 'POST', body: payload })
    },
    addInformationGap(projectId, payload) {
      return request(projectPath(projectId, '/information-gaps'), { method: 'POST', body: payload })
    },
    addAlternativeHypothesis(projectId, payload) {
      return request(projectPath(projectId, '/alternative-hypotheses'), {
        method: 'POST',
        body: payload,
      })
    },
    addJudgment(projectId, payload) {
      return request(projectPath(projectId, '/judgments'), { method: 'POST', body: payload })
    },
    addDecision(projectId, payload) {
      return request(projectPath(projectId, '/decisions'), { method: 'POST', body: payload })
    },
    addReview(projectId, payload) {
      return request(projectPath(projectId, '/reviews'), { method: 'POST', body: payload })
    },
    createExportManifest(projectId, payload) {
      return request(projectPath(projectId, '/exports'), { method: 'POST', body: payload })
    },
    getExportManifest(projectId, exportVersion, signal) {
      return request(projectPath(projectId, `/exports/${encodeURIComponent(exportVersion)}`), {
        signal,
        fallback: '读取导出清单失败',
      })
    },
    downloadExportArtifact(projectId, exportVersion, format, fields, signal) {
      return requestArtifact(projectId, exportVersion, format, fields, signal)
    },
    compareExportVersions(projectId, fromExportVersion, toExportVersion, signal) {
      const query = new URLSearchParams({
        from_export_version: String(fromExportVersion),
        to_export_version: String(toExportVersion),
      })
      return request(projectPath(projectId, `/export-comparisons?${query}`), {
        signal,
        fallback: '读取版本比较失败',
      })
    },
  })
}

export const researchWorkflowApi = createResearchWorkflowApi()
