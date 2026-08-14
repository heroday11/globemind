<template>
  <div class="ground-page" :style="{ '--atlas-image': `url(${atlasImage})` }">
    <nav class="ground-subnav" aria-label="全球新闻观察台次级导航">
      <span>Ground News</span>
      <RouterLink to="/data-service/ground-news">事件总览</RouterLink>
      <RouterLink to="/data-service/ground-news-desk">分析工作台</RouterLink>
    </nav>

    <header class="desk-topbar">
      <div class="brand-lockup">
        <span class="brand-mark">观</span>
        <div>
          <span class="eyebrow">GlobeMind 新闻观察台</span>
          <h1>事件档案工作台</h1>
        </div>
      </div>
      <div class="topbar-metrics">
        <span>{{ totalStories.toLocaleString('zh-CN') }} 张故事卡</span>
        <span>{{ activeStory?.article_count || 0 }} 条新闻</span>
        <span>{{ sourceCount }} 个信源</span>
      </div>
    </header>

    <main class="desk-grid">
      <aside class="library-rail">
        <section class="search-card" data-tour="ground-desk-filters">
          <label for="ground-news-story-search">检索故事卡</label>
          <div class="search-row">
            <input
              id="ground-news-story-search"
              v-model.trim="searchInput"
              type="search"
              placeholder="美国-伊朗、关税、北约..."
              @keydown.enter="loadCards"
            />
            <button type="button" :disabled="loadingCards" @click="loadCards">
              {{ loadingCards ? '加载中' : '搜索' }}
            </button>
          </div>
          <button
            type="button"
            class="mobile-desk-filter-toggle"
            aria-controls="ground-news-desk-filter-controls"
            :aria-expanded="mobileFiltersExpanded"
            @click="mobileFiltersExpanded = !mobileFiltersExpanded"
          >
            <span>筛选条件</span>
            <strong>{{ mobileFiltersExpanded ? '收起' : '展开' }}</strong>
          </button>
          <div
            id="ground-news-desk-filter-controls"
            class="desk-filter-controls"
            :class="{ 'is-mobile-expanded': mobileFiltersExpanded }"
          >
          <div class="filter-pills">
            <button
              v-for="item in familyFilters"
              :key="item.value"
              type="button"
              :class="{ active: familyFilter === item.value }"
              :aria-pressed="familyFilter === item.value"
              @click="setFamilyFilter(item.value)"
            >
              {{ item.label }}
            </button>
          </div>
          <div class="desk-filter-groups">
            <div class="desk-filter-group">
              <span>排序</span>
              <button
                v-for="item in sortOptions"
                :key="item.value"
                type="button"
                :class="{ active: sortFilter === item.value }"
                :aria-pressed="sortFilter === item.value"
                @click="setDeskFilter('sort', item.value)"
              >
                {{ item.label }}
              </button>
            </div>
            <div class="desk-filter-group">
              <span>时间</span>
              <button
                v-for="item in dateRangeOptions"
                :key="item.value"
                type="button"
                :class="{ active: dateDaysFilter === item.value }"
                :aria-pressed="dateDaysFilter === item.value"
                @click="setDeskFilter('date', item.value)"
              >
                {{ item.label }}
              </button>
            </div>
            <div class="desk-filter-group">
              <span>新闻数</span>
              <button
                v-for="item in minArticleOptions"
                :key="item.value"
                type="button"
                :class="{ active: minArticlesFilter === item.value }"
                :aria-pressed="minArticlesFilter === item.value"
                @click="setDeskFilter('articles', item.value)"
              >
                {{ item.label }}
              </button>
            </div>
            <div class="desk-filter-group">
              <span>信源数</span>
              <button
                v-for="item in minSourceOptions"
                :key="item.value"
                type="button"
                :class="{ active: minSourcesFilter === item.value }"
                :aria-pressed="minSourcesFilter === item.value"
                @click="setDeskFilter('sources', item.value)"
              >
                {{ item.label }}
              </button>
            </div>
            <div class="desk-filter-group">
              <span>质量</span>
              <button
                v-for="item in qualityOptions"
                :key="item.value"
                type="button"
                :class="{ active: qualityFilter === item.value }"
                :aria-pressed="qualityFilter === item.value"
                @click="setDeskFilter('quality', item.value)"
              >
                {{ item.label }}
              </button>
            </div>
          </div>
          </div>
        </section>

        <section class="story-list">
          <button
            v-for="story in stories"
            :key="story.cluster_id"
            type="button"
            class="story-list-card"
            :class="{ active: story.cluster_id === selectedClusterId }"
            :aria-pressed="story.cluster_id === selectedClusterId"
            @click="selectStory(story.cluster_id)"
          >
            <StoryCover :story="story" size="list" />
            <div class="story-list-card__top">
              <span>{{ familyLabel(story.event_family) }}</span>
              <strong>{{ story.article_count }}</strong>
            </div>
            <h3>{{ story.canonical_title || story.l1_title || story.cluster_id }}</h3>
            <div class="mini-spectrum" aria-label="第三方来源目录分组构成">
              <span
                v-for="bucket in listBiasBuckets(story)"
                :key="`${story.cluster_id}-${bucket.key}`"
                :style="{ width: `${bucket.value}%`, background: bucket.color }"
              />
            </div>
            <small>
              {{ formatRange(story.start_date, story.end_date) }} · {{ story.source_count || 0 }} 个信源 ·
              {{ statusLabel(story.source_analysis_status) }}
            </small>
          </button>
        </section>
      </aside>

      <section class="story-stage">
        <article v-if="loadingDetail && activeStory && !selectedListStory" class="dossier-preview">
          <div class="dossier-preview__art">
            <StoryCover :story="activeStory" size="preview" />
          </div>
          <div>
            <span class="eyebrow">正在构建信源档案</span>
            <h2>{{ activeStory.canonical_title || activeStory.l1_title || activeStory.title }}</h2>
            <p>
              正在加载来源目录、报道切面、关联线索和代表性标题证据。
            </p>
            <div class="dossier-preview__facts">
              <span>{{ activeStory.article_count || 0 }} 条新闻</span>
              <span>{{ activeStory.source_count || 0 }} 个信源</span>
              <span>{{ formatRange(activeStory.start_date, activeStory.end_date) }}</span>
            </div>
          </div>
        </article>
        <div v-else-if="!selectedStory" class="empty-state">请选择一张故事卡。</div>

        <template v-else>
          <article class="lead-story-card" data-tour="ground-desk-analysis">
            <div class="story-hero-cover">
              <StoryCover :story="selectedStory" size="hero" />
            </div>
            <div class="lead-story-copy">
              <div class="lead-story-card__meta">
                <span>{{ formatRange(selectedStory.start_date, selectedStory.end_date) }}</span>
                <span>{{ familyLabel(selectedStory.event_family) }}</span>
                <span :class="['signal-pill', coverageSignal.tone]">{{ coverageSignal.label }}</span>
              </div>
              <h2>{{ selectedStory.canonical_title || selectedStory.title }}</h2>
              <p class="lead-summary">
                {{ comparison.neutral_summary || entityLine }}
                <span v-if="selectedStory.location"> · {{ selectedStory.location }}</span>
              </p>
	              <div class="lead-facts">
	                <div>
	                  <strong>{{ selectedStory.article_count }}</strong>
                  <span>新闻总量</span>
                </div>
                <div>
                  <strong>{{ sourceCount }}</strong>
                  <span>独立信源</span>
                </div>
                <div>
                  <strong>{{ segmentCount }}</strong>
                  <span>L1.5 切面</span>
                </div>
                <div>
                  <strong>{{ relatedChains.length }}</strong>
	                  <span>关联线索</span>
	                </div>
	              </div>
	              <div class="lead-story-actions">
	                <button type="button" class="lead-assistant-btn" @click="sendGroundStoryToAssistant">
	                  数据助手研判
	                </button>
	              </div>
	            </div>
	          </article>

          <section class="dossier-board" aria-label="事件脉络与研究检查">
            <article class="dossier-panel dossier-panel--context">
              <div class="section-heading">
                <span>事件脉络</span>
                <small>{{ formatRange(selectedStory.start_date, selectedStory.end_date) }}</small>
              </div>
              <p>{{ dossierNarrative }}</p>
              <div class="context-timeline">
                <div v-for="item in contextMilestones" :key="item.key">
                  <span>{{ item.date }}</span>
                  <strong>{{ item.title }}</strong>
                  <small>{{ item.meta }}</small>
                </div>
              </div>
            </article>

            <article class="dossier-panel dossier-panel--value">
              <div class="section-heading">
                <span>研究检查</span>
                <small>{{ eventValueLevel.label }}</small>
              </div>
              <div class="value-score" :class="`tone-${eventValueLevel.tone}`">
                <strong>{{ eventValueLevel.score }}</strong>
                <span>{{ eventValueLevel.reason }}</span>
              </div>
              <details class="metric-explanation">
                <summary>查看未评级原因和现有输入</summary>
                <p>方法：{{ eventValueExplanation.method_version || '未建立' }}</p>
                <p>公式：{{ eventValueExplanation.formula }}</p>
                <p>证据定位：{{ eventValueExplanation.evidence.locator || '不可用' }}；{{ eventValueExplanation.reason_code }}</p>
                <ul>
                  <li v-for="input in eventValueExplanation.inputs" :key="input.field">
                    <code>{{ input.field }}</code>：{{ input.state === 'provided_unverified' ? `已提供但未核验（${input.value}）` : '不可用' }}
                  </li>
                </ul>
              </details>
              <div class="value-grid">
                <div v-for="item in valueSignals" :key="item.label">
                  <small>{{ item.label }}</small>
                  <strong>{{ item.value }}</strong>
                  <span>{{ item.detail }}</span>
                </div>
              </div>
            </article>

            <article class="dossier-panel dossier-panel--watch">
              <div class="section-heading">
                <span>下一步观察</span>
                <small>{{ watchpoints.length }} 条</small>
              </div>
              <ol class="watch-list">
                <li v-for="item in watchpoints" :key="item">
                  {{ item }}
                </li>
              </ol>
            </article>
          </section>

          <section class="story-comparison-board">
            <article class="comparison-primary">
              <div class="section-heading">
                <span>中立摘要</span>
                <small>{{ comparison.generated_by || 'dynamic' }}</small>
              </div>
              <p>{{ comparison.neutral_summary || '当前故事正在生成中立摘要。' }}</p>
              <div class="fact-list">
                <span v-for="fact in comparison.key_facts || []" :key="fact">{{ fact }}</span>
              </div>
            </article>
            <article class="comparison-differences">
              <div class="section-heading">
                <span>目录分组标题样本</span>
                <small>{{ activeSourceRows.length }} 条样本</small>
              </div>
              <p class="section-disclaimer">这里只列出不同第三方目录分组的标题样本，不等同于叙事立场差异。</p>
              <p v-for="line in comparison.difference_summary || []" :key="line">{{ line }}</p>
            </article>
            <article class="comparison-blindspot level-unknown">
              <div class="section-heading">
                <span>覆盖缺口检查</span>
                <small>尚未评估</small>
              </div>
              <strong>{{ blindspotMetric.valueLabel }}</strong>
              <p>请从在地来源、语言、第一手材料、所有权和转载关系分别核查；当前候选分不构成风险结论。</p>
              <details class="metric-explanation">
                <summary>查看旧候选公式及其证据限制</summary>
                <p>方法：{{ blindspotExplanation.method_version || '未建立' }}</p>
                <p>公式：{{ blindspotExplanation.formula }}</p>
                <p>证据定位：{{ blindspotExplanation.evidence.locator || '不可用' }}；{{ blindspotExplanation.reason_code }}</p>
              </details>
            </article>
          </section>

          <section class="coverage-board">
            <div class="spectrum-card">
              <div class="section-heading">
                <span>来源目录构成</span>
                <small>{{ statusLabel(sourceBreakdown?.analysis_status || selectedStory.source_analysis_status || 'not_built') }}</small>
              </div>
              <p class="section-disclaimer">目录标签只能帮助定位待复核样本，不能跨国家直接比较政治立场。</p>
              <div class="bias-bar">
                <span
                  v-for="bucket in biasBuckets"
                  :key="bucket.key"
                  :class="`bias-${bucket.key}`"
                  :style="{ flexBasis: `${Math.max(bucket.value, bucket.value > 0 ? 3 : 0)}%`, background: bucket.color }"
                />
              </div>
              <div class="bias-breakdown">
                <button
                  v-for="bucket in biasBuckets"
                  :key="`breakdown-${bucket.key}`"
                  type="button"
                  :class="{ active: selectedSourceGroup === bucket.key }"
                  :aria-pressed="selectedSourceGroup === bucket.key"
                  :style="{
                    '--fill-color': bucket.color,
                    '--fill': `${Math.max(0, Math.min(100, bucket.value))}%`,
                  }"
                  @click="selectSourceGroup(bucket.key)"
                >
                  <span :style="{ background: bucket.color }" />
                  <div>
                    <strong>{{ bucket.label }}</strong>
                    <small>{{ formatPct(bucket.value) }}%</small>
                  </div>
                </button>
              </div>
              <details class="metric-explanation">
                <summary>展开覆盖信号公式、输入和证据</summary>
                <p>方法：{{ coverageExplanation.method_version || '未建立' }}</p>
                <p>公式：{{ coverageExplanation.formula }}</p>
                <p>证据定位：{{ coverageExplanation.evidence.locator || '不可用' }}；{{ coverageExplanation.reason_code }}</p>
              </details>
            </div>

            <div class="signal-stack">
              <article v-for="card in coverageCards" :key="card.label" :class="['signal-card', card.tone]">
                <span>{{ card.label }}</span>
                <strong>{{ card.value }}</strong>
                <small>{{ card.detail }}</small>
              </article>
            </div>
          </section>

          <section class="source-intel-grid">
            <article class="source-intel-card source-intel-card--map">
              <div class="section-heading">
                <span>文章来源地构成</span>
                <small>按新闻条目统计</small>
              </div>
              <div class="region-ledger">
                <span v-for="row in countryRows.slice(0, 6)" :key="`geo-${row.key}`">
                  <strong>{{ row.value }}</strong>
                  {{ row.key || '未知' }}
                </span>
              </div>
            </article>

            <article class="source-intel-card">
              <div class="section-heading">
                <span>媒体类型构成</span>
                <small>信源类型</small>
              </div>
              <div class="source-type-bars">
                <div
                  v-for="row in sourceTypeRows"
                  :key="row.key"
                  :style="{ '--type-fill': `${sourceTypeFill(row.value)}%` }"
                >
                  <span>{{ sourceTypeLabel(row.key) }}</span>
                  <i :style="{ width: `${sourceTypeWidth(row.value)}%` }"></i>
                  <strong>{{ row.value }}</strong>
                </div>
              </div>
            </article>

            <article class="source-intel-card">
              <div class="section-heading">
                <span>代表性标题</span>
                <small>抽样 {{ evidence.length }} 条</small>
              </div>
              <div class="headline-ledger">
                <p v-for="item in topEvidence" :key="`headline-${item.news_id}`">
                  <strong>{{ item.source_name || item.domain || '未知信源' }}</strong>
                  {{ item.title }}
                </p>
              </div>
            </article>
          </section>

          <nav class="story-tabs">
            <button
              v-for="tab in tabs"
              :key="tab.key"
              type="button"
              :class="{ active: activeTab === tab.key }"
              :aria-pressed="activeTab === tab.key"
              @click="activeTab = tab.key"
            >
              {{ tab.label }}
            </button>
          </nav>

          <section v-if="activeTab === 'coverage'" class="coverage-grid">
            <article
              v-for="segment in segments"
              :key="segment.segment_id"
              class="angle-card"
              :class="`angle-${segment.story_angle || 'other'}`"
            >
              <div class="angle-card__top">
                <span>{{ angleLabel(segment.story_angle) }}</span>
                <small>{{ segment.article_count }} 条新闻</small>
              </div>
              <h3>{{ segment.title || segment.segment_id }}</h3>
              <small>{{ formatRange(segment.start_date, segment.end_date) }}</small>
              <ul>
                <li v-for="item in (segment.sample_news || []).slice(0, 3)" :key="item.news_id">
                  <a :href="newsDetailPath(item.news_id)" target="_blank" rel="noopener noreferrer">
                    {{ item.title || `新闻 ${item.news_id}` }}
                  </a>
                </li>
              </ul>
              <button type="button" class="card-action" @click="openSegment(segment)">查看切面样本</button>
            </article>
          </section>

          <section v-else-if="activeTab === 'timeline'" class="timeline-panel">
            <article
              v-for="segment in segments"
              :key="`timeline-${segment.segment_id}`"
              class="timeline-row"
            >
              <span>{{ angleLabel(segment.story_angle).slice(0, 2) }}</span>
              <div>
                <small>{{ formatRange(segment.start_date, segment.end_date) }}</small>
                <h3>{{ segment.title || segment.segment_id }}</h3>
                <p>{{ eventActionLabel(segment.event_action) }} · {{ segment.article_count }} 条新闻</p>
                <button type="button" class="inline-action" @click="openSegment(segment)">查看相关新闻</button>
              </div>
            </article>
          </section>

          <section v-else-if="activeTab === 'sources'" class="source-spectrum-grid">
            <article class="source-table-card">
              <div class="section-heading">
                <span>完整来源表</span>
                <small>{{ activeSourceRows.length }} / {{ sourceTable.length }}</small>
              </div>
              <div class="source-filter-strip">
                <button
                  v-for="option in sourceFilterOptions"
                  :key="option.key"
                  type="button"
                  :class="{ active: selectedSourceGroup === option.key }"
                  :aria-pressed="selectedSourceGroup === option.key"
                  @click="selectSourceGroup(option.key)"
                >
                  {{ option.label }} <strong>{{ option.count }}</strong>
                </button>
              </div>
              <div class="source-table">
                <RouterLink
                  v-for="row in activeSourceRows.slice(0, 40)"
                  :key="`source-row-${row.news_id}`"
                  class="source-table-row"
                  :to="sourcePath(row.domain)"
                >
                  <span>{{ row.source_name }}</span>
                  <strong>{{ row.title }}</strong>
                  <small>
                    {{ biasGroupLabel(row.political_group) }} ·
                    {{ credibilityLabel(row.credibility_tier) }} ·
                    {{ ownershipLabel(row.ownership_type) }} ·
                    {{ row.country || '未知地区' }}
                  </small>
                </RouterLink>
              </div>
            </article>
            <article v-for="column in sourceColumns" :key="column.key" class="source-column">
              <div class="source-column__head">
                <span :style="{ background: column.color }" />
                <strong>{{ column.label }}</strong>
                <small>展示 {{ column.items.length }} 条</small>
              </div>
              <div v-if="column.items.length" class="source-stack">
                <article v-for="item in column.items" :key="`${column.key}-${item.news_id}`" class="source-item">
                  <strong>{{ item.source_name || item.domain || '未知信源' }}</strong>
                  <p>{{ item.title }}</p>
                  <small>
                    {{ formatDate(item.published_at) }}
                    <span v-if="item.credibility_tier"> · {{ credibilityLabel(item.credibility_tier) }}</span>
                    <span v-if="item.country"> · {{ item.country }}</span>
                  </small>
                  <a class="news-detail-link" :href="newsDetailPath(item.news_id)" target="_blank" rel="noopener noreferrer">
                    查看新闻详情
                  </a>
                </article>
              </div>
              <p v-else class="muted">该分组暂无已评级信源。</p>
            </article>
          </section>

          <section v-else class="l2-dossier">
            <article class="chain-summary">
              <div class="section-heading">
                <span>关联线索</span>
                <small>质量指标未知</small>
              </div>
              <h3>{{ selectedChainMeta?.title || '请选择一条关联线索' }}</h3>
              <p v-if="selectedChainMeta">
                {{ selectedChainMeta.segment_count }} 个切面 · {{ selectedChainMeta.article_count }} 条新闻 ·
                评分 {{ qualityPct(selectedChainMeta.quality_score) }}
              </p>
              <details class="metric-explanation">
                <summary>展开链质量公式、输入和证据</summary>
                <p>方法：{{ timelineQualityExplanation.method_version || '未建立' }}</p>
                <p>公式：{{ timelineQualityExplanation.formula }}</p>
                <p>证据定位：{{ timelineQualityExplanation.evidence.locator || '不可用' }}；{{ timelineQualityExplanation.reason_code }}</p>
              </details>
              <div v-if="selectedChainMeta?.risk_flags?.length" class="risk-flags">
                <span v-for="flag in selectedChainMeta.risk_flags" :key="flag">{{ riskFlagLabel(flag) }}</span>
              </div>
            </article>

            <div v-if="chainNodes.length" class="chain-node-list">
              <article v-for="node in chainNodes" :key="node.segment_id" class="chain-node">
                <span>{{ node.segment_order }}</span>
                <div>
                  <h3>{{ node.title }}</h3>
                  <small>{{ angleLabel(node.story_angle) }} · {{ eventActionLabel(node.event_action) }} · {{ formatRange(node.start_date, node.end_date) }}</small>
                </div>
              </article>
              <article v-for="edge in chainEdges" :key="`${edge.from_id}-${edge.to_id}`" class="edge-card">
                <div>
                  <span>{{ edgeTypeLabel(edge.edge_type) }}</span>
                  <strong>{{ graphMetricPresentation('story_graph.edge_weight', { value: edge.edge_weight }).valueLabel }}</strong>
                </div>
                <small>{{ edge.relation_reason || '暂无关系说明' }}</small>
                <details class="metric-explanation">
                  <summary>展开边权重公式、输入和证据</summary>
                  <template v-for="explanation in [edgeWeightExplanation(edge)]" :key="`${edge.from_id}-${edge.to_id}-${explanation.metric_id}`">
                    <p>方法：{{ explanation.method_version || '未建立' }}</p>
                    <p>公式：{{ explanation.formula }}</p>
                    <p>证据定位：{{ explanation.evidence.locator || '不可用' }}；{{ explanation.reason_code }}</p>
                  </template>
                </details>
              </article>
            </div>
            <p v-else class="muted">当前故事尚未选择关联线索。</p>
          </section>
        </template>
      </section>

      <aside class="inspector-rail">
        <section class="inspector-card">
          <div class="section-heading">
            <span>故事结构</span>
            <small>L1 核心</small>
          </div>
          <dl class="anatomy-list">
            <div>
              <dt>领域</dt>
              <dd>{{ domainLabel(selectedStory?.event_domain) }}</dd>
            </div>
            <div>
              <dt>主题族</dt>
              <dd>{{ familyLabel(selectedStory?.event_family) }}</dd>
            </div>
            <div>
              <dt>动作</dt>
              <dd>{{ eventActionLabel(selectedStory?.event_action) }}</dd>
            </div>
            <div>
              <dt>语气</dt>
              <dd>{{ toneLabel(selectedStory?.tone) }}</dd>
            </div>
          </dl>
        </section>

        <section class="inspector-card">
          <div class="section-heading">
            <span>文章来源国家 / 地区</span>
            <small>新闻数</small>
          </div>
          <div class="chip-cloud">
            <span v-for="row in countryRows" :key="row.key">{{ row.key || '未知' }} · {{ row.value }}</span>
          </div>
        </section>

        <section class="inspector-card">
          <div class="section-heading">
            <span>目录评级</span>
            <small>新闻数</small>
          </div>
          <div class="chip-cloud">
            <span v-for="row in credibilityRows" :key="row.key">{{ credibilityLabel(row.key) }} · {{ row.value }}</span>
          </div>
        </section>

        <section class="inspector-card">
          <div class="section-heading">
            <span>媒体类型</span>
            <small>新闻数</small>
          </div>
          <div class="chip-cloud chip-cloud--compact">
            <span v-for="row in sourceTypeRows" :key="`type-${row.key}`">{{ sourceTypeLabel(row.key) }} · {{ row.value }}</span>
          </div>
        </section>

        <section class="inspector-card chain-picker">
          <div class="section-heading">
            <span>关联线索</span>
            <small>{{ relatedChains.length }} 条关联</small>
          </div>
          <details class="metric-explanation">
            <summary>展开列表链质量公式、输入和证据</summary>
            <p>方法：{{ timelineQualityExplanation.method_version || '未建立' }}</p>
            <p>公式：{{ timelineQualityExplanation.formula }}</p>
            <p>证据定位：{{ timelineQualityExplanation.evidence.locator || '不可用' }}；{{ timelineQualityExplanation.reason_code }}</p>
          </details>
          <button
            v-for="chain in relatedChains"
            :key="chain.chain_id"
            type="button"
            :class="{ active: chain.chain_id === selectedChainId }"
            :aria-pressed="chain.chain_id === selectedChainId"
            @click="selectChain(chain.chain_id)"
          >
            <strong>{{ chain.title || chain.chain_id }}</strong>
            <small>
              {{ familyGroupLabel(chain.family_group) }} · {{ chain.segment_count }} 个切面 ·
              {{ qualityPct(chain.quality_score) }}
            </small>
          </button>
          <p v-if="!relatedChains.length" class="muted">暂无关联线索。</p>
        </section>
      </aside>
    </main>

    <div v-if="selectedSegment" class="story-modal-backdrop" @click.self="closeSegment">
      <section
        ref="segmentDialogRef"
        class="story-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ground-news-modal-title"
        tabindex="-1"
        @keydown="handleSegmentDialogKeydown"
      >
        <button type="button" class="story-modal__close" aria-label="关闭相关新闻" @click="closeSegment">×</button>
        <div class="section-heading">
          <span>{{ angleLabel(selectedSegment.story_angle) }}</span>
          <small>{{ selectedSegment.article_count }} 条新闻</small>
        </div>
        <h2 id="ground-news-modal-title">{{ selectedSegment.title || selectedSegment.segment_id }}</h2>
        <p class="story-modal__meta">
          {{ formatRange(selectedSegment.start_date, selectedSegment.end_date) }} ·
          {{ eventActionLabel(selectedSegment.event_action) }} ·
          {{ selectedSegment.location || '地区未知' }}
        </p>
        <div v-if="selectedSegment.sample_news?.length" class="modal-news-list">
          <article v-for="item in selectedSegment.sample_news" :key="`modal-${item.news_id}`">
            <h3>{{ item.title || `新闻 ${item.news_id}` }}</h3>
            <small>{{ formatDate(item.published_at) }}</small>
            <a :href="newsDetailPath(item.news_id)" target="_blank" rel="noopener noreferrer">打开详细新闻页</a>
          </article>
        </div>
        <p v-else class="muted">该切面暂无样本新闻。</p>
      </section>
    </div>
    <AssistantDrawer
      v-model="assistantDrawerOpen"
      :drawer-key="assistantDrawerKey"
      :page-skill="groundNewsAssistantSkill"
      title="新闻观察台数据助手"
      subtitle="读取当前故事卡、来源目录、报道切面和关联线索"
    />
  </div>
</template>

<script setup>
import { computed, defineComponent, h, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { restoreDialogFocus, trapDialogTab } from '@/utils/dialogFocus'
import atlasImage from '@/assets/ground-news/coverage-atlas.webp'
import { AssistantDrawer } from '@/features/assistant/index.js'
import {
  graphMetricExplanation,
  graphMetricPresentation,
} from '@/governance/graphMetrics.js'
import {
  angleLabel,
  biasGroupLabel,
  buildGroundNewsSourceProfileModel,
  coverCredit,
  coverImageUrl,
  coverLabel,
  coverShort,
  coverTheme,
  credibilityLabel,
  dateRangeOptions,
  domainLabel,
  edgeTypeLabel,
  eventActionLabel,
  familyFilters,
  familyGroupLabel,
  familyLabel,
  formatDate,
  formatPct,
  formatRange,
  groundNewsApi,
  groundNewsTabs as tabs,
  listBiasBuckets,
  minArticleOptions,
  minSourceOptions,
  newsDetailPath,
  normalizeBiasBuckets,
  normalizeLeaning,
  ownershipLabel,
  qualityOptions,
  qualityPct,
  riskFlagLabel,
  sortOptions,
  sourceColumnsModel,
  sourcePath,
  sourceTypeLabel,
  statusLabel,
  toneLabel,
  topEntries,
} from '@/features/ground-news/index.js'

const route = useRoute()
const ASSISTANT_AUTORUN_CONTEXT_KEY = 'data_assistant_autorun_context_v1'
const assistantDrawerOpen = ref(false)
const assistantDrawerKey = ref(0)
const mobileFiltersExpanded = ref(false)

const stories = ref([])
const totalStories = ref(0)
const selectedClusterId = ref('')
const detail = ref(null)
const chainGraph = ref(null)
const selectedChainId = ref('')
const searchInput = ref('')
const familyFilter = ref('')
const sortFilter = ref('recent')
const dateDaysFilter = ref(120)
const minArticlesFilter = ref(2)
const minSourcesFilter = ref(3)
const qualityFilter = ref('ready')
const activeTab = ref('coverage')
const loadingCards = ref(false)
const loadingDetail = ref(false)
const selectedSegment = ref(null)
const segmentDialogRef = ref(null)
const selectedSourceGroup = ref('all')
const detailCache = new Map()
const chainCache = new Map()
let storyRequestSeq = 0
let chainRequestSeq = 0
let segmentReturnFocusTarget = null

watch(selectedSegment, async (value, previousValue) => {
  if (value) {
    if (!previousValue && typeof document !== 'undefined') {
      segmentReturnFocusTarget = document.activeElement
    }
    await nextTick()
    segmentDialogRef.value?.querySelector('.story-modal__close')?.focus()
    return
  }
  if (!previousValue) return
  await nextTick()
  restoreDialogFocus(segmentReturnFocusTarget)
  segmentReturnFocusTarget = null
})

const StoryCover = defineComponent({
  name: 'StoryCover',
  props: {
    story: { type: Object, required: true },
    size: { type: String, default: 'card' },
  },
  setup(props) {
    return () => {
      const story = props.story || {}
      const imageUrl = coverImageUrl(story)
      return h('div', {
        class: [
          'story-cover',
          `story-cover--${props.size}`,
          `theme-${coverTheme(story)}`,
          { 'has-image': Boolean(imageUrl) },
        ],
      }, [
        imageUrl ? h('img', {
          class: 'story-cover__image',
          src: imageUrl,
          alt: '',
          loading: props.size === 'hero' ? 'eager' : 'lazy',
          referrerpolicy: 'no-referrer',
          onError: (event) => {
            const image = event.currentTarget
            const frame = image.closest('.story-cover')
            image.style.display = 'none'
            frame?.classList.remove('has-image')
            frame?.classList.add('image-failed')
          },
        }) : null,
        h('div', { class: 'story-cover__grid' }),
        h('div', { class: 'story-cover__wash' }),
        h('div', { class: 'story-cover__label' }, [
          h('span', coverLabel(story)),
          h('strong', coverShort(story)),
          h('small', imageUrl ? coverCredit(story) : '编辑封面'),
        ]),
      ])
    }
  },
})

const selectedListStory = computed(() => stories.value.find((story) => story.cluster_id === selectedClusterId.value) || null)
const selectedStory = computed(() => detail.value?.story || selectedListStory.value || null)
const activeStory = computed(() => (
  loadingDetail.value
    ? (selectedListStory.value || selectedStory.value)
    : (selectedStory.value || selectedListStory.value)
))

const groundNewsAssistantSkill = computed(() => {
  const story = selectedStory.value || activeStory.value || {}
  return {
    page: '新闻观察台',
    path: '/ground-news',
    summary: `${story.canonical_title || story.title || '当前故事卡'}；可查看报道切面、来源目录、覆盖缺口检查和关联线索。`,
    access: [
      '导航进入新闻观察台',
      '数据搜索/故事图谱可带 cluster_id 定位故事',
      '页面右侧 AI 按钮打开侧栏助手',
    ],
    sections: [
      '故事卡列表与筛选',
      '报道切面',
      '来源目录',
      '时间线',
      '关联线索',
    ],
    actions: [
      '搜索故事卡',
      '调整主题/时间/文章数/信源筛选',
      '切换报道切面、时间线、来源目录和关联线索标签',
      '打开样本新闻详情',
    ],
    aiActions: [
      '解释当前故事主线和标题样本差异',
      '整理覆盖缺口与补证方向',
      '把观察台材料整理成报告提纲',
    ],
  }
})

const sourceBreakdown = computed(() => detail.value?.source_breakdown || null)
const comparison = computed(() => detail.value?.comparison || {})
const segments = computed(() => detail.value?.segments || [])
const relatedChains = computed(() => detail.value?.related_l2_chains || [])
const evidence = computed(() => detail.value?.evidence || [])
const sourceTable = computed(() => {
  const rows = Array.isArray(comparison.value?.source_table)
    ? comparison.value.source_table.slice(0, 100)
    : []
  return rows.map((row) => {
    const profile = buildGroundNewsSourceProfileModel(row)
    return {
      news_id: row?.news_id,
      title: typeof row?.title === 'string' ? row.title : '',
      published_at: row?.published_at,
      domain: profile.domain,
      source_name: profile.sourceName,
      country: profile.country,
      source_type: profile.values.sourceType,
      ownership_type: profile.values.ownershipType,
      political_leaning: profile.values.politicalLeaning,
      political_group: normalizeLeaning(profile.values.politicalLeaning),
      credibility_tier: profile.values.credibilityTier,
      source_profile_method: profile.method,
    }
  })
})
const blindspotDetail = computed(() => comparison.value?.blindspot || {})
const chronologicalSegments = computed(() => [...segments.value].sort((a, b) => (
  String(a.start_date || a.end_date || '').localeCompare(String(b.start_date || b.end_date || ''))
)))
const segmentCount = computed(() => {
  if (segments.value.length) return segments.value.length
  const counts = activeStory.value?.angle_counts || {}
  return Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0)
})
const chainNodes = computed(() => chainGraph.value?.nodes || [])
const chainEdges = computed(() => chainGraph.value?.edges || [])
const selectedChainMeta = computed(() => {
  if (chainGraph.value?.chain) return chainGraph.value.chain
  return relatedChains.value.find((chain) => chain.chain_id === selectedChainId.value) || null
})

const sourceCount = computed(() => Number(sourceBreakdown.value?.source_count || activeStory.value?.source_count || 0))
const entityLine = computed(() => {
  const initiator = selectedStory.value?.initiator || '?'
  const target = selectedStory.value?.target || '?'
  return `${initiator} -> ${target}`
})

const biasSource = computed(() => (
  sourceBreakdown.value?.political_group_pct_reviewed_known_sources
  || sourceBreakdown.value?.political_group_pct_all_sources
  || activeStory.value?.political_group_pct_reviewed_known_sources
  || {}
))

const biasBuckets = computed(() => normalizeBiasBuckets(biasSource.value))
const countryRows = computed(() => topEntries(sourceBreakdown.value?.country_counts || activeStory.value?.country_counts || {}, 8))
const credibilityRows = computed(() => topEntries(sourceBreakdown.value?.credibility_tier_counts || activeStory.value?.credibility_tier_counts || {}, 6))
const sourceTypeRows = computed(() => topEntries(sourceBreakdown.value?.source_type_counts || activeStory.value?.source_type_counts || {}, 6))
const topEvidence = computed(() => evidence.value.slice(0, 5))

const dossierNarrative = computed(() => {
  const story = selectedStory.value || {}
  const summary = comparison.value?.neutral_summary
  const actorText = actorLine.value
  if (summary && actorText) return `${summary} 关键参与方：${actorText}。`
  if (summary) return summary
  return `${story.canonical_title || story.title || '当前事件'} 正在形成多源报道，关键参与方为 ${actorText || entityLine.value}。`
})

const actorLine = computed(() => {
  const story = selectedStory.value || {}
  return [story.initiator, story.target, story.location]
    .filter(Boolean)
    .map((item) => String(item).trim())
    .filter((item, index, rows) => item && rows.indexOf(item) === index)
    .join(' / ')
})

const contextMilestones = computed(() => {
  const rows = chronologicalSegments.value.slice(0, 4).map((segment, index) => ({
    key: segment.segment_id || `segment-${index}`,
    date: formatDate(segment.start_date || segment.end_date),
    title: segment.title || angleLabel(segment.story_angle),
    meta: `${angleLabel(segment.story_angle)} · ${segment.article_count || 0} 条新闻`,
  }))
  if (rows.length) return rows
  const story = selectedStory.value
  if (!story) return []
  return [{
    key: story.cluster_id,
    date: formatRange(story.start_date, story.end_date),
    title: story.canonical_title || story.title || '事件主线',
    meta: `${familyLabel(story.event_family)} · ${story.article_count || 0} 条新闻`,
  }]
})

const eventValueLevel = computed(() => {
  const metric = graphMetricPresentation('ground_news.event_research_value', {
    value: selectedStory.value?.event_value_score,
  })
  return {
    score: metric.valueLabel,
    label: '暂不评级',
    tone: 'watch',
    reason: '方法未批准，且输入身份或证据定位未完整绑定。',
  }
})

const eventValueExplanation = computed(() => graphMetricExplanation(
  'ground_news.event_research_value',
  {
    inputs: {
      source_count: sourceCount.value,
      article_count: selectedStory.value?.article_count,
      related_chain_count: relatedChains.value.length,
      segment_count: segmentCount.value,
      blindspot_score: blindspotDetail.value?.score,
    },
  },
))

const blindspotMetric = computed(() => graphMetricPresentation(
  'ground_news.blindspot_score',
  { value: blindspotDetail.value?.score },
))

const blindspotExplanation = computed(() => graphMetricExplanation(
  'ground_news.blindspot_score',
  {
    inputs: {
      left_pct: biasBuckets.value.find((item) => item.key === 'left')?.value,
      center_pct: biasBuckets.value.find((item) => item.key === 'center')?.value,
      right_pct: biasBuckets.value.find((item) => item.key === 'right')?.value,
      state_aligned_pct: biasBuckets.value.find((item) => item.key === 'state_aligned')?.value,
      source_count: sourceCount.value,
      reviewed_known_source_count: sourceBreakdown.value?.reviewed_known_source_count,
      unknown_source_count: sourceBreakdown.value?.unknown_source_count,
    },
  },
))

const timelineQualityExplanation = computed(() => graphMetricExplanation(
  'ground_news.timeline_quality',
  {
    inputs: {
      quality_score: selectedChainMeta.value?.quality_score,
      chain_quality: selectedChainMeta.value?.chain_quality,
    },
  },
))

function edgeWeightExplanation(edge) {
  return graphMetricExplanation('story_graph.edge_weight', {
    inputs: { stored_edge_weight: edge?.edge_weight ?? edge?.weight },
  })
}

const valueSignals = computed(() => {
  return [
    {
      label: '影响范围',
      value: '未知',
      detail: '地区数量不是影响范围结论，且证据定位未绑定',
    },
    {
      label: '报道强度',
      value: '未知',
      detail: '报道、信源和切面计数只作未核验输入',
    },
    {
      label: '叙事偏向',
      value: '未知',
      detail: '目录标签构成不等于叙事或事实偏向',
    },
    {
      label: '走势关联',
      value: '未知',
      detail: '链数量、质量分和布局关系不能建立影响或因果',
    },
  ]
})

const watchpoints = computed(() => {
  const rows = []
  const newest = chronologicalSegments.value.at(-1)
  if (newest?.title) rows.push(`确认“${newest.title}”是否成为后续主线，而不是一次性更新。`)
  if (relatedChains.value.length) rows.push(`关注关联线索是否继续新增节点，尤其是 ${selectedChainMeta.value?.title || relatedChains.value[0]?.title || '当前链条'}。`)
  rows.push('覆盖缺口尚未评估；请分别补看在地来源、第一手材料、不同语言和原文。')
  if (sourceCount.value < 6) rows.push('当前来源样本偏少；继续补充不同来源并核查转载或共同供稿情况。')
  if (rows.length < 3 && topEvidence.value[0]?.source_name) rows.push(`复核 ${topEvidence.value[0].source_name} 等代表性来源的原文表述。`)
  if (rows.length < 3) rows.push('继续观察官方表态、市场反应和相邻国家/机构的跟进动作。')
  return rows.slice(0, 4)
})

const coverageSignal = computed(() => {
  const metric = graphMetricPresentation('ground_news.coverage_signal', {
    value: selectedStory.value?.coverage_signal,
  })
  return { label: `覆盖状态${metric.valueLabel}`, tone: 'warn' }
})

const coverageExplanation = computed(() => graphMetricExplanation(
  'ground_news.coverage_signal',
  {
    inputs: {
      source_count: sourceCount.value,
      analysis_status: sourceBreakdown.value?.analysis_status,
      left_pct: biasBuckets.value.find((item) => item.key === 'left')?.value,
      right_pct: biasBuckets.value.find((item) => item.key === 'right')?.value,
      known_directory_label_pct: sourceBreakdown.value?.known_directory_label_pct,
    },
  },
))

const coverageCards = computed(() => {
  return [
    {
      label: '信源数量',
      value: '未知',
      detail: '原始计数缺证据定位，不能证明独立性与多样性',
      tone: 'warn',
    },
    {
      label: '目录分组构成',
      value: '未知',
      detail: '需要逐字段 Source Profile 合同与来源级输入绑定',
      tone: 'warn',
    },
    {
      label: '覆盖盲区',
      value: '未知',
      detail: '未批准阈值不得生成盲区通过或风险结论',
      tone: 'warn',
    },
  ]
})

const sourceColumns = computed(() => {
  const groups = Object.fromEntries(sourceColumnsModel.map((column) => [column.key, []]))
  for (const item of sourceTable.value.length ? sourceTable.value : evidence.value) {
    groups[normalizeLeaning(item.political_group || item.political_leaning)].push(item)
  }
  return sourceColumnsModel.map((column) => ({
    ...column,
    items: groups[column.key].slice(0, 12),
  }))
})

const sourceFilterOptions = computed(() => {
  const counts = { all: sourceTable.value.length, left: 0, center: 0, right: 0, state_aligned: 0, unknown: 0 }
  for (const row of sourceTable.value) {
    const key = normalizeLeaning(row.political_group || row.political_leaning)
    counts[key] = (counts[key] || 0) + 1
  }
  return [
    { key: 'all', label: '全部', count: counts.all },
    ...sourceColumnsModel.map((item) => ({ key: item.key, label: item.label, count: counts[item.key] || 0 })),
  ]
})

const activeSourceRows = computed(() => {
  if (!sourceTable.value.length) return []
  if (selectedSourceGroup.value === 'all') return sourceTable.value
  return sourceTable.value.filter((row) => normalizeLeaning(row.political_group || row.political_leaning) === selectedSourceGroup.value)
})

function compactAssistantText(value, max = 520) {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  return text.length > max ? `${text.slice(0, max)}...` : text
}

function buildGroundStoryAssistantMaterial() {
  const story = selectedStory.value || {}
  const title = story.canonical_title || story.title || story.cluster_id || '当前故事卡'
  const facts = Array.isArray(comparison.value?.key_facts)
    ? comparison.value.key_facts.slice(0, 6).map((item, index) => `${index + 1}. ${compactAssistantText(item, 220)}`).join('\n')
    : ''
  const differences = Array.isArray(comparison.value?.difference_summary)
    ? comparison.value.difference_summary.slice(0, 5).map((item, index) => `${index + 1}. ${compactAssistantText(item, 240)}`).join('\n')
    : ''
  const sourceBuckets = biasBuckets.value.length
    ? '目录标签构成已提供，但未绑定可发布指标证据'
    : ''
  const regions = countryRows.value.slice(0, 6).map((row) => `${row.key || '未知'} ${row.value}`).join(' | ')
  const sourceTypes = sourceTypeRows.value.slice(0, 6).map((row) => `${sourceTypeLabel(row.key)} ${row.value}`).join(' | ')
  const segmentsText = chronologicalSegments.value.slice(0, 6).map((segment, index) => (
    `${index + 1}. ${formatRange(segment.start_date, segment.end_date)} | ${angleLabel(segment.story_angle)} | ${compactAssistantText(segment.title || segment.segment_id, 220)} | ${segment.article_count || 0}条`
  )).join('\n')
  const evidenceText = topEvidence.value.map((item, index) => (
    `${index + 1}. ${compactAssistantText(item.title || '', 240)} | ${item.source_name || item.domain || '未知信源'}${item.news_id ? ` | news_id=${item.news_id}` : ''}`
  )).join('\n')
  const chainsText = relatedChains.value.slice(0, 5).map((chain, index) => (
    `${index + 1}. ${compactAssistantText(chain.title || chain.chain_id, 220)} | ${familyGroupLabel(chain.family_group)} | ${chain.segment_count || 0}切面 | 质量${qualityPct(chain.quality_score)}`
  )).join('\n')
  const selectedChainNodes = chainNodes.value.slice(0, 6).map((node, index) => (
    `${index + 1}. ${node.segment_order || index + 1} | ${formatRange(node.start_date, node.end_date)} | ${angleLabel(node.story_angle)} | ${compactAssistantText(node.title, 220)}`
  )).join('\n')
  return [
    `故事卡: ${title}`,
    `Cluster ID: ${story.cluster_id || selectedClusterId.value || '--'}`,
    `时间: ${formatRange(story.start_date, story.end_date)}`,
    `领域/主题/动作/语气: ${domainLabel(story.event_domain)} / ${familyLabel(story.event_family)} / ${eventActionLabel(story.event_action)} / ${toneLabel(story.tone)}`,
    `参与方: ${actorLine.value || entityLine.value}`,
    story.location ? `地点: ${story.location}` : '',
    `新闻总量: ${story.article_count || 0} | 独立信源: ${sourceCount.value} | L1.5切面: ${segmentCount.value} | 关联L2链: ${relatedChains.value.length}`,
    `价值判断: ${eventValueLevel.value.label} (${eventValueLevel.value.score}) - ${eventValueLevel.value.reason}`,
    `覆盖信号: ${coverageSignal.value.label}`,
    comparison.value?.neutral_summary ? `中立摘要: ${compactAssistantText(comparison.value.neutral_summary, 700)}` : '',
    facts ? `关键事实:\n${facts}` : '',
    differences ? `目录分组标题样本（非叙事差异结论）:\n${differences}` : '',
    `覆盖缺口检查: ${blindspotMetric.value.valueLabel} | 证据定位不可用`,
    valueSignals.value.length ? `价值信号:\n${valueSignals.value.map((item) => `- ${item.label}: ${item.value} (${item.detail})`).join('\n')}` : '',
    watchpoints.value.length ? `下一步观察:\n${watchpoints.value.map((item) => `- ${item}`).join('\n')}` : '',
    sourceBuckets ? `信源立场: ${sourceBuckets}` : '',
    regions ? `信源地区: ${regions}` : '',
    sourceTypes ? `媒体类型: ${sourceTypes}` : '',
    segmentsText ? `事件切面:\n${segmentsText}` : '',
    evidenceText ? `代表性标题:\n${evidenceText}` : '',
    chainsText ? `关联L2链:\n${chainsText}` : '',
    selectedChainNodes ? `当前L2链节点:\n${selectedChainNodes}` : '',
  ].filter(Boolean).join('\n')
}

function buildGroundStoryAssistantContext() {
  const story = selectedStory.value || {}
  const title = story.canonical_title || story.title || story.cluster_id || '当前故事卡'
  const prompt = [
    '请基于以下 Ground News 故事卡材料做协同研判。',
    '请输出：1）事件主线；2）目录分组与覆盖缺口；3）需要继续核验的证据；4）可生成报告的提纲；5）建议进入关联线索或数据搜索的后续动作。',
    '如果材料不足，请明确列出缺口，不要补造事实。',
    '',
    buildGroundStoryAssistantMaterial(),
  ].join('\n')
  return {
    visibleMessage: `请研判 Ground News 故事卡「${compactAssistantText(title, 80)}」，并给出报告提纲和下一步核验。`,
    prompt,
    mode: 'expert',
  }
}

function sendGroundStoryToAssistant() {
  if (!localStorage.getItem('access_token')) {
    ElMessage.warning('请先登录后使用数据助手')
    return
  }
  if (!selectedStory.value) {
    ElMessage.warning('请先选择故事卡')
    return
  }
  sessionStorage.setItem(ASSISTANT_AUTORUN_CONTEXT_KEY, JSON.stringify(buildGroundStoryAssistantContext()))
  assistantDrawerKey.value += 1
  assistantDrawerOpen.value = true
}

watch(selectedClusterId, async (value) => {
  if (value) await loadStory(value)
})

watch(() => route.query.cluster_id, (value) => {
  const clusterId = String(value || '')
  if (clusterId && clusterId !== selectedClusterId.value) {
    selectStory(clusterId)
  }
})

onMounted(async () => {
  const requestedClusterId = String(route.query.cluster_id || '')
  if (requestedClusterId) {
    selectedClusterId.value = requestedClusterId
  }
  await loadCards()
})

async function loadCards() {
  loadingCards.value = true
  try {
    const payload = await groundNewsApi.listStories({
      pageSize: 24,
      minArticles: minArticlesFilter.value,
      minSources: minSourcesFilter.value,
      dateDays: dateDaysFilter.value,
      sort: sortFilter.value,
      quality: qualityFilter.value,
      includeFirstDetail: true,
      query: searchInput.value,
      eventFamily: familyFilter.value,
    })
    stories.value = payload.stories || []
    totalStories.value = payload.total || 0
    const initialDetail = payload.initial_detail
    if (initialDetail?.story?.cluster_id) {
      detailCache.set(initialDetail.story.cluster_id, initialDetail)
    }
    const requestedClusterId = String(route.query.cluster_id || '')
    const selectedStillVisible = stories.value.some((story) => story.cluster_id === selectedClusterId.value)
    if (requestedClusterId) {
      selectedClusterId.value = requestedClusterId
    } else if ((!selectedClusterId.value || !selectedStillVisible) && stories.value.length) {
      selectedClusterId.value = stories.value[0].cluster_id
    } else if (!stories.value.length) {
      selectedClusterId.value = ''
      detail.value = null
      chainGraph.value = null
    }
  } finally {
    loadingCards.value = false
  }
}

async function loadStory(clusterId) {
  const requestSeq = ++storyRequestSeq
  loadingDetail.value = true
  detail.value = null
  chainGraph.value = null
  selectedChainId.value = ''
  activeTab.value = 'coverage'
  selectedSourceGroup.value = 'all'
  const cached = detailCache.get(clusterId)
  if (cached) {
    detail.value = cached
    loadingDetail.value = false
    loadFirstChain(cached, clusterId)
    return
  }
  try {
    const storyDetail = await groundNewsApi.getStory(clusterId)
    if (requestSeq !== storyRequestSeq || selectedClusterId.value !== clusterId) return
    detailCache.set(clusterId, storyDetail)
    detail.value = storyDetail
    loadingDetail.value = false
    loadFirstChain(storyDetail, clusterId)
  } finally {
    if (requestSeq === storyRequestSeq && selectedClusterId.value === clusterId) {
      loadingDetail.value = false
    }
  }
}

function selectStory(clusterId) {
  if (selectedClusterId.value === clusterId) return
  selectedClusterId.value = clusterId
  selectedSegment.value = null
  selectedSourceGroup.value = 'all'
}

function selectSourceGroup(key) {
  selectedSourceGroup.value = selectedSourceGroup.value === key ? 'all' : key
  activeTab.value = 'sources'
}

function loadFirstChain(storyDetail, clusterId) {
  const firstChain = storyDetail?.related_l2_chains?.[0]
  if (firstChain) {
    void selectChain(firstChain.chain_id, clusterId)
  }
}

async function selectChain(chainId, expectedClusterId = selectedClusterId.value) {
  const requestSeq = ++chainRequestSeq
  selectedChainId.value = chainId
  const cached = chainCache.get(chainId)
  if (cached) {
    if (requestSeq === chainRequestSeq && selectedClusterId.value === expectedClusterId) {
      chainGraph.value = cached
    }
    return
  }
  const chainDetail = await groundNewsApi.getL2Chain(chainId)
  chainCache.set(chainId, chainDetail)
  if (requestSeq === chainRequestSeq && selectedClusterId.value === expectedClusterId) {
    chainGraph.value = chainDetail
  }
}

async function setFamilyFilter(value) {
  familyFilter.value = value
  await refreshStoryList()
}

async function setDeskFilter(type, value) {
  if (type === 'sort') sortFilter.value = value
  if (type === 'date') dateDaysFilter.value = value
  if (type === 'articles') minArticlesFilter.value = value
  if (type === 'sources') minSourcesFilter.value = value
  if (type === 'quality') qualityFilter.value = value
  await refreshStoryList()
}

async function refreshStoryList() {
  selectedClusterId.value = ''
  detail.value = null
  chainGraph.value = null
  selectedSegment.value = null
  detailCache.clear()
  chainCache.clear()
  await loadCards()
}

function openSegment(segment) {
  selectedSegment.value = segment
}

function closeSegment() {
  selectedSegment.value = null
}

function handleSegmentDialogKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeSegment()
    return
  }
  trapDialogTab(event, segmentDialogRef.value, document.activeElement)
}

function sourceTypeWidth(value) {
  const max = Math.max(1, ...sourceTypeRows.value.map((row) => Number(row.value) || 0))
  return Math.max(8, Math.round((Number(value || 0) / max) * 100))
}

function sourceTypeFill(value) {
  const total = sourceTypeRows.value.reduce((sum, row) => sum + Number(row.value || 0), 0)
  if (!total) return 0
  return Math.max(2, Math.round((Number(value || 0) / total) * 100))
}

</script>

<style scoped>
.ground-page {
  --ink: #121a21;
  --muted: #697682;
  --line: rgba(28, 42, 52, 0.15);
  --paper: rgba(255, 252, 244, 0.92);
  --paper-solid: #fffaf0;
  --wash: #f3ede0;
  --navy: #102234;
  --green: #12624d;
  --amber: #a86019;
  --red: #a83c31;
  --cyan: #2f99aa;
  --serif: "Noto Serif CJK SC", "Source Han Serif SC", "Songti SC", "STSong", Georgia, serif;
  --sans: "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei", "IBM Plex Sans", sans-serif;
  height: 100vh;
  padding: 88px 22px 24px;
  color: var(--ink);
  font-family: var(--sans);
  position: relative;
  overflow: hidden;
  background:
    linear-gradient(115deg, rgba(255, 250, 240, 0.82), rgba(237, 244, 241, 0.78)),
    radial-gradient(circle at 11% 12%, rgba(198, 142, 52, 0.24), transparent 26%),
    radial-gradient(circle at 83% 4%, rgba(32, 100, 117, 0.28), transparent 34%),
    #edf2ee;
}

.ground-page::before {
  content: "";
  position: fixed;
  inset: 64px 0 0;
  background-image:
    linear-gradient(90deg, rgba(255, 250, 240, 0.52), rgba(255, 250, 240, 0.1) 34%, rgba(238, 244, 239, 0.28)),
    var(--atlas-image);
  background-size: cover, cover;
  background-position: center top, center top;
  background-repeat: no-repeat;
  opacity: 0.28;
  mix-blend-mode: multiply;
  pointer-events: none;
}

.ground-page::after {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(18, 26, 33, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(18, 26, 33, 0.026) 1px, transparent 1px);
  background-size: 42px 42px;
  mask-image: linear-gradient(180deg, transparent 0%, #000 16%, transparent 92%);
}

.ground-subnav,
.desk-topbar,
.desk-grid {
  position: relative;
  z-index: 1;
}

.ground-subnav {
  min-height: 48px;
  width: fit-content;
  max-width: 1660px;
  margin: 0 auto 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 5px;
  border: 1px solid rgba(17, 17, 17, 0.12);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 8px 20px rgba(17, 17, 17, 0.06);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  overflow-x: auto;
}

.ground-subnav span {
  flex: 0 0 auto;
  padding: 0 10px;
  color: var(--accent);
  font-size: 10px;
  font-weight: 950;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.ground-subnav a {
  flex: 0 0 auto;
  min-height: 38px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 22px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: #333;
  font-size: 14px;
  font-weight: 900;
  text-decoration: none;
}

.ground-subnav a:hover {
  border-color: rgba(184, 0, 0, 0.14);
  background: rgba(184, 0, 0, 0.06);
  color: var(--accent);
}

.ground-subnav a.router-link-exact-active {
  border-color: rgba(184, 0, 0, 0.28);
  background: rgba(184, 0, 0, 0.1);
  color: var(--accent);
  box-shadow: inset 0 -2px 0 var(--accent);
}

.desk-topbar {
  max-width: 1660px;
  margin: 0 auto 14px;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
}

.brand-lockup {
  display: flex;
  align-items: center;
  gap: 16px;
}

.brand-mark {
  width: 48px;
  height: 48px;
  border-radius: 18px 18px 18px 6px;
  display: grid;
  place-items: center;
  background: var(--navy);
  color: #fff;
  font-weight: 900;
  letter-spacing: -0.06em;
  box-shadow: 0 18px 35px rgba(23, 40, 57, 0.22), inset 0 -10px 20px rgba(255, 255, 255, 0.08);
}

.eyebrow,
.section-heading,
.story-list-card__top,
.angle-card__top,
.search-card label {
  color: #8b6a38;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.13em;
  text-transform: uppercase;
}

.desk-topbar h1 {
  margin: 3px 0 0;
  font-family: var(--serif);
  font-size: clamp(34px, 4.4vw, 68px);
  line-height: 1.04;
  letter-spacing: -0.035em;
  font-weight: 900;
}

.topbar-metrics {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.topbar-metrics span,
.filter-pills button,
.chip-cloud span,
.risk-flags span {
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 8px 11px;
  background: rgba(255, 255, 255, 0.66);
  color: #41505d;
  font-size: 12px;
  font-weight: 700;
}

.desk-grid {
  max-width: 1660px;
  margin: 0 auto;
  display: grid;
  grid-template-columns: minmax(280px, 350px) minmax(0, 1fr) minmax(300px, 380px);
  gap: 16px;
  align-items: start;
  height: calc(100vh - 238px);
  min-height: 620px;
}

.library-rail,
.story-stage,
.inspector-rail {
  min-width: 0;
}

.library-rail,
.inspector-rail {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

.search-card,
.story-list-card,
.lead-story-card,
.spectrum-card,
.signal-card,
.angle-card,
.timeline-panel,
.source-column,
.chain-summary,
.chain-node,
.edge-card,
.inspector-card {
  border: 1px solid var(--line);
  background: var(--paper);
  box-shadow: 0 18px 44px rgba(64, 78, 88, 0.11);
}

.search-card,
.lead-story-card,
.spectrum-card,
.timeline-panel,
.source-column,
.chain-summary,
.inspector-card {
  border-radius: 28px;
}

.search-card {
  padding: 16px;
}

.search-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  margin: 8px 0 12px;
}

.search-row input {
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 12px 13px;
  outline: none;
  background: #fff;
  color: var(--ink);
}

.search-row button {
  border: 0;
  border-radius: 16px;
  padding: 0 14px;
  background: var(--navy);
  color: #fff;
  font-weight: 800;
  cursor: pointer;
}

.filter-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.filter-pills button {
  cursor: pointer;
}

.filter-pills button.active {
  background: var(--navy);
  color: #fff;
}

.desk-filter-groups {
  display: grid;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.desk-filter-group {
  display: grid;
  grid-template-columns: 48px repeat(4, minmax(0, 1fr));
  gap: 6px;
  align-items: center;
}

.desk-filter-group span {
  color: var(--muted);
  font-size: 11px;
  font-weight: 900;
}

.desk-filter-group button {
  min-height: 30px;
  border: 1px solid var(--line);
  border-radius: 0;
  background: #fff;
  color: #26323d;
  cursor: pointer;
  font-size: 11px;
  font-weight: 900;
}

.desk-filter-group button.active {
  border-color: #111;
  background: #111;
  color: #fff;
}

.story-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 0 5px 18px 0;
  scrollbar-gutter: stable;
}

.story-list-card {
  border-radius: 22px;
  padding: 14px;
  text-align: left;
  cursor: pointer;
  transition: transform 140ms ease, border-color 140ms ease, background 140ms ease;
}

.story-list-card:hover,
.chain-picker button:hover {
  transform: translateY(-1px);
  border-color: rgba(25, 111, 85, 0.34);
}

.story-list-card.active {
  background:
    linear-gradient(135deg, rgba(236, 250, 241, 0.94), rgba(255, 252, 244, 0.88)),
    var(--atlas-image);
  background-size: cover;
  background-blend-mode: normal, soft-light;
  border-color: rgba(18, 98, 77, 0.48);
}

.story-list-card__top {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.story-list-card__top strong {
  color: var(--navy);
  letter-spacing: 0;
}

.story-list-card h3 {
  margin: 8px 0;
  font-size: 14px;
  line-height: 1.35;
}

.story-list-card small,
.angle-card small,
.timeline-row small,
.timeline-row p,
.source-item small,
.chain-node small,
.edge-card small,
.muted {
  color: var(--muted);
}

.mini-spectrum {
  height: 7px;
  border-radius: 999px;
  overflow: hidden;
  display: flex;
  margin: 8px 0;
  background: #d7dde1;
}

.mini-spectrum span {
  display: block;
  min-width: 2px;
}

.story-stage {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 0 6px 36px 0;
  scrollbar-gutter: stable;
}

.story-stage > * {
  flex: 0 0 auto;
}

.empty-state {
  border: 1px dashed rgba(42, 53, 62, 0.22);
  border-radius: 28px;
  min-height: 360px;
  display: grid;
  place-items: center;
  background: rgba(255, 255, 255, 0.48);
  color: var(--muted);
}

.dossier-preview {
  min-height: 360px;
  border: 1px solid rgba(32, 100, 117, 0.22);
  border-radius: 32px;
  padding: 24px;
  display: grid;
  grid-template-columns: minmax(220px, 0.8fr) minmax(0, 1.1fr);
  gap: 24px;
  align-items: center;
  background:
    linear-gradient(135deg, rgba(255, 252, 244, 0.84), rgba(232, 242, 239, 0.88)),
    var(--atlas-image);
  background-size: cover;
  background-position: center;
  box-shadow: 0 26px 70px rgba(31, 58, 72, 0.14);
  overflow: hidden;
}

.dossier-preview__art {
  position: relative;
  min-height: 260px;
  border-radius: 26px;
  overflow: hidden;
  background: var(--navy);
}

.dossier-preview__art img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  opacity: 0.72;
  filter: saturate(0.86) contrast(1.08);
}

.dossier-preview__art span {
  position: absolute;
  left: 16px;
  bottom: 16px;
  border-radius: 999px;
  padding: 8px 11px;
  background: rgba(255, 250, 240, 0.88);
  color: var(--navy);
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.dossier-preview h2 {
  margin: 8px 0 12px;
  font-family: var(--serif);
  font-size: clamp(30px, 4vw, 58px);
  line-height: 0.96;
  letter-spacing: -0.052em;
}

.dossier-preview p {
  max-width: 620px;
  color: #4b5a63;
  line-height: 1.6;
}

.dossier-preview__facts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 18px;
}

.dossier-preview__facts span {
  border: 1px solid rgba(16, 34, 52, 0.15);
  border-radius: 999px;
  padding: 8px 11px;
  background: rgba(255, 250, 240, 0.72);
  font-size: 12px;
  font-weight: 800;
}

.lead-story-card {
  position: relative;
  overflow: hidden;
  padding: 28px;
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(210px, 0.45fr);
  gap: 18px;
  background:
    linear-gradient(135deg, rgba(255, 252, 244, 0.98), rgba(255, 252, 244, 0.84)),
    radial-gradient(circle at 8% 0%, rgba(198, 142, 52, 0.2), transparent 28%);
}

.lead-story-card::after {
  content: "";
  position: absolute;
  right: 20px;
  bottom: 18px;
  width: 180px;
  height: 180px;
  border: 1px solid rgba(23, 40, 57, 0.08);
  border-radius: 50%;
  pointer-events: none;
}

.lead-story-copy {
  position: relative;
  z-index: 1;
}

.lead-story-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.lead-story-card__meta span {
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 7px 10px;
  background: rgba(255, 255, 255, 0.62);
  font-size: 12px;
  font-weight: 800;
}

.signal-pill.good {
  color: var(--green);
}

.signal-pill.warn {
  color: var(--amber);
}

.signal-pill.risk {
  color: var(--red);
}

.lead-story-card h2 {
  max-width: 980px;
  margin: 0;
  font-family: var(--serif);
  font-size: clamp(30px, 3.1vw, 46px);
  line-height: 1.02;
  letter-spacing: -0.046em;
}

.lead-story-card p {
  max-width: 900px;
  margin: 16px 0 0;
  color: #485866;
  font-size: 16px;
  line-height: 1.6;
}

.lead-facts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-top: 18px;
}

.lead-atlas {
  position: relative;
  min-height: 250px;
  border-radius: 24px;
  overflow: hidden;
  align-self: stretch;
  background: #102234;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.16);
}

.lead-atlas img {
  width: 100%;
  height: 100%;
  min-height: 250px;
  object-fit: cover;
  filter: saturate(0.9) contrast(1.05);
  opacity: 0.82;
}

.lead-atlas__overlay {
  position: absolute;
  inset: auto 12px 12px 12px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 20px;
  padding: 12px;
  background: rgba(14, 28, 42, 0.72);
  backdrop-filter: blur(10px);
  color: #fffaf0;
}

.lead-atlas__overlay span,
.lead-atlas__overlay small {
  display: block;
  color: rgba(255, 250, 240, 0.72);
  font-size: 11px;
  letter-spacing: 0.11em;
  text-transform: uppercase;
}

.lead-atlas__overlay strong {
  display: block;
  margin: 4px 0 3px;
  font-family: var(--serif);
  font-size: 22px;
  line-height: 1;
}

.lead-facts div {
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 11px;
  background: rgba(255, 255, 255, 0.74);
}

.lead-facts strong {
  display: block;
  font-size: 25px;
  line-height: 1;
}

.lead-facts span {
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
}

.lead-story-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 16px;
}

.lead-assistant-btn {
  min-height: 36px;
  padding: 0 16px;
  border: 1px solid rgba(70, 58, 32, 0.16);
  border-radius: 999px;
  background: #121820;
  color: #fffaf0;
  font-size: 13px;
  font-weight: 900;
  cursor: pointer;
  box-shadow: 0 10px 22px rgba(18, 24, 32, 0.14);
  transition:
    transform 0.18s ease,
    border-color 0.18s ease,
    background 0.18s ease;
}

.lead-assistant-btn:hover {
  transform: translateY(-1px);
  border-color: rgba(70, 58, 32, 0.28);
  background: #27313c;
}

.story-comparison-board {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(280px, 0.95fr) minmax(210px, 0.55fr);
  gap: 14px;
}

.comparison-primary,
.comparison-differences,
.comparison-blindspot,
.source-table-card {
  border: 1px solid var(--line);
  border-radius: 28px;
  padding: 18px;
  background: rgba(255, 252, 244, 0.92);
  box-shadow: 0 18px 44px rgba(64, 78, 88, 0.1);
}

.comparison-primary p,
.comparison-differences p,
.comparison-blindspot p {
  margin: 0;
  color: #42525e;
  line-height: 1.62;
}

.comparison-differences {
  display: grid;
  align-content: start;
  gap: 9px;
}

.comparison-differences p {
  border-left: 3px solid rgba(47, 153, 170, 0.42);
  padding-left: 10px;
  font-size: 13px;
}

.fact-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.fact-list span {
  border: 1px solid rgba(16, 34, 52, 0.12);
  border-radius: 999px;
  padding: 8px 10px;
  background: rgba(255, 255, 255, 0.68);
  color: #43515d;
  font-size: 12px;
  font-weight: 800;
}

.comparison-blindspot.level-high,
.comparison-blindspot.level-medium {
  border-color: rgba(168, 60, 49, 0.34);
  background: linear-gradient(135deg, rgba(255, 252, 244, 0.94), rgba(255, 242, 236, 0.88));
}

.comparison-blindspot.level-watch {
  border-color: rgba(168, 96, 25, 0.34);
}

.comparison-blindspot strong {
  display: block;
  margin: 4px 0 9px;
  font-family: var(--serif);
  font-size: 48px;
  line-height: 0.9;
  color: var(--navy);
}

.comparison-blindspot p {
  font-size: 12px;
}

.coverage-board {
  display: grid;
  grid-template-columns: 1.5fr minmax(230px, 0.9fr);
  gap: 14px;
  align-items: stretch;
}

.spectrum-card,
.chain-summary,
.inspector-card,
.timeline-panel,
.source-column {
  padding: 18px;
}

.spectrum-card {
  min-height: 318px;
  display: grid;
  grid-template-rows: auto minmax(40px, 0.28fr) minmax(0, 1fr);
  gap: 16px;
}

.section-heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  margin-bottom: 14px;
}

.section-heading small {
  color: var(--muted);
  letter-spacing: 0;
  text-transform: none;
}

.bias-bar {
  height: 100%;
  min-height: 42px;
  border-radius: 999px;
  overflow: hidden;
  display: flex;
  background: #d7dde1;
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.04);
}

.bias-bar span {
  min-width: 0;
}

.bias-breakdown {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
  margin-top: 0;
  align-content: stretch;
}

.bias-breakdown button {
  position: relative;
  border: 1px solid var(--line);
  border-radius: 16px;
  min-width: 0;
  padding: 13px 8px 9px;
  background: rgba(255, 255, 255, 0.76);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-height: 64px;
  overflow: hidden;
  cursor: pointer;
  text-align: left;
}

.bias-breakdown button.active,
.source-filter-strip button.active {
  border-color: rgba(18, 98, 77, 0.48);
  background: rgba(236, 250, 241, 0.9);
}

.bias-breakdown button > span {
  position: absolute;
  inset: 0 0 auto 0;
  width: 100%;
  height: 5px;
  border-radius: 16px 16px 0 0;
}

.bias-breakdown button div {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  justify-content: space-between;
}

.bias-breakdown strong,
.bias-breakdown small {
  display: block;
  white-space: nowrap;
}

.bias-breakdown strong {
  font-size: 12px;
  letter-spacing: -0.02em;
}

.bias-breakdown small {
  font-size: 12px;
}

.signal-stack {
  display: grid;
  gap: 10px;
  height: 100%;
}

.signal-card {
  border-radius: 22px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.signal-card span,
.signal-card small {
  display: block;
  color: var(--muted);
  font-size: 12px;
}

.signal-card strong {
  display: block;
  margin: 5px 0;
  font-family: var(--serif);
  font-size: 24px;
  line-height: 1.05;
}

.signal-card.good {
  border-color: rgba(25, 111, 85, 0.24);
}

.signal-card.warn {
  border-color: rgba(185, 104, 30, 0.28);
}

.signal-card.risk {
  border-color: rgba(182, 66, 50, 0.32);
}

.source-intel-grid {
  display: grid;
  grid-template-columns: minmax(210px, 0.9fr) minmax(280px, 1.1fr);
  gap: 14px;
}

.source-intel-card {
  border: 1px solid var(--line);
  border-radius: 28px;
  padding: 18px;
  background: rgba(255, 252, 244, 0.9);
  box-shadow: 0 18px 44px rgba(64, 78, 88, 0.1);
  min-height: 236px;
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.source-intel-card:nth-child(3) {
  grid-column: 1 / -1;
  min-height: 168px;
}

.source-intel-card--map {
  background:
    linear-gradient(135deg, rgba(255, 252, 244, 0.86), rgba(242, 249, 246, 0.9)),
    var(--atlas-image);
  background-size: cover;
  background-position: center;
  background-blend-mode: normal, soft-light;
}

.region-ledger {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.region-ledger span {
  border: 1px solid rgba(16, 34, 52, 0.12);
  border-radius: 18px;
  padding: 10px;
  background: rgba(255, 250, 240, 0.75);
  color: #4a5864;
  font-size: 12px;
}

.region-ledger strong {
  display: block;
  color: var(--navy);
  font-family: var(--serif);
  font-size: 22px;
  line-height: 1;
}

.source-type-bars {
  display: grid;
  gap: 9px;
  flex: 1;
  align-content: space-between;
}

.source-type-bars div {
  display: grid;
  grid-template-columns: minmax(76px, 1fr) minmax(112px, 46%) auto;
  gap: 8px;
  align-items: center;
  position: relative;
  overflow: hidden;
  isolation: isolate;
  color: #4d5b66;
  font-size: 12px;
  border: 1px solid rgba(16, 34, 52, 0.1);
  border-radius: 16px;
  padding: 8px 10px;
  background: rgba(255, 255, 255, 0.58);
}

.source-type-bars div::before {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: var(--type-fill, 0%);
  background: linear-gradient(180deg, rgba(39, 124, 139, 0.04), rgba(39, 124, 139, 0.16));
  border-top: 1px solid rgba(39, 124, 139, 0.2);
  z-index: 0;
}

.source-type-bars span,
.source-type-bars strong {
  position: relative;
  z-index: 1;
  white-space: nowrap;
}

.source-type-bars i {
  display: block;
  position: relative;
  z-index: 1;
  height: 8px;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--cyan), var(--amber));
}

.source-type-bars strong {
  color: var(--navy);
}

.headline-ledger {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 9px;
  flex: 1;
  align-content: space-between;
}

.headline-ledger p {
  margin: 0;
  display: -webkit-box;
  overflow: hidden;
  color: #41505d;
  font-size: 12px;
  line-height: 1.42;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.headline-ledger strong {
  color: var(--navy);
  margin-right: 4px;
}

.story-tabs {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  overflow: visible;
  padding: 4px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.54);
}

.story-tabs button {
  border: 0;
  border-radius: 999px;
  padding: 11px 10px;
  background: transparent;
  color: var(--muted);
  font-weight: 900;
  cursor: pointer;
  white-space: nowrap;
  min-width: 0;
}

.story-tabs button.active {
  background: var(--navy);
  color: #fff;
}

.coverage-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.angle-card,
.chain-node,
.edge-card {
  border-radius: 24px;
  padding: 16px;
}

.angle-card {
  background: var(--paper-solid);
}

.angle-card a,
.source-item a,
.modal-news-list a {
  color: var(--green);
  font-weight: 800;
  text-decoration: none;
}

.angle-card a:hover,
.source-item a:hover,
.modal-news-list a:hover {
  text-decoration: underline;
}

.angle-card__top,
.source-column__head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
}

.angle-card h3,
.timeline-row h3,
.source-item p,
.chain-summary h3,
.chain-node h3 {
  margin: 8px 0 6px;
  line-height: 1.35;
}

.angle-card ul {
  margin: 12px 0 0;
  padding-left: 17px;
  color: #465663;
  font-size: 12px;
  line-height: 1.45;
}

.card-action,
.inline-action,
.news-detail-link {
  border: 0;
  border-radius: 999px;
  background: rgba(18, 34, 52, 0.08);
  color: var(--navy);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  font-weight: 900;
  line-height: 1;
  margin-top: 12px;
  padding: 8px 11px;
}

.card-action:hover,
.inline-action:hover,
.news-detail-link:hover {
  background: var(--navy);
  color: #fffaf0;
  text-decoration: none;
}

.timeline-panel {
  display: grid;
  gap: 12px;
}

.timeline-row {
  display: grid;
  grid-template-columns: 42px 1fr;
  gap: 12px;
  align-items: start;
  position: relative;
  cursor: pointer;
  border-radius: 18px;
  padding: 8px;
  transition: background 140ms ease;
}

.timeline-row:hover {
  background: rgba(255, 255, 255, 0.58);
}

.timeline-row > span {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: var(--navy);
  color: #fff;
  font-size: 11px;
  font-weight: 900;
}

.timeline-row p {
  margin: 0;
}

.source-spectrum-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 14px;
}

.source-table-card {
  grid-column: 1 / -1;
}

.source-filter-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.source-filter-strip button {
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 8px 10px;
  background: rgba(255, 255, 255, 0.7);
  color: #40505c;
  cursor: pointer;
  font-size: 12px;
  font-weight: 900;
}

.source-filter-strip strong {
  margin-left: 4px;
  color: var(--navy);
}

.source-table {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 9px;
}

.source-table-row {
  border: 1px solid rgba(16, 34, 52, 0.12);
  border-radius: 16px;
  padding: 10px;
  background: rgba(255, 255, 255, 0.64);
  color: inherit;
  display: grid;
  gap: 4px;
  min-width: 0;
  text-decoration: none;
}

.source-table-row:hover {
  border-color: rgba(18, 98, 77, 0.32);
  background: #fff;
}

.source-table-row span {
  color: var(--green);
  font-size: 12px;
  font-weight: 900;
}

.source-table-row strong {
  color: var(--ink);
  font-size: 13px;
  line-height: 1.35;
}

.source-table-row small {
  color: var(--muted);
  font-size: 11px;
}

.source-column {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.source-column__head span {
  width: 8px;
  height: 28px;
  border-radius: 999px;
}

.source-column__head strong {
  flex: 1;
  font-size: 13px;
}

.source-stack {
  display: grid;
  gap: 9px;
  align-content: start;
}

.source-item {
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 11px;
  background: rgba(255, 255, 255, 0.7);
  display: grid;
  gap: 6px;
}

.source-item strong {
  font-size: 12px;
}

.source-item p {
  font-size: 12px;
}

.l2-dossier {
  display: grid;
  gap: 12px;
}

.chain-summary h3 {
  font-family: var(--serif);
  font-size: 28px;
  letter-spacing: -0.035em;
}

.chain-summary p {
  color: var(--muted);
}

.risk-flags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chain-node-list {
  display: grid;
  gap: 10px;
}

.chain-node {
  display: grid;
  grid-template-columns: 38px 1fr;
  gap: 12px;
  background: var(--paper-solid);
}

.chain-node > span {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--navy);
  color: #fff;
  display: grid;
  place-items: center;
  font-weight: 900;
}

.edge-card {
  background: #f7f8f5;
}

.edge-card div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.inspector-rail {
  overflow-y: auto;
  overflow-x: hidden;
  padding: 0 5px 18px 0;
  scrollbar-gutter: stable;
}

.anatomy-list {
  margin: 0;
  display: grid;
  gap: 10px;
}

.anatomy-list div {
  border-bottom: 1px solid var(--line);
  padding-bottom: 9px;
}

.anatomy-list dt {
  color: var(--muted);
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.anatomy-list dd {
  margin: 3px 0 0;
  font-weight: 800;
}

.chip-cloud {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip-cloud--compact span {
  padding: 7px 9px;
}

.chain-picker {
  display: grid;
  gap: 8px;
}

.chain-picker button {
  border: 1px solid var(--line);
  border-radius: 19px;
  background: rgba(255, 255, 255, 0.74);
  padding: 12px;
  text-align: left;
  cursor: pointer;
  transition: transform 140ms ease, border-color 140ms ease;
}

.chain-picker button.active {
  border-color: rgba(25, 111, 85, 0.46);
  background: #f3fbf5;
}

.chain-picker strong,
.chain-picker small {
  display: block;
  line-height: 1.35;
}

.chain-picker small {
  color: var(--muted);
  margin-top: 5px;
}

.muted {
  margin: 0;
  font-size: 13px;
}

.story-list,
.story-stage,
.inspector-rail {
  scrollbar-color: rgba(16, 34, 52, 0.28) transparent;
  scrollbar-width: thin;
}

.story-list::-webkit-scrollbar,
.story-stage::-webkit-scrollbar,
.inspector-rail::-webkit-scrollbar {
  width: 8px;
}

.story-list::-webkit-scrollbar-track,
.story-stage::-webkit-scrollbar-track,
.inspector-rail::-webkit-scrollbar-track {
  background: rgba(255, 255, 255, 0.22);
  border-radius: 999px;
}

.story-list::-webkit-scrollbar-thumb,
.story-stage::-webkit-scrollbar-thumb,
.inspector-rail::-webkit-scrollbar-thumb {
  background: linear-gradient(180deg, rgba(18, 34, 52, 0.38), rgba(18, 98, 77, 0.36));
  border-radius: 999px;
}

.story-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 300;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(14, 28, 42, 0.34);
  backdrop-filter: blur(8px);
}

.story-modal {
  position: relative;
  width: min(760px, 94vw);
  max-height: min(760px, 86vh);
  overflow-y: auto;
  border: 1px solid rgba(255, 255, 255, 0.36);
  border-radius: 30px;
  padding: 24px;
  background:
    linear-gradient(135deg, rgba(255, 252, 244, 0.96), rgba(240, 247, 243, 0.94)),
    var(--atlas-image);
  background-size: cover;
  background-blend-mode: normal, soft-light;
  box-shadow: 0 30px 90px rgba(9, 23, 38, 0.28);
}

.story-modal__close {
  position: absolute;
  top: 14px;
  right: 14px;
  width: 34px;
  height: 34px;
  border: 0;
  border-radius: 50%;
  background: var(--navy);
  color: #fffaf0;
  cursor: pointer;
  font-size: 20px;
}

.story-modal h2 {
  margin: 0;
  padding-right: 38px;
  font-family: var(--serif);
  font-size: clamp(26px, 3vw, 42px);
  line-height: 1.12;
}

.story-modal__meta {
  color: var(--muted);
  font-size: 13px;
  font-weight: 800;
}

.modal-news-list {
  display: grid;
  gap: 10px;
  margin-top: 18px;
}

.modal-news-list article {
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 13px;
  background: rgba(255, 255, 255, 0.68);
}

.modal-news-list h3 {
  margin: 0 0 6px;
  font-size: 15px;
  line-height: 1.35;
}

.modal-news-list small,
.modal-news-list a {
  display: inline-flex;
  margin-right: 10px;
  font-size: 12px;
}

/* Story page redesign: quieter surfaces, clearer reading order, real covers. */
.ground-page {
  --ink: #17212b;
  --muted: #66717c;
  --line: rgba(23, 33, 43, 0.12);
  --paper: rgba(255, 255, 255, 0.94);
  --paper-solid: #ffffff;
  --wash: #eef3f5;
  --navy: #17283a;
  --green: #0f6b57;
  --amber: #a7641d;
  --red: #a83c31;
  --cyan: #277c8b;
  --panel-radius: 8px;
  height: auto;
  min-height: 100vh;
  overflow-x: hidden;
  overflow-y: auto;
  padding: 78px 18px 34px;
  background:
    linear-gradient(180deg, rgba(249, 251, 252, 0.98), rgba(239, 244, 246, 0.98)),
    linear-gradient(115deg, rgba(226, 236, 240, 0.72), rgba(248, 250, 250, 0.94) 44%, rgba(236, 243, 241, 0.84)),
    #f4f7f8;
}

.ground-page::before {
  inset: 0;
  height: auto;
  opacity: 0.92;
  background-image:
    linear-gradient(180deg, rgba(255, 255, 255, 0.82), rgba(255, 255, 255, 0.18) 34%, rgba(255, 255, 255, 0.62)),
    repeating-linear-gradient(0deg, rgba(26, 44, 58, 0.026) 0 1px, transparent 1px 34px),
    repeating-linear-gradient(90deg, rgba(26, 44, 58, 0.018) 0 1px, transparent 1px 34px),
    repeating-linear-gradient(114deg, transparent 0 132px, rgba(215, 228, 233, 0.34) 132px 190px, transparent 190px 340px);
  background-size: auto, 34px 34px, 34px 34px, 340px 340px;
  background-position: center top, left top, left top, 0 0;
  background-repeat: repeat;
  mix-blend-mode: normal;
}

.ground-page::after {
  opacity: 0.18;
  background-size: 32px 32px;
  mask-image: none;
}

.desk-topbar {
  max-width: 1760px;
  margin-bottom: 14px;
  align-items: center;
}

.brand-mark {
  width: 40px;
  height: 40px;
  border-radius: var(--panel-radius);
  box-shadow: none;
}

.desk-topbar h1 {
  font-family: var(--sans);
  font-size: clamp(25px, 2.4vw, 38px);
  letter-spacing: -0.02em;
  line-height: 1.12;
}

.topbar-metrics span,
.filter-pills button,
.chip-cloud span,
.risk-flags span {
  border-radius: var(--panel-radius);
  background: rgba(255, 255, 255, 0.76);
  box-shadow: none;
}

.desk-grid {
  max-width: 1760px;
  height: calc(100vh - 172px);
  min-height: 680px;
  grid-template-columns: minmax(260px, 308px) minmax(620px, 1fr) minmax(250px, 310px);
  gap: 14px;
  align-items: stretch;
}

.library-rail,
.inspector-rail {
  position: sticky;
  top: 78px;
  height: 100%;
  max-height: none;
  min-height: 0;
  overflow-x: hidden;
}

.library-rail {
  overflow: hidden;
}

.inspector-rail {
  overflow-y: auto;
  padding-right: 4px;
}

.story-stage {
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 0 4px 48px 0;
  gap: 16px;
}

.search-card,
.story-list-card,
.lead-story-card,
.spectrum-card,
.signal-card,
.angle-card,
.timeline-panel,
.source-column,
.chain-summary,
.chain-node,
.edge-card,
.inspector-card,
.comparison-primary,
.comparison-differences,
.comparison-blindspot,
.source-intel-card,
.source-table-card,
.story-modal,
.modal-news-list article {
  border-radius: var(--panel-radius);
  background: var(--paper);
  box-shadow: 0 10px 28px rgba(28, 42, 52, 0.06);
}

.search-card,
.spectrum-card,
.chain-summary,
.inspector-card,
.timeline-panel,
.source-column,
.source-intel-card,
.source-table-card,
.comparison-primary,
.comparison-differences,
.comparison-blindspot {
  padding: 16px;
}

.search-row input,
.search-row button,
.filter-pills button,
.lead-story-card__meta span,
.lead-facts div,
.bias-breakdown button,
.region-ledger span,
.source-type-bars div,
.story-tabs,
.story-tabs button,
.source-filter-strip button,
.source-table-row,
.source-item,
.chain-picker button {
  border-radius: var(--panel-radius);
}

.story-list-card {
  display: grid;
  grid-template-columns: 104px minmax(0, 1fr);
  column-gap: 12px;
  row-gap: 5px;
  padding: 10px;
}

.story-list-card :deep(.story-cover) {
  grid-row: 1 / span 5;
}

.story-list-card__top,
.story-list-card h3,
.story-list-card .mini-spectrum,
.story-list-card small {
  grid-column: 2;
}

.story-list-card h3 {
  margin: 0;
  font-size: 13px;
  line-height: 1.34;
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}

.story-list-card__top {
  margin: 0;
}

.story-list-card .mini-spectrum {
  margin: 4px 0 2px;
}

.story-list-card.active {
  background: #f3fbf7;
  background-image: none;
}

.story-cover {
  position: relative;
  overflow: hidden;
  isolation: isolate;
  background: linear-gradient(135deg, #13273a, #286d64);
  border-radius: var(--panel-radius);
  border: 1px solid rgba(23, 33, 43, 0.1);
}

.story-cover--list {
  width: 104px;
  min-height: 86px;
  aspect-ratio: 16 / 10;
  background: #dfe7ea;
}

.story-cover--preview {
  width: 100%;
  height: 100%;
  min-height: 260px;
}

.story-cover--hero {
  width: 100%;
  height: 100%;
  min-height: 440px;
  border-radius: 0;
  border: 0;
}

.story-cover__image {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  filter: saturate(0.96) contrast(1.04);
}

.story-cover.has-image::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, rgba(7, 16, 26, 0.08), rgba(7, 16, 26, 0.62));
  z-index: 1;
}

.story-cover__grid,
.story-cover__wash {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.story-cover__grid {
  opacity: 0.36;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.11) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.1) 1px, transparent 1px);
  background-size: 24px 24px;
}

.story-cover__wash {
  background:
    linear-gradient(145deg, transparent 0 50%, rgba(255, 255, 255, 0.18) 51% 52%, transparent 53%),
    radial-gradient(circle at 72% 28%, rgba(255, 255, 255, 0.18), transparent 28%);
}

.story-cover.has-image .story-cover__grid,
.story-cover.has-image .story-cover__wash {
  opacity: 0;
}

.story-cover__label {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 12px;
  z-index: 2;
  color: #fff;
  border-radius: var(--panel-radius);
  padding: 10px 11px;
  background: rgba(9, 18, 28, 0.72);
  backdrop-filter: blur(8px);
}

.story-cover__label span,
.story-cover__label small {
  display: block;
  color: rgba(255, 255, 255, 0.78);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.story-cover__label strong {
  display: block;
  margin: 3px 0;
  font-size: 22px;
  line-height: 1;
}

.story-cover--list .story-cover__label {
  left: 8px;
  right: 8px;
  bottom: 8px;
  padding: 7px;
}

.story-cover--list .story-cover__label strong {
  font-size: 13px;
}

.story-cover--list .story-cover__label small {
  display: none;
}

.theme-diplomacy { background: linear-gradient(135deg, #17283a, #59716d); }
.theme-security { background: linear-gradient(135deg, #17202c, #8c3b34); }
.theme-markets { background: linear-gradient(135deg, #15392f, #2b7f91); }
.theme-technology { background: linear-gradient(135deg, #1d3044, #4566a3); }
.theme-civic { background: linear-gradient(135deg, #263246, #7a6549); }
.theme-policy { background: linear-gradient(135deg, #253042, #59636f); }
.theme-public { background: linear-gradient(135deg, #184336, #81905a); }
.theme-border { background: linear-gradient(135deg, #2a3140, #82614f); }
.theme-climate { background: linear-gradient(135deg, #163c48, #5f7d65); }

.ground-page :deep(.story-cover__image) {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  filter: saturate(0.96) contrast(1.04);
}

.ground-page :deep(.story-cover--list .story-cover__image) {
  object-fit: contain;
  padding: 0;
}

.ground-page :deep(.story-cover--list.has-image .story-cover__label) {
  display: none;
}

.ground-page :deep(.story-cover.has-image::after) {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, rgba(7, 16, 26, 0.06), rgba(7, 16, 26, 0.7));
  z-index: 1;
}

.ground-page :deep(.story-cover--list.has-image::after) {
  background: linear-gradient(180deg, rgba(7, 16, 26, 0.02), rgba(7, 16, 26, 0.16));
}

.ground-page :deep(.story-cover__grid),
.ground-page :deep(.story-cover__wash) {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.ground-page :deep(.story-cover.has-image .story-cover__grid),
.ground-page :deep(.story-cover.has-image .story-cover__wash) {
  opacity: 0;
}

.ground-page :deep(.story-cover__label) {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 12px;
  z-index: 2;
  border-radius: var(--panel-radius);
  padding: 10px 11px;
  background: rgba(9, 18, 28, 0.76);
  color: #fff;
  backdrop-filter: blur(8px);
}

.ground-page :deep(.story-cover__label span),
.ground-page :deep(.story-cover__label small) {
  display: block;
  color: rgba(255, 255, 255, 0.78);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.ground-page :deep(.story-cover__label strong) {
  display: block;
  margin: 3px 0;
  color: #fff;
  font-size: 22px;
  line-height: 1;
}

.ground-page :deep(.story-cover--list .story-cover__label) {
  left: 8px;
  right: 8px;
  bottom: 8px;
  padding: 7px;
}

.ground-page :deep(.story-cover--list .story-cover__label strong) {
  font-size: 13px;
}

.ground-page :deep(.story-cover--list .story-cover__label small) {
  display: none;
}

.dossier-preview {
  border-radius: var(--panel-radius);
  min-height: 330px;
  background: var(--paper);
  box-shadow: 0 10px 28px rgba(28, 42, 52, 0.06);
}

.dossier-preview__art {
  border-radius: var(--panel-radius);
  background: transparent;
}

.dossier-preview__art img,
.lead-atlas,
.lead-atlas img,
.lead-atlas__overlay {
  display: none;
}

.lead-story-card {
  display: grid;
  grid-template-columns: minmax(300px, 42%) minmax(0, 1fr);
  gap: 0;
  padding: 0;
  overflow: hidden;
  background: var(--paper-solid);
}

.lead-story-card::after {
  display: none;
}

.story-hero-cover {
  min-height: 440px;
  background: #13273a;
}

.lead-story-copy {
  padding: 28px 30px;
}

.lead-story-card h2 {
  max-width: 860px;
  font-family: var(--sans);
  font-size: clamp(28px, 3vw, 42px);
  letter-spacing: -0.03em;
  line-height: 1.1;
}

.lead-summary {
  max-width: 860px;
  margin-top: 14px;
  color: #3e4b57;
}

.lead-facts {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-top: 22px;
}

.lead-facts div {
  background: #f7f9fa;
  box-shadow: none;
}

.story-comparison-board {
  grid-template-columns: minmax(0, 1.38fr) minmax(240px, 0.62fr);
  gap: 12px;
}

.comparison-primary {
  grid-column: 1;
}

.comparison-blindspot {
  grid-column: 2;
  grid-row: 1;
  position: relative;
  display: grid;
  align-content: center;
  min-height: 236px;
  overflow: hidden;
  text-align: center;
}

.comparison-blindspot::before {
  content: "";
  position: absolute;
  inset: 14px;
  border-radius: var(--panel-radius);
  background:
    radial-gradient(circle at 50% 45%, rgba(168, 96, 25, 0.14), transparent 0 48%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.54), rgba(255, 255, 255, 0));
  pointer-events: none;
}

.comparison-blindspot .section-heading,
.comparison-blindspot strong,
.comparison-blindspot p {
  position: relative;
  z-index: 1;
}

.comparison-blindspot strong {
  margin: 12px 0 9px;
  font-size: clamp(82px, 8vw, 118px);
  line-height: 0.78;
  letter-spacing: -0.06em;
}

.comparison-blindspot p {
  max-width: 180px;
  margin: 0 auto;
  color: #5a4c3e;
  font-size: 13px;
  font-weight: 800;
  line-height: 1.45;
}

.comparison-differences {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.comparison-differences .section-heading {
  grid-column: 1 / -1;
}

.comparison-differences p {
  border-left: 0;
  border-top: 3px solid rgba(39, 124, 139, 0.38);
  padding: 10px 0 0;
}

.fact-list span {
  border-radius: var(--panel-radius);
  background: #f7f9fa;
}

.coverage-board {
  grid-template-columns: minmax(0, 1.35fr) minmax(260px, 0.65fr);
  gap: 12px;
}

.spectrum-card {
  min-height: 260px;
  grid-template-rows: auto 42px minmax(0, 1fr);
}

.bias-breakdown {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}

.bias-breakdown button {
  isolation: isolate;
}

.bias-breakdown button::before {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: var(--fill, 0%);
  background: color-mix(in srgb, var(--fill-color, #aab3bd) 26%, transparent);
  z-index: 0;
  transition: height 160ms ease;
}

.bias-breakdown button::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: var(--fill, 0%);
  height: 1px;
  background: color-mix(in srgb, var(--fill-color, #aab3bd) 62%, transparent);
  opacity: 0.68;
  z-index: 0;
}

.bias-breakdown button > span,
.bias-breakdown button div {
  position: relative;
  z-index: 1;
}

.signal-card {
  box-shadow: none;
}

.source-intel-grid {
  grid-template-columns: minmax(190px, 0.8fr) minmax(260px, 1fr) minmax(300px, 1.3fr);
}

.source-intel-card:nth-child(3) {
  grid-column: auto;
  min-height: 236px;
}

.headline-ledger {
  grid-template-columns: 1fr;
  align-content: start;
}

.story-tabs {
  position: sticky;
  top: 0;
  z-index: 4;
  border-radius: var(--panel-radius);
  background: rgba(255, 255, 255, 0.88);
  backdrop-filter: blur(10px);
  box-shadow: 0 10px 22px rgba(28, 42, 52, 0.08);
}

.story-tabs button {
  padding: 10px;
}

.coverage-grid {
  grid-template-columns: minmax(0, 1fr);
  gap: 10px;
}

.angle-card {
  display: grid;
  grid-template-columns: minmax(150px, 0.28fr) minmax(0, 1fr) auto;
  gap: 12px;
  align-items: start;
}

.angle-card__top {
  display: grid;
  gap: 4px;
  justify-content: start;
}

.angle-card h3 {
  margin: 0;
}

.angle-card > small,
.angle-card ul {
  grid-column: 2;
}

.angle-card ul {
  margin: 4px 0 0;
}

.angle-card .card-action {
  grid-column: 3;
  grid-row: 1 / span 3;
  align-self: center;
  margin-top: 0;
}

.source-spectrum-grid {
  grid-template-columns: minmax(0, 1fr);
}

.source-table {
  grid-template-columns: 1fr;
  gap: 0;
  border: 1px solid var(--line);
  border-radius: var(--panel-radius);
  overflow: hidden;
}

.source-table-row {
  border: 0;
  border-bottom: 1px solid var(--line);
  border-radius: 0;
  grid-template-columns: 150px minmax(0, 1fr) 250px;
  align-items: start;
  background: #fff;
}

.source-table-row:last-child {
  border-bottom: 0;
}

.source-column {
  box-shadow: none;
}

.inspector-card {
  box-shadow: none;
}

/* Newsroom density pass: compact lists and square editorial surfaces. */
.ground-page {
  --ink: #111111;
  --muted: #5f6368;
  --line: #d6d6d6;
  --paper: #ffffff;
  --paper-solid: #ffffff;
  --navy: #111111;
  --green: #006c5b;
  --accent: #b80000;
  --panel-radius: 0;
  background: #f6f6f4;
}

.ground-page::before,
.ground-page::after {
  display: none;
}

.ground-subnav {
  width: fit-content;
  min-height: 48px;
  max-width: 1760px;
  margin: 0 auto 18px;
  justify-content: center;
  gap: 6px;
  padding: 5px;
  border: 1px solid rgba(17, 17, 17, 0.12);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 8px 20px rgba(17, 17, 17, 0.06);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}

.ground-subnav span {
  align-self: stretch;
  display: inline-flex;
  align-items: center;
  padding: 0 12px;
  border-right: 1px solid rgba(17, 17, 17, 0.08);
  background: transparent;
  color: var(--accent);
  font-size: 11px;
  letter-spacing: 0.08em;
}

.ground-subnav a {
  min-height: 38px;
  padding: 0 22px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: #333;
  font-size: 14px;
  font-weight: 950;
}

.ground-subnav a:hover {
  border-color: rgba(184, 0, 0, 0.14);
  background: rgba(184, 0, 0, 0.06);
  color: var(--accent);
}

.ground-subnav a.router-link-exact-active {
  border-color: rgba(184, 0, 0, 0.28);
  background: rgba(184, 0, 0, 0.1);
  color: var(--accent);
  box-shadow: inset 0 -2px 0 var(--accent);
}

.desk-topbar {
  align-items: flex-end;
  border-bottom: 5px solid #111;
  padding-bottom: 10px;
}

.brand-mark {
  border-radius: 0;
  background: var(--accent);
}

.desk-topbar h1 {
  font-family: var(--serif);
  font-size: clamp(30px, 3vw, 50px);
  letter-spacing: 0;
}

.eyebrow,
.section-heading,
.story-list-card__top,
.angle-card__top,
.search-card label {
  color: var(--accent);
  letter-spacing: 0;
}

.topbar-metrics span,
.filter-pills button,
.chip-cloud span,
.risk-flags span {
  border-radius: 0;
  background: #fff;
}

.desk-grid {
  grid-template-columns: minmax(320px, 380px) minmax(0, 1fr) minmax(280px, 330px);
  gap: 18px;
}

.search-card,
.story-list-card,
.lead-story-card,
.spectrum-card,
.signal-card,
.angle-card,
.timeline-panel,
.source-column,
.chain-summary,
.chain-node,
.edge-card,
.inspector-card,
.comparison-primary,
.comparison-differences,
.comparison-blindspot,
.source-intel-card,
.source-table-card,
.story-modal,
.modal-news-list article {
  border-radius: 0;
  background: #fff;
  box-shadow: none;
}

.search-card,
.inspector-card,
.spectrum-card,
.timeline-panel,
.source-column,
.source-intel-card,
.source-table-card,
.comparison-primary,
.comparison-differences,
.comparison-blindspot {
  border-top: 4px solid #111;
}

.search-row input,
.search-row button,
.filter-pills button,
.lead-story-card__meta span,
.lead-facts div,
.bias-breakdown button,
.region-ledger span,
.source-type-bars div,
.story-tabs,
.story-tabs button,
.source-filter-strip button,
.source-table-row,
.source-item,
.chain-picker button,
.fact-list span,
.card-action,
.inline-action,
.news-detail-link {
  border-radius: 0;
}

.story-list {
  gap: 0;
  border-top: 4px solid #111;
  background: #fff;
  padding: 0 4px 18px 0;
}

.story-list-card {
  grid-template-columns: 92px minmax(0, 1fr);
  gap: 4px 10px;
  border: 0;
  border-bottom: 1px solid var(--line);
  padding: 9px 0;
  background: #fff;
}

.story-list-card:hover,
.chain-picker button:hover {
  transform: none;
  border-color: var(--line);
  background: #f7f7f7;
}

.story-list-card.active {
  border-left: 4px solid var(--accent);
  padding-left: 8px;
  background: #f7f7f7;
}

.story-list-card :deep(.story-cover) {
  grid-row: 1 / span 5;
}

.story-list-card :deep(.story-cover--list) {
  width: 92px;
  min-height: 68px;
  border-radius: 0;
  aspect-ratio: 4 / 3;
}

.ground-page :deep(.story-cover--list .story-cover__image) {
  object-fit: cover;
}

.story-list-card h3 {
  font-size: 13px;
  line-height: 1.28;
  -webkit-line-clamp: 2;
}

.mini-spectrum,
.bias-bar {
  border-radius: 0;
}

.lead-story-card {
  border: 0;
  border-top: 4px solid #111;
  grid-template-columns: minmax(260px, 38%) minmax(0, 1fr);
}

.story-hero-cover {
  min-height: 360px;
}

.lead-story-copy {
  padding: 20px 22px;
}

.lead-story-card h2 {
  font-family: var(--serif);
  font-size: clamp(28px, 3vw, 44px);
  letter-spacing: 0;
  line-height: 1.08;
}

.lead-story-card__meta span {
  border: 0;
  padding: 0;
  background: transparent;
  color: var(--muted);
}

.lead-facts {
  border-top: 1px solid var(--line);
  padding-top: 12px;
}

.lead-facts div {
  border: 0;
  border-left: 1px solid var(--line);
  padding: 0 0 0 10px;
  background: transparent;
}

.lead-facts div:first-child {
  border-left: 0;
  padding-left: 0;
}

.story-comparison-board,
.coverage-board,
.source-intel-grid {
  gap: 12px;
}

.comparison-blindspot::before {
  display: none;
}

.comparison-blindspot strong {
  letter-spacing: 0;
}

.bias-breakdown button {
  background: #fff;
}

.signal-card {
  border-top: 1px solid var(--line);
}

.source-type-bars div,
.region-ledger span {
  background: #fff;
}

.story-tabs {
  border-radius: 0;
  padding: 0;
  background: #fff;
  box-shadow: none;
}

.story-tabs button {
  border-right: 1px solid var(--line);
}

.story-tabs button.active {
  background: #111;
  color: #fff;
}

.angle-card {
  border-top: 1px solid var(--line);
}

.card-action,
.inline-action,
.news-detail-link {
  background: #f1f1f1;
}

.story-cover,
.story-cover--list,
.story-cover--preview,
.story-cover--hero,
.story-cover__label,
.ground-page :deep(.story-cover__label) {
  border-radius: 0;
}

.ground-page :deep(.story-cover__label) {
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.68);
}

.ground-page :deep(.story-cover--list.has-image .story-cover__label) {
  display: none;
}

/* Dossier polish pass: keep the newsroom density, but make the selected story read like an analyst file. */
.ground-page {
  --ink: #14202a;
  --muted: #66737d;
  --line: #d8dfdd;
  --paper: rgba(255, 255, 255, 0.96);
  --paper-solid: #ffffff;
  --navy: #152c3e;
  --green: #0b725b;
  --accent: #b80000;
  --panel-radius: 10px;
  background:
    linear-gradient(180deg, rgba(246, 249, 248, 0.98), rgba(235, 241, 240, 0.96)),
    radial-gradient(circle at 12% 8%, rgba(184, 0, 0, 0.08), transparent 28%),
    radial-gradient(circle at 90% 4%, rgba(11, 114, 91, 0.1), transparent 30%),
    #eef3f2;
}

.ground-page::before {
  display: block;
  opacity: 0.42;
  background-image:
    linear-gradient(90deg, rgba(255, 255, 255, 0.72), rgba(255, 255, 255, 0.18)),
    repeating-linear-gradient(0deg, rgba(21, 44, 62, 0.04) 0 1px, transparent 1px 36px),
    repeating-linear-gradient(90deg, rgba(21, 44, 62, 0.028) 0 1px, transparent 1px 36px);
}

.desk-topbar {
  border: 1px solid rgba(21, 44, 62, 0.1);
  border-left: 5px solid var(--accent);
  border-radius: var(--panel-radius);
  padding: 14px 16px;
  background: rgba(255, 255, 255, 0.88);
  box-shadow: 0 14px 34px rgba(21, 44, 62, 0.08);
}

.brand-mark {
  border-radius: var(--panel-radius);
  background: linear-gradient(135deg, var(--accent), #152c3e);
}

.desk-topbar h1 {
  font-family: var(--sans);
  font-size: clamp(28px, 2.5vw, 42px);
  letter-spacing: -0.02em;
}

.desk-grid {
  grid-template-columns: minmax(300px, 350px) minmax(660px, 1fr) minmax(270px, 320px);
  gap: 16px;
  height: calc(100vh - 190px);
}

.search-card,
.story-list-card,
.lead-story-card,
.spectrum-card,
.signal-card,
.angle-card,
.timeline-panel,
.source-column,
.chain-summary,
.chain-node,
.edge-card,
.inspector-card,
.comparison-primary,
.comparison-differences,
.comparison-blindspot,
.source-intel-card,
.source-table-card,
.story-modal,
.modal-news-list article,
.dossier-panel {
  border: 1px solid rgba(21, 44, 62, 0.12);
  border-radius: var(--panel-radius);
  background: var(--paper);
  box-shadow: 0 12px 30px rgba(21, 44, 62, 0.07);
}

.search-card,
.inspector-card,
.spectrum-card,
.timeline-panel,
.source-column,
.source-intel-card,
.source-table-card,
.comparison-primary,
.comparison-differences,
.comparison-blindspot {
  border-top: 1px solid rgba(21, 44, 62, 0.12);
}

.story-list {
  border: 1px solid rgba(21, 44, 62, 0.12);
  border-radius: var(--panel-radius);
  background: rgba(255, 255, 255, 0.82);
  padding: 6px 4px 18px;
}

.story-list-card {
  border: 1px solid transparent;
  border-bottom-color: rgba(21, 44, 62, 0.1);
  padding: 9px;
}

.story-list-card.active {
  border-left: 4px solid var(--accent);
  border-color: rgba(184, 0, 0, 0.24);
  background: linear-gradient(90deg, rgba(184, 0, 0, 0.07), rgba(255, 255, 255, 0.96));
}

.lead-story-card {
  border: 1px solid rgba(21, 44, 62, 0.12);
  grid-template-columns: minmax(300px, 39%) minmax(0, 1fr);
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.98), rgba(245, 249, 248, 0.96)),
    #fff;
}

.story-hero-cover,
.story-cover--hero {
  min-height: 390px;
}

.lead-story-copy {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 26px 28px;
}

.lead-story-card h2 {
  font-family: var(--serif);
  font-size: clamp(30px, 3.2vw, 48px);
  line-height: 1.05;
}

.lead-facts {
  border-top: 1px solid rgba(21, 44, 62, 0.12);
}

.lead-facts div {
  border-left-color: rgba(21, 44, 62, 0.12);
}

.dossier-board {
  display: grid;
  grid-template-columns: minmax(0, 1.18fr) minmax(260px, 0.82fr);
  gap: 12px;
  align-items: stretch;
}

.dossier-panel {
  padding: 16px;
  min-width: 0;
}

.dossier-panel--context {
  grid-row: span 2;
  position: relative;
  overflow: hidden;
}

.dossier-panel--context::before {
  display: none;
}

.dossier-panel--context p {
  position: relative;
  margin: 0 0 15px;
  color: #334450;
  font-size: 14px;
  line-height: 1.62;
}

.context-timeline {
  position: relative;
  display: grid;
  gap: 10px;
  padding-left: 34px;
}

.context-timeline::before {
  content: "";
  position: absolute;
  left: 7px;
  top: 9px;
  bottom: 9px;
  width: 2px;
  border-radius: 999px;
  background: linear-gradient(180deg, var(--accent), rgba(11, 114, 91, 0.3));
}

.context-timeline div {
  position: relative;
  display: grid;
  gap: 3px;
  padding: 10px 12px;
  border: 1px solid rgba(21, 44, 62, 0.1);
  border-radius: var(--panel-radius);
  background: rgba(247, 249, 250, 0.86);
}

.context-timeline div::before {
  content: "";
  position: absolute;
  left: -33px;
  top: 14px;
  width: 12px;
  height: 12px;
  border: 3px solid #fff;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 1px rgba(184, 0, 0, 0.32), 0 4px 10px rgba(21, 44, 62, 0.12);
}

.context-timeline span,
.value-grid small,
.watch-list li::marker {
  color: var(--accent);
  font-size: 11px;
  font-weight: 900;
}

.context-timeline strong {
  color: var(--ink);
  font-size: 14px;
  line-height: 1.34;
}

.context-timeline small {
  color: var(--muted);
  font-size: 12px;
}

.value-score {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 12px;
  align-items: center;
  margin-bottom: 12px;
  padding: 14px;
  border-radius: var(--panel-radius);
  background: linear-gradient(135deg, rgba(21, 44, 62, 0.06), rgba(11, 114, 91, 0.08));
}

.value-score strong {
  font-family: var(--serif);
  font-size: 56px;
  line-height: 0.86;
  color: var(--navy);
}

.value-score span {
  color: #334450;
  font-size: 13px;
  font-weight: 800;
  line-height: 1.45;
}

.value-score.tone-high {
  background: linear-gradient(135deg, rgba(184, 0, 0, 0.09), rgba(11, 114, 91, 0.11));
}

.value-score.tone-watch {
  background: linear-gradient(135deg, rgba(167, 100, 29, 0.12), rgba(21, 44, 62, 0.05));
}

.value-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.value-grid div {
  display: grid;
  gap: 4px;
  padding: 10px;
  border: 1px solid rgba(21, 44, 62, 0.1);
  border-radius: var(--panel-radius);
  background: #fff;
}

.value-grid strong {
  color: var(--ink);
  font-size: 18px;
  line-height: 1;
}

.value-grid span {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.35;
}

.watch-list {
  display: grid;
  gap: 10px;
  margin: 0;
  padding-left: 20px;
  color: #334450;
  font-size: 13px;
  line-height: 1.52;
}

.watch-list li {
  padding-left: 4px;
}

.story-comparison-board {
  grid-template-columns: minmax(0, 1.2fr) minmax(250px, 0.8fr);
}

.comparison-primary,
.comparison-differences,
.comparison-blindspot {
  background: rgba(255, 255, 255, 0.94);
}

.comparison-blindspot {
  text-align: left;
  align-content: start;
  min-height: 0;
}

.comparison-blindspot strong {
  font-size: clamp(54px, 5vw, 84px);
  color: var(--accent);
}

.comparison-blindspot p {
  max-width: none;
  margin: 0;
  color: #4f5d66;
}

.story-tabs {
  border: 1px solid rgba(21, 44, 62, 0.12);
  border-radius: var(--panel-radius);
  overflow: hidden;
}

.story-tabs button {
  border-right: 1px solid rgba(21, 44, 62, 0.1);
}

.story-tabs button.active {
  background: var(--navy);
}

.angle-card {
  border-top: 1px solid rgba(21, 44, 62, 0.12);
}

.inspector-card {
  background: rgba(255, 255, 255, 0.9);
}

@media (max-width: 1280px) {
  .desk-grid {
    grid-template-columns: minmax(260px, 320px) minmax(0, 1fr);
    height: auto;
    min-height: 0;
  }

  .inspector-rail {
    position: static;
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    max-height: none;
    overflow: visible;
  }

  .source-spectrum-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .lead-story-card,
  .story-comparison-board,
  .dossier-board,
  .source-intel-grid {
    grid-template-columns: 1fr;
  }

  .dossier-panel--context {
    grid-row: auto;
  }
}

@media (max-width: 900px) {
  .ground-page {
    height: auto;
    min-height: 100vh;
    overflow-x: hidden;
    overflow-y: auto;
    padding: 72px 12px 18px;
  }

  .ground-subnav {
    width: 100%;
    justify-content: center;
    border-radius: 14px;
  }

  .ground-subnav span {
    display: none;
  }

  .ground-subnav a {
    flex: 1 1 0;
    min-width: 0;
    padding: 0 10px;
    font-size: 14px;
  }

  .desk-topbar,
  .desk-grid,
  .coverage-board,
  .coverage-grid,
  .story-comparison-board,
  .dossier-board,
  .inspector-rail,
  .source-spectrum-grid {
    grid-template-columns: 1fr;
  }

  .source-table {
    grid-template-columns: 1fr;
  }

  .desk-topbar {
    display: grid;
    overflow: hidden;
  }

  .topbar-metrics {
    justify-content: flex-start;
    flex-wrap: nowrap;
    overflow-x: auto;
    padding-bottom: 4px;
  }

  .desk-grid {
    width: 100%;
    max-width: 100%;
    overflow: hidden;
  }

  .library-rail,
  .inspector-rail {
    position: static;
    width: 100%;
    max-width: 100%;
    height: auto;
    max-height: none;
    overflow: visible;
  }

  .story-list,
  .story-stage {
    width: 100%;
    max-width: 100%;
    height: auto;
    max-height: none;
    overflow: visible;
    padding-right: 0;
  }

  .story-list-card {
    width: 100%;
    max-width: calc(100vw - 24px);
    min-width: 0;
    box-sizing: border-box;
    overflow: hidden;
    grid-template-columns: 1fr;
    justify-content: start;
    white-space: normal;
  }

  .story-list-card :deep(.story-cover) {
    grid-row: auto;
  }

  .story-list-card__top,
  .story-list-card h3,
  .story-list-card .mini-spectrum,
  .story-list-card small {
    grid-column: 1;
  }

  .story-list-card h3,
  .story-list-card small {
    width: 100%;
    min-width: 0;
    max-width: min(100%, calc(100vw - 48px));
    white-space: normal;
    overflow-wrap: break-word;
    word-break: normal;
  }

  .story-list-card h3 {
    display: block;
    max-width: calc(100vw - 78px);
    max-height: none;
    font-size: 13px;
    line-height: 1.32;
    overflow: hidden;
    text-overflow: clip;
    -webkit-line-clamp: unset;
    -webkit-box-orient: unset;
  }

  .story-list-card :deep(.story-cover--list) {
    width: 100%;
    min-height: 108px;
    aspect-ratio: 16 / 7;
  }

  .lead-facts,
  .bias-breakdown {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .spectrum-card {
    min-height: 0;
  }

  .lead-story-card {
    padding: 22px;
  }

  .dossier-preview {
    grid-template-columns: 1fr;
  }

  .dossier-preview__art,
  .lead-atlas,
  .story-hero-cover,
  .story-cover--hero {
    min-height: 260px;
  }

  .lead-atlas img {
    min-height: 210px;
  }

  .lead-story-card {
    grid-template-columns: 1fr;
  }

  .lead-story-copy {
    padding: 20px;
  }

  .story-tabs {
    position: static;
  }

  .angle-card,
  .source-table-row {
    grid-template-columns: 1fr;
  }

  .angle-card > small,
  .angle-card ul,
  .angle-card .card-action {
    grid-column: auto;
    grid-row: auto;
  }

  .angle-card .card-action {
    justify-self: start;
  }
}

/* QA-02/QA-03: source-verifiable touch targets and compact mobile filters. */
.section-disclaimer {
  margin: 0 0 10px;
  border-left: 3px solid var(--red);
  padding-left: 10px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.55;
}

.mobile-desk-filter-toggle {
  display: none;
}

.ground-page button,
.ground-page input,
.ground-page select {
  min-height: 44px;
  touch-action: manipulation;
}

.ground-page .ground-subnav a,
.ground-page .angle-card a,
.ground-page .news-detail-link,
.ground-page .modal-news-list a,
.ground-page .source-table-row {
  box-sizing: border-box;
  min-height: 44px;
}

.ground-page .angle-card a,
.ground-page .modal-news-list a {
  display: inline-flex;
  align-items: center;
}

.ground-page .story-modal__close {
  width: 44px;
  min-width: 44px;
  height: 44px;
}

@media (max-width: 900px) {
  .search-card {
    position: sticky;
    top: 68px;
    z-index: 12;
    max-height: calc(100vh - 82px);
    overflow-y: auto;
    overscroll-behavior: contain;
  }

  .mobile-desk-filter-toggle {
    box-sizing: border-box;
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    border: 1px solid var(--line);
    padding: 8px 12px;
    background: #fff;
    color: var(--ink);
    font: inherit;
    font-weight: 900;
    cursor: pointer;
  }

  .desk-filter-controls {
    display: none;
  }

  .desk-filter-controls.is-mobile-expanded {
    display: block;
  }

  .desk-filter-group {
    grid-template-columns: 52px repeat(2, minmax(0, 1fr));
  }
}
</style>
