import { safeExternalHttpUrl } from '../../utils/externalUrl.js'

const PROFILE_CONTRACT_VERSION = 'ground-news-source-profile-v1'
const METHOD_CARD_SCHEMA_VERSION = 'ground-news-source-profile-method-card-v1'
const KNOWN_PROFILE_VERSION = 'media_profile_seed_v1'
const MAX_METHODS = 16
const MAX_UNKNOWN_METHODS = 32

function safeEvidenceUrl(value) {
  const safeUrl = safeExternalHttpUrl(value)
  if (!safeUrl) return null
  try {
    const parsed = new URL(safeUrl)
    parsed.search = ''
    parsed.hash = ''
    return parsed.href
  } catch {
    return null
  }
}

const METHOD_DEFINITIONS = Object.freeze({
  historical_wave1_seed_v1: {
    method_version: 'v1',
    kind: 'catalog_seed',
    version_state: 'profile_version_bound',
    supports_fields: ['source_identity', 'source_structure'],
    assurance_scope: 'catalog_structure_only',
    label: '历史 Wave1 来源目录种子',
    scope: '仅提供目录身份与结构字段',
  },
  structural_ownership_inference_v1: {
    method_version: 'v1',
    kind: 'structural_rule',
    version_state: 'controlled_mapping',
    supports_fields: ['ownership_type'],
    assurance_scope: 'structural_catalog_label_only',
    label: '所有权结构映射规则',
    scope: '仅为结构性目录标签',
  },
  geo_alignment_rule_v1: {
    method_version: 'v1',
    kind: 'structural_rule',
    version_state: 'explicit',
    supports_fields: ['geo_alignment'],
    assurance_scope: 'composition_grouping_only',
    label: '地理构成分组规则',
    scope: '仅用于来源构成分组',
  },
  structural_review_v1: {
    method_version: 'v1',
    kind: 'structural_review',
    version_state: 'explicit',
    supports_fields: ['ownership_type', 'source_type'],
    assurance_scope: 'structural_catalog_label_only',
    label: '来源结构复核规则',
    scope: '事实准确率与来源可靠性均未核验，不能据此作结论',
  },
  ground_news_rating_v1: {
    method_version: 'v1',
    kind: 'third_party_directory_rating',
    version_state: 'explicit',
    supports_fields: ['political_leaning', 'credibility_tier'],
    assurance_scope: 'third_party_catalog_label_only',
    label: 'Ground News 第三方目录评级',
    scope: '转述第三方目录标签，未做独立校验',
  },
  mbfc_rating_v1: {
    method_version: 'v1',
    kind: 'third_party_directory_rating',
    version_state: 'explicit',
    supports_fields: ['political_leaning', 'credibility_tier'],
    assurance_scope: 'third_party_catalog_label_only',
    label: 'MBFC 第三方目录评级',
    scope: '转述第三方目录标签，未做独立校验',
  },
  institutional_override_v1: {
    method_version: 'v1',
    kind: 'institutional_evidence',
    version_state: 'explicit',
    supports_fields: ['ownership_type', 'political_leaning'],
    assurance_scope: 'institutional_alignment_only',
    label: '机构关系证据映射',
    scope: '仅支持机构关系或国家立场标签',
  },
  review_import_legacy: {
    method_version: null,
    kind: 'manual_review_import',
    version_state: 'legacy_unversioned',
    supports_fields: ['ownership_type', 'review_status'],
    assurance_scope: 'catalog_review_metadata_only',
    label: '旧版人工复核导入',
    scope: '方法未版本化，仅作目录复核元数据',
  },
})

const SOURCE_TYPE_LABELS = Object.freeze({
  business_media: '商业财经媒体',
  executive_government: '政府行政机构',
  foreign_ministry: '外交部门',
  foreign_service: '外交服务机构',
  global_major_media: '全球主流媒体',
  international_organization: '国际组织',
  international_security_org: '国际安全组织',
  national_major_media: '全国性媒体',
  public_broadcaster: '公共广播机构',
  regional_major_media: '地区媒体',
  state_media: '国家媒体',
  supranational_executive: '超国家行政机构',
  wire_service: '通讯社',
})
const OWNERSHIP_LABELS = Object.freeze({
  government: '政府',
  intergovernmental: '政府间组织',
  nonprofit: '非营利',
  party_affiliated: '政党关联',
  private: '私营',
  public: '公共机构',
  state: '国有',
  wire_service: '通讯社',
})
const GEO_LABELS = Object.freeze({
  china: '中国',
  global_south: '全球南方',
  middle_east: '中东',
  mixed: '混合',
  neutral: '中性分组',
  russia: '俄罗斯',
  western: '西方',
})
const POLITICAL_LABELS = Object.freeze({
  center: '中间',
  center_left: '偏左',
  center_right: '偏右',
  left: '左翼',
  right: '右翼',
  state_aligned: '国家立场',
})
const CREDIBILITY_LABELS = Object.freeze({ high: '高', medium: '中', low: '低' })
const CONFIDENCE_LABELS = Object.freeze({ high: '高', medium: '中', low: '低' })
const REVIEW_LABELS = Object.freeze({
  seeded: '仅种子记录',
  needs_review: '待人工复核',
  reviewed: '已复核',
  locked: '已锁定复核',
})

const FIELD_NAMES = [
  'source_type',
  'ownership_type',
  'geo_alignment',
  'political_leaning',
  'credibility_tier',
  'label_confidence',
  'review_status',
]
const DISPOSITION_STATES = new Set([
  'unknown',
  'catalog_value',
  'third_party_catalog_label',
  'institutional_catalog_label',
  'catalog_metadata',
])
const REASON_CODES = new Set([
  'PROFILE_VERSION_UNKNOWN',
  'METHOD_INPUT_OUT_OF_BOUNDS',
  'CATALOG_VALUE_UNKNOWN',
  'CONTROLLED_METHOD_MISSING',
  'PROFILE_NOT_REVIEWED',
  'CONTROLLED_RATING_METHOD_MISSING',
  'QUALITY_LABEL_NOT_RELEASED',
  'CONTROLLED_PROFILE_VERSION',
  'CONTROLLED_THIRD_PARTY_DIRECTORY_METHOD',
  'CONTROLLED_INSTITUTIONAL_ALIGNMENT_METHOD',
  'QUALITY_LABEL_RELEASED_WITH_CONTROLLED_METHOD',
])
const NOTE_DISPOSITIONS = Object.freeze({
  EXTERNAL_RATING_REQUIRED: '仍需外部目录评级',
  FACTUALITY_RATING_PENDING: '事实性目录评级仍待补充',
  GEO_ALIGNMENT_NOT_POLITICAL_BIAS: '地理构成分组不是政治倾向评级',
  INSTITUTIONAL_CONTEXT_ONLY: '机构背景仅用于结构说明',
  INSTITUTIONAL_NOT_LEFT_RIGHT: '国家立场标签不是左右翼评级',
  MULTI_DIRECTORY_CONSERVATIVE_MAPPING: '多目录冲突按保守标签映射',
  OWNERSHIP_REVIEW_DETAIL: '所有权复核细节已归入受控结构字段',
  SOURCE_IDENTITY_AMBIGUITY: '同名来源识别仍有歧义',
})

function exactObject(value, keys) {
  return Boolean(value)
    && typeof value === 'object'
    && !Array.isArray(value)
    && JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...keys].sort())
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => (
      `${JSON.stringify(key)}:${stableJson(value[key])}`
    )).join(',')}}`
  }
  return JSON.stringify(value)
}

function boundedText(value, maximum = 512) {
  if (typeof value !== 'string') return null
  const normalized = value.trim()
  if (!normalized || normalized.length > maximum) return null
  if ([...normalized].some((character) => {
    const code = character.charCodeAt(0)
    return code < 32 || code === 127
  })) return null
  return normalized
}

function validMethod(method) {
  if (!exactObject(method, [
    'method_id',
    'method_version',
    'kind',
    'version_state',
    'supports_fields',
    'assurance_scope',
  ])) return false
  const expected = METHOD_DEFINITIONS[method.method_id]
  if (!expected) return false
  return method.method_version === expected.method_version
    && method.kind === expected.kind
    && method.version_state === expected.version_state
    && stableJson(method.supports_fields) === stableJson(expected.supports_fields)
    && method.assurance_scope === expected.assurance_scope
}

function validDisposition(value, methodIds) {
  return exactObject(value, ['state', 'reason_code', 'method_ids'])
    && DISPOSITION_STATES.has(value.state)
    && REASON_CODES.has(value.reason_code)
    && Array.isArray(value.method_ids)
    && value.method_ids.length <= MAX_METHODS
    && value.method_ids.every((methodId, index) => (
      typeof methodId === 'string'
      && methodIds.has(methodId)
      && (index === 0 || value.method_ids[index - 1] < methodId)
    ))
    && (value.state === 'unknown' ? value.method_ids.length === 0 : value.method_ids.length > 0)
}

function validAssurance(value) {
  return exactObject(value, [
    'state',
    'independent_validation',
    'source_reliability_conclusion',
    'fact_accuracy_conclusion',
    'reason_code',
  ])
    && value.state === 'catalog_labels_only'
    && value.independent_validation === 'not_performed'
    && value.source_reliability_conclusion === 'not_established'
    && value.fact_accuracy_conclusion === 'not_established'
    && value.reason_code === 'DIRECTORY_LABELS_ARE_NOT_RELIABILITY_FINDINGS'
}

function validMethodCard(profile) {
  const card = profile?.method_card
  if (!exactObject(card, [
    'schema_version',
    'profile_contract_version',
    'catalog_profile_version',
    'catalog_profile_version_state',
    'overall_state',
    'methods',
    'note_disposition_codes',
    'unknown_method_count',
    'method_input_truncated',
    'assurance',
    'field_dispositions',
  ])) return false
  if (
    profile.profile_contract_version !== PROFILE_CONTRACT_VERSION
    || card.schema_version !== METHOD_CARD_SCHEMA_VERSION
    || card.profile_contract_version !== PROFILE_CONTRACT_VERSION
    || !['recognized', 'unknown'].includes(card.catalog_profile_version_state)
    || !['controlled_catalog', 'partial_unknown', 'unknown'].includes(card.overall_state)
    || !Number.isInteger(card.unknown_method_count)
    || card.unknown_method_count < 0
    || card.unknown_method_count > MAX_UNKNOWN_METHODS
    || typeof card.method_input_truncated !== 'boolean'
    || !validAssurance(card.assurance)
    || !Array.isArray(card.methods)
    || card.methods.length > MAX_METHODS
    || !card.methods.every(validMethod)
  ) return false
  if (
    !Array.isArray(card.note_disposition_codes)
    || card.note_disposition_codes.length > MAX_UNKNOWN_METHODS
    || card.note_disposition_codes.some((code, index) => (
      !Object.hasOwn(NOTE_DISPOSITIONS, code)
      || (index > 0 && card.note_disposition_codes[index - 1] >= code)
    ))
  ) return false
  const methodIds = card.methods.map((method) => method.method_id)
  if (new Set(methodIds).size !== methodIds.length) return false
  if (card.catalog_profile_version_state === 'recognized') {
    if (
      card.catalog_profile_version !== KNOWN_PROFILE_VERSION
      || profile.profile_version !== KNOWN_PROFILE_VERSION
    ) return false
  } else if (card.catalog_profile_version !== null || profile.profile_version !== null) {
    return false
  }
  if (card.method_input_truncated && card.overall_state !== 'unknown') return false
  if (
    card.catalog_profile_version_state === 'recognized'
    && !card.method_input_truncated
    && card.methods.length > 0
    && (
      (card.unknown_method_count > 0 && card.overall_state !== 'partial_unknown')
      || (card.unknown_method_count === 0 && card.overall_state !== 'controlled_catalog')
    )
  ) return false
  if (card.methods.length === 0 && card.overall_state !== 'unknown') return false
  if (!exactObject(card.field_dispositions, FIELD_NAMES)) return false
  const methodIdSet = new Set(methodIds)
  return FIELD_NAMES.every((field) => (
    validDisposition(card.field_dispositions[field], methodIdSet)
  ))
}

function dispositionAllows(profile, field, states, allowedMethodIds = null) {
  const card = profile.method_card
  const disposition = card.field_dispositions[field]
  if (profile[field] === 'unknown') return disposition.state === 'unknown'
  if (!states.includes(disposition.state)) return false
  if (!allowedMethodIds) return true
  return disposition.method_ids.length > 0 && disposition.method_ids.every((methodId) => (
    allowedMethodIds.includes(methodId)
  ))
}

function profileMatchesMethodCard(profile) {
  if (!validMethodCard(profile)) return false
  const knownOrUnknown = (value, labels) => value === 'unknown' || Object.hasOwn(labels, value)
  if (!knownOrUnknown(profile.source_type, SOURCE_TYPE_LABELS)) return false
  if (!knownOrUnknown(profile.ownership_type, OWNERSHIP_LABELS)) return false
  if (!knownOrUnknown(profile.geo_alignment, GEO_LABELS)) return false
  if (!knownOrUnknown(profile.political_leaning, POLITICAL_LABELS)) return false
  if (!knownOrUnknown(profile.credibility_tier, CREDIBILITY_LABELS)) return false
  if (!knownOrUnknown(profile.label_confidence, CONFIDENCE_LABELS)) return false
  if (!knownOrUnknown(profile.review_status, REVIEW_LABELS)) return false
  if (!dispositionAllows(
    profile,
    'source_type',
    ['catalog_value'],
    ['historical_wave1_seed_v1', 'structural_review_v1'],
  )) return false
  if (!dispositionAllows(
    profile,
    'ownership_type',
    ['catalog_value'],
    [
      'historical_wave1_seed_v1',
      'structural_ownership_inference_v1',
      'structural_review_v1',
      'institutional_override_v1',
      'review_import_legacy',
    ],
  )) return false
  if (!dispositionAllows(
    profile,
    'geo_alignment',
    ['catalog_value'],
    ['historical_wave1_seed_v1', 'geo_alignment_rule_v1'],
  )) return false
  const politicalDisposition = profile.method_card.field_dispositions.political_leaning
  if (profile.political_leaning === 'unknown') {
    if (!dispositionAllows(profile, 'political_leaning', [])) return false
  } else if (politicalDisposition.state === 'third_party_catalog_label') {
    if (!dispositionAllows(
      profile,
      'political_leaning',
      ['third_party_catalog_label'],
      ['ground_news_rating_v1', 'mbfc_rating_v1'],
    )) return false
  } else if (
    profile.political_leaning !== 'state_aligned'
    || !dispositionAllows(
      profile,
      'political_leaning',
      ['institutional_catalog_label'],
      ['institutional_override_v1'],
    )
  ) return false
  if (!dispositionAllows(
    profile,
    'credibility_tier',
    ['third_party_catalog_label'],
    ['ground_news_rating_v1', 'mbfc_rating_v1'],
  )) return false
  if (!dispositionAllows(
    profile,
    'label_confidence',
    ['catalog_metadata'],
    ['ground_news_rating_v1', 'mbfc_rating_v1', 'institutional_override_v1'],
  )) return false
  return dispositionAllows(
    profile,
    'review_status',
    ['catalog_metadata'],
    ['historical_wave1_seed_v1'],
  )
}

function unknownModel(profile = {}) {
  const domain = boundedText(profile.domain, 253) || 'unknown'
  return {
    contractValid: false,
    domain,
    sourceName: boundedText(profile.source_name, 200) || domain,
    country: boundedText(profile.country, 128) || '未知地区',
    values: {
      sourceType: 'unknown',
      ownershipType: 'unknown',
      geoAlignment: 'unknown',
      politicalLeaning: 'unknown',
      credibilityTier: 'unknown',
      labelConfidence: 'unknown',
      reviewStatus: 'unknown',
    },
    labels: {
      sourceType: '来源类型未知',
      politicalLeaning: '政治倾向未评级',
      credibility: '目录标签：未知',
      confidence: '标签置信度未知',
      ownership: '所有权未知',
      geoAlignment: '地理分组未知',
      reviewStatus: '复核状态未知',
    },
    method: {
      status: '方法未知',
      schemaVersion: METHOD_CARD_SCHEMA_VERSION,
      profileVersion: '未知',
      methods: [],
      notes: [],
      unknownMethodCount: 0,
      limitation: '目录标签不代表独立事实准确率或来源可靠性结论。',
    },
    evidenceUrl: null,
    updatedAt: null,
  }
}

export function buildGroundNewsSourceProfileModel(value) {
  const profile = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  if (!profileMatchesMethodCard(profile)) return unknownModel(profile)
  const card = profile.method_card
  const structuralLabel = (field, labels, fallback) => (
    profile[field] === 'unknown' ? fallback : labels[profile[field]] || fallback
  )
  const political = profile.political_leaning === 'unknown'
    ? '政治倾向未评级'
    : `第三方目录政治倾向：${POLITICAL_LABELS[profile.political_leaning] || '未评级'}`
  const credibility = profile.credibility_tier === 'unknown'
    ? '目录标签：未知'
    : `第三方目录标签：${CREDIBILITY_LABELS[profile.credibility_tier] || '未知'}`
  const confidence = profile.label_confidence === 'unknown'
    ? '标签置信度未知'
    : `标签置信度：${CONFIDENCE_LABELS[profile.label_confidence] || '未知'}`
  return {
    contractValid: true,
    domain: boundedText(profile.domain, 253) || 'unknown',
    sourceName: boundedText(profile.source_name, 200)
      || boundedText(profile.domain, 253)
      || 'unknown',
    country: boundedText(profile.country, 128) || '未知地区',
    values: {
      sourceType: profile.source_type,
      ownershipType: profile.ownership_type,
      geoAlignment: profile.geo_alignment,
      politicalLeaning: profile.political_leaning,
      credibilityTier: profile.credibility_tier,
      labelConfidence: profile.label_confidence,
      reviewStatus: profile.review_status,
    },
    labels: {
      sourceType: structuralLabel('source_type', SOURCE_TYPE_LABELS, '来源类型未知'),
      politicalLeaning: political,
      credibility,
      confidence,
      ownership: structuralLabel('ownership_type', OWNERSHIP_LABELS, '所有权未知'),
      geoAlignment: structuralLabel('geo_alignment', GEO_LABELS, '地理分组未知'),
      reviewStatus: profile.review_status === 'unknown'
        ? '复核状态未知'
        : REVIEW_LABELS[profile.review_status] || '复核状态未知',
    },
    method: {
      status: card.overall_state === 'controlled_catalog'
        ? '目录方法已受控'
        : card.overall_state === 'partial_unknown'
          ? '部分方法未知'
          : '方法未知',
      schemaVersion: card.schema_version,
      profileVersion: card.catalog_profile_version || '未知',
      methods: card.overall_state === 'unknown' ? [] : card.methods.map((method) => ({
        id: method.method_id,
        label: METHOD_DEFINITIONS[method.method_id].label,
        version: method.method_version || '未版本化',
        scope: METHOD_DEFINITIONS[method.method_id].scope,
      })),
      notes: card.note_disposition_codes.map((code) => NOTE_DISPOSITIONS[code]),
      unknownMethodCount: card.unknown_method_count,
      limitation: '目录标签不代表独立事实准确率或来源可靠性结论。',
    },
    evidenceUrl: safeEvidenceUrl(profile.evidence_url),
    updatedAt: boundedText(profile.updated_at, 64),
  }
}

export const sourceProfileContractVersion = PROFILE_CONTRACT_VERSION
