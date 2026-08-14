<template>
  <div class="intel-page">
    <header class="intel-topbar" data-tour="story-workspace">
      <div class="intel-topbar__brand">
        <span class="intel-kicker">GlobeMind</span>
        <div>
          <h1>{{ pageTitle }}</h1>
          <p>{{ pageSubtitle }}</p>
        </div>
      </div>

      <div class="intel-topbar__meta">
        <div class="intel-mode-switch" data-tour="story-level-switch" role="tablist" aria-label="图谱层级">
          <button
            type="button"
            role="tab"
            :aria-selected="graphMode === 'l3'"
            :class="{ 'is-active': graphMode === 'l3' }"
            @click="switchGraphMode('l3')"
          >
            L3 大事件
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="graphMode === 'l2'"
            :class="{ 'is-active': graphMode === 'l2' }"
            @click="switchGraphMode('l2')"
          >
            L2 走势链
          </button>
        </div>
        <span class="intel-badge intel-badge--build"> {{ graphModeLabel }} {{ activeRunId }} </span>
        <span class="intel-badge" v-if="currentStory">
          {{ currentStory.story_title || `走势链 ${currentStory.story_id}` }}
        </span>
        <span class="intel-badge intel-badge--soft" v-if="storyStatsText">
          {{ storyStatsText }}
        </span>
        <button type="button" class="intel-btn intel-btn--ghost" :disabled="!graph" @click="fitCanvas">
          适配视图
        </button>
        <button type="button" class="intel-btn intel-btn--ghost" :disabled="!graph" @click="toggleContextLinks">
          {{ showContextLinks ? '隐藏关联线' : '显示关联线' }}
        </button>
        <button type="button" class="intel-btn intel-btn--assistant intel-topbar__assistant" @click="openStoryAssistantDrawer()">
          数据助手
        </button>
      </div>
    </header>

    <div
      class="intel-shell"
      :class="{
        'is-left-collapsed': leftCollapsed,
      }"
    >
      <aside class="intel-side intel-side--left" data-tour="story-library" :class="{ 'is-collapsed': leftCollapsed }">
        <div v-if="!leftCollapsed" class="intel-panel">
          <div class="intel-panel__header">
            <div>
              <span class="intel-panel__eyebrow">{{ graphModeLabel }} 导航</span>
              <h2>{{ libraryTitle }}</h2>
            </div>
            <button type="button" class="intel-icon-btn" @click="leftCollapsed = true" title="收起导航">
              <span>⟨</span>
            </button>
          </div>

          <section class="intel-section">
            <label class="intel-label" for="story-graph-query">{{ searchLabel }}</label>
            <input
              id="story-graph-query"
              v-model.trim="storySearch"
              class="intel-input"
              type="text"
              :placeholder="searchPlaceholder"
              aria-describedby="story-graph-query-status"
            />
            <p
              id="story-graph-query-status"
              class="intel-query-status"
              :class="{ 'is-empty': storySearch && !filteredStories.length }"
              role="status"
              aria-live="polite"
            >
              {{ libraryQueryMessage }}
            </p>
          </section>

          <section class="intel-section intel-section--library">
            <div class="intel-section__title">
              <span>{{ libraryTitle }}</span>
              <span>{{ filteredStories.length }}</span>
            </div>
            <div class="intel-story-list">
              <button
                v-for="story in filteredStories"
                :key="story.id"
                type="button"
                class="intel-story-card"
                :class="{ 'is-active': String(story.id) === selectedStoryId }"
                @click="selectStory(story.id)"
              >
                <div class="intel-story-card__top">
                  <span class="intel-story-card__type">{{
                    familyLabel(story.family_group || story.event_type)
                  }}</span>
                  <span class="intel-story-card__count">{{
                    researchValueLabel(researchValueScore(story))
                  }}</span>
                </div>
                <strong>{{ displayStoryTitle(story) }}</strong>
                <small>{{ storyResearchLine(story) }}</small>
                <div class="intel-story-card__signals">
                  <span>{{ storyResearchMetric(story).valueLabel }}</span>
                  <span>{{ evidenceLevel(story.article_count) }}</span>
                  <span>{{ formatRange(story.start_date, story.end_date) }}</span>
                </div>
              </button>
            </div>
          </section>

          <section class="intel-section" v-if="currentStory">
            <div class="intel-section__title">
              <span>{{ currentLabel }}</span>
              <span>{{ currentStory.story_id }}</span>
            </div>
            <div class="intel-summary-card">
              <strong>{{
                displayStoryTitle(currentStory)
              }}</strong>
              <p>{{ buildStorySummaryLine(currentStory) }}</p>
              <div class="intel-research-strip">
                <span v-for="signal in currentResearchSignals" :key="signal.label">
                  <label>{{ signal.label }}</label>
                  <strong>{{ signal.value }}</strong>
                </span>
              </div>
              <div class="intel-chip-row">
                <span class="intel-chip">{{
                  familyLabel(currentStory.meta?.dominant_type || 'mixed')
                }}</span>
                <span class="intel-chip">{{
                  graphMode === 'l3'
                    ? `${graphMetrics.mainNodes} 个 L2 节点`
                    : `${graphMetrics.mainNodes} 个走势节点`
                }}</span>
                <span class="intel-chip">{{ evidenceLevel(currentStory.meta?.article_count) }}</span>
              </div>
            </div>
          </section>

          <section class="intel-section intel-section--related" v-if="branchStories.length">
            <div class="intel-section__title">
              <span>关联分支</span>
              <span>{{ branchStories.length }}</span>
            </div>
            <div class="intel-related-list">
              <button
                v-for="item in branchStories"
                :key="item.story_id"
                type="button"
                class="intel-related-card"
                @click="selectStory(item.story_id)"
              >
                <div class="intel-related-card__meta">
                  <span>{{ item.layer }}</span>
                  <span>{{ relationLabel(item) }}</span>
                </div>
                <strong>{{ item.title || `走势链 ${item.story_id}` }}</strong>
                <small>{{ formatRange(item.start_date, item.end_date) }}</small>
              </button>
            </div>
          </section>
        </div>
      </aside>

      <main class="intel-canvas-panel" data-tour="story-canvas">
        <button
          v-if="leftCollapsed"
          type="button"
          class="intel-side-toggle intel-side-toggle--left"
          @click="leftCollapsed = false"
          title="打开导航"
        >
          导航
        </button>
        <div class="intel-canvas-toolbar">
          <div class="intel-chip-row">
            <span class="intel-chip intel-chip--signal" v-if="graphMetrics.mainNodes">
              {{ graphMode === 'l3' ? '宏观节点' : '主链' }} {{ graphMetrics.mainNodes }}
            </span>
            <span class="intel-chip" v-if="graphMetrics.branchStories">
              分支 {{ graphMetrics.branchStories }}
            </span>
            <span class="intel-chip" v-if="graphMetrics.contextLinks">
              关联线 {{ graphMetrics.contextLinks }}
            </span>
          </div>
        </div>

        <p
          v-if="currentStory && samplingNotice"
          class="intel-sampling-notice"
          role="status"
          aria-live="polite"
        >
          {{ samplingNotice }}
        </p>

        <p id="story-graph-keyboard-help" class="intel-sr-only">
          图谱节点和关系可用 Tab 聚焦，并用 Enter 或空格选择。也可展开键盘与列表视图逐项浏览。
        </p>
        <div
          ref="graphContainer"
          class="intel-canvas"
          role="region"
          aria-label="事件故事关系图"
          aria-describedby="story-graph-keyboard-help"
        ></div>

        <details v-if="currentStory" class="intel-accessible-list">
          <summary>
            <span>键盘与列表视图</span>
            <strong>{{ accessibleGraphList.nodes.length }} 节点 · {{ accessibleGraphList.edges.length }} 关系</strong>
          </summary>
          <div class="intel-accessible-list__body">
            <p>列表与画布使用同一图谱数据；选择一项会打开相同的结构化焦点面板。</p>
            <section aria-labelledby="story-graph-node-list-title">
              <h2 id="story-graph-node-list-title">节点</h2>
              <ul>
                <li v-for="item in accessibleGraphList.nodes" :key="`accessible-node-${item.id}`">
                  <button type="button" @click="selectInspector(item.payload)">
                    <strong>{{ item.title }}</strong>
                    <small>{{ item.meta }}</small>
                  </button>
                </li>
              </ul>
            </section>
            <section v-if="accessibleGraphList.edges.length" aria-labelledby="story-graph-edge-list-title">
              <h2 id="story-graph-edge-list-title">关系</h2>
              <ul>
                <li v-for="item in accessibleGraphList.edges" :key="`accessible-edge-${item.id}`">
                  <button type="button" @click="selectInspector(item.payload)">
                    <strong>{{ item.title }}</strong>
                    <small>{{ item.meta }}</small>
                  </button>
                </li>
              </ul>
            </section>
          </div>
        </details>

        <div v-if="loading" class="intel-overlay" role="status" aria-live="polite">
          <div class="intel-spinner"></div>
          <span>正在加载{{ graphMode === 'l3' ? ' L3 大事件图谱' : ' L2 走势工作台' }}...</span>
        </div>

        <div v-else-if="error" class="intel-overlay intel-overlay--error" role="alert">
          <strong>走势图谱加载失败</strong>
          <p>{{ error }}</p>
          <button type="button" class="intel-btn" @click="reloadCurrentStory">重试</button>
        </div>

        <div v-else-if="!currentStory" class="intel-overlay intel-overlay--quiet">
          <strong>{{ queryStatusTitle }}</strong>
          <p :role="queryStatus.role" aria-live="polite">{{ queryStatus.message }}</p>
        </div>

        <div class="intel-footerbar" v-if="currentStory">
          <div class="intel-footerbar__focus">
            <span class="intel-footerbar__label">当前焦点</span>
            <strong>{{ selectedInspectorTitle }}</strong>
            <small>{{ selectedInspectorSubtitle }}</small>
          </div>
          <div class="intel-footerbar__legend">
            <span
              ><i class="legend-swatch legend-swatch--main"></i
              >{{ graphMode === 'l3' ? 'L3 主干' : 'L2 主链' }}</span
            >
            <span
              ><i class="legend-swatch legend-swatch--branch"></i
              >{{ graphMode === 'l3' ? '角度支线' : '关联分支' }}</span
            >
            <span><i class="legend-swatch legend-swatch--context"></i>关系假设</span>
          </div>
        </div>

        <Transition name="focus-sheet">
          <section v-if="focusPanelOpen && selectedInspector" class="intel-focus-sheet" @click.stop>
            <header class="intel-focus-sheet__header">
              <div>
                <span class="intel-focus-sheet__eyebrow">{{
                  focusKindLabel(selectedInspector.kind)
                }}</span>
                <h3>{{ selectedInspectorTitle }}</h3>
                <p>{{ selectedInspectorSubtitle }}</p>
              </div>
              <button type="button" class="intel-icon-btn" title="关闭详情" @click="closeFocusPanel">
                <span>×</span>
              </button>
            </header>

            <div class="intel-focus-sheet__chips">
              <span v-for="chip in selectedInspectorChips" :key="chip" class="intel-chip">
                {{ chip }}
              </span>
            </div>

            <div class="intel-focus-sheet__metrics">
              <div>
                <label>类型</label>
                <strong>{{ focusPrimaryType }}</strong>
              </div>
              <div>
                <label>研究价值</label>
                <strong>{{ focusResearchValueText }}</strong>
              </div>
              <div>
                <label>报道覆盖</label>
                <strong>{{ focusEvidenceText }}</strong>
              </div>
            </div>

            <details class="intel-metric-explanation">
              <summary>展开指标公式、输入和证据</summary>
              <dl>
                <div>
                  <dt>指标</dt>
                  <dd>{{ focusMetricExplanation.label }} · {{ focusMetricExplanation.valueLabel }}</dd>
                </div>
                <div>
                  <dt>方法版本</dt>
                  <dd>{{ focusMetricExplanation.method_version || '未建立' }}</dd>
                </div>
                <div>
                  <dt>公式</dt>
                  <dd>{{ focusMetricExplanation.formula }}</dd>
                </div>
                <div>
                  <dt>证据定位</dt>
                  <dd>{{ focusMetricExplanation.evidence.locator || '不可用' }}</dd>
                </div>
                <div>
                  <dt>状态</dt>
                  <dd>{{ focusMetricExplanation.reason_code }} · 不可用于事实或排序</dd>
                </div>
              </dl>
              <ul>
                <li v-for="input in focusMetricExplanation.inputs" :key="input.field">
                  <code>{{ input.field }}</code>：{{ input.state === 'provided_unverified' ? `已提供但未核验（${input.value}）` : '不可用' }}
                </li>
              </ul>
            </details>

            <div class="intel-focus-sheet__actions">
              <button
                v-if="selectedInspector.kind === 'l3-chain' && selectedInspector.l2ChainId"
                type="button"
                class="intel-btn"
                @click="openL2Chain(selectedInspector.l2ChainId)"
              >
                打开 L2
              </button>
              <RouterLink
                v-if="selectedInspector.detailUrl"
                class="intel-evidence-link"
                :to="selectedInspector.detailUrl"
              >
                打开 Story Page
              </RouterLink>
              <button type="button" class="intel-btn intel-btn--assistant" @click="openStoryAssistantDrawer(focusAssistantQuestion)">
                让助手研判
              </button>
            </div>

            <section class="intel-focus-section">
              <div class="intel-focus-section__title">
                <span>结构化信息</span>
                <span>{{ focusDetailRows.length }}</span>
              </div>
              <div class="intel-focus-detail-grid">
                <div v-for="row in focusDetailRows" :key="row.label">
                  <label>{{ row.label }}</label>
                  <strong>{{ row.value }}</strong>
                </div>
              </div>
            </section>

            <section v-if="focusInsightText" class="intel-focus-section">
              <div class="intel-focus-section__title">
                <span>研判线索</span>
                <span>{{ focusKindLabel(selectedInspector.kind) }}</span>
              </div>
              <p class="intel-focus-insight">{{ focusInsightText }}</p>
            </section>

            <section v-if="focusResearchActions.length" class="intel-focus-section">
              <div class="intel-focus-section__title">
                <span>研究使用建议</span>
                <span>{{ focusResearchActions.length }}</span>
              </div>
              <ul class="intel-research-actions">
                <li v-for="action in focusResearchActions" :key="action">{{ action }}</li>
              </ul>
            </section>

            <section class="intel-focus-news">
              <div class="intel-focus-news__title">
                <span>相关新闻</span>
                <span>{{ selectedEvidenceNews.length }}</span>
              </div>
              <div v-if="evidenceLoading" class="intel-evidence-loading">正在加载新闻证据...</div>
              <div v-else-if="focusEvidenceNews.length" class="intel-focus-news__list">
                <template v-for="item in focusEvidenceNews" :key="item.news_id">
                  <a
                    v-if="item.safe_url"
                    class="intel-focus-news__item"
                    :href="item.safe_url"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <small>{{ evidenceNewsMeta(item) }}</small>
                    <strong>{{ item.title || `新闻 ${item.news_id}` }}</strong>
                  </a>
                  <div
                    v-else
                    class="intel-focus-news__item is-unavailable"
                    aria-disabled="true"
                    title="原文链接不可用"
                  >
                    <small>{{ evidenceNewsMeta(item) }}</small>
                    <strong>{{ item.title || `新闻 ${item.news_id}` }}</strong>
                  </div>
                </template>
              </div>
              <div v-else class="intel-evidence-empty">
                {{ focusEmptyText }}
              </div>
            </section>
          </section>
        </Transition>
      </main>

    </div>

    <AssistantDrawer
      v-if="assistantDrawerOpen"
      v-model="assistantDrawerOpen"
      :drawer-key="assistantDrawerKey"
      :page-skill="storyGraphAssistantSkill"
      title="故事图谱数据助手"
      subtitle="读取当前图谱、焦点节点、关系边和证据新闻"
    />
    <button
      ref="assistantFabRef"
      type="button"
      :class="['intel-assistant-fab', { 'is-dragging': assistantFabDragging }]"
      :style="assistantFabStyle"
      title="打开数据助手"
      aria-label="打开数据助手，可拖动"
      @click="handleAssistantFabClick"
      @pointerdown="startAssistantFabDrag"
    >
      <span>AI</span>
      <strong>数据助手</strong>
    </button>
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue'
import { getCurrentUser, getToken } from '@/utils/auth'
import { safeExternalHttpUrl } from '@/utils/externalUrl.js'
import {
  graphMetricExplanation,
  graphMetricPresentation,
} from '@/governance/graphMetrics.js'
import {
  L2_RUN_ID as l2RunId,
  L3_RUN_ID as l3RunId,
  angleLabel,
  buildStoryGraphAccessibleList,
  buildGraphMetrics,
  buildStoryGraphEvidenceParams,
  buildStoryGraphQueryStatus,
  buildStoryStatsText,
  buildStorySummaryLine,
  cleanStoryTitle,
  createLatestRequestGate,
  edgeLabel,
  evidenceLevel,
  familyLabel,
  filterStorySummaries,
  focusKindLabel,
  formatActorPair,
  formatCountLabel,
  formatNewsDate,
  formatRange,
  graphSamplingNotice,
  getFallbackStoryTitle,
  getSummaryCountLabel,
  hasStoryGraphEvidenceTarget,
  loadStoryGraphRenderer,
  makeMainInspector,
  normalizeL2ChainSummary,
  normalizeL3MacroSummary,
  relationEvidenceText,
  relationLabel,
  relationResearchGuidance,
  relationStrengthLabel,
  reportingCoverageGuidance,
  researchValueLabel,
  researchValueScore,
  sortRelatedStories,
  sortStorySummariesByResearchValue,
  storyGraphApi,
  transformL2ChainResponse,
  transformL3MacroResponse,
} from '@/features/story-graph/index.js'

const ASSISTANT_SESSION_CACHE_KEY = 'story_graph_assistant_sessions_v1'
const ASSISTANT_AUTORUN_CONTEXT_KEY = 'data_assistant_autorun_context_v1'
const ASSISTANT_FAB_POSITION_KEY = 'story_graph_assistant_fab_position_v1'
const AssistantDrawer = defineAsyncComponent(() =>
  import('@/features/assistant/index.js').then((module) => module.AssistantDrawer),
)

const graphMode = ref('l3')
const stories = ref([])
const storySearch = ref('')
const selectedStoryId = ref('')
const currentStory = ref(null)
const listLoading = ref(false)
const workspaceLoading = ref(false)
const loading = computed(() => listLoading.value || workspaceLoading.value)
const error = ref('')
const queryStatus = ref(buildStoryGraphQueryStatus('idle', { mode: graphMode.value }))
const leftCollapsed = ref(false)
const showContextLinks = ref(true)
const agentMessages = ref([])
const assistantSessionId = ref('')
const selectedInspector = ref(null)
const selectedClusterDetail = ref(null)
const evidenceLoading = ref(false)
const focusPanelOpen = ref(false)
const assistantDrawerOpen = ref(false)
const assistantDrawerKey = ref(0)
const assistantFabRef = ref(null)
const assistantFabPosition = ref(null)
const assistantFabDragging = ref(false)
const assistantFabMoved = ref(false)

const graphContainer = ref(null)
const graph = shallowRef(null)
let reactFlowFactoryPromise = null

let resizeObserver = null
let listAbortController = null
let storyAbortController = null
let clusterAbortController = null
let messageId = 0
let resizeFitTimer = null
let assistantFabDrag = null
const listRequestGate = createLatestRequestGate()
const workspaceRequestGate = createLatestRequestGate()
const evidenceRequestGate = createLatestRequestGate()

const activeRunId = computed(() => (graphMode.value === 'l3' ? l3RunId : l2RunId))
const graphModeLabel = computed(() => (graphMode.value === 'l3' ? 'L3' : 'L2'))
const pageTitle = computed(() =>
  graphMode.value === 'l3' ? 'L3 大事件脉络图谱' : 'L2 事件走势图谱',
)
const pageSubtitle = computed(() =>
  graphMode.value === 'l3'
    ? '从 L2 micro-chain 聚合大事件 · 观察主线、支线、角度和待核验关系假设'
    : '从 L1.5 切面进入 L2 走势链 · 追踪阶段节点、关系强度和演化路径',
)
const libraryTitle = computed(() => (graphMode.value === 'l3' ? '大事件库' : '走势链库'))
const currentLabel = computed(() => (graphMode.value === 'l3' ? '当前大事件' : '当前走势'))
const searchLabel = computed(() => (graphMode.value === 'l3' ? '检索大事件' : '检索走势链'))
const searchPlaceholder = computed(() =>
  graphMode.value === 'l3'
    ? '输入国家、战争、冲突、贸易或宏观议题...'
    : '输入国家、主体、议题或链标题...',
)
const filteredStories = computed(() =>
  sortStorySummariesByResearchValue(filterStorySummaries(stories.value, storySearch.value)),
)
const libraryQueryMessage = computed(() => {
  const query = String(storySearch.value || '').trim()
  if (query && !filteredStories.value.length) {
    return buildStoryGraphQueryStatus('search-empty', {
      mode: graphMode.value,
      query,
    }).message
  }
  if (query) return `当前查询“${query}”匹配 ${filteredStories.value.length} 条。`
  return `当前批次共 ${stories.value.length} 条。`
})
const queryStatusTitle = computed(() => ({
  error: '当前查询失败',
  'list-empty': '当前批次暂无结果',
  'workspace-empty': '所选结果暂无图谱节点',
  'search-empty': '当前查询无结果',
  'list-loading': '正在加载事件库',
  'workspace-loading': '正在加载图谱',
  'selection-required': '请选择当前查询结果',
}[queryStatus.value.kind] || `请选择${graphMode.value === 'l3' ? '一个大事件' : '一条走势链'}`))
const branchStories = computed(() => sortRelatedStories(currentStory.value?.related_stories))
const graphMetrics = computed(() => buildGraphMetrics(currentStory.value, branchStories.value))
const accessibleGraphList = computed(() => buildStoryGraphAccessibleList(currentStory.value, {
  includeContext: showContextLinks.value,
}))
const storyStatsText = computed(() =>
  buildStoryStatsText(currentStory.value, graphMetrics.value, branchStories.value),
)
const samplingNotice = computed(() => graphSamplingNotice(currentStory.value))
const currentResearchSignals = computed(() => {
  const story = currentStory.value
  if (!story) return []
  const metric = storyResearchMetric({
    ...story,
    article_count: story.meta?.article_count,
    segment_count: story.meta?.segment_count,
    l2_chain_count: story.meta?.l2_chain_count,
    quality_score: story.meta?.quality_score,
  })
  const visibleNodes = Number.isInteger(story.meta?.visible_node_count)
    ? story.meta.visible_node_count
    : graphMetrics.value.mainNodes
  const totalNodes = Number.isInteger(story.meta?.total_node_count)
    ? story.meta.total_node_count
    : null
  return [
    { label: '研究价值', value: `${metric.valueLabel} · 公式/输入/证据未形成可发布合同` },
    { label: '报道规模', value: evidenceLevel(story.meta?.article_count) },
    {
      label: '结构覆盖',
      value: totalNodes === null
        ? `${visibleNodes} 个已加载节点 / 候选总数未知`
        : `${visibleNodes}/${totalNodes} 个候选节点（非完整图）`,
    },
    { label: '运行批次', value: story.meta?.run_id || activeRunId.value },
  ]
})

function storyResearchMetric(item = {}) {
  const meta = item.meta || {}
  return graphMetricPresentation('story_graph.research_value', {
    value: item.research_value,
    method_card: item.research_value_method_card,
    evidence_locator: item.research_value_evidence_locator,
    inputs: {
      article_count: item.article_count ?? item.articleCount ?? meta.article_count,
      segment_count: item.segment_count ?? item.segmentCount ?? meta.segment_count,
      l2_chain_count: item.l2_chain_count ?? item.cluster_count ?? meta.l2_chain_count,
      quality_score: item.quality_score ?? item.importance_score ?? meta.quality_score,
    },
  })
}
const assistantFabStyle = computed(() => {
  const position = assistantFabPosition.value
  if (!position) return {}
  return {
    left: `${Math.round(position.x)}px`,
    top: `${Math.round(position.y)}px`,
    right: 'auto',
    bottom: 'auto',
  }
})

const storyGraphAssistantSkill = computed(() => {
  const story = currentStory.value || {}
  const focus = selectedInspectorTitle.value || '尚未选择焦点'
  return {
    page: '事件故事脉络',
    path: '/data-service/story-graph',
    summary: `${story.story_title || story.story_id || '当前故事图谱'}；节点 ${(story.nodes || []).length} 个，关系边 ${(story.edges || []).length} 条；${samplingNotice.value || '抽样范围未知，不得视为完整图。'}当前焦点 ${focus}。`,
    access: [
      '数据服务 > 事件故事脉络',
      '数据搜索页的 L2/L3 结果可进入故事图谱',
      '新闻详情和观察台可通过链路跳转到对应故事',
    ],
    sections: [
      '左侧 L2/L3 故事列表',
      '中间关系图谱',
      '焦点节点/关系边面板',
      '证据新闻与助手分析台',
    ],
    actions: [
      '切换 L2/L3 故事',
      '搜索故事链',
      '点击节点或关系边聚焦',
      '打开证据新闻',
      '进入 L2 链详情',
    ],
    aiActions: [
      '解释当前图谱主线和待核验关系假设',
      '基于焦点节点/关系边指出证据缺口',
      '生成后续核验问题和报告提纲',
    ],
  }
})

const selectedInspectorTitle = computed(() => {
  if (!selectedInspector.value) {
    return displayStoryTitle(currentStory.value) || '尚未选择焦点'
  }
  return cleanStoryTitle(selectedInspector.value.title, selectedInspector.value) || displayStoryTitle(currentStory.value) || '走势焦点'
})

const selectedInspectorSubtitle = computed(() => {
  if (!selectedInspector.value) {
    return currentStory.value
      ? buildStorySummaryLine(currentStory.value)
      : '请选择一条走势链开始分析。'
  }
  if (selectedInspector.value.kind === 'l2-segment') {
    return `${angleLabel(selectedInspector.value.storyAngle || selectedInspector.value.eventType)} · ${formatRange(selectedInspector.value.startDate, selectedInspector.value.endDate)}`
  }
  if (selectedInspector.value.kind === 'l3-chain') {
    return `${familyLabel(selectedInspector.value.lane || selectedInspector.value.eventType)} · ${formatRange(selectedInspector.value.startDate, selectedInspector.value.endDate)}`
  }
  if (selectedInspector.value.kind === 'cluster') {
    return `${angleLabel(selectedInspector.value.eventType) || '未知类型'} · ${formatActorPair(selectedInspector.value.initiator, selectedInspector.value.target, '主体未知')}`
  }
  if (selectedInspector.value.kind === 'branch-story') {
    return `${relationLabel(selectedInspector.value.relation)} · ${formatRange(selectedInspector.value.startDate, selectedInspector.value.endDate)}`
  }
  if (selectedInspector.value.kind === 'edge') {
    return relationEvidenceText(selectedInspector.value)
  }
  return selectedInspector.value.subtitle || buildStorySummaryLine(currentStory.value)
})

const selectedInspectorChips = computed(() => {
  if (!selectedInspector.value) return []
  if (selectedInspector.value.kind === 'l2-segment') {
    return [
      angleLabel(selectedInspector.value.storyAngle || selectedInspector.value.eventType),
      formatCountLabel(selectedInspector.value.articleCount, {
        unit: '条新闻',
        unknown: '新闻数未知',
      }),
      formatRange(selectedInspector.value.startDate, selectedInspector.value.endDate),
    ].filter(Boolean)
  }
  if (selectedInspector.value.kind === 'l3-chain') {
    return [
      familyLabel(selectedInspector.value.lane || selectedInspector.value.eventType),
      formatCountLabel(selectedInspector.value.segmentCount, {
        unit: '个 L1.5 片段',
        unknown: 'L1.5 片段数未知',
      }),
      formatCountLabel(selectedInspector.value.articleCount, {
        unit: '条新闻',
        unknown: '新闻数未知',
      }),
      formatRange(selectedInspector.value.startDate, selectedInspector.value.endDate),
    ].filter(Boolean)
  }
  if (selectedInspector.value.kind === 'cluster') {
    return [
      angleLabel(selectedInspector.value.eventType) || '未知类型',
      formatCountLabel(selectedInspector.value.articleCount, {
        unit: '条新闻',
        unknown: '新闻数未知',
      }),
      formatRange(selectedInspector.value.startDate, selectedInspector.value.endDate),
    ].filter(Boolean)
  }
  if (selectedInspector.value.kind === 'branch-story') {
    return [
      selectedInspector.value.relation?.layer || '关联',
      relationLabel(selectedInspector.value.relation),
      selectedInspector.value.nodeCount == null
        ? '节点数未知'
        : `${selectedInspector.value.nodeCount} 个节点`,
    ].filter(Boolean)
  }
  if (selectedInspector.value.kind === 'edge') {
    return [
      edgeLabel(selectedInspector.value.edgeType),
      selectedInspector.value.layer || 'story',
      graphMetricPresentation('story_graph.edge_weight', {
        value: selectedInspector.value.weight,
      }).valueLabel,
    ].filter(Boolean)
  }
  return [
    familyLabel(currentStory.value?.meta?.dominant_type || 'mixed'),
    `${graphMetrics.value.mainNodes} 个节点`,
    `${graphMetrics.value.branchStories} 个分支`,
  ]
})

const selectedEvidenceNews = computed(() => {
  return Array.isArray(selectedClusterDetail.value?.news) ? selectedClusterDetail.value.news : []
})

const focusEvidenceNews = computed(() => selectedEvidenceNews.value.slice(0, 8).map((item) => ({
  ...item,
  safe_url: safeExternalHttpUrl(item.url),
})))

const focusArticleCount = computed(() => {
  return selectedClusterDetail.value?.article_count
    ?? selectedClusterDetail.value?.articleCount
    ?? selectedInspector.value?.articleCount
    ?? selectedInspector.value?.segmentCount
    ?? null
})

const focusResearchScore = computed(() => {
  const item = selectedClusterDetail.value || selectedInspector.value || currentStory.value
  if (!item) return 0
  return researchValueScore({
    ...item,
    article_count: focusArticleCount.value ?? item.article_count ?? item.articleCount,
    segment_count: item.segment_count ?? item.segmentCount,
    quality_score: item.quality_score ?? item.importanceScore ?? currentStory.value?.meta?.quality_score,
    start_date: item.start_date || item.startDate,
    end_date: item.end_date || item.endDate,
  })
})

const focusResearchValueText = computed(() => {
  if (selectedInspector.value?.kind === 'edge') return relationStrengthLabel(selectedInspector.value)
  return `${graphMetricPresentation('story_graph.research_value', {
    value: focusResearchScore.value,
  }).valueLabel} · ${researchValueLabel(focusResearchScore.value)}`
})

const focusMetricExplanation = computed(() => {
  const item = selectedInspector.value || currentStory.value || {}
  if (selectedInspector.value?.kind === 'edge') {
    return graphMetricExplanation('story_graph.relation_strength', {
      inputs: {
        edge_weight: item.weight ?? item.edge_weight,
        shared_actor_count: item.sharedActorCount ?? item.shared_actor_count,
        shared_topic_count: item.sharedTopicCount ?? item.shared_topic_count,
        relation_reason: item.relationReason ?? item.relation_reason,
      },
    })
  }
  const meta = item.meta || currentStory.value?.meta || {}
  return graphMetricExplanation('story_graph.research_value', {
    inputs: {
      article_count: focusArticleCount.value ?? item.article_count ?? item.articleCount,
      segment_count: item.segment_count ?? item.segmentCount ?? meta.segment_count,
      l2_chain_count: item.l2_chain_count ?? item.cluster_count ?? meta.l2_chain_count,
      quality_score: item.quality_score ?? item.importanceScore ?? meta.quality_score,
      actor_count: Object.keys(meta.actor_counts || {}).length,
      topic_count: Object.keys(meta.topic_counts || {}).length,
    },
  })
})

const focusEvidenceText = computed(() => {
  const isDirectEvidence = ['l2-segment', 'cluster'].includes(selectedInspector.value?.kind)
  return evidenceLevel(focusArticleCount.value, { direct: isDirectEvidence })
})

const focusPrimaryType = computed(() => {
  const item = selectedInspector.value
  if (!item) return '未选择'
  if (item.kind === 'edge') return edgeLabel(item.edgeType)
  return (
    angleLabel(item.storyAngle || item.eventType || selectedClusterDetail.value?.event_type) ||
    familyLabel(item.lane || item.eventType || currentStory.value?.meta?.dominant_type || 'mixed')
  )
})

const focusTimeRange = computed(() => {
  const item = selectedInspector.value || {}
  return formatRange(
    item.startDate || item.start_date || selectedClusterDetail.value?.start_date,
    item.endDate || item.end_date || selectedClusterDetail.value?.end_date,
  )
})

const focusEmptyText = computed(() => {
  if (selectedInspector.value?.kind === 'l3-chain') {
    return '该节点是一条 L2 支线，可打开 L2 查看内部阶段和原文证据。'
  }
  if (selectedInspector.value?.kind === 'edge') {
    return relationEvidenceText(selectedInspector.value)
  }
  return '当前焦点暂无可展示的新闻证据。'
})

const focusDetailRows = computed(() => {
  const item = selectedInspector.value
  if (!item) return []

  const detail = selectedClusterDetail.value || {}
  const rows = [
    ['焦点类型', focusKindLabel(item.kind)],
    [
      '关系主体',
      formatActorPair(item.initiator || detail.initiator, item.target || detail.target, ''),
    ],
    ['时间范围', focusTimeRange.value],
    ['分类角度', focusPrimaryType.value],
    ['研究价值', focusResearchValueText.value],
    ['报道量状态', focusEvidenceText.value],
  ]

  if (item.kind === 'l3-chain') {
    rows.push(
      ['L2 链 ID', item.l2ChainId],
      ['片段规模', formatCountLabel(item.segmentCount, {
        unit: '个 L1.5 片段',
        unknown: 'L1.5 片段数未知',
      })],
      ['取证方式', '打开 L2 后查看阶段证据'],
    )
  } else if (item.kind === 'l2-segment') {
    rows.push(['Segment ID', item.segmentId], ['Story Page', item.detailUrl ? '可打开' : '未提供'])
  } else if (item.kind === 'cluster') {
    rows.push(['Cluster ID', item.originalClusterId || item.l1ClusterId])
  } else if (item.kind === 'edge') {
    rows.push(
      ['起点', findStoryNodeTitle(item.sourceId)],
      ['终点', findStoryNodeTitle(item.targetId)],
      ['主张 ID', item.claimId || '不可用（旧载荷）'],
      ['引用定位', item.citationLocator || '不可用'],
      ['未知门禁', item.unknownGate || 'explicit_unknown'],
      ['原因码', item.reasonCode || 'GRAPH_RELATION_CLAIM_CONTRACT_MISSING'],
      ['关系强度', relationStrengthLabel(item)],
      ['判定依据', relationEvidenceText(item)],
    )
  }

  return rows
    .map(([label, value]) => ({ label, value: displayValue(value) }))
    .filter((row) => row.value !== '未标注')
})

const focusInsightText = computed(() => {
  const item = selectedInspector.value
  if (!item) return ''
  if (item.kind === 'edge') {
    return `该边表示“${edgeLabel(item.edgeType)}”。${relationEvidenceText(item)}；作为研究证据时应优先核验两端节点的原文、日期先后和共同主体，避免把单纯时间重叠解读为因果。`
  }
  if (item.kind === 'l3-chain') {
    return `该支线承担“${familyLabel(item.lane || item.eventType)}”角色，当前显示${formatCountLabel(item.segmentCount, { unit: '个片段', unknown: '片段数未知' })}和${formatCountLabel(item.articleCount, { unit: '条新闻', unknown: '新闻数未知' })}。它可作为待核验研究子样本，但直接取证需要进入 L2。`
  }
  if (item.kind === 'l2-segment') {
    return `该节点来自 L1 事件聚类，当前${formatCountLabel(item.articleCount, { unit: '条新闻', unknown: '新闻数未知' })}；适合核验其时间位置、主体关系、来源分布，以及是否构成走势拐点。`
  }
  if (item.kind === 'cluster') {
    return buildStorySummaryLine(selectedClusterDetail.value || item) || selectedInspectorSubtitle.value
  }
  return selectedInspectorSubtitle.value
})

const focusResearchActions = computed(() => {
  const item = selectedInspector.value
  if (!item) return []
  if (item.kind === 'edge') {
    return relationResearchGuidance(item)
  }
  if (item.kind === 'l3-chain') {
    return [
      '作为宏观事件的子样本下钻到 L2，避免只凭 L3 聚合标题判断。',
      '比较同一 L3 内其他支线，确认它是主线、支线还是背景变量。',
      '记录 L2 链 ID 和 run_id，保证后续复现实验样本。',
    ]
  }
  if (item.kind === 'l2-segment' || item.kind === 'cluster') {
    return [
      '优先打开相关新闻，核验标题、来源和发布时间。',
      '检查该节点前后相邻节点，判断它是否是拐点或只是并行报道。',
      reportingCoverageGuidance(focusArticleCount.value),
    ]
  }
  return [
    '先看研究价值和未核验报道覆盖，再决定是否下钻。',
    '保留 run_id、时间范围和节点 ID，便于复现。',
  ]
})

const focusAssistantQuestion = computed(() => {
  return `请基于当前焦点「${selectedInspectorTitle.value}」做结构化研判：解释其在整张图谱中的位置、与相邻节点的候选关系方向、证据强弱、仍需核验的问题和下一步观察清单。`
})

watch(selectedStoryId, async (value) => {
  if (!value) return
  await loadStoryWorkspace(value)
})

watch(storySearch, () => {
  handleStorySearchChange()
})

watch(showContextLinks, async () => {
  if (currentStory.value) await renderWorkspaceGraph()
})

onMounted(async () => {
  restoreAssistantFabPosition()
  window.addEventListener('resize', clampAssistantFabPosition)
  await loadStories()
  await nextTick()
  setupResizeObserver()
})

onUnmounted(() => {
  window.removeEventListener('resize', clampAssistantFabPosition)
  listRequestGate.invalidate()
  workspaceRequestGate.invalidate()
  evidenceRequestGate.invalidate()
  listAbortController?.abort()
  storyAbortController?.abort()
  clusterAbortController?.abort()
  if (resizeFitTimer) window.clearTimeout(resizeFitTimer)
  resizeObserver?.disconnect()
  graph.value?.destroy()
  graph.value = null
})

async function switchGraphMode(mode, preferredId = '') {
  if (!['l2', 'l3'].includes(mode)) return
  if (graphMode.value !== mode) {
    graphMode.value = mode
    storySearch.value = ''
  }
  await loadStories(preferredId)
}

async function loadStories(preferredId = '') {
  listAbortController?.abort()
  listAbortController = new AbortController()
  const requestToken = listRequestGate.begin()
  const requestedMode = graphMode.value
  listLoading.value = true
  error.value = ''
  stories.value = []
  clearWorkspaceState({ clearSelection: true })
  queryStatus.value = buildStoryGraphQueryStatus('list-loading', { mode: requestedMode })

  try {
    const isL3 = requestedMode === 'l3'
    const payload = await storyGraphApi.listStories(requestedMode, {
      signal: listAbortController.signal,
    })
    if (!listRequestGate.isCurrent(requestToken) || graphMode.value !== requestedMode) return

    const nextStories = isL3
      ? (payload.macros || []).map(normalizeL3MacroSummary)
      : (payload.chains || []).map(normalizeL2ChainSummary)
    stories.value = nextStories

    if (!nextStories.length) {
      queryStatus.value = buildStoryGraphQueryStatus('list-empty', { mode: requestedMode })
      return
    }

    const preferred = preferredId ? String(preferredId) : ''
    const nextId =
      preferred && nextStories.some((story) => String(story.id) === preferred)
        ? preferred
        : nextStories[0]?.id
    if (nextId) {
      queryStatus.value = buildStoryGraphQueryStatus('selection-required', {
        count: nextStories.length,
        mode: requestedMode,
      })
      selectedStoryId.value = String(nextId)
    }
  } catch (err) {
    if (!listRequestGate.isCurrent(requestToken) || isCanceledRequest(err)) return
    stories.value = []
    clearWorkspaceState({ clearSelection: true })
    error.value = `无法加载 ${requestedMode === 'l3' ? 'L3' : 'L2'} 图谱列表，请稍后重试。`
    queryStatus.value = buildStoryGraphQueryStatus('error', {
      error: error.value,
      mode: requestedMode,
    })
  } finally {
    if (listRequestGate.isCurrent(requestToken)) {
      listLoading.value = false
      listAbortController = null
    }
  }
}

function handleStorySearchChange() {
  if (listLoading.value) return
  const query = String(storySearch.value || '').trim()
  const matches = filteredStories.value

  if (!query) {
    if (!stories.value.length) {
      clearWorkspaceState({ clearSelection: true })
      queryStatus.value = buildStoryGraphQueryStatus('list-empty', { mode: graphMode.value })
      return
    }
    if (!selectedStoryId.value) {
      queryStatus.value = buildStoryGraphQueryStatus('selection-required', {
        count: stories.value.length,
        mode: graphMode.value,
      })
      selectedStoryId.value = String(stories.value[0].id)
    }
    return
  }

  if (!matches.length) {
    clearWorkspaceState({ clearSelection: true })
    error.value = ''
    queryStatus.value = buildStoryGraphQueryStatus('search-empty', {
      mode: graphMode.value,
      query,
    })
    return
  }

  const selectedStillMatches = matches.some(
    (story) => String(story.id) === selectedStoryId.value,
  )
  if (!selectedStillMatches) {
    clearWorkspaceState({ clearSelection: true })
    error.value = ''
    queryStatus.value = buildStoryGraphQueryStatus('selection-required', {
      count: matches.length,
      mode: graphMode.value,
      query,
    })
  }
}

function selectStory(storyId) {
  focusPanelOpen.value = false
  const nextStoryId = String(storyId)
  if (selectedStoryId.value === nextStoryId && !currentStory.value) {
    void loadStoryWorkspace(nextStoryId)
    return
  }
  selectedStoryId.value = nextStoryId
}

async function reloadCurrentStory() {
  if (!selectedStoryId.value) return
  await loadStoryWorkspace(selectedStoryId.value)
}

async function loadStoryWorkspace(storyId) {
  storyAbortController?.abort()
  storyAbortController = new AbortController()
  const requestController = storyAbortController
  const requestToken = workspaceRequestGate.begin()
  const requestedMode = graphMode.value
  const requestedStoryId = String(storyId)
  workspaceLoading.value = true
  error.value = ''
  clearWorkspacePresentation()
  queryStatus.value = buildStoryGraphQueryStatus('workspace-loading', {
    mode: requestedMode,
    storyId: requestedStoryId,
  })

  try {
    const isL3 = requestedMode === 'l3'
    const payload = await storyGraphApi.getWorkspace(requestedMode, requestedStoryId, {
      signal: requestController.signal,
    })
    if (
      !workspaceRequestGate.isCurrent(requestToken)
      || graphMode.value !== requestedMode
      || selectedStoryId.value !== requestedStoryId
    ) return

    const nextStory = isL3
      ? transformL3MacroResponse(payload)
      : transformL2ChainResponse(payload)
    if (!Array.isArray(nextStory.nodes) || !nextStory.nodes.length) {
      clearWorkspacePresentation()
      queryStatus.value = buildStoryGraphQueryStatus('workspace-empty', {
        mode: requestedMode,
        storyId: requestedStoryId,
      })
      return
    }
    currentStory.value = nextStory
    selectedInspector.value = makeMainInspector(nextStory, requestedMode)
    await bootstrapStoryAssistantSession(requestToken, requestController.signal)
    if (!workspaceRequestGate.isCurrent(requestToken) || currentStory.value !== nextStory) return
    await nextTick()
    await renderWorkspaceGraph(requestToken, nextStory)
    if (!workspaceRequestGate.isCurrent(requestToken) || currentStory.value !== nextStory) return
    queryStatus.value = buildStoryGraphQueryStatus('ready', {
      mode: requestedMode,
      storyId: requestedStoryId,
    })
  } catch (err) {
    if (!workspaceRequestGate.isCurrent(requestToken) || isCanceledRequest(err)) return
    clearWorkspacePresentation()
    error.value = `无法加载所选 ${requestedMode === 'l3' ? 'L3' : 'L2'} 图谱，请稍后重试。`
    queryStatus.value = buildStoryGraphQueryStatus('error', {
      error: error.value,
      mode: requestedMode,
    })
  } finally {
    if (workspaceRequestGate.isCurrent(requestToken)) {
      workspaceLoading.value = false
      if (storyAbortController === requestController) storyAbortController = null
    }
  }
}

function isCanceledRequest(errorValue) {
  return errorValue?.name === 'AbortError'
    || errorValue?.name === 'CanceledError'
    || errorValue?.code === 'ERR_CANCELED'
}

function invalidateEvidenceRequest() {
  evidenceRequestGate.invalidate()
  clusterAbortController?.abort()
  clusterAbortController = null
  evidenceLoading.value = false
  selectedClusterDetail.value = null
}

function clearWorkspacePresentation() {
  invalidateEvidenceRequest()
  currentStory.value = null
  selectedInspector.value = null
  focusPanelOpen.value = false
  resetAgentState()
  graph.value?.destroy()
  graph.value = null
}

function clearWorkspaceState({ clearSelection = false } = {}) {
  workspaceRequestGate.invalidate()
  storyAbortController?.abort()
  storyAbortController = null
  workspaceLoading.value = false
  if (clearSelection) selectedStoryId.value = ''
  clearWorkspacePresentation()
}

function setupResizeObserver() {
  if (!graphContainer.value || typeof ResizeObserver === 'undefined') return
  resizeObserver = new ResizeObserver((entries) => {
    const entry = entries[0]
    if (!entry) return
    const { width, height } = entry.contentRect
    if (width < 160 || height < 160) return
    if (!graph.value || !currentStory.value) return
    if (resizeFitTimer) window.clearTimeout(resizeFitTimer)
    resizeFitTimer = window.setTimeout(() => {
      graph.value?.fitView()
    }, 180)
  })
  resizeObserver.observe(graphContainer.value)
}

function fallbackStoryTitle(id) {
  return getFallbackStoryTitle(graphMode.value, id)
}

function displayStoryTitle(story) {
  if (!story) return ''
  return cleanStoryTitle(story.story_title || story.title, story) || fallbackStoryTitle(story.story_id || story.id)
}

function storyResearchLine(story) {
  if (!story) return ''
  const parts = [
    familyLabel(story.family_group || story.event_type || story.meta?.dominant_type),
    getSummaryCountLabel(graphMode.value, story),
    formatCountLabel(story.article_count ?? story.meta?.article_count, {
      unit: '条新闻',
      unknown: '新闻数未知',
    }),
  ].filter(Boolean)
  return parts.join(' · ')
}

function evidenceNewsMeta(item) {
  const source = item?.source || item?.domain || item?.site_name || '未知来源'
  return `${source} · ${formatNewsDate(item)}`
}

async function renderWorkspaceGraph(requestToken = null, story = currentStory.value) {
  if (!graphContainer.value || !story) return
  if (!reactFlowFactoryPromise) {
    reactFlowFactoryPromise = loadStoryGraphRenderer()
  }
  const createStoryGraphRenderer = await reactFlowFactoryPromise
  if (
    (requestToken !== null && !workspaceRequestGate.isCurrent(requestToken))
    || currentStory.value !== story
  ) return

  if (!graph.value) {
    graph.value = createStoryGraphRenderer(graphContainer.value)
  }

  graph.value.render({
    storyGraph: story,
    showContextLinks: showContextLinks.value,
    onSelectInspector: selectInspector,
    onCanvasClick() {
      selectedInspector.value = makeMainInspector(currentStory.value, graphMode.value)
      selectedClusterDetail.value = null
      focusPanelOpen.value = false
    },
  })
}

async function selectInspector(payload) {
  if (selectedInspector.value === payload && focusPanelOpen.value) return
  selectedInspector.value = payload
  focusPanelOpen.value = payload.kind !== 'main-story'

  if (['l2-segment', 'cluster', 'l3-chain'].includes(payload.kind)) {
    await loadNodeEvidence(payload)
    return
  }

  if (payload.kind === 'edge') {
    invalidateEvidenceRequest()
    return
  }

  invalidateEvidenceRequest()
}

async function loadNodeEvidence(payload) {
  const params = buildStoryGraphEvidenceParams(payload, {
    currentL2RunId: currentStory.value?.meta?.l2_run_id,
  })

  if (!hasStoryGraphEvidenceTarget(params)) {
    invalidateEvidenceRequest()
    selectedClusterDetail.value = payload
    return
  }

  clusterAbortController?.abort()
  clusterAbortController = new AbortController()
  const requestToken = evidenceRequestGate.begin()
  const workspaceStory = currentStory.value
  selectedClusterDetail.value = payload
  evidenceLoading.value = true
  try {
    const evidence = await storyGraphApi.getEvidence(params, {
      signal: clusterAbortController.signal,
    })
    if (
      !evidenceRequestGate.isCurrent(requestToken)
      || currentStory.value !== workspaceStory
      || selectedInspector.value !== payload
    ) return
    selectedClusterDetail.value = {
      ...payload,
      ...evidence,
      news: evidence.news || [],
    }
  } catch (err) {
    if (!evidenceRequestGate.isCurrent(requestToken) || isCanceledRequest(err)) return
    selectedClusterDetail.value = payload
  } finally {
    if (evidenceRequestGate.isCurrent(requestToken)) {
      evidenceLoading.value = false
      clusterAbortController = null
    }
  }
}

async function openL2Chain(chainId) {
  if (!chainId) return
  focusPanelOpen.value = false
  await switchGraphMode('l2', chainId)
}

function closeFocusPanel() {
  focusPanelOpen.value = false
}

function toggleContextLinks() {
  showContextLinks.value = !showContextLinks.value
}

async function fitCanvas() {
  if (!graph.value) return
  await graph.value.fitView()
}

function resetAgentState({ keepSession = false } = {}) {
  agentMessages.value = []
  if (!keepSession) assistantSessionId.value = ''
}

function safeParseJson(raw, fallback) {
  try {
    const parsed = JSON.parse(raw)
    return parsed ?? fallback
  } catch {
    return fallback
  }
}

function currentUserCacheKey() {
  const user = getCurrentUser?.() || {}
  return String(user.id || user.user_id || user.username || getToken().slice(0, 24) || 'anonymous')
}

function storyAssistantCacheKey() {
  const storyId = currentStory.value?.story_id || selectedStoryId.value || 'none'
  return `${currentUserCacheKey()}:${graphMode.value}:${storyId}`
}

function readAssistantSessionCache() {
  return safeParseJson(localStorage.getItem(ASSISTANT_SESSION_CACHE_KEY), {})
}

function readCachedStoryAssistantSession() {
  const cache = readAssistantSessionCache()
  const value = cache[storyAssistantCacheKey()]
  const sid = value ? String(value).trim() : ''
  return /^\d+$/.test(sid) ? sid : ''
}

function clearCachedStoryAssistantSession() {
  const cache = readAssistantSessionCache()
  delete cache[storyAssistantCacheKey()]
  localStorage.setItem(ASSISTANT_SESSION_CACHE_KEY, JSON.stringify(cache))
}

function mapAssistantMessageRow(row) {
  return {
    id: row?.id != null ? `db-${row.id}` : `local-${++messageId}`,
    role: row?.role === 'assistant' ? 'assistant' : 'user',
    text: String(row?.content || ''),
  }
}

async function loadAssistantSessionMessages(sessionId, requestToken, signal) {
  const sid = String(sessionId || '').trim()
  if (!sid || !getToken()) return false
  try {
    const rows = await storyGraphApi.getAssistantSessionMessages(sid, {
      token: getToken(),
      signal,
    })
    if (!workspaceRequestGate.isCurrent(requestToken)) return null
    agentMessages.value = Array.isArray(rows) ? rows.map(mapAssistantMessageRow) : []
    return true
  } catch (err) {
    if (!workspaceRequestGate.isCurrent(requestToken) || isCanceledRequest(err)) return null
    return false
  }
}

async function bootstrapStoryAssistantSession(requestToken, signal) {
  resetAgentState()
  if (!workspaceRequestGate.isCurrent(requestToken) || !currentStory.value || !getToken()) return
  const cachedSessionId = readCachedStoryAssistantSession()
  if (!cachedSessionId) return
  assistantSessionId.value = cachedSessionId
  const loaded = await loadAssistantSessionMessages(cachedSessionId, requestToken, signal)
  if (loaded === false && workspaceRequestGate.isCurrent(requestToken)) {
    assistantSessionId.value = ''
    clearCachedStoryAssistantSession()
    agentMessages.value = []
  }
}

function buildStoryGraphAutorunContext(question = '') {
  const fallbackQuestion = '请解释当前事件故事脉络，指出主线、关键节点、待核验关系假设、证据缺口和下一步观察方向。'
  const visibleQuestion = String(question || fallbackQuestion).trim()
  return {
    visibleMessage: visibleQuestion,
    prompt: buildStoryGraphAssistantPrompt(visibleQuestion),
    mode: 'expert',
    extraBody: { tool_mode: 'context_only' },
  }
}

function openStoryAssistantDrawer(question = '') {
  if (currentStory.value) {
    sessionStorage.setItem(
      ASSISTANT_AUTORUN_CONTEXT_KEY,
      JSON.stringify(buildStoryGraphAutorunContext(question)),
    )
  } else {
    sessionStorage.removeItem(ASSISTANT_AUTORUN_CONTEXT_KEY)
  }
  assistantDrawerKey.value += 1
  assistantDrawerOpen.value = true
}

function handleAssistantFabClick() {
  if (assistantFabMoved.value) {
    assistantFabMoved.value = false
    return
  }
  openStoryAssistantDrawer()
}

function startAssistantFabDrag(event) {
  if (event.pointerType === 'mouse' && event.button !== 0) return
  const target = event.currentTarget
  const rect = target.getBoundingClientRect()
  assistantFabDrag = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    originX: rect.left,
    originY: rect.top,
    width: rect.width,
    height: rect.height,
  }
  assistantFabMoved.value = false
  assistantFabDragging.value = true
  target.setPointerCapture?.(event.pointerId)
  window.addEventListener('pointermove', dragAssistantFab)
  window.addEventListener('pointerup', stopAssistantFabDrag)
  window.addEventListener('pointercancel', stopAssistantFabDrag)
}

function dragAssistantFab(event) {
  if (!assistantFabDrag || event.pointerId !== assistantFabDrag.pointerId) return
  const deltaX = event.clientX - assistantFabDrag.startX
  const deltaY = event.clientY - assistantFabDrag.startY
  if (Math.abs(deltaX) + Math.abs(deltaY) > 4) assistantFabMoved.value = true
  assistantFabPosition.value = clampAssistantFabPoint({
    x: assistantFabDrag.originX + deltaX,
    y: assistantFabDrag.originY + deltaY,
  }, assistantFabDrag.width, assistantFabDrag.height)
}

function stopAssistantFabDrag(event) {
  if (assistantFabDrag && event?.pointerId && event.pointerId !== assistantFabDrag.pointerId) return
  const target = assistantFabRef.value
  target?.releasePointerCapture?.(assistantFabDrag?.pointerId)
  assistantFabDragging.value = false
  assistantFabDrag = null
  window.removeEventListener('pointermove', dragAssistantFab)
  window.removeEventListener('pointerup', stopAssistantFabDrag)
  window.removeEventListener('pointercancel', stopAssistantFabDrag)
  persistAssistantFabPosition()
  if (assistantFabMoved.value) {
    window.setTimeout(() => {
      assistantFabMoved.value = false
    }, 0)
  }
}

function clampAssistantFabPoint(point, width = 132, height = 48) {
  if (typeof window === 'undefined') return point
  const margin = window.innerWidth <= 760 ? 12 : 18
  const maxX = Math.max(margin, window.innerWidth - width - margin)
  const maxY = Math.max(margin, window.innerHeight - height - margin)
  return {
    x: Math.min(maxX, Math.max(margin, Number(point?.x || margin))),
    y: Math.min(maxY, Math.max(margin, Number(point?.y || margin))),
  }
}

function clampAssistantFabPosition() {
  if (!assistantFabPosition.value) return
  const rect = assistantFabRef.value?.getBoundingClientRect()
  assistantFabPosition.value = clampAssistantFabPoint(
    assistantFabPosition.value,
    rect?.width,
    rect?.height,
  )
  persistAssistantFabPosition()
}

function restoreAssistantFabPosition() {
  try {
    const raw = localStorage.getItem(ASSISTANT_FAB_POSITION_KEY)
    const parsed = raw ? JSON.parse(raw) : null
    if (!parsed || typeof parsed !== 'object') return
    assistantFabPosition.value = clampAssistantFabPoint(parsed)
  } catch {
    assistantFabPosition.value = null
  }
}

function persistAssistantFabPosition() {
  if (!assistantFabPosition.value) return
  try {
    localStorage.setItem(ASSISTANT_FAB_POSITION_KEY, JSON.stringify(assistantFabPosition.value))
  } catch {
    // Ignore storage failures; dragging should still work for the current session.
  }
}

function buildStoryGraphAssistantPrompt(question) {
  const context = buildStoryGraphAssistantContext()
  return `${context}\n\n----\n【用户问题】\n${question}`
}

function buildStoryGraphAssistantContext() {
  const story = currentStory.value
  if (!story) return '【StoryGraph 当前图谱上下文】当前没有加载走势图谱。'

  const nodes = (story.nodes || []).slice(0, 18).map((node, index) => {
    const type = node.source_kind === 'l3-chain' ? 'L2支线' : '节点'
    const title = compactText(cleanStoryTitle(node.label || node.title, node) || node.id, 120)
    const family = familyLabel(node.lane || node.story_angle || node.event_type || node.event_family)
    const dates = formatRange(node.start_date, node.end_date)
    const evidence = evidenceLevel(node.article_count)
    const actors = formatActorPair(node.initiator, node.target, '')
    const value = researchValueScore(node)
    return `${index + 1}. ${type} ${title}；${family}；${dates}；研究价值 ${researchValueLabel(value)}${evidence ? `；${evidence}` : ''}${actors ? `；主体 ${actors}` : ''}`
  })

  const edges = (story.edges || []).slice(0, 22).map((edge, index) => {
    const from = findStoryNodeTitle(edge.from_id)
    const to = findStoryNodeTitle(edge.to_id)
    const label = edgeLabel(edge.edge_type)
    const reason = compactText(relationEvidenceText(edge), 160)
    return `${index + 1}. ${from} -> ${to}；${label}；${relationStrengthLabel(edge)}；依据 ${reason}`
  })

  const branches = branchStories.value.slice(0, 10).map((item, index) => {
    const title = compactText(cleanStoryTitle(item.title, item) || item.story_id, 110)
    return `${index + 1}. ${title}；${relationLabel(item)}；${formatRange(item.start_date, item.end_date)}；${evidenceLevel(item.article_count)}`
  })

  const focus = selectedInspector.value
  const focusLines = focus
    ? [
        `焦点类型：${focusKindLabel(focus.kind)}`,
        `焦点标题：${compactText(selectedInspectorTitle.value, 160)}`,
        `焦点说明：${compactText(selectedInspectorSubtitle.value, 220)}`,
        `焦点标签：${selectedInspectorChips.value.join(' / ') || '无'}`,
        `焦点研究价值：${focusResearchValueText.value}`,
        `焦点报道覆盖（未核验）：${focusEvidenceText.value}`,
        `研究使用建议：${focusResearchActions.value.join('；') || '无'}`,
      ]
    : ['当前未选择具体焦点。']

  const evidence = selectedEvidenceNews.value.slice(0, 8).map((item, index) => {
    const title = compactText(item.title || item.news_title || item.news_id, 150)
    const source = item.source || item.domain || '未知来源'
    return `${index + 1}. ${title}；${source}；${formatNewsDate(item)}`
  })

  const lines = [
    '【StoryGraph 当前图谱上下文】',
    `来源页面：故事图谱分析台，已接入数据助手会话。`,
    `图谱层级：${graphMode.value === 'l3' ? 'L3 大事件' : 'L2 走势链'}；run_id=${activeRunId.value}`,
    `图谱标题：${compactText(story.story_title || story.story_id, 180)}`,
    `图谱 ID：${story.story_id}`,
    `摘要：${compactText(story.meta?.summary || buildStorySummaryLine(story), 500)}`,
    `统计：${storyStatsText.value || '无'}`,
    `抽样与覆盖：${samplingNotice.value || '范围未知，不得视为完整图。'}`,
    `研究基线：${currentResearchSignals.value.map((signal) => `${signal.label}=${signal.value}`).join('；')}`,
    '',
    '【当前焦点】',
    ...focusLines,
    '',
    `【主要节点｜最多 ${nodes.length} 条】`,
    ...(nodes.length ? nodes : ['无节点数据']),
    '',
    `【关系边｜最多 ${edges.length} 条】`,
    ...(edges.length ? edges : ['无关系边数据']),
    '',
    `【关联分支｜最多 ${branches.length} 条】`,
    ...(branches.length ? branches : ['无关联分支']),
    '',
    `【焦点候选报道（未核验）｜最多 ${evidence.length} 条】`,
    ...(evidence.length ? evidence : ['当前焦点未加载候选报道']),
    '',
    '【回答要求】',
    '请把图谱结构当作当前工作台上下文，首轮只基于这里给出的节点、关系边、分支和未核验候选报道研判，不自动调用外部工具。',
    '输出结构：1）主线；2）关键节点与关系强弱；3）证据缺口；4）下一步观察；5）后续精确检索词（最多 4 条）。',
    '明确区分“图谱事实 / 分析推断 / 待核实”。不得把时间相邻、时间重叠或未验证关系写成影响或因果；弱边必须说明局限。',
    '只把节点视为当前有界抽样或已加载候选；不得声称图谱完整，也不得据此推断未展示节点不存在。',
    '不得引入当前材料之外的人物言论、会议细节、争议传闻或新闻事实。需要补证时只写检索建议，不得假装已经检索。',
    '正文控制在 1200 字以内，优先回答用户问题，不复述全部上下文。',
  ]
  return clampText(lines.join('\n'), 11000)
}

function findStoryNodeTitle(nodeId) {
  const id = String(nodeId || '')
  const node = (currentStory.value?.nodes || []).find((item) => String(item.id) === id)
  return compactText(cleanStoryTitle(node?.label || node?.title, node) || id || '未知节点', 80)
}

function compactText(value, max = 160) {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  if (!text) return ''
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

function displayValue(value) {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  return text || '未标注'
}

function clampText(value, max = 11000) {
  const text = String(value || '')
  return text.length > max ? `${text.slice(0, max)}\n【上下文已截断】` : text
}

</script>

<style scoped>
.intel-page {
  --nav-offset: 72px;
  --bg: #f4f7fa;
  --paper: rgba(252, 254, 255, 0.96);
  --paper-soft: rgba(243, 248, 252, 0.92);
  --paper-strong: rgba(255, 255, 255, 0.97);
  --paper-muted: rgba(232, 240, 247, 0.78);
  --panel: rgba(250, 253, 255, 0.93);
  --panel-strong: rgba(250, 253, 255, 0.97);
  --stroke: rgba(43, 83, 119, 0.26);
  --stroke-strong: rgba(31, 113, 184, 0.58);
  --text: #10243c;
  --muted: #5b7085;
  --soft: #2b4c6a;
  --accent: #1f7bbd;
  --accent-2: #0f8d9d;
  --amber: #2563eb;
  --walnut: #174a72;
  --copper: #256ea8;
  --sage: #4f88a8;
  --navy: #10243c;
  --panel-radius: 8px;
  position: relative;
  min-height: 100vh;
  height: 100vh;
  padding-top: var(--nav-offset);
  background-color: var(--bg);
  background-image:
    linear-gradient(180deg, rgba(255, 255, 255, 0.88), rgba(241, 246, 250, 0.78)),
    radial-gradient(circle at 12% 0%, rgba(110, 166, 208, 0.08), transparent 32%),
    radial-gradient(circle at 86% 12%, rgba(48, 132, 185, 0.06), transparent 30%),
    repeating-linear-gradient(135deg, rgba(67, 116, 157, 0.024) 0 1px, transparent 1px 18px);
  background-repeat: no-repeat, no-repeat, no-repeat, repeat;
  background-size:
    auto,
    auto,
    auto,
    220px 220px;
  color: var(--text);
  font-family:
    'Noto Sans CJK SC', 'Source Han Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.intel-page::before,
.intel-page::after {
  content: '';
  position: fixed;
  inset: 0;
  pointer-events: none;
}

.intel-page::before {
  opacity: 0.32;
  background-image:
    linear-gradient(
      180deg,
      rgba(255, 255, 255, 0.56),
      rgba(235, 243, 250, 0.1) 36%,
      rgba(62, 123, 171, 0.045)
    ),
    repeating-linear-gradient(0deg, rgba(37, 91, 135, 0.018) 0 1px, transparent 1px 34px),
    repeating-linear-gradient(90deg, rgba(37, 91, 135, 0.014) 0 1px, transparent 1px 34px),
    repeating-linear-gradient(
      114deg,
      transparent 0 132px,
      rgba(71, 128, 174, 0.028) 132px 190px,
      transparent 190px 340px
    );
  background-size:
    auto,
    34px 34px,
    34px 34px,
    340px 340px;
}

.intel-page::after {
  opacity: 0.07;
  background-image:
    linear-gradient(rgba(25, 82, 128, 0.022) 1px, transparent 1px),
    linear-gradient(90deg, rgba(25, 82, 128, 0.016) 1px, transparent 1px);
  background-size: 32px 32px;
}

.intel-topbar {
  position: relative;
  z-index: 1;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 18px;
  padding: 12px 20px 10px;
  border-bottom: 1px solid var(--stroke);
  background: rgba(252, 254, 255, 0.93);
  backdrop-filter: blur(14px);
}

.intel-topbar__brand {
  display: flex;
  align-items: flex-start;
  gap: 18px;
}

.intel-kicker {
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--amber);
  padding-top: 6px;
  font-weight: 900;
}

.intel-topbar h1 {
  margin: 0;
  font-size: clamp(24px, 2.1vw, 34px);
  letter-spacing: 0;
  line-height: 1.08;
  font-weight: 900;
}

.intel-topbar p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 13px;
}

.intel-topbar__meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.intel-mode-switch {
  display: inline-flex;
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--stroke);
  border-radius: var(--panel-radius);
  background: rgba(244, 248, 252, 0.94);
}

.intel-mode-switch button {
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  padding: 9px 11px;
  min-width: 44px;
  min-height: 44px;
  font-size: 12px;
  font-weight: 900;
  cursor: pointer;
}

.intel-mode-switch button.is-active {
  background: var(--navy);
  color: #fff;
}

.intel-badge,
.intel-chip {
  border: 1px solid var(--stroke);
  background: var(--paper-strong);
  color: #314352;
  border-radius: var(--panel-radius);
  padding: 7px 10px;
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
}

.intel-badge--soft,
.intel-chip--signal {
  border-color: rgba(38, 126, 173, 0.32);
  background: rgba(219, 240, 252, 0.9);
  color: #235f86;
}

.intel-badge--build {
  border-color: rgba(38, 99, 235, 0.22);
  background: rgba(232, 241, 255, 0.88);
  color: #275c9b;
}

.intel-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.intel-chip--action {
  cursor: pointer;
}

.intel-shell {
  position: relative;
  z-index: 1;
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: clamp(280px, 20vw, 340px) minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr);
  align-items: stretch;
  gap: 10px;
  padding: 10px 12px 12px;
  width: 100%;
  max-width: none;
  margin: 0;
  overflow: hidden;
}

.intel-shell.is-left-collapsed {
  grid-template-columns: 0 minmax(0, 1fr);
}

.intel-side {
  min-width: 0;
  min-height: 0;
  height: 100%;
  max-height: 100%;
  overflow: hidden;
  transition:
    width 180ms ease,
    opacity 180ms ease;
}

.intel-side.is-collapsed {
  width: 0;
  opacity: 0;
  pointer-events: none;
}

.intel-panel,
.intel-canvas-panel {
  border: 1px solid var(--stroke);
  background: var(--panel);
  backdrop-filter: blur(18px);
  box-shadow:
    0 18px 44px rgba(24, 67, 106, 0.14),
    0 4px 12px rgba(24, 67, 106, 0.1);
  border-radius: var(--panel-radius);
  min-height: 0;
}

.intel-panel {
  height: 100%;
  max-height: 100%;
  background: rgba(252, 254, 255, 0.93);
  padding: 15px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: hidden;
}

.intel-panel__header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
}

.intel-panel__eyebrow {
  display: block;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.16em;
  color: var(--amber);
  margin-bottom: 4px;
}

.intel-panel__header h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
}

.intel-icon-btn,
.intel-btn {
  border: 1px solid var(--stroke);
  background: var(--paper-strong);
  color: var(--navy);
  border-radius: var(--panel-radius);
  padding: 10px 14px;
  min-height: 44px;
  font-size: 13px;
  cursor: pointer;
  transition:
    transform 120ms ease,
    border-color 120ms ease,
    background 120ms ease;
}

.intel-icon-btn {
  padding: 10px 12px;
  min-width: 44px;
}

.intel-btn:hover,
.intel-icon-btn:hover,
.intel-story-card:hover,
.intel-related-card:hover {
  transform: translateY(-1px);
  border-color: rgba(31, 123, 189, 0.42);
}

.intel-btn:disabled,
.intel-icon-btn:disabled {
  cursor: not-allowed;
  opacity: 0.45;
  transform: none;
}

.intel-btn--ghost {
  background: var(--paper-soft);
}

.intel-btn--assistant {
  border-color: rgba(31, 123, 189, 0.32);
  background: linear-gradient(135deg, rgba(31, 123, 189, 0.16), rgba(99, 179, 237, 0.12));
  color: var(--navy);
  font-weight: 900;
}

.intel-assistant-fab {
  position: fixed;
  right: 28px;
  bottom: 28px;
  z-index: 1200;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  min-height: 46px;
  padding: 0 18px 0 12px;
  border: 1px solid rgba(31, 123, 189, 0.28);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.94);
  color: var(--navy);
  box-shadow:
    0 16px 36px rgba(15, 23, 42, 0.2),
    0 0 0 1px rgba(255, 255, 255, 0.72) inset;
  cursor: grab;
  touch-action: none;
  user-select: none;
  transition:
    transform 0.16s ease,
    box-shadow 0.16s ease,
    border-color 0.16s ease;
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
}

.intel-assistant-fab span {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), #63b3ed);
  color: #fff;
  font-size: 12px;
  font-weight: 900;
}

.intel-assistant-fab strong {
  font-size: 14px;
  font-weight: 900;
  white-space: nowrap;
}

.intel-assistant-fab:hover {
  transform: translateY(-2px);
}

.intel-assistant-fab.is-dragging {
  cursor: grabbing;
  transform: scale(1.02);
  border-color: rgba(31, 123, 189, 0.52);
  box-shadow:
    0 20px 44px rgba(15, 23, 42, 0.26),
    0 0 0 1px rgba(255, 255, 255, 0.86) inset;
  transition: none;
}

.intel-assistant-fab:disabled {
  cursor: not-allowed;
  opacity: 0.48;
  transform: none;
}

@media (min-width: 1101px) {
  .intel-topbar__brand {
    max-width: min(520px, 42vw);
  }

  .intel-topbar__meta {
    flex: 1 1 auto;
  }

  .intel-topbar__assistant {
    display: none;
  }
}

.intel-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
}

.intel-section--library {
  flex: 1;
}

.intel-section--related {
  min-height: 0;
}

.intel-section__title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  color: var(--soft);
  font-size: 13px;
  letter-spacing: 0;
  font-weight: 800;
}

.intel-label {
  font-size: 12px;
  letter-spacing: 0;
  color: var(--muted);
  font-weight: 800;
}

.intel-input,
.intel-textarea {
  width: 100%;
  border: 1px solid var(--stroke);
  background: var(--paper-strong);
  color: var(--text);
  border-radius: var(--panel-radius);
  padding: 12px 14px;
  font-size: 14px;
  outline: none;
}

.intel-input:focus,
.intel-textarea:focus {
  border-color: rgba(39, 124, 139, 0.42);
  box-shadow: 0 0 0 3px rgba(39, 124, 139, 0.12);
}

.intel-query-status {
  min-height: 34px;
  margin: -2px 0 0;
  border-radius: 6px;
  padding: 7px 9px;
  color: var(--muted);
  background: rgba(232, 240, 247, 0.62);
  font-size: 11px;
  line-height: 1.45;
}

.intel-query-status.is-empty {
  color: #8a341f;
  background: rgba(254, 235, 226, 0.82);
}

.intel-textarea {
  resize: vertical;
  min-height: 118px;
}

.intel-story-list,
.intel-related-list {
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-right: 4px;
}

.intel-story-list {
  flex: 1;
}

.intel-related-list {
  max-height: 26vh;
}

.intel-story-card,
.intel-related-card {
  border: 1px solid var(--stroke);
  background: var(--paper);
  color: var(--text);
  border-radius: var(--panel-radius);
  padding: 10px 12px;
  text-align: left;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 6px;
  box-shadow: 0 8px 18px rgba(24, 67, 106, 0.07);
  transition:
    transform 140ms ease,
    border-color 140ms ease,
    box-shadow 140ms ease,
    background 140ms ease;
}

.intel-story-card.is-active {
  border-color: rgba(31, 123, 189, 0.64);
  background: rgba(226, 242, 255, 0.96);
  box-shadow:
    inset 4px 0 0 rgba(31, 123, 189, 0.86),
    0 12px 24px rgba(24, 67, 106, 0.12);
}

.intel-story-card__top,
.intel-related-card__meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  color: var(--muted);
  font-size: 11px;
  letter-spacing: 0;
  font-weight: 800;
}

.intel-story-card__signals,
.intel-research-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}

.intel-story-card__signals span,
.intel-research-strip span {
  min-width: 0;
  border: 1px solid rgba(52, 91, 128, 0.12);
  border-radius: 6px;
  background: rgba(245, 250, 254, 0.72);
  padding: 6px 7px;
  color: var(--soft);
  font-size: 10.5px;
  font-weight: 900;
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.intel-story-card__signals span:first-child {
  background: rgba(31, 123, 189, 0.12);
  color: #174a72;
}

.intel-story-card strong,
.intel-related-card strong,
.intel-summary-card strong,
.intel-evidence-item strong {
  font-size: 14px;
  line-height: 1.34;
}

.intel-story-card small,
.intel-related-card small,
.intel-summary-card p,
.intel-evidence-item small {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.35;
}

.intel-summary-card {
  border: 1px solid var(--stroke);
  background: var(--paper);
  border-radius: var(--panel-radius);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  box-shadow: 0 10px 22px rgba(24, 67, 106, 0.08);
}

.intel-research-strip {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.intel-research-strip label {
  display: block;
  margin-bottom: 3px;
  color: var(--muted);
  font-size: 10px;
  font-weight: 900;
}

.intel-research-strip strong {
  display: block;
  font-size: 12px;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.intel-canvas-panel {
  position: relative;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  padding: 58px 12px 18px;
}

.intel-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background-color: #f6f9fc;
  background-image:
    repeating-linear-gradient(0deg, rgba(43, 101, 150, 0.07) 0 1px, transparent 1px 42px),
    repeating-linear-gradient(90deg, rgba(43, 101, 150, 0.055) 0 1px, transparent 1px 42px),
    radial-gradient(circle at 24% 18%, rgba(255, 255, 255, 0.72), transparent 28%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.82), rgba(239, 246, 252, 0.72));
  background-repeat: repeat, repeat, no-repeat, no-repeat;
  background-size:
    42px 42px,
    42px 42px,
    auto,
    auto;
}

.intel-canvas-toolbar,
.intel-footerbar {
  position: absolute;
  left: 18px;
  z-index: 5;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.intel-canvas-toolbar {
  top: 16px;
  right: 18px;
}

.intel-sampling-notice {
  position: absolute;
  z-index: 5;
  top: 58px;
  left: 18px;
  right: 18px;
  width: fit-content;
  max-width: min(820px, calc(100% - 36px));
  margin: 0;
  padding: 8px 11px;
  border: 1px solid rgba(168, 101, 32, 0.25);
  border-radius: 7px;
  background: rgba(255, 247, 232, 0.94);
  color: #744516;
  font-size: 12px;
  line-height: 1.45;
  box-shadow: 0 4px 12px rgba(80, 56, 28, 0.08);
}

.intel-footerbar {
  bottom: 18px;
  right: auto;
  width: min(620px, calc(100% - 320px));
  min-width: min(500px, calc(100% - 36px));
  padding: 12px 14px;
  background: var(--paper-strong);
  border: 1px solid var(--stroke);
  border-radius: var(--panel-radius);
  backdrop-filter: blur(12px);
  box-shadow: 0 16px 34px rgba(24, 67, 106, 0.16);
}

.intel-footerbar__focus {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.intel-footerbar__label {
  font-size: 11px;
  letter-spacing: 0.06em;
  color: var(--muted);
}

.intel-footerbar__focus strong {
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.intel-footerbar__focus small,
.intel-footerbar__legend {
  color: var(--muted);
  font-size: 12px;
}

.intel-footerbar__legend {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: flex-end;
  min-width: 210px;
}

.intel-focus-sheet {
  position: absolute;
  right: 18px;
  top: 72px;
  bottom: 18px;
  z-index: 7;
  width: min(620px, calc(100% - 36px));
  max-height: none;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border: 1px solid rgba(50, 94, 132, 0.32);
  border-radius: 12px;
  background: rgba(252, 254, 255, 0.96);
  box-shadow:
    0 34px 82px rgba(21, 64, 102, 0.24),
    0 10px 26px rgba(21, 64, 102, 0.14),
    inset 0 0 0 1px rgba(255, 255, 255, 0.78);
  backdrop-filter: blur(18px);
  overflow: auto;
}

.intel-focus-sheet__header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 14px;
}

.intel-focus-sheet__eyebrow {
  display: block;
  margin-bottom: 5px;
  color: var(--copper);
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.intel-focus-sheet h3 {
  margin: 0;
  font-size: 18px;
  line-height: 1.25;
}

.intel-focus-sheet p {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.55;
}

.intel-focus-sheet__chips,
.intel-focus-sheet__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.intel-focus-sheet__metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}

.intel-focus-sheet__metrics div {
  border: 1px solid rgba(52, 91, 128, 0.14);
  border-radius: var(--panel-radius);
  background: rgba(245, 250, 254, 0.88);
  padding: 9px 10px;
}

.intel-focus-sheet__metrics label {
  display: block;
  margin-bottom: 4px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 800;
}

.intel-focus-sheet__metrics strong {
  color: var(--text);
  font-size: 13px;
  line-height: 1.3;
}

.intel-focus-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.intel-focus-section__title {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: var(--soft);
  font-size: 12px;
  font-weight: 900;
}

.intel-focus-detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
}

.intel-focus-detail-grid div {
  min-width: 0;
  border: 1px solid rgba(52, 91, 128, 0.14);
  border-radius: var(--panel-radius);
  background: rgba(245, 250, 254, 0.78);
  padding: 8px 10px;
}

.intel-focus-detail-grid label {
  display: block;
  margin-bottom: 4px;
  color: var(--muted);
  font-size: 10.5px;
  font-weight: 900;
}

.intel-focus-detail-grid strong {
  display: block;
  color: var(--text);
  font-size: 12.5px;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.intel-focus-sheet .intel-focus-insight {
  margin: 0;
  border: 1px solid rgba(31, 123, 189, 0.18);
  border-left: 3px solid rgba(31, 123, 189, 0.72);
  border-radius: var(--panel-radius);
  background: rgba(235, 247, 255, 0.72);
  padding: 10px 12px;
  color: #1b405c;
  font-size: 13px;
  line-height: 1.6;
}

.intel-research-actions {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.intel-research-actions li {
  position: relative;
  border: 1px solid rgba(52, 91, 128, 0.14);
  border-radius: var(--panel-radius);
  background: rgba(252, 254, 255, 0.88);
  padding: 8px 10px 8px 24px;
  color: #24435e;
  font-size: 12.5px;
  line-height: 1.45;
}

.intel-research-actions li::before {
  content: '';
  position: absolute;
  left: 10px;
  top: 15px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
}

.intel-focus-news {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
}

.intel-focus-news__title {
  display: flex;
  justify-content: space-between;
  color: var(--soft);
  font-size: 13px;
  font-weight: 900;
}

.intel-focus-news__list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.intel-focus-news__item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 11px;
  border: 1px solid rgba(52, 91, 128, 0.14);
  border-radius: var(--panel-radius);
  background: rgba(252, 254, 255, 0.94);
  color: var(--text);
  text-decoration: none;
  transition:
    transform 140ms ease,
    border-color 140ms ease,
    box-shadow 140ms ease;
}

.intel-focus-news__item:hover {
  transform: translateY(-1px);
  border-color: rgba(31, 123, 189, 0.4);
  box-shadow: 0 12px 26px rgba(28, 80, 124, 0.12);
}

.intel-focus-news__item.is-unavailable {
  opacity: 0.72;
}

.intel-focus-news__item.is-unavailable:hover {
  transform: none;
  border-color: rgba(52, 91, 128, 0.14);
  box-shadow: none;
}

.intel-focus-news__item small {
  color: var(--muted);
  font-size: 11px;
}

.intel-focus-news__item strong {
  display: -webkit-box;
  font-size: 12.5px;
  line-height: 1.4;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}

.focus-sheet-enter-active,
.focus-sheet-leave-active {
  transition:
    opacity 180ms ease,
    transform 180ms ease;
}

.focus-sheet-enter-from,
.focus-sheet-leave-to {
  opacity: 0;
  transform: translate(16px, 14px) scale(0.98);
}

.legend-swatch {
  display: inline-block;
  width: 14px;
  height: 3px;
  border-radius: 999px;
  margin-right: 6px;
  vertical-align: middle;
}

.legend-swatch--main {
  background: #4f8d73;
}

.legend-swatch--branch {
  background: #b99a45;
}

.legend-swatch--context {
  background: #7e74a8;
}

.intel-overlay {
  position: absolute;
  inset: 76px 24px 96px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 10px;
  background: var(--paper-strong);
  border: 1px solid var(--stroke);
  border-radius: var(--panel-radius);
  z-index: 6;
  text-align: center;
  padding: 24px;
}

.intel-overlay--quiet {
  background: var(--paper-strong);
}

.intel-overlay--error {
  background: rgba(255, 232, 231, 0.94);
}

.intel-spinner {
  width: 42px;
  height: 42px;
  border-radius: 50%;
  border: 3px solid rgba(39, 124, 139, 0.18);
  border-top-color: var(--accent);
  animation: spin 0.9s linear infinite;
}

.intel-evidence-empty,
.intel-evidence-loading {
  border: 1px dashed rgba(52, 91, 128, 0.32);
  background: var(--paper-soft);
  border-radius: var(--panel-radius);
  padding: 18px;
  color: var(--muted);
}

.intel-detail-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}

.intel-detail-grid label {
  display: block;
  margin-bottom: 4px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
}

.intel-evidence-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  text-decoration: none;
  color: var(--text);
  border: 1px solid var(--stroke);
  background: var(--paper);
  border-radius: var(--panel-radius);
  padding: 14px;
}

.intel-evidence-link {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  width: max-content;
  margin-top: 10px;
  border: 1px solid rgba(31, 123, 189, 0.36);
  border-radius: var(--panel-radius);
  padding: 9px 11px;
  background: rgba(222, 240, 254, 0.92);
  color: var(--walnut);
  font-size: 13px;
  font-weight: 900;
  text-decoration: none;
}

.intel-evidence-button {
  cursor: pointer;
}

.intel-side-toggle {
  position: absolute;
  top: 50%;
  z-index: 6;
  transform: translateY(-50%);
  writing-mode: vertical-rl;
  text-orientation: mixed;
  border: 1px solid var(--stroke);
  background: var(--paper-strong);
  color: var(--soft);
  padding: 12px 10px;
  min-width: 44px;
  min-height: 44px;
  border-radius: var(--panel-radius);
  cursor: pointer;
}

.intel-side-toggle--left {
  left: 12px;
}

.intel-sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.intel-accessible-list {
  position: absolute;
  top: 64px;
  right: 18px;
  z-index: 6;
  box-sizing: border-box;
  width: min(420px, calc(100% - 36px));
  border: 1px solid var(--stroke);
  border-radius: var(--panel-radius);
  background: var(--paper-strong);
  box-shadow: 0 14px 32px rgba(24, 67, 106, 0.16);
}

.intel-accessible-list > summary {
  box-sizing: border-box;
  min-height: 44px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  color: var(--soft);
  cursor: pointer;
  font-size: 13px;
  font-weight: 900;
}

.intel-accessible-list > summary strong {
  color: var(--muted);
  font-size: 11px;
  white-space: nowrap;
}

.intel-accessible-list__body {
  max-height: min(52vh, 480px);
  overflow-y: auto;
  overscroll-behavior: contain;
  border-top: 1px solid var(--stroke);
  padding: 12px;
}

.intel-accessible-list__body > p {
  margin: 0 0 12px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.5;
}

.intel-accessible-list__body h2 {
  margin: 12px 0 6px;
  color: var(--soft);
  font-size: 13px;
}

.intel-accessible-list__body ul {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.intel-accessible-list__body button {
  box-sizing: border-box;
  width: 100%;
  min-height: 44px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 3px;
  border: 1px solid var(--stroke);
  border-radius: 8px;
  padding: 8px 10px;
  background: var(--paper-soft);
  color: var(--text);
  text-align: left;
  cursor: pointer;
}

.intel-accessible-list__body button small {
  color: var(--muted);
  line-height: 1.35;
}

.intel-page button,
.intel-page input,
.intel-page .intel-focus-news__item {
  min-height: 44px;
  touch-action: manipulation;
}

.intel-page .intel-icon-btn {
  min-width: 44px;
}

.intel-canvas :deep(.react-flow__node:focus-visible),
.intel-canvas :deep(.react-flow__edge:focus-visible) {
  outline: 3px solid #0b6ea8;
  outline-offset: 4px;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1400px) {
  .intel-shell {
    grid-template-columns: 280px minmax(0, 1fr);
    gap: 8px;
    padding: 8px;
  }

  .intel-shell.is-left-collapsed {
    grid-template-columns: 0 minmax(0, 1fr);
  }

  .intel-topbar h1 {
    font-size: 28px;
  }

  .intel-footerbar {
    width: min(560px, calc(100% - 280px));
  }
}

@media (max-width: 1100px) {
  .intel-page {
    height: auto;
    min-height: 100vh;
    overflow: auto;
  }

  .intel-topbar {
    flex-direction: column;
  }

  .intel-assistant-fab {
    top: auto;
    right: 18px;
    bottom: 18px;
  }

  .intel-shell {
    grid-template-columns: 1fr;
    grid-template-rows: auto minmax(560px, 680px) auto;
    overflow: visible;
  }

  .intel-side {
    display: block;
    height: auto;
    max-height: none;
    overflow: visible;
  }

  .intel-side-toggle {
    display: none;
  }

  .intel-panel {
    max-height: 620px;
  }

  .intel-story-list,
  .intel-related-list {
    max-height: 360px;
  }

  .intel-canvas-panel {
    min-height: 680px;
    padding-bottom: 96px;
  }

  .intel-footerbar {
    width: auto;
    min-width: 0;
    right: 18px;
  }

  .intel-focus-sheet {
    top: 80px;
    left: 18px;
    right: 18px;
    width: auto;
  }

  .intel-focus-news__list,
  .intel-focus-detail-grid {
    grid-template-columns: 1fr;
  }

  .intel-accessible-list {
    top: 174px;
    left: 18px;
    right: 18px;
    width: auto;
  }

  .intel-accessible-list__body {
    max-height: 390px;
  }
}

@media (max-width: 640px) {
  .intel-topbar {
    gap: 12px;
    padding: 14px 12px 12px;
  }

  .intel-topbar__brand {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    width: 100%;
    gap: 4px;
  }

  .intel-kicker {
    padding-top: 0;
  }

  .intel-topbar__brand > div {
    min-width: 0;
  }

  .intel-topbar h1 {
    font-size: clamp(23px, 7.5vw, 28px);
    overflow-wrap: anywhere;
  }

  .intel-topbar p {
    line-height: 1.45;
  }

  .intel-topbar__meta {
    width: 100%;
    min-width: 0;
    justify-content: flex-start;
  }

  .intel-mode-switch {
    width: 100%;
  }

  .intel-mode-switch button {
    flex: 1 1 50%;
  }

  .intel-badge,
  .intel-chip {
    max-width: 100%;
    line-height: 1.35;
    white-space: normal;
    overflow-wrap: anywhere;
  }

  .intel-topbar__meta > .intel-btn {
    flex: 1 1 calc(50% - 5px);
    min-width: 0;
  }

  .intel-shell {
    gap: 6px;
    padding: 6px;
  }

  .intel-panel {
    padding: 12px;
  }

  .intel-canvas-panel {
    min-height: 620px;
  }

  .intel-accessible-list {
    left: 10px;
    right: 10px;
  }

  .intel-footerbar {
    left: 10px;
    right: 10px;
  }

  .intel-assistant-fab {
    right: 12px;
    bottom: 12px;
    padding-right: 12px;
  }
}
</style>
