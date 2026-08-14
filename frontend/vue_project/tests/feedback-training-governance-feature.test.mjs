import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { buildFeedbackDto } from '../src/features/sentiment/dto.js'

const here = path.dirname(fileURLToPath(import.meta.url))

test('feedback DTO is an explicit structured non-training correction', () => {
  assert.deepEqual(buildFeedbackDto({ id: 7, impact_index: -12, sentiment: -0.3 }, 'too_negative'), {
    news_id: 7,
    correction: 'too_negative',
    purpose: 'quality_correction',
    training_consent: false,
    training_opt_out: true,
  })
})

test('feedback DTO rejects invalid identities and correction labels', () => {
  assert.throws(() => buildFeedbackDto({ id: true }, 'correct'), /news id/i)
  assert.throws(() => buildFeedbackDto({ id: 0 }, 'correct'), /news id/i)
  assert.throws(() => buildFeedbackDto({ id: 7 }, 'free text'), /correction/i)
})

test('feedback UI discloses the training, retention, and review boundary', () => {
  const source = fs.readFileSync(
    path.join(here, '../src/views/sentimentAnalysis.vue'),
    'utf8',
  )

  assert.match(source, /结构化纠错仅作待人工复核记录/)
  assert.match(source, /默认不用于模型训练/)
  assert.match(source, /保留期限.*尚未批准/)
  assert.match(source, /正式人审流程尚未配置/)
})
