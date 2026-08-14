import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  SEARCH_MODE_SEMANTICS_VERSION,
  SEARCH_MODES,
  searchModeDisclosure,
} from '../src/features/search/index.js'

test('search modes expose one versioned public term contract', () => {
  assert.equal(SEARCH_MODE_SEMANTICS_VERSION, 'search-mode-semantics-v1')
  assert.deepEqual(SEARCH_MODES.map((item) => item.value), ['exact', 'fuzzy'])

  const exact = searchModeDisclosure('exact')
  assert.deepEqual(Object.keys(exact).sort(), [
    'aliasExpansion',
    'description',
    'explicitBoolean',
    'label',
    'literalPhrase',
    'queryLanguage',
    'unquotedTerms',
    'value',
    'vectorSimilarity',
    'version',
  ].sort())
  assert.equal(exact.label, '全部词')
  assert.equal(exact.queryLanguage, 'boolean-v1')
  assert.equal(exact.unquotedTerms, 'token_and')
  assert.equal(exact.literalPhrase, 'quoted_literal')
  assert.equal(exact.explicitBoolean, 'uppercase_operators_preserved')
  assert.equal(exact.aliasExpansion, 'entity_aliases_within_leaf_only')
  assert.equal(exact.vectorSimilarity, false)
  assert.match(exact.description, /未加引号.*AND/)
  assert.match(exact.description, /双引号.*原样短语/)

  const fuzzy = searchModeDisclosure('fuzzy')
  assert.equal(fuzzy.label, '主题扩展')
  assert.equal(fuzzy.queryLanguage, 'boolean-v1')
  assert.equal(fuzzy.unquotedTerms, 'topic_or')
  assert.equal(fuzzy.literalPhrase, 'quoted_literal')
  assert.equal(fuzzy.explicitBoolean, 'uppercase_operators_preserved')
  assert.equal(fuzzy.aliasExpansion, 'versioned_catalog_leaf_or_review_disclosed')
  assert.equal(fuzzy.vectorSimilarity, false)
  assert.match(fuzzy.description, /OR/)
  assert.match(fuzzy.description, /不是向量相似度/)

  const unavailable = searchModeDisclosure('vector-secret-mode')
  assert.equal(unavailable.value, 'unavailable')
  assert.equal(unavailable.label, '模式未知')
  assert.equal(unavailable.vectorSimilarity, false)
  assert.doesNotMatch(JSON.stringify(unavailable), /vector-secret-mode/)
})

test('data search and assistant tool details consume the shared mode disclosure', async () => {
  const [searchPage, assistantPage] = await Promise.all([
    readFile(new URL('../src/views/DataService/data-search.vue', import.meta.url), 'utf8'),
    readFile(new URL('../src/features/assistant/AssistantExperience.vue', import.meta.url), 'utf8'),
  ])

  assert.match(searchPage, /searchModeDisclosure\(preset\.mode\)\.label/)
  assert.match(searchPage, /searchModeDisclosure\(searchParams\.mode\)\.description/)
  assert.doesNotMatch(searchPage, /preset\.mode === ['"]fuzzy['"] \? ['"]主题扩展['"] : ['"]全部词['"]/)

  assert.match(assistantPage, /from ['"]@\/governance\/searchQuerySemantics\.js['"]/)
  assert.doesNotMatch(assistantPage, /from ['"]@\/features\/search\//)
  assert.match(assistantPage, /searchModeDisclosure\(mode\)\.label/)
  assert.match(assistantPage, /searchModeDisclosure\(tc\.invoke\.mode\)\.description/)
  assert.doesNotMatch(assistantPage, /exact:\s*['"]精确['"]|fuzzy:\s*['"]语义['"]/)
})
