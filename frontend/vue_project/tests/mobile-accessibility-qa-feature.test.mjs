import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { compileTemplate, parse } from '@vue/compiler-sfc'

import { buildStoryGraphAccessibleList } from '../src/features/story-graph/index.js'

const viewUrl = (name) => new URL(`../src/views/${name}`, import.meta.url)

test('story graph exposes the same stable node and edge payloads through its list alternative', () => {
  const graph = {
    story_id: 'story-1',
    nodes: [
      {
        id: 'node-a',
        story_id: 'story-1',
        source_kind: 'l2-segment',
        segment_id: 'segment-a',
        label: '节点 A',
        story_angle: 'main_event',
        article_count: 7,
        start_date: '2026-08-01',
        end_date: '2026-08-02',
      },
      {
        id: 'node-b',
        story_id: 'story-1',
        l1_cluster_id: 'cluster-b',
        label: '节点 B',
        event_type: 'context_update',
        article_count: 2,
      },
    ],
    edges: [
      {
        from_id: 'node-a',
        to_id: 'node-b',
        edge_type: 'continuation',
        relation_semantics: {
          schema_version: 'story-relation-semantics-v1',
          ontology_state: 'bounded',
          public_edge_type: 'continuation',
          relation_kind: 'temporal_sequence',
          temporal_basis: 'ordered_or_adjacent',
          causal_status: 'not_established',
          influence_status: 'not_established',
          evidence_role: 'derived_signal',
          derivation: 'stored_derived_relation',
          reason_code: 'TEMPORAL_ORDER_NOT_CAUSAL',
          source_reason_disclosed: false,
        },
        layer: 'story',
        edge_weight: 0.8,
      },
      {
        from_id: 'node-b',
        to_id: 'node-a',
        edge_type: 'context',
        relation_semantics: {
          schema_version: 'story-relation-semantics-v1',
          ontology_state: 'bounded',
          public_edge_type: 'context',
          relation_kind: 'contextual_association',
          temporal_basis: 'not_applicable',
          causal_status: 'not_established',
          influence_status: 'not_established',
          evidence_role: 'derived_signal',
          derivation: 'stored_derived_relation',
          reason_code: 'CONTEXT_ASSOCIATION_NOT_CAUSAL',
          source_reason_disclosed: false,
        },
        layer: 'context',
      },
    ],
  }

  const full = buildStoryGraphAccessibleList(graph)
  assert.deepEqual(full.nodes.map((item) => item.id), ['node-a', 'node-b'])
  assert.equal(full.nodes[0].payload.kind, 'l2-segment')
  assert.equal(full.nodes[0].payload.segmentId, 'segment-a')
  assert.equal(full.nodes[1].payload.kind, 'cluster')
  assert.equal(full.edges[0].id, 'node-a:node-b:continuation:story')
  assert.equal(full.edges[0].payload.sourceId, 'node-a')
  assert.equal(full.edges[0].payload.targetId, 'node-b')
  assert.match(full.edges[0].meta, /节点 A → 节点 B/)

  const withoutContext = buildStoryGraphAccessibleList(graph, { includeContext: false })
  assert.equal(withoutContext.edges.length, 1)
  assert.equal(withoutContext.edges[0].payload.layer, 'story')
})

test('data search provides mobile disclosures, a reachable sticky action row, and 44px targets', async () => {
  const source = await readFile(viewUrl('DataService/data-search.vue'), 'utf8')
  const styles = await readFile(viewUrl('DataService/data-search.less'), 'utf8')

  assert.match(source, /aria-controls="search-workspace-panel"/)
  assert.match(source, /:aria-expanded="workspacePanelExpanded"/)
  assert.match(source, /aria-controls="search-filter-controls"/)
  assert.match(source, /:aria-expanded="filtersExpanded"/)
  assert.match(source, /class="favorite-card-shell"/)
  const favoriteLinkBody = source.match(
    /<router-link class="favorite-card"[^>]*>([\s\S]*?)<\/router-link>/,
  )?.[1]
  assert.ok(favoriteLinkBody)
  assert.doesNotMatch(favoriteLinkBody, /<button/)
  assert.doesNotMatch(source, /class="l1-card"[^>]*@click=/)
  assert.match(source, /class="l1-card-header"[\s\S]*?:aria-expanded=/)

  assert.match(styles, /\.search-page button,[\s\S]*min-height:\s*44px;/)
  assert.match(styles, /\.favorite-card-close,[\s\S]*min-width:\s*44px;/)
  assert.match(styles, /\.filter-controls\.is-mobile-expanded\s*\{/)
  assert.match(styles, /\.main-content \.search-condition \.action-btns\s*\{[\s\S]*position:\s*sticky;/)
  assert.match(styles, /env\(safe-area-inset-bottom\)/)
})

test('Ground News routes keep long mobile pages reachable with disclosures, shortcuts, and 44px targets', async () => {
  const [home, desk, feed, source, timeline] = await Promise.all([
    readFile(viewUrl('GroundNewsHome.vue'), 'utf8'),
    readFile(viewUrl('GroundNewsDesk.vue'), 'utf8'),
    readFile(viewUrl('GroundNewsFeed.vue'), 'utf8'),
    readFile(viewUrl('GroundNewsSource.vue'), 'utf8'),
    readFile(viewUrl('GroundNewsTimeline.vue'), 'utf8'),
  ])

  assert.match(home, /\.ground-home button,[\s\S]*min-height:\s*44px;/)
  assert.match(home, /\.ground-home \.section-nav\s*\{[\s\S]*position:\s*sticky;/)
  assert.match(home, /overflow-x:\s*auto;/)

  assert.match(desk, /aria-controls="ground-news-desk-filter-controls"/)
  assert.match(desk, /:aria-expanded="mobileFiltersExpanded"/)
  assert.match(desk, /\.desk-filter-controls\.is-mobile-expanded\s*\{/)
  assert.match(desk, /\.search-card\s*\{[\s\S]*position:\s*sticky;/)
  assert.match(desk, /\.ground-page button,[\s\S]*min-height:\s*44px;/)
  assert.doesNotMatch(desk, /class="timeline-row"[^>]*@click=/)
  assert.match(desk, /role="dialog"[\s\S]*?aria-modal="true"/)
  assert.match(desk, /class="story-modal__close" aria-label="关闭相关新闻"/)
  assert.match(desk, /trapDialogTab\(event, segmentDialogRef\.value/)
  assert.match(desk, /restoreDialogFocus\(segmentReturnFocusTarget\)/)

  assert.match(feed, /\.ground-feed \.back-link,[\s\S]*min-height:\s*44px;/)
  assert.match(source, /\.source-page \.back-link,[\s\S]*min-height:\s*44px;/)
  assert.match(timeline, /\.node-actions a\s*\{[\s\S]*min-height:\s*44px;/)
})

test('global freshness notice stays in document flow and remains operable when collapsed', async () => {
  const [notice, app] = await Promise.all([
    readFile(new URL('../src/components/DataFreshnessNotice.vue', import.meta.url), 'utf8'),
    readFile(new URL('../src/App.vue', import.meta.url), 'utf8'),
  ])
  const { descriptor, errors } = parse(notice, { filename: 'DataFreshnessNotice.vue' })
  assert.deepEqual(errors, [])
  assert.ok(descriptor.template)
  const compiled = compileTemplate({
    filename: 'DataFreshnessNotice.vue',
    id: 'qa-data-freshness-notice',
    source: descriptor.template.content,
  })
  assert.deepEqual(compiled.errors, [])

  assert.match(app, /<appNav[\s\S]*?<DataFreshnessNotice[\s\S]*?id="main-content"/)
  assert.doesNotMatch(notice, /\.freshness-notice\s*\{[^}]*position:\s*fixed/s)
  assert.match(notice, /\.freshness-notice\s*\{[^}]*width:\s*min\(920px,\s*calc\(100vw - 28px\)\)/s)
  assert.match(notice, /@media \(max-width:\s*720px\)[\s\S]*width:\s*calc\(100vw - 20px\)/)
  assert.match(notice, /min-width:\s*0/)
  assert.match(notice, /:aria-expanded=/)
  assert.match(notice, /aria-controls="freshness-notice-detail"/)
  assert.match(notice, /@media \(prefers-reduced-motion:\s*reduce\)/)
  assert.doesNotMatch(notice, /@keyframes/)
  assert.match(notice, /transition:\s*none/)
  assert.match(notice, /animation:\s*none/)
})

test('story graph canvas supports keyboard selection and an explicit list alternative', async () => {
  const [view, renderer, rendererStyles] = await Promise.all([
    readFile(viewUrl('StoryGraphView.vue'), 'utf8'),
    readFile(new URL('../src/features/story-graph/story-graph-react-flow.js', import.meta.url), 'utf8'),
    readFile(new URL('../src/features/story-graph/story-graph-react-flow.css', import.meta.url), 'utf8'),
  ])

  assert.match(view, /role="region"[\s\S]*aria-describedby="story-graph-keyboard-help"/)
  assert.match(view, /class="intel-accessible-list"/)
  assert.match(view, /buildStoryGraphAccessibleList\(currentStory\.value/)
  assert.match(view, /\.intel-accessible-list > summary\s*\{[\s\S]*min-height:\s*44px;/)
  assert.match(view, /\.react-flow__node:focus-visible/)
  assert.match(renderer, /nodesFocusable:\s*true/)
  assert.match(renderer, /edgesFocusable:\s*true/)
  assert.match(renderer, /onSelectionChange\(/)
  assert.match(renderer, /selectable:\s*false,[\s\S]*focusable:\s*false/)
  assert.match(renderer, /focusable:\s*true,[\s\S]*ariaLabel:\s*`图谱节点/)
  assert.match(renderer, /ariaLabel:\s*`图谱节点/)
  assert.match(renderer, /ariaLabel:\s*`图谱关系/)
  assert.match(renderer, /interactionWidth:\s*44/)
  assert.match(rendererStyles, /\.story-rf-toolbar__button\s*\{[\s\S]*min-height:\s*44px;/)
})

test('all changed Vue views remain valid compilable SFC templates', async () => {
  const names = [
    'DataService/data-search.vue',
    'GroundNewsHome.vue',
    'GroundNewsDesk.vue',
    'GroundNewsFeed.vue',
    'GroundNewsSource.vue',
    'GroundNewsTimeline.vue',
    'StoryGraphView.vue',
  ]

  for (const name of names) {
    const source = await readFile(viewUrl(name), 'utf8')
    const { descriptor, errors } = parse(source, { filename: name })
    assert.deepEqual(errors, [], `${name}: parse errors`)
    assert.ok(descriptor.template, `${name}: missing template`)
    const compiled = compileTemplate({
      filename: name,
      id: `qa-${name.replaceAll('/', '-')}`,
      source: descriptor.template.content,
    })
    assert.deepEqual(compiled.errors, [], `${name}: template compile errors`)
  }
})
