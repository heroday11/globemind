import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  buildTranslationWorkload,
  databaseTranslationProvenance,
  normalizeMachineTranslationResponse,
  requestMachineTranslation,
  sha256Text,
  summarizeMachineTranslationProvenance,
  translationDisclosure,
} from '../src/features/translation/index.js'

const newsDetailSource = await readFile(
  new URL('../src/views/DataService/news-detail.vue', import.meta.url),
  'utf8',
)
const translationApiSource = await readFile(
  new URL('../src/features/translation/api.js', import.meta.url),
  'utf8',
)

async function validResponse(sourceText = 'Source paragraph') {
  return {
    schema_version: 'globemind.news-translation.v1',
    generated_at: '2026-08-09T18:45:00Z',
    text: '来源段落',
    provenance: {
      mode: 'machine_translation',
      backend: 'local-vllm-loopback',
      model_id: 'translation-model-v1',
      source_language: 'und',
      target_language: 'zh-Hans',
      source_text_sha256: await sha256Text(sourceText),
      source_text_length: Array.from(sourceText).length,
      human_review_state: 'not_reviewed',
      quality_state: 'not_measured',
      terminology_version: 'not_configured',
      persistence: 'not_persisted_by_endpoint',
      provider_scope: 'loopback_only',
    },
  }
}

test('machine translation response binds exact source and unreviewed provenance', async () => {
  const sourceText = 'Source paragraph'
  const normalized = normalizeMachineTranslationResponse(
    await validResponse(sourceText),
    {
      sourceText,
      sourceTextSha256: await sha256Text(sourceText),
      sourceLanguage: 'und',
      targetLanguage: 'zh-Hans',
    },
  )

  assert.equal(normalized.text, '来源段落')
  assert.equal(normalized.provenance.human_review_state, 'not_reviewed')
  assert.equal(normalized.provenance.quality_state, 'not_measured')
  assert.match(translationDisclosure(normalized.provenance), /未经人工复核/)
  assert.match(translationDisclosure(normalized.provenance), /质量未测量/)
})

test('translation contract fails closed on drift, source mismatch, or extra data', async () => {
  const sourceText = 'Source paragraph'
  const expected = {
    sourceText,
    sourceTextSha256: await sha256Text(sourceText),
    sourceLanguage: 'und',
    targetLanguage: 'zh-Hans',
  }
  const valid = await validResponse(sourceText)

  for (const invalid of [
    { ...valid, schema_version: 'globemind.news-translation.v2' },
    { ...valid, endpoint: 'http://user:secret@provider.test' },
    {
      ...valid,
      provenance: { ...valid.provenance, source_text_sha256: 'a'.repeat(64) },
    },
    {
      ...valid,
      provenance: { ...valid.provenance, human_review_state: 'reviewed' },
    },
  ]) {
    assert.throws(
      () => normalizeMachineTranslationResponse(invalid, expected),
      /TRANSLATION_RESPONSE_INVALID/,
    )
  }
})

test('translation request requires auth and reads only bounded JSON', async () => {
  const sourceText = 'Source paragraph'
  const valid = await validResponse(sourceText)
  let request = null
  const result = await requestMachineTranslation({
    apiPrefix: '/api',
    fetchImpl: async (url, options) => {
      request = { url, options }
      return new Response(JSON.stringify(valid), {
        status: 200,
        headers: { 'content-type': 'application/json; charset=utf-8' },
      })
    },
    sourceText,
    sourceLanguage: 'und',
    targetLanguage: 'zh-Hans',
    token: 'opaque-token',
  })

  assert.equal(result.text, '来源段落')
  assert.equal(request.url, '/api/dashboard/news/translate-paragraph')
  assert.equal(request.options.headers.Authorization, 'Bearer opaque-token')
  assert.deepEqual(JSON.parse(request.options.body), {
    text: sourceText,
    source_language: 'und',
    target_language: 'zh-Hans',
  })

  let called = false
  await assert.rejects(
    requestMachineTranslation({
      apiPrefix: '/api',
      fetchImpl: async () => {
        called = true
        return new Response('{}')
      },
      sourceText,
      token: '',
    }),
    /TRANSLATION_AUTH_REQUIRED/,
  )
  assert.equal(called, false)

  await assert.rejects(
    requestMachineTranslation({
      apiPrefix: '/api',
      fetchImpl: async () => new Response(JSON.stringify(valid), {
        status: 200,
        headers: { 'content-type': 'text/html' },
      }),
      sourceText,
      token: 'opaque-token',
    }),
    /TRANSLATION_RESPONSE_INVALID/,
  )
  await assert.rejects(
    requestMachineTranslation({
      apiPrefix: '/api',
      fetchImpl: async () => new Response('x'.repeat(64 * 1024 + 1), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
      sourceText,
      token: 'opaque-token',
    }),
    /TRANSLATION_RESPONSE_INVALID/,
  )
})

test('client rejects duplicate/deep JSON, invalid UTF-8, and chunked overflow', async () => {
  const sourceText = 'Source paragraph'
  const valid = await validResponse(sourceText)
  const duplicate = JSON.stringify(valid).replace(
    '"schema_version":"globemind.news-translation.v1"',
    '"schema_version":"invalid","schema_version":"globemind.news-translation.v1"',
  )

  const requests = [
    async () => new Response(duplicate, {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
    async () => {
      let nested = '0'
      for (let index = 0; index < 65; index += 1) nested = `[${nested}]`
      const raw = JSON.stringify(valid).replace(
        '"text":"来源段落"',
        `"padding":${nested},"text":"来源段落"`,
      )
      return new Response(raw, {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    },
    async () => new Response(new Uint8Array([0xff, 0xfe]), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
    async () => {
      const chunks = [new Uint8Array(32 * 1024), new Uint8Array(32 * 1024), new Uint8Array(1)]
      let index = 0
      return {
        ok: true,
        headers: { get: (name) => name === 'content-type' ? 'application/json' : null },
        body: {
          getReader: () => ({
            read: async () => index < chunks.length
              ? { done: false, value: chunks[index++] }
              : { done: true, value: undefined },
            cancel: async () => {},
            releaseLock: () => {},
          }),
        },
      }
    },
  ]

  for (const fetchImpl of requests) {
    await assert.rejects(
      requestMachineTranslation({
        apiPrefix: '/api',
        fetchImpl,
        sourceText,
        token: 'opaque-token',
      }),
      /TRANSLATION_RESPONSE_INVALID/,
    )
  }
})

test('client preserves abort identity and redacts stream reader failures', async () => {
  const abort = Object.assign(new Error('abort requested'), { name: 'AbortError' })
  await assert.rejects(
    requestMachineTranslation({
      apiPrefix: '/api',
      fetchImpl: async () => { throw abort },
      sourceText: 'Source paragraph',
      token: 'opaque-token',
    }),
    (error) => error === abort,
  )

  await assert.rejects(
    requestMachineTranslation({
      apiPrefix: '/api',
      fetchImpl: async () => ({
        ok: true,
        headers: { get: (name) => name === 'content-type' ? 'application/json' : null },
        body: {
          getReader: () => ({
            read: async () => { throw new Error('provider-secret-canary') },
            releaseLock: () => {},
          }),
        },
      }),
      sourceText: 'Source paragraph',
      token: 'opaque-token',
    }),
    (error) => error?.code === 'TRANSLATION_RESPONSE_INVALID'
      && !error.message.includes('canary'),
  )
})

test('client rejects lone-surrogate source text before network use', async () => {
  let called = false
  await assert.rejects(
    requestMachineTranslation({
      apiPrefix: '/api',
      fetchImpl: async () => {
        called = true
        return new Response('{}')
      },
      sourceText: '\ud800',
      token: 'opaque-token',
    }),
    /TRANSLATION_WORKLOAD_LIMIT_EXCEEDED/,
  )
  assert.equal(called, false)
})

test('translation workload and database fallback remain bounded and honest', () => {
  const workload = buildTranslationWorkload({
    title: 'Title',
    abstract: 'Abstract',
    paragraphs: ['One', 'Two'],
  })
  assert.equal(workload.segmentCount, 4)
  assert.equal(workload.totalCharacters, 19)
  assert.throws(
    () => buildTranslationWorkload({ paragraphs: Array(101).fill('x') }),
    /TRANSLATION_WORKLOAD_LIMIT_EXCEEDED/,
  )
  assert.throws(
    () => buildTranslationWorkload({ paragraphs: ['x'.repeat(6001)] }),
    /TRANSLATION_WORKLOAD_LIMIT_EXCEEDED/,
  )

  const provenance = databaseTranslationProvenance()
  assert.equal(provenance.provenance_state, 'not_available')
  assert.equal(provenance.human_review_state, 'unknown')
  assert.match(translationDisclosure(provenance), /provenance 未登记/)

  const receipt = {
    mode: 'machine_translation',
    backend: 'local-vllm-loopback',
    model_id: 'translation-model-v1',
    source_language: 'und',
    target_language: 'zh-Hans',
    source_text_sha256: 'a'.repeat(64),
    source_text_length: 1,
    human_review_state: 'not_reviewed',
    quality_state: 'not_measured',
    terminology_version: 'not_configured',
    persistence: 'not_persisted_by_endpoint',
    provider_scope: 'loopback_only',
  }
  assert.throws(
    () => summarizeMachineTranslationProvenance([
      { ...receipt, provider_scope: 'external-provider' },
    ]),
    /TRANSLATION_RESPONSE_INVALID/,
  )
})

test('article translation UI authenticates, rejects late writes, and discloses quality state', () => {
  assert.match(newsDetailSource, /const translationRequestGate = createLatestRequestGate\(\)/)
  assert.match(newsDetailSource, /requestMachineTranslation\(/)
  assert.match(translationApiSource, /Authorization: `Bearer \$\{token\}`/)
  assert.match(translationApiSource, /target_language: targetLanguage/)
  assert.match(newsDetailSource, /translationRequestGate\.invalidate\(\)/)
  assert.match(newsDetailSource, /未经人工复核/)
  assert.match(newsDetailSource, /质量未测量/)
  assert.match(newsDetailSource, /provenance 未登记/)
  assert.doesNotMatch(newsDetailSource, /<span v-else-if="translation" class="meta-item">已翻译<\/span>/)
  assert.doesNotMatch(newsDetailSource, /本地 LLM 翻译失败：\$\{e/)
  assert.doesNotMatch(newsDetailSource, /console\.error\('获取翻译失败:/)
})

test('a failed translation batch closes its worker write gate before abort fallback', () => {
  assert.match(newsDetailSource, /let translationRequestClosed = false/)
  assert.match(
    newsDetailSource,
    /const isCurrent = \(\) => \(\s*!translationRequestClosed\s*&& isCurrentRequest\(\)/,
  )
  assert.match(
    newsDetailSource,
    /const shouldHandleFailure = isCurrent\(\)\s*translationRequestClosed = true\s*controller\.abort\(\)\s*if \(!shouldHandleFailure\) return/,
  )
})
