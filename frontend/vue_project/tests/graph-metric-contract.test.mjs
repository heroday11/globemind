import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  GRAPH_METRIC_INVENTORY,
  graphMetricExplanation,
  graphMetricPresentation,
  projectGraphMetric,
} from '../src/governance/graphMetrics.js'
import {
  relationStrengthLabel,
  researchValueLabel,
  researchValueScore,
  sortStorySummariesByResearchValue,
} from '../src/features/story-graph/presentation.js'
import { qualityPct } from '../src/features/ground-news/presentation.js'

const expectedMetricIds = new Set([
  'graph_briefing.membership_score',
  'graph_briefing.opinion_aggregate',
  'graph_briefing.quality_score',
  'graph_sampling.coverage',
  'ground_news.blindspot_score',
  'ground_news.coverage_signal',
  'ground_news.event_research_value',
  'ground_news.rank_score',
  'ground_news.source_profile_labels',
  'ground_news.timeline_quality',
  'story_graph.edge_weight',
  'story_graph.layout_weight',
  'story_graph.quality_score',
  'story_graph.related_story_score',
  'story_graph.relation_strength',
  'story_graph.research_value',
])

test('graph metric inventory is bounded, exact, and explicitly incomplete', () => {
  assert.equal(GRAPH_METRIC_INVENTORY.schema_version, 'graph-metric-inventory-v1')
  assert.equal(GRAPH_METRIC_INVENTORY.complete_runtime_surface_claim, false)
  assert.deepEqual(new Set(GRAPH_METRIC_INVENTORY.metrics.map((item) => item.metric_id)), expectedMetricIds)
  assert.ok(GRAPH_METRIC_INVENTORY.metrics.length <= 32)

  for (const item of GRAPH_METRIC_INVENTORY.metrics) {
    assert.equal(item.fact_status, 'not_established')
    assert.equal(item.evidence_locator, null)
    assert.equal(item.method_card.metric_id, item.metric_id)
    assert.equal(item.method_card.schema_version, 'graph-metric-method-card-v1')
  }

  const byId = new Map(GRAPH_METRIC_INVENTORY.metrics.map((item) => [item.metric_id, item]))
  assert.match(byId.get('story_graph.research_value').method_card.formula, /quality_score \* 20 if 0 < quality_score <= 1/)
  assert.deepEqual(byId.get('story_graph.relation_strength').method_card.inputs, [
    'edge_weight',
    'shared_actor_count',
    'shared_topic_count',
    'relation_reason',
  ])
  assert.match(byId.get('story_graph.relation_strength').method_card.formula, /edge_weight >= 0\.75/)
  assert.match(byId.get('ground_news.blindspot_score').method_card.formula, /unknown_source_count\/max\(source_count,1\)\*26/)
  assert.match(byId.get('ground_news.coverage_signal').method_card.formula, /analysis_status=missing_political_ratings/)
})

test('raw scores, fake cards, free text, and locators cannot bypass the unknown gate', () => {
  for (const metricId of [...expectedMetricIds].filter((id) => ![
    'graph_sampling.coverage',
    'ground_news.source_profile_labels',
    'story_graph.layout_weight',
  ].includes(id))) {
    const result = projectGraphMetric(metricId, {
      value: 99.99,
      method_card: {
        schema_version: 'graph-metric-method-card-v1',
        metric_id: metricId,
        approval_state: 'approved',
        formula: 'trust me',
        inputs: ['free text'],
      },
      inputs: { weight: 0.99, reason: 'high confidence' },
      evidence_locator: 'https://example.test/fabricated',
    })
    assert.equal(result.value, null)
    assert.equal(result.value_state, 'unknown')
    assert.equal(result.usable_for_ranking, false)
    assert.equal(result.usable_as_fact, false)
    assert.equal(result.evidence_locator, null)
  }

  assert.equal(projectGraphMetric('user.free-text', { value: 100 }).reason_code, 'METRIC_NOT_IN_BOUNDED_INVENTORY')
})

test('presentation helpers suppress legacy graph quality, value, and relation scores', () => {
  const research = researchValueScore({
    article_count: 200,
    segment_count: 10,
    quality_score: 1,
    start_date: '2026-01-01',
    end_date: '2026-02-01',
  })
  assert.equal(research, null)
  assert.equal(researchValueLabel(research), '研究价值未知')
  assert.equal(researchValueLabel(100), '研究价值未知')
  assert.equal(qualityPct(1), '质量指标未知')
  assert.equal(relationStrengthLabel({
    weight: 0.99,
    usableAsFact: true,
    claim: { usable_as_fact: true },
    evidenceStatus: 'available',
  }), '关系强度未知（方法或证据不可用，不可作为事实）')

  assert.deepEqual(
    sortStorySummariesByResearchValue([
      { id: 'z', title: 'Z', article_count: 999, research_value: 100 },
      { id: 'a', title: 'A', article_count: 1, research_value: 0 },
    ]).map((item) => item.id),
    ['a', 'z'],
  )
})

test('explanation exposes formula, bounded input states, evidence state, and reason', () => {
  const explanation = graphMetricExplanation('story_graph.research_value', {
    inputs: { article_count: 12, segment_count: 3 },
  })
  assert.equal(explanation.metric_id, 'story_graph.research_value')
  assert.match(explanation.formula, /ln\(1 \+ article_count\)/)
  assert.ok(explanation.inputs.some((item) => item.field === 'article_count' && item.state === 'provided_unverified'))
  assert.ok(explanation.inputs.some((item) => item.field === 'quality_score' && item.state === 'unavailable'))
  assert.equal(explanation.evidence.state, 'unavailable')
  assert.equal(explanation.evidence.locator, null)
  assert.equal(explanation.value_state, 'unknown')
  assert.equal(graphMetricPresentation('ground_news.rank_score', { value: 99 }).valueLabel, '未知')
})

test('all visible graph score surfaces consume the centralized unknown presentation', () => {
  const storyView = readFileSync(new URL('../src/views/StoryGraphView.vue', import.meta.url), 'utf8')
  const timelineView = readFileSync(new URL('../src/views/GroundNewsTimeline.vue', import.meta.url), 'utf8')
  const homeView = readFileSync(new URL('../src/views/GroundNewsHome.vue', import.meta.url), 'utf8')
  const feedView = readFileSync(new URL('../src/views/GroundNewsFeed.vue', import.meta.url), 'utf8')
  const deskView = readFileSync(new URL('../src/views/GroundNewsDesk.vue', import.meta.url), 'utf8')
  const dataSearchView = readFileSync(new URL('../src/views/DataService/data-search.vue', import.meta.url), 'utf8')
  const renderer = readFileSync(new URL('../src/features/story-graph/story-graph-react-flow.js', import.meta.url), 'utf8')

  assert.match(storyView, /graphMetricExplanation/)
  assert.doesNotMatch(storyView, /\{\{\s*researchValueScore\(story\)\s*\}\}/)
  assert.doesNotMatch(storyView, /`权重 \$\{Number\(selectedInspector\.value\.weight\)/)

  assert.match(timelineView, /graphMetricExplanation/)
  assert.doesNotMatch(timelineView, /qualityPct\(chain\.quality_score\)/)
  assert.doesNotMatch(timelineView, /Math\.round\(node\.blindspot\.score\)/)

  assert.match(homeView, /graphMetricPresentation/)
  assert.doesNotMatch(homeView, /Math\.round\(leadStory\.rank_score \|\| 0\)/)

  assert.match(feedView, /graphMetricPresentation/)
  assert.doesNotMatch(feedView, /Math\.round\(props\.story\.blindspot_score/)

  assert.match(deskView, /graphMetricPresentation/)
  assert.doesNotMatch(deskView, /score \+= Math\.min\(32, sourceCount\.value \* 4\)/)
  assert.doesNotMatch(deskView, /Math\.round\(blindspotDetail\.score \|\| 0\)/)

  assert.match(dataSearchView, /hierarchyQualityExplanation\(item\)/)
  assert.doesNotMatch(dataSearchView, /Number\(item\.quality_score\)\.toFixed\(2\)/)

  assert.doesNotMatch(renderer, /relation\?\.score\s*\|\|\s*0/)
  assert.doesNotMatch(renderer, /weight:\s*edge\.edge_weight\s*\|\|\s*edge\.weight/)
})
