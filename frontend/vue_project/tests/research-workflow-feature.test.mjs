import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'

import {
  RESEARCH_ARTIFACT_DEFAULT_FIELDS,
  RESEARCH_ARTIFACT_FORMATS,
  RESEARCH_ARTIFACT_OPTIONAL_FIELDS,
  RESEARCH_ARTIFACT_SCHEMA,
  RESEARCH_FIELD_SELECTION_SCHEMA,
  ResearchWorkflowApiError,
  SEARCH_SNAPSHOT_LINK_FIELDS,
  buildSavedSearchValues,
  comparisonChangeCount,
  createResearchWorkflowApi,
  exportReadiness,
  normalizeResearchProject,
  normalizeVersionComparison,
  projectRole,
  triggerResearchArtifactDownload,
  versionedMutation,
  workflowPermissions,
  workflowStageStates,
} from '../src/features/research-workflow/index.js'

function response(payload, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    headers: { get: () => 'application/json' },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  }
}

function artifactResponse(format = 'json', overrides = {}, fields = RESEARCH_ARTIFACT_DEFAULT_FIELDS) {
  const contracts = {
    json: { extension: 'json', mediaType: 'application/json' },
    markdown: { extension: 'md', mediaType: 'text/markdown; charset=utf-8' },
    html: { extension: 'html', mediaType: 'text/html; charset=utf-8' },
    csv: { extension: 'csv', mediaType: 'text/csv; charset=utf-8' },
  }
  const contract = contracts[format]
  const filename =
    `research-reviewed-draft-${'1'.repeat(32)}-v1-fields-${'a'.repeat(12)}.${contract.extension}`
  const values = {
    'content-type': contract.mediaType,
    'content-disposition': `attachment; filename="${filename}"`,
    'x-research-artifact-schema': RESEARCH_ARTIFACT_SCHEMA,
    'x-research-artifact-format': format,
    'x-research-artifact-sha256': 'd'.repeat(64),
    etag: `"sha256-${'d'.repeat(64)}"`,
    'x-research-report-content-sha256': 'e'.repeat(64),
    'x-research-manifest-sha256': 'f'.repeat(64),
    'x-research-publication-status': 'reviewed_draft',
    'x-researcher-acceptance': 'unavailable',
    'x-research-distribution-status': 'not_for_publication',
    'x-research-export-fields': fields.join(','),
    'x-research-field-selection-schema': RESEARCH_FIELD_SELECTION_SCHEMA,
    'x-research-source-license-status': 'unknown',
    'cache-control': 'private, no-store',
    'x-content-type-options': 'nosniff',
    vary: 'Authorization',
    ...(format === 'html'
      ? {
          'content-security-policy':
            "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; sandbox",
        }
      : {}),
    ...overrides,
  }
  const blob = { size: 17, format }
  return {
    ok: true,
    status: 200,
    headers: { get: (name) => values[String(name).toLowerCase()] || null },
    blob: async () => blob,
    json: async () => ({}),
    text: async () => '',
  }
}

function projectFixture() {
  return {
    schema_version: 'research-project-v1',
    id: '1'.repeat(32),
    title: 'Pilot research project',
    version: 9,
    members: [
      { username: 'alice', role: 'owner' },
      { username: 'bob', role: 'reviewer' },
      { username: 'carol', role: 'reader' },
    ],
    research_questions: [{ id: 'q1' }],
    saved_searches: [{ id: 's1', snapshot_status: 'unavailable' }],
    evidence_items: [
      { id: 'e1', relation: 'support' },
      { id: 'e2', relation: 'opposing' },
      { id: 'e3', relation: 'background' },
    ],
    information_gaps: [{ id: 'g1' }],
    alternative_hypotheses: [{ id: 'h1' }],
    judgments: [
      {
        id: 'j1',
        supporting_evidence_ids: ['e1'],
        opposing_evidence_ids: ['e2'],
        information_gap_ids: ['g1'],
        alternative_hypothesis_ids: ['h1'],
      },
    ],
    human_decisions: [{ id: 'd1', judgment_id: 'j1', decision: 'confirm' }],
    reviews: [
      {
        id: 'r1',
        review_type: 'peer_review',
        target_type: 'decision',
        target_id: 'd1',
        outcome: 'approved',
      },
      {
        id: 'r2',
        review_type: 'approval',
        target_type: 'decision',
        target_id: 'd1',
        outcome: 'approved',
      },
    ],
    export_manifests: [],
    change_history: [],
    audit_events: [],
  }
}

test('project contract and RBAC helpers fail closed', () => {
  const missing = normalizeResearchProject({ schema_version: 'invented' })
  assert.equal(missing.available, false)
  assert.equal(missing.project, null)

  const fixture = projectFixture()
  const normalized = normalizeResearchProject(fixture)
  assert.equal(normalized.available, true)
  assert.equal(projectRole(normalized.project, 'alice'), 'owner')
  assert.equal(projectRole(normalized.project, 'bob'), 'reviewer')
  assert.equal(projectRole(normalized.project, 'carol'), 'reader')
  assert.equal(projectRole(normalized.project, 'mallory'), null)
  assert.deepEqual(workflowPermissions(null), {
    canRead: false,
    canEdit: false,
    canPeerReview: false,
    canReadAudit: false,
  })
  assert.equal(workflowPermissions('reader').canEdit, false)
  assert.equal(workflowPermissions('reviewer').canPeerReview, true)
  assert.equal(workflowPermissions('owner').canEdit, true)
})

test('stage and export readiness require evidence opposition plus approval chain', () => {
  const fixture = projectFixture()
  const stages = workflowStageStates(fixture)
  assert.equal(stages.length, 9)
  assert.equal(stages.find((stage) => stage.id === 'question').status, 'recorded')
  assert.equal(stages.find((stage) => stage.id === 'export').status, 'missing')
  assert.deepEqual(exportReadiness(fixture), { ready: true, reasonCodes: [] })

  const incomplete = {
    ...fixture,
    evidence_items: fixture.evidence_items.filter((item) => item.relation !== 'opposing'),
    reviews: [],
  }
  assert.deepEqual(exportReadiness(incomplete), {
    ready: false,
    reasonCodes: ['OPPOSING_EVIDENCE_MISSING', 'APPROVED_HUMAN_DECISION_CHAIN_MISSING'],
  })

  const superseded = {
    ...fixture,
    human_decisions: [
      ...fixture.human_decisions,
      { id: 'd2', judgment_id: 'j1', decision: 'reject' },
    ],
  }
  assert.equal(exportReadiness(superseded).ready, false)
  assert.deepEqual(exportReadiness(superseded).reasonCodes, [
    'APPROVED_HUMAN_DECISION_CHAIN_MISSING',
  ])
})

test('mutations always carry the persisted version and a reason', () => {
  assert.deepEqual(versionedMutation({ version: 7 }, 'correct source link', { question: 'Why?' }), {
    question: 'Why?',
    expected_version: 7,
    reason: 'correct source link',
  })
  assert.throws(() => versionedMutation({ version: 0 }, 'reason'), /persisted project version/)
  assert.throws(() => versionedMutation({ version: 1 }, ''), /change reason/)
})

test('saved-search snapshot references are optional but strictly all-or-none', () => {
  const base = buildSavedSearchValues(
    { name: '  China run  ', query: '  China AND gas  ' },
    { time_field: 'published_at' },
  )
  assert.deepEqual(base, {
    name: 'China run',
    query: 'China AND gas',
    filters: { time_field: 'published_at' },
  })

  const searchSnapshotId = 'search-snap-20260809T010203000000Z-0123456789abcdef'
  const hashes = {
    query_receipt_sha256: 'a'.repeat(64),
    normalized_contract_sha256: 'b'.repeat(64),
    ordered_returned_ids_sha256: 'c'.repeat(64),
  }
  const linked = buildSavedSearchValues(
    {
      name: 'Captured run',
      query: 'China',
      search_snapshot_id: ` ${searchSnapshotId} `,
      ...hashes,
    },
    {},
  )
  assert.deepEqual(SEARCH_SNAPSHOT_LINK_FIELDS, [
    'search_snapshot_id',
    'query_receipt_sha256',
    'normalized_contract_sha256',
    'ordered_returned_ids_sha256',
  ])
  assert.equal(linked.search_snapshot_id, searchSnapshotId)
  assert.equal(linked.query_receipt_sha256, hashes.query_receipt_sha256)

  assert.throws(
    () =>
      buildSavedSearchValues(
        {
          name: 'Partial',
          query: 'China',
          search_snapshot_id: searchSnapshotId,
          query_receipt_sha256: hashes.query_receipt_sha256,
        },
        {},
      ),
    /必须一起填写/,
  )
  assert.throws(
    () =>
      buildSavedSearchValues(
        {
          name: 'Bad hash',
          query: 'China',
          search_snapshot_id: searchSnapshotId,
          ...hashes,
          normalized_contract_sha256: 'A'.repeat(64),
        },
        {},
      ),
    /SHA-256 格式无效/,
  )
})

test('version comparison contract normalizes stable diff categories', () => {
  const invalid = normalizeVersionComparison({ schema_version: 'invented' })
  assert.equal(invalid.available, false)

  const normalized = normalizeVersionComparison({
    schema_version: 'research-version-comparison-v1',
    project_id: '1'.repeat(32),
    from_export: { export_version: 1 },
    to_export: { export_version: 2 },
    categories: [
      {
        id: 'support_evidence',
        added: [{ id: 'e2', value: { summary: 'new evidence' } }],
        removed: [],
        modified: [
          {
            id: 'e1',
            before: { summary: 'before' },
            after: { summary: 'after' },
            changed_fields: ['summary'],
          },
        ],
      },
    ],
    summary: { added: 1, removed: 0, modified: 1 },
    access: { content_visibility: 'project-acl', source_manifests_persisted: true },
  })
  assert.equal(normalized.available, true)
  assert.equal(normalized.comparison.categories[0].id, 'support_evidence')
  assert.equal(comparisonChangeCount(normalized.comparison.categories[0]), 2)
  assert.equal(normalized.comparison.access.content_visibility, 'project-acl')
})

test('API requires auth and preserves structured conflict evidence', async () => {
  let calls = 0
  const noAuth = createResearchWorkflowApi({
    fetchImpl: async () => {
      calls += 1
      return response({})
    },
    getAccessToken: () => '',
  })
  await assert.rejects(noAuth.listProjects(), (error) => {
    assert.equal(error instanceof ResearchWorkflowApiError, true)
    assert.equal(error.code, 'AUTH_REQUIRED')
    return true
  })
  assert.equal(calls, 0)

  const requests = []
  const api = createResearchWorkflowApi({
    apiPrefix: '/api',
    getAccessToken: () => 'opaque-test-token',
    fetchImpl: async (url, options) => {
      requests.push({ url, options })
      return response(
        {
          detail: {
            code: 'PROJECT_VERSION_CONFLICT',
            expected_version: 2,
            current_version: 3,
          },
        },
        { ok: false, status: 409 },
      )
    },
  })
  await assert.rejects(
    api.addQuestion('project/id', { expected_version: 2, reason: 'test', question: 'Why?' }),
    (error) => {
      assert.equal(error.status, 409)
      assert.equal(error.code, 'PROJECT_VERSION_CONFLICT')
      assert.equal(error.detail.current_version, 3)
      return true
    },
  )
  assert.equal(requests[0].url, '/api/research/projects/project%2Fid/questions')
  assert.equal(requests[0].options.headers.Authorization, 'Bearer opaque-test-token')
})

test('API compares two persisted manifest versions with encoded query parameters', async () => {
  const requests = []
  const api = createResearchWorkflowApi({
    apiPrefix: '/api',
    getAccessToken: () => 'opaque-reader-token',
    fetchImpl: async (url, options) => {
      requests.push({ url, options })
      return response({ schema_version: 'research-version-comparison-v1' })
    },
  })
  await api.compareExportVersions('project/id', 1, 4)
  assert.equal(
    requests[0].url,
    '/api/research/projects/project%2Fid/export-comparisons?from_export_version=1&to_export_version=4',
  )
  assert.equal(requests[0].options.method, 'GET')
  assert.equal(requests[0].options.headers.Authorization, 'Bearer opaque-reader-token')
})

test('API passes the complete saved-search snapshot reference to the authenticated endpoint', async () => {
  const requests = []
  const api = createResearchWorkflowApi({
    apiPrefix: '/api',
    getAccessToken: () => 'opaque-owner-token',
    fetchImpl: async (url, options) => {
      requests.push({ url, options })
      return response(projectFixture())
    },
  })
  const payload = {
    expected_version: 9,
    reason: 'link explicit capture',
    name: 'Captured run',
    query: 'China',
    filters: {},
    search_snapshot_id: 'search-snap-20260809T010203000000Z-0123456789abcdef',
    query_receipt_sha256: 'a'.repeat(64),
    normalized_contract_sha256: 'b'.repeat(64),
    ordered_returned_ids_sha256: 'c'.repeat(64),
  }

  await api.addSavedSearch('project/id', payload)

  assert.equal(requests[0].url, '/api/research/projects/project%2Fid/saved-searches')
  assert.equal(requests[0].options.method, 'POST')
  assert.equal(requests[0].options.headers.Authorization, 'Bearer opaque-owner-token')
  assert.deepEqual(JSON.parse(requests[0].options.body), payload)
})

test('API downloads all deterministic artifact formats with fail-closed headers', async () => {
  const requests = []
  const digested = []
  const api = createResearchWorkflowApi({
    apiPrefix: '/api',
    getAccessToken: () => 'opaque-reader-token',
    computeArtifactSha256: async (blob) => {
      digested.push(blob)
      return 'd'.repeat(64)
    },
    fetchImpl: async (url, options) => {
      requests.push({ url, options })
      const format = new URL(url, 'https://example.test').searchParams.get('format')
      const fields = new URL(url, 'https://example.test').searchParams.getAll('fields')
      return artifactResponse(format, {}, fields)
    },
  })

  const requestedFields = ['evidence_summaries', 'uncertainty']
  const jsonArtifact = await api.downloadExportArtifact(
    'project/id',
    1,
    'json',
    requestedFields,
  )
  const markdownArtifact = await api.downloadExportArtifact(
    'project/id',
    1,
    'markdown',
    requestedFields,
  )
  const htmlArtifact = await api.downloadExportArtifact(
    'project/id',
    1,
    'html',
    requestedFields,
  )
  const csvArtifact = await api.downloadExportArtifact(
    'project/id',
    1,
    'csv',
    requestedFields,
  )

  assert.deepEqual(RESEARCH_ARTIFACT_FORMATS, ['json', 'markdown', 'html', 'csv'])
  assert.deepEqual(RESEARCH_ARTIFACT_OPTIONAL_FIELDS, [
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
  assert.equal(jsonArtifact.schemaVersion, RESEARCH_ARTIFACT_SCHEMA)
  assert.equal(
    jsonArtifact.filename,
    `research-reviewed-draft-${'1'.repeat(32)}-v1-fields-${'a'.repeat(12)}.json`,
  )
  assert.equal(jsonArtifact.publicationStatus, 'reviewed_draft')
  assert.equal(jsonArtifact.researcherAcceptance, 'unavailable')
  assert.equal(jsonArtifact.distributionStatus, 'not_for_publication')
  assert.equal(jsonArtifact.sourceLicenseStatus, 'unknown')
  assert.deepEqual(jsonArtifact.selectedFields, ['uncertainty', 'evidence_summaries'])
  assert.equal(jsonArtifact.sha256, 'd'.repeat(64))
  assert.equal(jsonArtifact.reportContentSha256, 'e'.repeat(64))
  assert.equal(jsonArtifact.manifestSha256, 'f'.repeat(64))
  assert.equal(jsonArtifact.blob.format, 'json')
  assert.equal(markdownArtifact.filename.endsWith('.md'), true)
  assert.equal(htmlArtifact.filename.endsWith('.html'), true)
  assert.equal(csvArtifact.filename.endsWith('.csv'), true)
  assert.deepEqual(
    requests.map(({ url }) => url),
    [
      '/api/research/projects/project%2Fid/exports/1/artifact?format=json&fields=uncertainty&fields=evidence_summaries',
      '/api/research/projects/project%2Fid/exports/1/artifact?format=markdown&fields=uncertainty&fields=evidence_summaries',
      '/api/research/projects/project%2Fid/exports/1/artifact?format=html&fields=uncertainty&fields=evidence_summaries',
      '/api/research/projects/project%2Fid/exports/1/artifact?format=csv&fields=uncertainty&fields=evidence_summaries',
    ],
  )
  assert.equal(requests[0].options.method, 'GET')
  assert.equal(requests[0].options.headers.Accept, 'application/json')
  assert.equal(requests[1].options.headers.Accept, 'text/markdown')
  assert.equal(requests[2].options.headers.Accept, 'text/html')
  assert.equal(requests[3].options.headers.Accept, 'text/csv')
  assert.equal(requests[0].options.headers.Authorization, 'Bearer opaque-reader-token')
  assert.deepEqual(digested, [
    jsonArtifact.blob,
    markdownArtifact.blob,
    htmlArtifact.blob,
    csvArtifact.blob,
  ])

  for (const unsupported of ['pdf', 'word', 'ppt']) {
    await assert.rejects(
      api.downloadExportArtifact('project/id', 1, unsupported, requestedFields),
      (error) => {
        assert.equal(error.code, 'ARTIFACT_FORMAT_UNSUPPORTED')
        return true
      },
    )
  }
  for (const invalidFields of [
    ['uncertainty', 'source_note'],
    ['uncertainty', 'uncertainty'],
  ]) {
    await assert.rejects(
      api.downloadExportArtifact('project/id', 1, 'json', invalidFields),
      (error) => {
        assert.equal(error.code, 'ARTIFACT_FIELDS_INVALID')
        return true
      },
    )
  }
  assert.equal(requests.length, 4)

  const invalid = createResearchWorkflowApi({
    getAccessToken: () => 'token',
    fetchImpl: async () => artifactResponse('json', { 'x-research-artifact-sha256': 'bad' }),
  })
  await assert.rejects(invalid.downloadExportArtifact('p', 1, 'json'), (error) => {
    assert.equal(error.code, 'ARTIFACT_RESPONSE_INVALID')
    return true
  })

  const inconsistentEtag = createResearchWorkflowApi({
    getAccessToken: () => 'token',
    fetchImpl: async () => artifactResponse('json', { etag: `"sha256-${'a'.repeat(64)}"` }),
  })
  await assert.rejects(inconsistentEtag.downloadExportArtifact('p', 1, 'json'), (error) => {
    assert.equal(error.code, 'ARTIFACT_RESPONSE_INVALID')
    return true
  })

  const invalidMediaType = createResearchWorkflowApi({
    getAccessToken: () => 'token',
    fetchImpl: async () => artifactResponse('html', { 'content-type': 'text/html-unsafe' }),
  })
  await assert.rejects(invalidMediaType.downloadExportArtifact('p', 1, 'html'), (error) => {
    assert.equal(error.code, 'ARTIFACT_RESPONSE_INVALID')
    return true
  })

  const missingDraftBoundary = createResearchWorkflowApi({
    getAccessToken: () => 'token',
    fetchImpl: async () =>
      artifactResponse('json', { 'x-researcher-acceptance': null }),
  })
  await assert.rejects(
    missingDraftBoundary.downloadExportArtifact('p', 1, 'json'),
    (error) => {
      assert.equal(error.code, 'ARTIFACT_RESPONSE_INVALID')
      return true
    },
  )

  for (const overrides of [
    { 'x-research-export-fields': 'method,uncertainty' },
    { 'x-research-field-selection-schema': 'research-export-field-selection-v9' },
    { 'x-research-source-license-status': 'approved' },
    { 'cache-control': 'public, max-age=3600' },
    { 'x-content-type-options': null },
    { vary: '*' },
  ]) {
    const invalidBoundary = createResearchWorkflowApi({
      getAccessToken: () => 'token',
      fetchImpl: async () => artifactResponse('json', overrides),
    })
    await assert.rejects(
      invalidBoundary.downloadExportArtifact('p', 1, 'json'),
      (error) => {
        assert.equal(error.code, 'ARTIFACT_RESPONSE_INVALID')
        return true
      },
    )
  }

  const invalidHtmlCsp = createResearchWorkflowApi({
    getAccessToken: () => 'token',
    fetchImpl: async () => artifactResponse('html', { 'content-security-policy': "default-src 'self'" }),
  })
  await assert.rejects(invalidHtmlCsp.downloadExportArtifact('p', 1, 'html'), (error) => {
    assert.equal(error.code, 'ARTIFACT_RESPONSE_INVALID')
    return true
  })

  const mismatchedBody = createResearchWorkflowApi({
    getAccessToken: () => 'token',
    computeArtifactSha256: async () => 'a'.repeat(64),
    fetchImpl: async () => artifactResponse('json'),
  })
  await assert.rejects(mismatchedBody.downloadExportArtifact('p', 1, 'json'), (error) => {
    assert.equal(error.code, 'ARTIFACT_INTEGRITY_MISMATCH')
    return true
  })

  const unavailableDigest = createResearchWorkflowApi({
    getAccessToken: () => 'token',
    computeArtifactSha256: async () => {
      throw new Error('digest unavailable')
    },
    fetchImpl: async () => artifactResponse('json'),
  })
  await assert.rejects(unavailableDigest.downloadExportArtifact('p', 1, 'json'), (error) => {
    assert.equal(error.code, 'ARTIFACT_INTEGRITY_UNAVAILABLE')
    return true
  })
})

test('artifact client rejects format substitution and oversized bodies', async () => {
  const mismatched = createResearchWorkflowApi({
    getAccessToken: () => 'token',
    fetchImpl: async () =>
      artifactResponse('html', { 'x-research-artifact-format': 'markdown' }),
  })
  await assert.rejects(mismatched.downloadExportArtifact('p', 1, 'html'), (error) => {
    assert.equal(error.code, 'ARTIFACT_RESPONSE_INVALID')
    return true
  })

  const emptyResponse = artifactResponse('json')
  emptyResponse.blob = async () => ({ size: 0 })
  const empty = createResearchWorkflowApi({
    getAccessToken: () => 'token',
    fetchImpl: async () => emptyResponse,
  })
  await assert.rejects(empty.downloadExportArtifact('p', 1, 'json'), (error) => {
    assert.equal(error.code, 'ARTIFACT_RESPONSE_INVALID')
    return true
  })

  const oversizedResponse = artifactResponse('csv')
  oversizedResponse.blob = async () => ({ size: 8 * 1024 * 1024 + 1 })
  const oversized = createResearchWorkflowApi({
    getAccessToken: () => 'token',
    fetchImpl: async () => oversizedResponse,
  })
  await assert.rejects(oversized.downloadExportArtifact('p', 1, 'csv'), (error) => {
    assert.equal(error.code, 'ARTIFACT_RESPONSE_TOO_LARGE')
    return true
  })

  const oversizedHtmlResponse = artifactResponse('html')
  oversizedHtmlResponse.blob = async () => ({ size: 16 * 1024 * 1024 + 1 })
  const oversizedHtml = createResearchWorkflowApi({
    getAccessToken: () => 'token',
    fetchImpl: async () => oversizedHtmlResponse,
  })
  await assert.rejects(oversizedHtml.downloadExportArtifact('p', 1, 'html'), (error) => {
    assert.equal(error.code, 'ARTIFACT_RESPONSE_TOO_LARGE')
    return true
  })
})

test('research workflow HTTP errors do not disclose arbitrary response bodies', async () => {
  let textRead = false
  const plain = createResearchWorkflowApi({
    getAccessToken: () => 'token',
    fetchImpl: async () => ({
      ok: false,
      status: 503,
      headers: { get: (name) => (String(name).toLowerCase() === 'content-type' ? 'text/plain' : null) },
      text: async () => {
        textRead = true
        return 'database_url=postgres://secret-canary'
      },
    }),
  })
  await assert.rejects(plain.listProjects(), (error) => {
    assert.equal(error.message, '读取研究项目失败')
    assert.equal(error.status, 503)
    assert.equal(error.code, 'HTTP_ERROR')
    assert.equal(JSON.stringify(error).includes('secret-canary'), false)
    return true
  })
  assert.equal(textRead, false)

  const structured = createResearchWorkflowApi({
    getAccessToken: () => 'token',
    fetchImpl: async () =>
      response(
        {
          detail: {
            code: 'PROJECT_VERSION_CONFLICT',
            current_version: 3,
            message: 'token=secret-canary',
            internal_trace: 'secret-canary',
          },
        },
        { ok: false, status: 409 },
      ),
  })
  await assert.rejects(structured.listProjects(), (error) => {
    assert.equal(error.message, '读取研究项目失败')
    assert.equal(error.code, 'PROJECT_VERSION_CONFLICT')
    assert.deepEqual(error.detail, { code: 'PROJECT_VERSION_CONFLICT', current_version: 3 })
    assert.equal(JSON.stringify(error).includes('secret-canary'), false)
    return true
  })
})

test('artifact download adapter uses the safe server filename and always revokes the URL', () => {
  const events = []
  const anchor = {
    href: '',
    download: '',
    rel: '',
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
      return 'blob:research-artifact'
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
  const artifact = {
    blob: { size: 3 },
    filename: `research-reviewed-draft-project-v1-fields-${'a'.repeat(12)}.json`,
  }

  triggerResearchArtifactDownload(artifact, { urlApi, documentRef })

  assert.equal(anchor.href, 'blob:research-artifact')
  assert.equal(anchor.download, artifact.filename)
  assert.equal(anchor.rel, 'noopener')
  assert.deepEqual(events, [
    ['create', artifact.blob],
    ['element', 'a'],
    'append',
    'click',
    'remove',
    ['revoke', 'blob:research-artifact'],
  ])
  assert.throws(
    () =>
      triggerResearchArtifactDownload(
        { blob: artifact.blob, filename: '../unsafe.json' },
        { urlApi, documentRef },
      ),
    /contract is invalid/,
  )
  assert.throws(
    () =>
      triggerResearchArtifactDownload(
        { blob: artifact.blob, filename: 'research-report-project-v1.json' },
        { urlApi, documentRef },
      ),
    /contract is invalid/,
  )
  assert.throws(
    () =>
      triggerResearchArtifactDownload(
        { blob: artifact.blob, filename: 'research-reviewed-draft-project-v1.json' },
        { urlApi, documentRef },
      ),
    /contract is invalid/,
  )
})

test('minimal research view states its assurance boundary and exposes all workflow steps', async () => {
  const view = await readFile(
    new URL('../src/views/ResearchWorkspace.vue', import.meta.url),
    'utf8',
  )
  assert.match(view, /不代表真实研究员验收/)
  assert.match(view, /不是正式报告或决策建议/)
  assert.match(view, /保存检索/)
  assert.match(view, /证据项/)
  assert.match(view, /信息缺口/)
  assert.match(view, /替代假设/)
  assert.match(view, /人工决定/)
  assert.match(view, /同行审阅/)
  assert.match(view, /版本化导出清单/)
  assert.match(view, /版本比较/)
  assert.match(view, /比较已持久化版本/)
  assert.match(view, /服务端只读核验/)
  assert.match(view, /不会自动抓取或创建快照/)
  assert.match(view, /必须先在[\s\S]*搜索页[\s\S]*显式捕获查询快照/)
  assert.match(view, /不会隐式捕获、重放查询或读取正文/)
  assert.match(view, /下载已持久化报告/)
  assert.match(view, /只由对应 manifest 确定性生成/)
  assert.match(view, /不新增事实或无引用 AI/)
  assert.match(view, /reviewed draft/)
  assert.match(view, /researcher acceptance unavailable/)
  assert.match(view, /downloadExportArtifact/)
  assert.match(view, /RESEARCH_ARTIFACT_FORMATS/)
  assert.match(view, /json: 'JSON'/)
  assert.match(view, /markdown: 'Markdown'/)
  assert.match(view, /html: 'HTML'/)
  assert.match(view, /csv: 'CSV 证据清单'/)
  assert.match(view, /无脚本 HTML/)
  assert.match(view, /CSV[\s\S]*仅提供同一 manifest 的证据\/引用清单/)
  assert.match(view, /不代表[\s\S]*Word、PDF、PPT 或正式报告已完成/)
  assert.match(view, /来源定位地址不等于复用许可/)
  assert.match(view, /许可状态未知/)
  assert.match(view, /字段选择只接受受控白名单/)
  assert.match(view, /RESEARCH_ARTIFACT_OPTIONAL_FIELDS/)
  assert.match(view, /v-model="selectedArtifactFields"/)
  assert.match(view, /敏感字段始终排除/)
  assert.match(view, /HTML 上限 16 MiB/)
  assert.match(view, /CSV 上限 8 MiB、5000[\s\S]*不会静默截断/)
  assert.match(view, /报告 artifact 下载失败/)
  for (const field of SEARCH_SNAPSHOT_LINK_FIELDS) assert.match(view, new RegExp(field))
  assert.doesNotMatch(view, /source_content_sha256/)
  assert.match(view, /min-height: 44px/)
})

test('research workspace artifact selector remains a valid Vue SFC', async () => {
  const view = await readFile(
    new URL('../src/views/ResearchWorkspace.vue', import.meta.url),
    'utf8',
  )
  const parsed = parse(view, { filename: 'ResearchWorkspace.vue' })
  assert.deepEqual(parsed.errors, [])
  assert.doesNotThrow(() => compileScript(parsed.descriptor, { id: 'research-workflow-test' }))
  const compiledTemplate = compileTemplate({
    id: 'research-workflow-test',
    filename: 'ResearchWorkspace.vue',
    source: parsed.descriptor.template.content,
  })
  assert.deepEqual(compiledTemplate.errors, [])
})
