export const MODEL_ASSURANCE_SCHEMA_VERSION = 'globemind.model-assurance.v1'
export const MODEL_ASSURANCE_CONTRACT_VERSION = '1.0.0'
export const MODEL_ASSURANCE_METRIC_METHOD_VERSION = 'binary-assurance-metrics-1.0.0'
export const MODEL_ASSURANCE_STORE_SCHEMA_VERSION = 'globemind.model-assurance.entry.v1'

export const MODEL_ASSURANCE_DIMENSIONS = Object.freeze(['country', 'language', 'topic'])

const EVALUATION_ID = /^eval\.[a-z0-9][a-z0-9_.-]{1,119}$/
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$/
const SHA256 = /^[0-9a-f]{64}$/
const USER_REFERENCE = /^user:[1-9][0-9]*$/
const TIMEZONE_SUFFIX = /(?:Z|[+-][0-9]{2}:[0-9]{2})$/
const COUNTRY_CODE = /^[A-Z0-9][A-Z0-9-]{1,11}$/
const LANGUAGE_CODE = /^[A-Za-z][A-Za-z0-9-]{1,23}$/
const TOPIC_CODE = /^[a-z0-9][a-z0-9_.-]{1,63}$/
const MAX_MANIFEST_CHARACTERS = 1024 * 1024
const TOLERANCE = 1e-8

const STATUS_VALUES = Object.freeze({
  operational: ['not_observed', 'observed'],
  release: ['blocked', 'eligible'],
  gold: ['not_observed', 'manifest_attested'],
  gate: ['blocked', 'eligible'],
  drift: ['not_observed', 'within_threshold', 'detected'],
  rollback: ['proceed', 'hold_release', 'rollback_to_baseline'],
})

export class ModelAssuranceContractError extends Error {
  constructor(path, reason) {
    super(`模型保障响应不符合受信契约：${path} ${reason}`)
    this.name = 'ModelAssuranceContractError'
    this.code = 'MODEL_ASSURANCE_CONTRACT_INVALID'
    this.path = path
  }
}

function fail(path, reason) {
  throw new ModelAssuranceContractError(path, reason)
}

function isRecord(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function recordValue(value, path) {
  if (!isRecord(value)) fail(path, '必须是对象')
  return value
}

function stringValue(value, path, { pattern, nullable = false } = {}) {
  if (nullable && value === null) return null
  if (typeof value !== 'string' || value.length === 0 || value.trim() !== value) {
    fail(path, '必须是非空且已规范化的字符串')
  }
  if (pattern && !pattern.test(value)) fail(path, '格式无效')
  return value
}

function dateValue(value, path) {
  const normalized = stringValue(value, path)
  if (!TIMEZONE_SUFFIX.test(normalized) || Number.isNaN(Date.parse(normalized))) {
    fail(path, '必须是带时区的时间')
  }
  return normalized
}

function booleanValue(value, path) {
  if (typeof value !== 'boolean') fail(path, '必须是布尔值')
  return value
}

function integerValue(value, path, { minimum = 0, maximum = 1_000_000_000 } = {}) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    fail(path, '必须是范围内的安全整数')
  }
  return value
}

function numberValue(value, path, { minimum = 0, maximum = 1, nullable = false } = {}) {
  if (nullable && value === null) return null
  if (typeof value !== 'number' || !Number.isFinite(value) || value < minimum || value > maximum) {
    fail(path, '必须是范围内的有限数值')
  }
  return value
}

function enumValue(value, path, allowed) {
  if (!allowed.includes(value)) fail(path, '枚举值无效')
  return value
}

function uniqueStringArray(value, path, { pattern, maximum = 500 } = {}) {
  if (!Array.isArray(value) || value.length > maximum) fail(path, '必须是有界数组')
  const result = value.map((item, index) =>
    stringValue(item, `${path}[${index}]`, { pattern }),
  )
  if (new Set(result).size !== result.length) fail(path, '不能包含重复值')
  return Object.freeze(result)
}

function nullableSha(value, path) {
  return value === null ? null : stringValue(value, path, { pattern: SHA256 })
}

function nullableReference(value, path) {
  return value === null ? null : stringValue(value, path)
}

function approximatelyEqual(left, right) {
  return Math.abs(left - right) <= TOLERANCE * Math.max(1, Math.abs(left), Math.abs(right))
}

function sameValues(left, right) {
  if (left.length !== right.length) return false
  const leftSorted = [...left].sort()
  const rightSorted = [...right].sort()
  return leftSorted.every((value, index) => value === rightSorted[index])
}

function sanitizeReasonCodes(value, path) {
  return uniqueStringArray(value, path, { pattern: /^[A-Z0-9_]+$/, maximum: 100 })
}

function sanitizeSummary(value, path) {
  const raw = recordValue(value, path)
  const gateState = enumValue(raw.gate_state, `${path}.gate_state`, STATUS_VALUES.gate)
  const releaseEligible = booleanValue(raw.release_eligible, `${path}.release_eligible`)
  const driftState = enumValue(raw.drift_state, `${path}.drift_state`, STATUS_VALUES.drift)
  const rollbackAction = enumValue(
    raw.rollback_action,
    `${path}.rollback_action`,
    STATUS_VALUES.rollback,
  )
  const reasonCodes = sanitizeReasonCodes(raw.reason_codes, `${path}.reason_codes`)
  if (releaseEligible !== (gateState === 'eligible')) fail(path, '门禁与放行声明矛盾')
  if (releaseEligible && (reasonCodes.length || driftState !== 'within_threshold')) {
    fail(path, '放行摘要仍包含阻断或未建立漂移基准')
  }
  if (releaseEligible !== (rollbackAction === 'proceed')) fail(path, '回滚动作与门禁矛盾')
  if (rollbackAction === 'rollback_to_baseline' && driftState !== 'detected') {
    fail(path, '未检测到漂移却建议回滚')
  }
  return Object.freeze({
    evaluationId: stringValue(raw.evaluation_id, `${path}.evaluation_id`, {
      pattern: EVALUATION_ID,
    }),
    modelId: stringValue(raw.model_id, `${path}.model_id`, { pattern: IDENTIFIER }),
    modelVersion: stringValue(raw.model_version, `${path}.model_version`),
    methodVersion: stringValue(raw.method_version, `${path}.method_version`),
    datasetId: stringValue(raw.dataset_id, `${path}.dataset_id`, { pattern: IDENTIFIER }),
    datasetSha256: stringValue(raw.dataset_sha256, `${path}.dataset_sha256`, {
      pattern: SHA256,
    }),
    cutoffAt: dateValue(raw.cutoff_at, `${path}.cutoff_at`),
    storedAt: dateValue(raw.stored_at, `${path}.stored_at`),
    entrySha256: stringValue(raw.entry_sha256, `${path}.entry_sha256`, { pattern: SHA256 }),
    gateState,
    releaseEligible,
    driftState,
    rollbackAction,
    reasonCodes,
  })
}

function sanitizeStatus(value) {
  const raw = recordValue(value, 'status')
  if (raw.schema_version !== MODEL_ASSURANCE_SCHEMA_VERSION) {
    fail('status.schema_version', '版本不受支持')
  }
  const available = booleanValue(raw.available, 'status.available')
  const operationalState = enumValue(
    raw.operational_state,
    'status.operational_state',
    STATUS_VALUES.operational,
  )
  const releaseStatus = enumValue(
    raw.release_status,
    'status.release_status',
    STATUS_VALUES.release,
  )
  const goldStandardState = enumValue(
    raw.gold_standard_state,
    'status.gold_standard_state',
    STATUS_VALUES.gold,
  )
  const evaluationCount = integerValue(raw.evaluation_count, 'status.evaluation_count')
  const eligibleCount = integerValue(raw.eligible_count, 'status.eligible_count')
  const latest = raw.latest === null ? null : sanitizeSummary(raw.latest, 'status.latest')
  const reasonCodes = sanitizeReasonCodes(raw.reason_codes, 'status.reason_codes')
  if (eligibleCount > evaluationCount) fail('status', '可放行数量超过评测总数')
  if (evaluationCount === 0) {
    if (
      available
      || operationalState !== 'not_observed'
      || releaseStatus !== 'blocked'
      || goldStandardState !== 'not_observed'
      || eligibleCount !== 0
      || latest !== null
      || !reasonCodes.includes('NO_EVALUATION_MANIFESTS')
      || !reasonCodes.includes('GOLD_STANDARD_NOT_OBSERVED')
      || !reasonCodes.includes('RELEASE_BLOCKED')
    ) {
      fail('status', '空账本必须保持 blocked/not_observed')
    }
  } else {
    if (!available || operationalState !== 'observed' || latest === null) {
      fail('status', '非空账本的观测状态矛盾')
    }
    if (releaseStatus !== (latest.releaseEligible ? 'eligible' : 'blocked')) {
      fail('status.release_status', '与最新评测门禁不一致')
    }
    if (latest.releaseEligible && (eligibleCount < 1 || goldStandardState !== 'manifest_attested')) {
      fail('status', '放行状态缺少清单级金标准声明')
    }
    if (!latest.releaseEligible && !reasonCodes.includes('RELEASE_BLOCKED')) {
      fail('status.reason_codes', '受阻状态缺少 RELEASE_BLOCKED')
    }
  }
  return Object.freeze({
    schemaVersion: MODEL_ASSURANCE_SCHEMA_VERSION,
    generatedAt: dateValue(raw.generated_at, 'status.generated_at'),
    available,
    operationalState,
    releaseStatus,
    goldStandardState,
    evaluationCount,
    eligibleCount,
    latest,
    reasonCodes,
  })
}

function sanitizeList(value) {
  if (!Array.isArray(value) || value.length > 500) fail('evaluations', '必须是有界数组')
  const rows = value.map((item, index) => sanitizeSummary(item, `evaluations[${index}]`))
  if (new Set(rows.map((item) => item.evaluationId)).size !== rows.length) {
    fail('evaluations', '评测 ID 重复')
  }
  if (new Set(rows.map((item) => item.entrySha256)).size !== rows.length) {
    fail('evaluations', 'entry SHA-256 重复')
  }
  return Object.freeze(rows)
}

function sanitizeConfusion(value, path) {
  const raw = recordValue(value, path)
  const result = Object.freeze({
    truePositive: integerValue(raw.true_positive, `${path}.true_positive`),
    falsePositive: integerValue(raw.false_positive, `${path}.false_positive`),
    trueNegative: integerValue(raw.true_negative, `${path}.true_negative`),
    falseNegative: integerValue(raw.false_negative, `${path}.false_negative`),
  })
  if (Object.values(result).reduce((sum, item) => sum + item, 0) <= 0) {
    fail(path, '样本不能为空')
  }
  return result
}

function sanitizeCalibrationBin(value, path) {
  const raw = recordValue(value, path)
  const result = {
    lowerBound: numberValue(raw.lower_bound, `${path}.lower_bound`),
    upperBound: numberValue(raw.upper_bound, `${path}.upper_bound`),
    sampleCount: integerValue(raw.sample_count, `${path}.sample_count`, { minimum: 1 }),
    positiveCount: integerValue(raw.positive_count, `${path}.positive_count`),
    predictedProbabilitySum: numberValue(raw.predicted_probability_sum, `${path}.predicted_probability_sum`, {
      maximum: 1_000_000_000,
    }),
    positiveProbabilitySum: numberValue(raw.positive_probability_sum, `${path}.positive_probability_sum`, {
      maximum: 1_000_000_000,
    }),
    squaredProbabilitySum: numberValue(raw.squared_probability_sum, `${path}.squared_probability_sum`, {
      maximum: 1_000_000_000,
    }),
  }
  if (result.upperBound <= result.lowerBound || result.positiveCount > result.sampleCount) {
    fail(path, 'bin 边界或计数无效')
  }
  const count = result.sampleCount
  const positiveCount = result.positiveCount
  const negativeCount = count - positiveCount
  const probabilitySum = result.predictedProbabilitySum
  const positiveProbabilitySum = result.positiveProbabilitySum
  const negativeProbabilitySum = probabilitySum - positiveProbabilitySum
  const squaredProbabilitySum = result.squaredProbabilitySum
  const minimumSum = result.lowerBound * result.sampleCount
  const maximumSum = result.upperBound * result.sampleCount
  const momentUpperBound = (result.lowerBound + result.upperBound) * probabilitySum
    - result.lowerBound * result.upperBound * count
  let groupedMomentLowerBound = 0
  if (positiveCount > 0) {
    groupedMomentLowerBound += positiveProbabilitySum ** 2 / positiveCount
  } else if (Math.abs(positiveProbabilitySum) > TOLERANCE) {
    fail(path, 'bin 概率充分统计不自洽')
  }
  if (negativeCount > 0) {
    groupedMomentLowerBound += negativeProbabilitySum ** 2 / negativeCount
  }
  const brierNumerator = squaredProbabilitySum
    - 2 * positiveProbabilitySum + positiveCount
  if (
    probabilitySum < minimumSum - TOLERANCE
    || probabilitySum > maximumSum + TOLERANCE
    || probabilitySum > count + TOLERANCE
    || positiveProbabilitySum > probabilitySum + TOLERANCE
    || positiveProbabilitySum > positiveCount + TOLERANCE
    || squaredProbabilitySum > probabilitySum + TOLERANCE
    || squaredProbabilitySum + TOLERANCE < probabilitySum ** 2 / count
    || squaredProbabilitySum > momentUpperBound + TOLERANCE
    || squaredProbabilitySum + TOLERANCE < groupedMomentLowerBound
    || squaredProbabilitySum + TOLERANCE < result.lowerBound ** 2 * count
    || squaredProbabilitySum > result.upperBound ** 2 * count + TOLERANCE
    || negativeProbabilitySum < -TOLERANCE
    || (positiveCount > 0 && (
      positiveProbabilitySum < result.lowerBound * positiveCount - TOLERANCE
      || positiveProbabilitySum > result.upperBound * positiveCount + TOLERANCE
    ))
    || (negativeCount > 0 && (
      negativeProbabilitySum < result.lowerBound * negativeCount - TOLERANCE
      || negativeProbabilitySum > result.upperBound * negativeCount + TOLERANCE
    ))
    || (negativeCount === 0 && Math.abs(negativeProbabilitySum) > TOLERANCE)
    || brierNumerator < -TOLERANCE
    || brierNumerator > count + TOLERANCE
  ) {
    fail(path, 'bin 概率充分统计不自洽')
  }
  return Object.freeze(result)
}

function sanitizeSlice(value, path, threshold) {
  const raw = recordValue(value, path)
  const confusion = sanitizeConfusion(raw.confusion, `${path}.confusion`)
  if (!Array.isArray(raw.calibration_bins) || raw.calibration_bins.length < 1 || raw.calibration_bins.length > 20) {
    fail(`${path}.calibration_bins`, '必须包含 1 至 20 个 bin')
  }
  const calibrationBins = Object.freeze(
    raw.calibration_bins.map((item, index) =>
      sanitizeCalibrationBin(item, `${path}.calibration_bins[${index}]`),
    ),
  )
  if (!approximatelyEqual(calibrationBins[0].lowerBound, 0)
    || !approximatelyEqual(calibrationBins.at(-1).upperBound, 1)) {
    fail(`${path}.calibration_bins`, 'bin 必须覆盖 0 到 1')
  }
  for (let index = 1; index < calibrationBins.length; index += 1) {
    if (!approximatelyEqual(calibrationBins[index - 1].upperBound, calibrationBins[index].lowerBound)) {
      fail(`${path}.calibration_bins`, 'bin 必须连续且不重叠')
    }
  }
  if (!calibrationBins.slice(1).some((item) => approximatelyEqual(item.lowerBound, threshold))) {
    fail(`${path}.calibration_bins`, '分类阈值必须是 bin 边界')
  }
  const sampleCount = confusion.truePositive + confusion.falsePositive
    + confusion.trueNegative + confusion.falseNegative
  const positiveCount = confusion.truePositive + confusion.falseNegative
  const predictedPositiveCount = confusion.truePositive + confusion.falsePositive
  if (calibrationBins.reduce((sum, item) => sum + item.sampleCount, 0) !== sampleCount
    || calibrationBins.reduce((sum, item) => sum + item.positiveCount, 0) !== positiveCount
    || calibrationBins
      .filter((item) => item.lowerBound >= threshold - TOLERANCE)
      .reduce((sum, item) => sum + item.sampleCount, 0) !== predictedPositiveCount) {
    fail(path, '校准计数与混淆矩阵不一致')
  }
  return Object.freeze({ confusion, calibrationBins })
}

function sanitizeDataset(value, path) {
  const raw = recordValue(value, path)
  const evaluationRole = enumValue(
    raw.evaluation_role,
    `${path}.evaluation_role`,
    ['holdout', 'evaluation_set', 'gold_standard'],
  )
  const goldStandardStatus = enumValue(
    raw.gold_standard_status,
    `${path}.gold_standard_status`,
    ['not_observed', 'declared', 'independently_reviewed'],
  )
  if (evaluationRole !== 'gold_standard' && goldStandardStatus !== 'not_observed') {
    fail(path, '非金标准数据集不能声明金标准状态')
  }
  return Object.freeze({
    datasetId: stringValue(raw.dataset_id, `${path}.dataset_id`, { pattern: IDENTIFIER }),
    datasetVersion: stringValue(raw.dataset_version, `${path}.dataset_version`),
    sha256: stringValue(raw.sha256, `${path}.sha256`, { pattern: SHA256 }),
    cutoffAt: dateValue(raw.cutoff_at, `${path}.cutoff_at`),
    evaluationRole,
    goldStandardStatus,
    labelSchemaVersion: nullableReference(raw.label_schema_version, `${path}.label_schema_version`),
    annotationProtocolRef: nullableReference(raw.annotation_protocol_ref, `${path}.annotation_protocol_ref`),
    provenanceRef: nullableReference(raw.provenance_ref, `${path}.provenance_ref`),
  })
}

function sanitizeModel(value, path) {
  const raw = recordValue(value, path)
  if (raw.task_type !== 'binary_classification') fail(`${path}.task_type`, '仅支持二元分类')
  return Object.freeze({
    modelId: stringValue(raw.model_id, `${path}.model_id`, { pattern: IDENTIFIER }),
    modelVersion: stringValue(raw.model_version, `${path}.model_version`),
    methodVersion: stringValue(raw.method_version, `${path}.method_version`),
    ownerOrganization: stringValue(raw.owner_organization, `${path}.owner_organization`),
    taskType: 'binary_classification',
    positiveLabel: stringValue(raw.positive_label, `${path}.positive_label`),
  })
}

function sanitizeCoverageDeclaration(value, path) {
  const raw = recordValue(value, path)
  return Object.freeze({
    countries: uniqueStringArray(raw.countries, `${path}.countries`, {
      pattern: COUNTRY_CODE,
      maximum: 100,
    }),
    languages: uniqueStringArray(raw.languages, `${path}.languages`, {
      pattern: LANGUAGE_CODE,
      maximum: 50,
    }),
    topics: uniqueStringArray(raw.topics, `${path}.topics`, {
      pattern: TOPIC_CODE,
      maximum: 100,
    }),
  })
}

function sanitizeThresholds(value, path) {
  if (value === null) return null
  const raw = recordValue(value, path)
  return Object.freeze({
    minimumPrecision: numberValue(raw.minimum_precision, `${path}.minimum_precision`),
    minimumRecall: numberValue(raw.minimum_recall, `${path}.minimum_recall`),
    minimumF1: numberValue(raw.minimum_f1, `${path}.minimum_f1`),
    maximumBrierScore: numberValue(raw.maximum_brier_score, `${path}.maximum_brier_score`),
    maximumEce: numberValue(raw.maximum_ece, `${path}.maximum_ece`),
    minimumStratumF1: numberValue(raw.minimum_stratum_f1, `${path}.minimum_stratum_f1`),
    minimumOverallSamples: integerValue(raw.minimum_overall_samples, `${path}.minimum_overall_samples`, {
      minimum: 1,
    }),
    minimumSamplesPerStratum: integerValue(
      raw.minimum_samples_per_stratum,
      `${path}.minimum_samples_per_stratum`,
      { minimum: 1 },
    ),
    maximumF1DropFromBaseline: numberValue(
      raw.maximum_f1_drop_from_baseline,
      `${path}.maximum_f1_drop_from_baseline`,
    ),
    maximumBrierIncreaseFromBaseline: numberValue(
      raw.maximum_brier_increase_from_baseline,
      `${path}.maximum_brier_increase_from_baseline`,
    ),
    maximumEceIncreaseFromBaseline: numberValue(
      raw.maximum_ece_increase_from_baseline,
      `${path}.maximum_ece_increase_from_baseline`,
    ),
  })
}

function sanitizeReview(value, path, dataset, model) {
  if (value === null) return null
  const raw = recordValue(value, path)
  const reviewedAt = dateValue(raw.reviewed_at, `${path}.reviewed_at`)
  const validUntil = raw.valid_until === null
    ? null
    : dateValue(raw.valid_until, `${path}.valid_until`)
  const reviewerOrganization = stringValue(
    raw.reviewer_organization,
    `${path}.reviewer_organization`,
  )
  const independenceAttestation = booleanValue(
    raw.independence_attestation,
    `${path}.independence_attestation`,
  )
  if (Date.parse(reviewedAt) < Date.parse(dataset.cutoffAt)) fail(path, '审阅早于数据截止时间')
  if (validUntil !== null && Date.parse(validUntil) <= Date.parse(reviewedAt)) {
    fail(path, '审阅有效期必须晚于审阅时间')
  }
  if (
    independenceAttestation
    && reviewerOrganization.toLocaleLowerCase() === model.ownerOrganization.toLocaleLowerCase()
  ) {
    fail(path, '独立审阅组织与模型所有者相同')
  }
  return Object.freeze({
    reviewId: stringValue(raw.review_id, `${path}.review_id`, { pattern: IDENTIFIER }),
    reviewerId: stringValue(raw.reviewer_id, `${path}.reviewer_id`),
    reviewerOrganization,
    independenceAttestation,
    decision: enumValue(raw.decision, `${path}.decision`, ['approved', 'rejected']),
    reviewedAt,
    validUntil,
    evidenceRef: stringValue(raw.evidence_ref, `${path}.evidence_ref`),
    evidenceSha256: stringValue(raw.evidence_sha256, `${path}.evidence_sha256`, {
      pattern: SHA256,
    }),
  })
}

function sanitizeEvaluationIntegrity(value, path, dataset) {
  if (value === null) return null
  const raw = recordValue(value, path)
  const developmentDatasetSha256s = uniqueStringArray(
    raw.development_dataset_sha256s,
    `${path}.development_dataset_sha256s`,
    { pattern: SHA256, maximum: 100 },
  )
  if (!developmentDatasetSha256s.length) fail(path, '必须声明至少一个开发数据集摘要')
  if (developmentDatasetSha256s.includes(dataset.sha256)) {
    fail(path, 'holdout 与开发集重叠')
  }
  return Object.freeze({
    labelSource: enumValue(
      raw.label_source,
      `${path}.label_source`,
      ['human_gold', 'silver', 'synthetic', 'unreviewed'],
    ),
    partitionRole: enumValue(
      raw.partition_role,
      `${path}.partition_role`,
      ['holdout', 'validation', 'development', 'not_observed'],
    ),
    holdoutAccessStatus: enumValue(
      raw.holdout_access_status,
      `${path}.holdout_access_status`,
      ['sealed', 'development_accessed', 'not_observed'],
    ),
    developmentDatasetSha256s,
    separationEvidenceRef: stringValue(
      raw.separation_evidence_ref,
      `${path}.separation_evidence_ref`,
    ),
    separationEvidenceSha256: stringValue(
      raw.separation_evidence_sha256,
      `${path}.separation_evidence_sha256`,
      { pattern: SHA256 },
    ),
  })
}

function sanitizeBaseline(value, path, evaluationId) {
  if (value === null) return null
  const raw = recordValue(value, path)
  const baseline = Object.freeze({
    evaluationId: stringValue(raw.evaluation_id, `${path}.evaluation_id`, {
      pattern: EVALUATION_ID,
    }),
    entrySha256: stringValue(raw.entry_sha256, `${path}.entry_sha256`, { pattern: SHA256 }),
  })
  if (baseline.evaluationId === evaluationId) fail(path, '不能引用自身作为基线')
  return baseline
}

function aggregatePartition(overall, strata, dimension) {
  const fields = ['truePositive', 'falsePositive', 'trueNegative', 'falseNegative']
  for (const field of fields) {
    const aggregate = strata.reduce((sum, item) => sum + item.slice.confusion[field], 0)
    if (aggregate !== overall.confusion[field]) fail(`manifest.strata.${dimension}`, '混淆矩阵未分区总体')
  }
  overall.calibrationBins.forEach((overallBin, index) => {
    const bins = strata.map((item) => item.slice.calibrationBins[index])
    for (const field of [
      'sampleCount',
      'positiveCount',
      'predictedProbabilitySum',
      'positiveProbabilitySum',
      'squaredProbabilitySum',
    ]) {
      const aggregate = bins.reduce((sum, item) => sum + item[field], 0)
      if (!approximatelyEqual(aggregate, overallBin[field])) {
        fail(`manifest.strata.${dimension}`, `校准字段 ${field} 未分区总体`)
      }
    }
  })
}

function sanitizeManifest(value, path = 'detail.manifest') {
  const raw = recordValue(value, path)
  const evaluationId = stringValue(raw.evaluation_id, `${path}.evaluation_id`, {
    pattern: EVALUATION_ID,
  })
  const dataset = sanitizeDataset(raw.dataset, `${path}.dataset`)
  const model = sanitizeModel(raw.model, `${path}.model`)
  const classificationThreshold = numberValue(
    raw.classification_threshold,
    `${path}.classification_threshold`,
  )
  if (classificationThreshold <= 0 || classificationThreshold >= 1) {
    fail(`${path}.classification_threshold`, '必须位于 0 与 1 之间')
  }
  const overall = sanitizeSlice(raw.overall, `${path}.overall`, classificationThreshold)
  if (!Array.isArray(raw.strata) || raw.strata.length > 250) fail(`${path}.strata`, '必须是有界数组')
  const strata = Object.freeze(raw.strata.map((item, index) => {
    const row = recordValue(item, `${path}.strata[${index}]`)
    return Object.freeze({
      dimension: enumValue(
        row.dimension,
        `${path}.strata[${index}].dimension`,
        MODEL_ASSURANCE_DIMENSIONS,
      ),
      value: stringValue(row.value, `${path}.strata[${index}].value`),
      slice: sanitizeSlice(row, `${path}.strata[${index}]`, classificationThreshold),
    })
  }))
  const identities = strata.map((item) => `${item.dimension}:${item.value}`)
  if (new Set(identities).size !== identities.length) fail(`${path}.strata`, '分层重复')
  for (const stratum of strata) {
    const sameBins = stratum.slice.calibrationBins.every((bin, index) => {
      const overallBin = overall.calibrationBins[index]
      return Boolean(overallBin)
        && approximatelyEqual(bin.lowerBound, overallBin.lowerBound)
        && approximatelyEqual(bin.upperBound, overallBin.upperBound)
    })
    if (!sameBins || stratum.slice.calibrationBins.length !== overall.calibrationBins.length) {
      fail(`${path}.strata`, '所有分层必须使用相同 bin')
    }
  }
  const coverage = sanitizeCoverageDeclaration(raw.coverage, `${path}.coverage`)
  const declaration = {
    country: coverage.countries,
    language: coverage.languages,
    topic: coverage.topics,
  }
  for (const dimension of MODEL_ASSURANCE_DIMENSIONS) {
    const dimensionStrata = strata.filter((item) => item.dimension === dimension)
    if (declaration[dimension].length
      && sameValues(declaration[dimension], dimensionStrata.map((item) => item.value))) {
      aggregatePartition(overall, dimensionStrata, dimension)
    }
  }
  const thresholds = sanitizeThresholds(raw.thresholds, `${path}.thresholds`)
  const review = sanitizeReview(raw.independent_review, `${path}.independent_review`, dataset, model)
  const evaluationIntegrity = sanitizeEvaluationIntegrity(
    raw.evaluation_integrity,
    `${path}.evaluation_integrity`,
    dataset,
  )
  const baseline = sanitizeBaseline(raw.baseline, `${path}.baseline`, evaluationId)
  return Object.freeze({
    evaluationId,
    evaluationVersion: stringValue(raw.evaluation_version, `${path}.evaluation_version`),
    dataset,
    model,
    classificationThreshold,
    overall,
    strata,
    coverage,
    thresholds,
    independentReview: review,
    evaluationIntegrity,
    baseline,
  })
}

function expectedMetrics(slice) {
  const counts = slice.confusion
  const sampleCount = counts.truePositive + counts.falsePositive
    + counts.trueNegative + counts.falseNegative
  const positiveCount = counts.truePositive + counts.falseNegative
  const predictedPositiveCount = counts.truePositive + counts.falsePositive
  const precision = predictedPositiveCount > 0 ? counts.truePositive / predictedPositiveCount : null
  const recall = positiveCount > 0 ? counts.truePositive / positiveCount : null
  const f1Denominator = 2 * counts.truePositive + counts.falsePositive + counts.falseNegative
  const f1 = f1Denominator > 0 ? 2 * counts.truePositive / f1Denominator : null
  const brierNumerator = slice.calibrationBins.reduce(
    (sum, bin) => sum + bin.squaredProbabilitySum
      - 2 * bin.positiveProbabilitySum + bin.positiveCount,
    0,
  )
  const brierScore = brierNumerator / sampleCount
  const expectedCalibrationError = slice.calibrationBins.reduce(
    (sum, bin) => sum + bin.sampleCount / sampleCount * Math.abs(
      bin.predictedProbabilitySum / bin.sampleCount - bin.positiveCount / bin.sampleCount,
    ),
    0,
  )
  return {
    sampleCount,
    positiveCount,
    predictedPositiveCount,
    precision,
    recall,
    f1,
    brierScore,
    expectedCalibrationError,
  }
}

function sanitizeMetrics(value, path, slice) {
  const raw = recordValue(value, path)
  const result = Object.freeze({
    sampleCount: integerValue(raw.sample_count, `${path}.sample_count`, { minimum: 1 }),
    positiveCount: integerValue(raw.positive_count, `${path}.positive_count`),
    predictedPositiveCount: integerValue(
      raw.predicted_positive_count,
      `${path}.predicted_positive_count`,
    ),
    precision: numberValue(raw.precision, `${path}.precision`, { nullable: true }),
    recall: numberValue(raw.recall, `${path}.recall`, { nullable: true }),
    f1: numberValue(raw.f1, `${path}.f1`, { nullable: true }),
    brierScore: numberValue(raw.brier_score, `${path}.brier_score`),
    expectedCalibrationError: numberValue(
      raw.expected_calibration_error,
      `${path}.expected_calibration_error`,
    ),
  })
  const expected = expectedMetrics(slice)
  for (const field of ['sampleCount', 'positiveCount', 'predictedPositiveCount']) {
    if (result[field] !== expected[field]) fail(`${path}.${field}`, '与输入计数不一致')
  }
  for (const field of ['precision', 'recall', 'f1', 'brierScore', 'expectedCalibrationError']) {
    if (expected[field] === null ? result[field] !== null : (
      result[field] === null || !approximatelyEqual(result[field], expected[field])
    )) {
      fail(`${path}.${field}`, '未通过客户端独立重算')
    }
  }
  return result
}

function sanitizeDimensionMap(value, path) {
  const raw = recordValue(value, path)
  return Object.freeze(Object.fromEntries(MODEL_ASSURANCE_DIMENSIONS.map((dimension) => [
    dimension,
    uniqueStringArray(raw[dimension], `${path}.${dimension}`, { maximum: 250 }),
  ])))
}

function sanitizeCoverageResult(value, path, manifest, resultStrata) {
  const raw = recordValue(value, path)
  const expected = sanitizeDimensionMap(raw.expected, `${path}.expected`)
  const observed = sanitizeDimensionMap(raw.observed, `${path}.observed`)
  const missing = sanitizeDimensionMap(raw.missing, `${path}.missing`)
  const unexpected = sanitizeDimensionMap(raw.unexpected, `${path}.unexpected`)
  const declaration = {
    country: manifest.coverage.countries,
    language: manifest.coverage.languages,
    topic: manifest.coverage.topics,
  }
  const thresholds = manifest.thresholds
  const minimumSamplesSatisfied = Boolean(
    thresholds
    && expectedMetrics(manifest.overall).sampleCount >= thresholds.minimumOverallSamples
    && resultStrata.every(
      (item) => item.metrics.sampleCount >= thresholds.minimumSamplesPerStratum,
    )
  )
  let dimensionsComplete = true
  for (const dimension of MODEL_ASSURANCE_DIMENSIONS) {
    const actualObserved = resultStrata
      .filter((item) => item.dimension === dimension)
      .map((item) => item.value)
    const actualMissing = declaration[dimension].filter((item) => !actualObserved.includes(item))
    const actualUnexpected = actualObserved.filter((item) => !declaration[dimension].includes(item))
    if (!sameValues(expected[dimension], declaration[dimension])
      || !sameValues(observed[dimension], actualObserved)
      || !sameValues(missing[dimension], actualMissing)
      || !sameValues(unexpected[dimension], actualUnexpected)) {
      fail(`${path}.${dimension}`, '覆盖统计与清单分层不一致')
    }
    if (!declaration[dimension].length || actualMissing.length || actualUnexpected.length) {
      dimensionsComplete = false
    }
  }
  const state = enumValue(raw.state, `${path}.state`, ['complete', 'incomplete'])
  if (booleanValue(raw.minimum_samples_satisfied, `${path}.minimum_samples_satisfied`)
    !== minimumSamplesSatisfied) {
    fail(`${path}.minimum_samples_satisfied`, '与阈值重算不一致')
  }
  if ((state === 'complete') !== (dimensionsComplete && minimumSamplesSatisfied)) {
    fail(`${path}.state`, '覆盖状态与重算结果矛盾')
  }
  return Object.freeze({
    state,
    expected,
    observed,
    missing,
    unexpected,
    minimumSamplesSatisfied,
  })
}

function sanitizeDrift(value, path, baseline) {
  const raw = recordValue(value, path)
  const state = enumValue(raw.state, `${path}.state`, STATUS_VALUES.drift)
  const baselineEvaluationId = raw.baseline_evaluation_id === null
    ? null
    : stringValue(raw.baseline_evaluation_id, `${path}.baseline_evaluation_id`, {
      pattern: EVALUATION_ID,
    })
  const result = Object.freeze({
    state,
    baselineEvaluationId,
    f1Delta: numberValue(raw.f1_delta, `${path}.f1_delta`, {
      minimum: -1,
      nullable: true,
    }),
    brierDelta: numberValue(raw.brier_delta, `${path}.brier_delta`, {
      minimum: -1,
      nullable: true,
    }),
    eceDelta: numberValue(raw.ece_delta, `${path}.ece_delta`, {
      minimum: -1,
      nullable: true,
    }),
    reasonCodes: sanitizeReasonCodes(raw.reason_codes, `${path}.reason_codes`),
  })
  const deltas = [result.f1Delta, result.brierDelta, result.eceDelta]
  if (baseline === null) {
    if (baselineEvaluationId !== null || state !== 'not_observed' || deltas.some((item) => item !== null)) {
      fail(path, '未声明基线却声称漂移观测')
    }
  } else {
    if (baselineEvaluationId !== baseline.evaluationId) fail(path, '漂移基线与清单不一致')
    if (state !== 'not_observed' && deltas.some((item) => item === null)) {
      fail(path, '漂移差值不完整')
    }
    if (state === 'not_observed' && deltas.some((item) => item === null)
      && deltas.some((item) => item !== null)) {
      fail(path, '未观测漂移差值只记录了一部分')
    }
  }
  if (state === 'within_threshold' && result.reasonCodes.length) fail(path, '阈值内漂移仍有原因码')
  if (state !== 'within_threshold' && !result.reasonCodes.length) fail(path, '缺少漂移原因码')
  return result
}

function sanitizeRollback(value, path, drift, baseline, releaseEligible) {
  const raw = recordValue(value, path)
  const action = enumValue(raw.action, `${path}.action`, STATUS_VALUES.rollback)
  const targetEvaluationId = raw.target_evaluation_id === null
    ? null
    : stringValue(raw.target_evaluation_id, `${path}.target_evaluation_id`, {
      pattern: EVALUATION_ID,
    })
  const reasonCodes = sanitizeReasonCodes(raw.reason_codes, `${path}.reason_codes`)
  if (releaseEligible !== (action === 'proceed')) fail(path, '动作与放行状态矛盾')
  if (action === 'proceed' && (targetEvaluationId !== null || reasonCodes.length)) {
    fail(path, '放行动作不能包含回滚目标或原因')
  }
  if (action === 'rollback_to_baseline'
    && (drift.state !== 'detected'
      || baseline === null
      || targetEvaluationId !== baseline.evaluationId
      || !reasonCodes.length)) {
    fail(path, '回滚目标或漂移证据不完整')
  }
  if (action === 'hold_release' && targetEvaluationId !== null) fail(path, '暂停动作不能带回滚目标')
  return Object.freeze({ action, targetEvaluationId, reasonCodes })
}

function releaseThresholdsSatisfied(thresholds, overall, strata) {
  return Boolean(
    thresholds
    && overall.precision !== null
    && overall.precision >= thresholds.minimumPrecision
    && overall.recall !== null
    && overall.recall >= thresholds.minimumRecall
    && overall.f1 !== null
    && overall.f1 >= thresholds.minimumF1
    && overall.brierScore <= thresholds.maximumBrierScore
    && overall.expectedCalibrationError <= thresholds.maximumEce
    && overall.sampleCount >= thresholds.minimumOverallSamples
    && strata.every((item) => (
      item.metrics.f1 !== null
      && item.metrics.f1 >= thresholds.minimumStratumF1
      && item.metrics.sampleCount >= thresholds.minimumSamplesPerStratum
    ))
  )
}

function sanitizeResult(value, path, manifest) {
  const raw = recordValue(value, path)
  if (raw.schema_version !== MODEL_ASSURANCE_SCHEMA_VERSION
    || raw.contract_version !== MODEL_ASSURANCE_CONTRACT_VERSION
    || raw.metric_method_version !== MODEL_ASSURANCE_METRIC_METHOD_VERSION
    || raw.evidence_status !== 'manifest_only') {
    fail(path, '结果版本或证据状态不受支持')
  }
  const evaluationId = stringValue(raw.evaluation_id, `${path}.evaluation_id`, {
    pattern: EVALUATION_ID,
  })
  if (evaluationId !== manifest.evaluationId) fail(path, '评测 ID 与清单不一致')
  const overall = sanitizeMetrics(raw.overall, `${path}.overall`, manifest.overall)
  if (!Array.isArray(raw.strata) || raw.strata.length !== manifest.strata.length) {
    fail(`${path}.strata`, '指标分层与清单不一致')
  }
  const manifestStrata = new Map(
    manifest.strata.map((item) => [`${item.dimension}:${item.value}`, item]),
  )
  const strata = Object.freeze(raw.strata.map((item, index) => {
    const row = recordValue(item, `${path}.strata[${index}]`)
    const dimension = enumValue(
      row.dimension,
      `${path}.strata[${index}].dimension`,
      MODEL_ASSURANCE_DIMENSIONS,
    )
    const stratumValue = stringValue(row.value, `${path}.strata[${index}].value`)
    const manifestStratum = manifestStrata.get(`${dimension}:${stratumValue}`)
    if (!manifestStratum) fail(`${path}.strata[${index}]`, '清单中不存在该分层')
    return Object.freeze({
      dimension,
      value: stratumValue,
      metrics: sanitizeMetrics(
        row.metrics,
        `${path}.strata[${index}].metrics`,
        manifestStratum.slice,
      ),
    })
  }))
  if (new Set(strata.map((item) => `${item.dimension}:${item.value}`)).size !== strata.length) {
    fail(`${path}.strata`, '结果分层重复')
  }
  const coverage = sanitizeCoverageResult(raw.coverage, `${path}.coverage`, manifest, strata)
  const drift = sanitizeDrift(raw.drift, `${path}.drift`, manifest.baseline)
  const gateState = enumValue(raw.gate_state, `${path}.gate_state`, STATUS_VALUES.gate)
  const releaseEligible = booleanValue(raw.release_eligible, `${path}.release_eligible`)
  const reasonCodes = sanitizeReasonCodes(raw.reason_codes, `${path}.reason_codes`)
  const evaluatedAt = dateValue(raw.evaluated_at, `${path}.evaluated_at`)
  if (releaseEligible !== (gateState === 'eligible')) fail(path, '门禁与放行布尔值矛盾')
  if (releaseEligible) {
    if (
      reasonCodes.length
      || coverage.state !== 'complete'
      || drift.state !== 'within_threshold'
      || !releaseThresholdsSatisfied(manifest.thresholds, overall, strata)
      || manifest.baseline === null
      || manifest.independentReview?.independenceAttestation !== true
      || manifest.independentReview?.decision !== 'approved'
      || manifest.independentReview?.validUntil === null
      || Date.parse(manifest.independentReview.reviewedAt) > Date.parse(evaluatedAt)
      || Date.parse(manifest.independentReview.validUntil) <= Date.parse(evaluatedAt)
      || manifest.evaluationIntegrity?.labelSource !== 'human_gold'
      || manifest.evaluationIntegrity?.partitionRole !== 'holdout'
      || manifest.evaluationIntegrity?.holdoutAccessStatus !== 'sealed'
      || manifest.dataset.evaluationRole !== 'gold_standard'
      || manifest.dataset.goldStandardStatus !== 'independently_reviewed'
      || manifest.dataset.labelSchemaVersion === null
      || manifest.dataset.annotationProtocolRef === null
      || manifest.dataset.provenanceRef === null
    ) {
      fail(path, '放行结果缺少阈值、覆盖、基线、金标准或独立审阅元数据')
    }
  } else if (!reasonCodes.length) {
    fail(`${path}.reason_codes`, '受阻结果必须给出原因码')
  }
  const rollback = sanitizeRollback(
    raw.rollback,
    `${path}.rollback`,
    drift,
    manifest.baseline,
    releaseEligible,
  )
  return Object.freeze({
    schemaVersion: MODEL_ASSURANCE_SCHEMA_VERSION,
    contractVersion: MODEL_ASSURANCE_CONTRACT_VERSION,
    metricMethodVersion: MODEL_ASSURANCE_METRIC_METHOD_VERSION,
    evaluationId,
    manifestSha256: stringValue(raw.manifest_sha256, `${path}.manifest_sha256`, {
      pattern: SHA256,
    }),
    evaluatedAt,
    evidenceStatus: 'manifest_only',
    overall,
    strata,
    coverage,
    drift,
    rollback,
    gateState,
    releaseEligible,
    reasonCodes,
  })
}

function sanitizeDetail(value) {
  const raw = recordValue(value, 'detail')
  if (raw.storage_schema_version !== MODEL_ASSURANCE_STORE_SCHEMA_VERSION) {
    fail('detail.storage_schema_version', '版本不受支持')
  }
  const sequence = integerValue(raw.sequence, 'detail.sequence', { minimum: 1, maximum: 10_000 })
  const storedAt = dateValue(raw.stored_at, 'detail.stored_at')
  const previousEntrySha256 = nullableSha(
    raw.previous_entry_sha256,
    'detail.previous_entry_sha256',
  )
  if ((sequence === 1) !== (previousEntrySha256 === null)) {
    fail('detail.previous_entry_sha256', '与账本序号矛盾')
  }
  const manifest = sanitizeManifest(raw.manifest)
  const result = sanitizeResult(raw.result, 'detail.result', manifest)
  if (Date.parse(storedAt) !== Date.parse(result.evaluatedAt)) fail('detail', '存储与评测时间不一致')
  return Object.freeze({
    storageSchemaVersion: MODEL_ASSURANCE_STORE_SCHEMA_VERSION,
    sequence,
    storedAt,
    submittedBy: stringValue(raw.submitted_by, 'detail.submitted_by', {
      pattern: USER_REFERENCE,
    }),
    previousEntrySha256,
    manifest,
    result,
    entrySha256: stringValue(raw.entry_sha256, 'detail.entry_sha256', { pattern: SHA256 }),
  })
}

export function sanitizeModelAssuranceResponse(kind, payload) {
  if (kind === 'status') return sanitizeStatus(payload)
  if (kind === 'list') return sanitizeList(payload)
  if (kind === 'detail') return sanitizeDetail(payload)
  throw new TypeError(`Unknown model assurance response kind: ${kind}`)
}

export function reconcileModelAssuranceLedger(status, evaluations) {
  if (!isRecord(status) || !Array.isArray(evaluations)) {
    throw new TypeError('Sanitized status and evaluations are required')
  }
  if (status.evaluationCount < evaluations.length) fail('ledger', '列表数量超过状态总数')
  if (status.evaluationCount === 0 && evaluations.length) fail('ledger', '空状态却返回评测列表')
  if (status.evaluationCount > 0 && !evaluations.length) fail('ledger', '非空状态未返回最新评测')
  if (evaluations.length) {
    const latest = evaluations[0]
    for (const field of [
      'evaluationId',
      'entrySha256',
      'gateState',
      'releaseEligible',
      'driftState',
      'rollbackAction',
    ]) {
      if (latest[field] !== status.latest?.[field]) fail(`ledger.latest.${field}`, '状态与列表矛盾')
    }
    const visibleEligible = evaluations.filter((item) => item.releaseEligible).length
    if (visibleEligible > status.eligibleCount) fail('ledger', '列表放行数超过状态汇总')
  }
  return Object.freeze({ status, evaluations })
}

export function emptyModelAssuranceLedger(phase = 'idle', error = '') {
  return Object.freeze({
    phase,
    status: null,
    evaluations: Object.freeze([]),
    error: typeof error === 'string' ? error : '',
  })
}

export function emptyModelAssuranceDetail(phase = 'idle', error = '') {
  return Object.freeze({
    phase,
    evaluation: null,
    error: typeof error === 'string' ? error : '',
  })
}

export function isModelAssuranceAbortError(error, signal) {
  return error?.name === 'AbortError'
    || error?.name === 'CanceledError'
    || error?.code === 'ERR_CANCELED'
    || Boolean(signal?.aborted)
}

export function createLatestModelAssuranceRequest({
  AbortControllerImpl = globalThis.AbortController,
} = {}) {
  if (typeof AbortControllerImpl !== 'function') {
    throw new TypeError('Model assurance requests require AbortController')
  }
  let generation = 0
  let active = null
  return Object.freeze({
    async run(executor) {
      active?.controller.abort()
      const id = ++generation
      const controller = new AbortControllerImpl()
      active = { id, controller }
      try {
        const value = await executor(controller.signal)
        if (id !== generation) return { status: 'stale', latest: false }
        return { status: 'success', latest: true, value }
      } catch (error) {
        if (id !== generation) return { status: 'stale', latest: false, error }
        if (isModelAssuranceAbortError(error, controller.signal)) {
          return { status: 'aborted', latest: true, error }
        }
        return { status: 'error', latest: true, error }
      } finally {
        if (active?.id === id) active = null
      }
    },
    cancel() {
      generation += 1
      active?.controller.abort()
      active = null
    },
  })
}

function freezeTree(value) {
  if (!value || typeof value !== 'object' || Object.isFrozen(value)) return value
  Object.values(value).forEach(freezeTree)
  return Object.freeze(value)
}

function validateManifestTree(value, path = 'manifest', depth = 0, counter = { count: 0 }) {
  counter.count += 1
  if (counter.count > 20_000 || depth > 40) throw new Error('评测清单过大或嵌套过深')
  if (!value || typeof value !== 'object') return
  for (const [key, child] of Object.entries(value)) {
    if (['__proto__', 'prototype', 'constructor'].includes(key)) {
      throw new Error(`${path}.${key} 是禁止的 JSON 键`)
    }
    if ([
      'precision',
      'recall',
      'f1',
      'brier_score',
      'expected_calibration_error',
      'release_eligible',
      'gate_state',
      'result',
    ].includes(key)) {
      throw new Error('提交清单不能包含客户端派生指标或放行结果')
    }
    validateManifestTree(child, `${path}.${key}`, depth + 1, counter)
  }
}

function assertNoDuplicateJsonKeys(source) {
  let index = 0

  function skipWhitespace() {
    while (' \n\r\t'.includes(source[index])) index += 1
  }

  function scanString({ decode = false } = {}) {
    const start = index
    index += 1
    while (index < source.length) {
      if (source[index] === '\\') {
        index += 2
      } else if (source[index] === '"') {
        index += 1
        return decode ? JSON.parse(source.slice(start, index)) : undefined
      } else {
        index += 1
      }
    }
    throw new Error('JSON 字符串未闭合')
  }

  function scanValue() {
    skipWhitespace()
    if (source[index] === '{') {
      index += 1
      skipWhitespace()
      const keys = new Set()
      if (source[index] === '}') {
        index += 1
        return
      }
      while (index < source.length) {
        skipWhitespace()
        const key = scanString({ decode: true })
        if (keys.has(key)) throw new Error('评测清单包含重复 JSON 键')
        keys.add(key)
        skipWhitespace()
        index += 1
        scanValue()
        skipWhitespace()
        if (source[index] === '}') {
          index += 1
          return
        }
        index += 1
      }
      return
    }
    if (source[index] === '[') {
      index += 1
      skipWhitespace()
      if (source[index] === ']') {
        index += 1
        return
      }
      while (index < source.length) {
        scanValue()
        skipWhitespace()
        if (source[index] === ']') {
          index += 1
          return
        }
        index += 1
      }
      return
    }
    if (source[index] === '"') {
      scanString()
      return
    }
    while (index < source.length && !' \n\r\t,]}'.includes(source[index])) index += 1
  }

  scanValue()
}

export function parseModelAssuranceManifestJson(source) {
  if (typeof source !== 'string' || !source.trim()) throw new Error('请输入完整 JSON 评测清单')
  if (source.length > MAX_MANIFEST_CHARACTERS) throw new Error('JSON 评测清单超过 1 MiB 前端限制')
  let manifest
  try {
    manifest = JSON.parse(source)
  } catch {
    throw new Error('JSON 语法无效；未提交任何内容')
  }
  assertNoDuplicateJsonKeys(source)
  if (!isRecord(manifest)) throw new Error('评测清单顶层必须是 JSON 对象')
  validateManifestTree(manifest)
  const allowed = new Set([
    'evaluation_id',
    'evaluation_version',
    'dataset',
    'model',
    'classification_threshold',
    'overall',
    'strata',
    'coverage',
    'thresholds',
    'independent_review',
    'evaluation_integrity',
    'baseline',
  ])
  const unknown = Object.keys(manifest).filter((key) => !allowed.has(key))
  if (unknown.length) throw new Error(`评测清单包含未知顶层字段：${unknown.join(', ')}`)
  for (const field of [
    'evaluation_id',
    'evaluation_version',
    'dataset',
    'model',
    'classification_threshold',
    'overall',
    'strata',
    'coverage',
  ]) {
    if (!Object.hasOwn(manifest, field)) throw new Error(`评测清单缺少字段：${field}`)
  }
  stringValue(manifest.evaluation_id, 'manifest.evaluation_id', { pattern: EVALUATION_ID })
  stringValue(manifest.evaluation_version, 'manifest.evaluation_version')
  const dataset = recordValue(manifest.dataset, 'manifest.dataset')
  stringValue(dataset.dataset_id, 'manifest.dataset.dataset_id', { pattern: IDENTIFIER })
  stringValue(dataset.sha256, 'manifest.dataset.sha256', { pattern: SHA256 })
  dateValue(dataset.cutoff_at, 'manifest.dataset.cutoff_at')
  const model = recordValue(manifest.model, 'manifest.model')
  stringValue(model.model_id, 'manifest.model.model_id', { pattern: IDENTIFIER })
  stringValue(model.model_version, 'manifest.model.model_version')
  stringValue(model.method_version, 'manifest.model.method_version')
  numberValue(manifest.classification_threshold, 'manifest.classification_threshold')
  recordValue(manifest.overall, 'manifest.overall')
  if (!Array.isArray(manifest.strata)) throw new Error('manifest.strata 必须是数组')
  recordValue(manifest.coverage, 'manifest.coverage')
  return freezeTree(manifest)
}

export function formatAssuranceMetric(value, digits = 4) {
  if (value === null || value === undefined) return '未定义'
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '未定义'
}

export function formatAssuranceDelta(value, digits = 4) {
  if (value === null || value === undefined) return '未观测'
  if (typeof value !== 'number' || !Number.isFinite(value)) return '未观测'
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`
}
