export const SEARCH_MODE_SEMANTICS_VERSION = 'search-mode-semantics-v1'

export const SEARCH_MODES = Object.freeze([
  Object.freeze({
    version: SEARCH_MODE_SEMANTICS_VERSION,
    label: '全部词',
    value: 'exact',
    queryLanguage: 'boolean-v1',
    unquotedTerms: 'token_and',
    literalPhrase: 'quoted_literal',
    explicitBoolean: 'uppercase_operators_preserved',
    aliasExpansion: 'entity_aliases_within_leaf_only',
    vectorSimilarity: false,
    description: '未加引号的多个词按 token AND；大写 AND、OR、NOT 与括号按 boolean-v1 执行；推荐用双引号请求原样短语；实体别名只在叶节点内按 OR。',
  }),
  Object.freeze({
    version: SEARCH_MODE_SEMANTICS_VERSION,
    label: '主题扩展',
    value: 'fuzzy',
    queryLanguage: 'boolean-v1',
    unquotedTerms: 'topic_or',
    literalPhrase: 'quoted_literal',
    explicitBoolean: 'uppercase_operators_preserved',
    aliasExpansion: 'versioned_catalog_leaf_or_review_disclosed',
    vectorSimilarity: false,
    description: '未写运算符的多个主题词按 OR；显式 Boolean 运算符保持不变；推荐用双引号请求原样短语；叶节点按版本目录展开主题与实体别名并另行披露复核状态；不是向量相似度。',
  }),
])

const UNKNOWN_SEARCH_MODE = Object.freeze({
  version: SEARCH_MODE_SEMANTICS_VERSION,
  label: '模式未知',
  value: 'unavailable',
  queryLanguage: 'unavailable',
  unquotedTerms: 'unavailable',
  literalPhrase: 'unavailable',
  explicitBoolean: 'unavailable',
  aliasExpansion: 'unavailable',
  vectorSimilarity: false,
  description: '查询模式契约不可用；不得推断精确、语义、短语或别名扩展行为。',
})

export function searchModeDisclosure(mode) {
  return SEARCH_MODES.find((item) => item.value === mode) || UNKNOWN_SEARCH_MODE
}
