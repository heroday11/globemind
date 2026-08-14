<template>
  <div class="search-page">
    <!-- 背景光斑与网格 -->
    <div class="bg-orbs">
      <div class="bg-orb bg-orb--1" />
      <div class="bg-orb bg-orb--2" />
      <div class="bg-orb bg-orb--3" />
    </div>
    <div class="bg-grid" />

    <!-- 左侧固定边栏 -->
    <div
      class="left-sidebar"
      :class="{ 'is-mobile-expanded': workspacePanelExpanded }"
      data-tour="search-workfolders"
    >
      <div class="sidebar-head">
        <div class="sidebar-title">新闻事件检索台</div>
        <div class="sidebar-subtitle">news 库 · L1/L2/L3 事件体系</div>
        <div class="sidebar-badges">
          <span>新库检索</span>
          <span>事件热度</span>
          <span>2026.06</span>
        </div>
      </div>
      <button
        type="button"
        class="mobile-sidebar-toggle"
        aria-controls="search-workspace-panel"
        :aria-expanded="workspacePanelExpanded"
        @click="workspacePanelExpanded = !workspacePanelExpanded"
      >
        <span>工作文件夹与收藏</span>
        <strong>{{ workspacePanelExpanded ? '收起' : '展开' }}</strong>
      </button>
      <div id="search-workspace-panel" class="mobile-sidebar-panel">
      <div class="folder-toolbar">
        <div>
          <div class="folder-toolbar-title">工作文件夹</div>
          <p>星标新闻会保存到当前文件夹</p>
        </div>
        <button type="button" class="folder-add-btn" @click="addNewTheme" title="新增文件夹">+</button>
      </div>

      <div class="folder-stack" role="list" aria-label="工作文件夹">
        <article
          v-for="folder in folderCards"
          :key="folder.name"
          class="folder-card"
          :class="{ active: folder.name === themeName }"
          role="listitem"
        >
          <span class="folder-tab" aria-hidden="true" />
          <div class="folder-main">
            <span class="folder-mark" aria-hidden="true" />
            <div class="folder-copy">
              <div v-if="isEditingTheme && folder.name === themeName" class="folder-edit-row">
                <label class="search-sr-only" for="search-folder-name">文件夹名称</label>
                <input
                  id="search-folder-name"
                  name="folderName"
                  class="folder-edit-input"
                  v-model="themeNameDraft"
                  @click.stop
                  @keyup.enter="saveThemeName"
                  @blur="saveThemeName"
                />
              </div>
              <button
                v-else
                type="button"
                class="folder-name"
                @click.stop="folder.name === themeName ? startEditThemeName() : switchTheme(folder.name)"
                :title="folder.name === themeName ? '重命名当前文件夹' : '切换文件夹'"
              >
                {{ folder.displayName }}
              </button>
              <div class="folder-meta">
                <span>{{ folder.count }} 条收藏</span>
                <span>{{ folder.latestLabel }}</span>
              </div>
            </div>
            <button
              v-if="themeList.length > 1"
              type="button"
              class="folder-del"
              @click.stop="deleteTheme(folder.name)"
              title="删除该文件夹及其收藏"
            >
              ×
            </button>
          </div>
          <div class="folder-preview" :class="{ empty: !folder.latestTitle }">
            {{ folder.latestTitle || '空文件夹，等待星标新闻' }}
          </div>
        </article>
      </div>

      <div class="sidebar-block sidebar-block--favorites">
        <div class="sidebar-block-title">当前文件夹</div>
        <div v-if="favoriteCards.length === 0" class="favorite-empty">暂无收藏，点击新闻列表右侧星标即可加入。</div>
        <div v-else class="favorite-cards">
          <div
            v-for="card in favoriteCards.slice(0, 8)"
            :key="`fav-${card.id}`"
            class="favorite-card-shell"
          >
            <router-link class="favorite-card" :to="`/data-service/news/${card.id}`">
              <div class="favorite-card-title">{{ card.title || '无标题' }}</div>
              <div class="favorite-card-meta">
                <span>{{ card.source || '未知来源' }}</span>
                <span>新闻发布日期：{{ formatDateTime(publishedTimeValue(card)) }}</span>
              </div>
            </router-link>
            <button
              type="button"
              class="favorite-card-close"
              @click="removeFavoriteCard(card.id)"
              :aria-label="`从当前文件夹移除：${card.title || '无标题'}`"
              title="移除收藏"
            >
              ×
            </button>
          </div>
        </div>
      </div>
      </div>
    </div>

    <!-- 右侧可滚动主内容区 -->
    <div class="main-content">
      <!-- 搜索条件区 -->
      <div class="search-condition" data-tour="search-form">
        <h1>新闻与事件检索</h1>
        <section class="situation-presets" aria-label="态势检索模板">
          <div class="preset-copy">
            <div class="preset-copy-kicker">
              <span>态势模板</span>
              <em>最长 8 秒</em>
            </div>
            <strong>按新闻、事件、走势与大事选择最短检索路径</strong>
          </div>
          <div class="preset-list">
            <button
              v-for="preset in situationPresets"
              :key="preset.label"
              type="button"
              class="preset-btn"
              :title="preset.hint"
              @click="applySituationPreset(preset)"
            >
              <b>{{ preset.label }}</b>
              <span><i>{{ preset.strategyLabel }}</i>{{ preset.typeLabel }} · {{ preset.timeLabel }}</span>
              <small>实际查询：{{ preset.topic }} · {{ searchModeDisclosure(preset.mode).label }}</small>
            </button>
          </div>
        </section>
        <div class="condition-group">
          <div class="condition-item">
            <label class="tag must-include" for="search-topic">● 主关键词</label>
            <input
              id="search-topic"
              name="topic"
              type="text"
              placeholder="输入主题，如：中国、芯片、南海"
              v-model="searchParams.topic"
              @keyup.enter="performSearch"
            />
          </div>
          <div class="condition-item">
            <label class="tag must-include" for="search-must-include">● 必须包含</label>
            <input
              id="search-must-include"
              name="mustInclude"
              type="text"
              placeholder="进一步限定关键词"
              v-model="searchParams.mustInclude"
              @keyup.enter="performSearch"
            />
          </div>
          <div class="condition-item">
            <label class="tag any-include" for="search-any-include">任意包含</label>
            <input
              id="search-any-include"
              name="anyInclude"
              type="text"
              placeholder="按Enter键对输入框进行赋值"
              v-model="searchParams.anyInclude"
              @keyup.enter="performSearch"
            />
          </div>
          <div class="condition-item">
            <label class="tag need-exclude" for="search-need-exclude">● 需要排除</label>
            <input
              id="search-need-exclude"
              name="needExclude"
              type="text"
              placeholder="按Enter键对输入框进行赋值"
              v-model="searchParams.needExclude"
              @keyup.enter="performSearch"
            />
          </div>
        </div>
        <div class="action-btns">
          <button type="button" class="reset-btn" @click="resetSearch">重置</button>
          <button type="button" class="search-btn" @click="performSearch" :disabled="isLoading">
            {{ isLoading ? '搜索中...' : '搜索' }}
          </button>
        </div>
      </div>

      <!-- 筛选条件区 -->
      <div class="filter-section" data-tour="search-filters">
        <button
          type="button"
          class="mobile-filter-toggle"
          aria-controls="search-filter-controls"
          :aria-expanded="filtersExpanded"
          @click="filtersExpanded = !filtersExpanded"
        >
          <span>结果筛选与工具</span>
          <strong>{{ filtersExpanded ? '收起' : '展开' }}</strong>
        </button>
        <div
          id="search-filter-controls"
          class="filter-controls"
          :class="{ 'is-mobile-expanded': filtersExpanded }"
        >
        <div class="filter-item">
          <span>{{ timeFilterLabel }}</span>
          <button
            v-for="timeRange in timeRanges"
            :key="timeRange.value"
            type="button"
            :class="{ active: searchParams.publishTime === timeRange.value }"
            :aria-pressed="searchParams.publishTime === timeRange.value"
            aria-describedby="search-time-field-hint"
            @click="setPublishTime(timeRange.value)"
          >
            {{ timeRange.label }}
          </button>
          <label class="search-sr-only" for="search-start-time">{{ timeFilterLabel }}起始时间</label>
          <input
            id="search-start-time"
            name="startTime"
            type="datetime-local"
            aria-describedby="search-time-field-hint"
            v-model="searchParams.startTime"
            @change="onTimeRangeChange"
          />
          <span>至</span>
          <label class="search-sr-only" for="search-end-time">{{ timeFilterLabel }}结束时间</label>
          <input
            id="search-end-time"
            name="endTime"
            type="datetime-local"
            aria-describedby="search-time-field-hint"
            v-model="searchParams.endTime"
            @change="onTimeRangeChange"
          />
          <span id="search-time-field-hint" class="time-field-hint">{{ timeFilterHint }}</span>
        </div>
        <div class="filter-item filter-item--row">
          <span>命中位置</span>
          <button
            v-for="location in hitLocations"
            :key="location.value"
            type="button"
            :class="{ active: searchParams.hitLocation === location.value }"
            :aria-pressed="searchParams.hitLocation === location.value"
            :disabled="searchParams.searchType !== 'news'"
            @click="handleHitLocationChange(location.value)"
          >
            {{ location.label }}
          </button>
          <span class="filter-sep">检索模式</span>
          <button
            v-for="m in searchModes"
            :key="m.value"
            type="button"
            :class="{ active: searchParams.mode === m.value }"
            :aria-pressed="searchParams.mode === m.value"
            aria-describedby="search-mode-hint"
            @click="setSearchMode(m.value)"
          >
            {{ m.label }}
          </button>
          <span id="search-mode-hint" class="search-mode-hint">
            {{ searchModeDisclosure(searchParams.mode).description }} 每个 OR 分支必须有正向词；邻近、字段权重、通配符和正则 DSL 会明确拒绝。
          </span>
          <span class="filter-sep">搜索类型</span>
          <button
            v-for="t in searchTypes"
            :key="t.value"
            type="button"
            :class="{ active: searchParams.searchType === t.value }"
            :aria-pressed="searchParams.searchType === t.value"
            @click="setSearchType(t.value)"
          >
            {{ t.label }}
          </button>
          <span class="filter-sep">排序</span>
          <button
            v-if="searchParams.searchType === 'news'"
            type="button"
            :class="{ active: searchParams.sortBy === 'similarity' || !searchParams.sortBy }"
            :aria-pressed="searchParams.sortBy === 'similarity' || !searchParams.sortBy"
            @click="setSortBy('similarity')"
          >标题命中优先</button>
          <button
            v-if="searchParams.searchType === 'news'"
            type="button"
            :class="{ active: searchParams.sortBy === 'published_at' }"
            :aria-pressed="searchParams.sortBy === 'published_at'"
            @click="setSortBy('published_at')"
          >新闻发布日期</button>
          <span v-else class="search-mode-hint">{{ searchSortLabel }}</span>
        </div>
        <div class="filter-item filter-item--compact" data-tour="search-result-tools">
          <span>数据源</span>
          <label class="search-sr-only" for="search-data-source">数据源</label>
          <input
            id="search-data-source"
            name="dataSource"
            type="text"
            list="dataSourceList"
            v-model="searchParams.dataSource"
            :disabled="searchParams.searchType !== 'news'"
            :title="searchParams.searchType === 'news' ? '按新闻来源筛选' : '层级搜索暂不支持来源筛选'"
            @change="performSearch"
            placeholder="输入或选择网站"
            style="min-width:140px;padding:6px 12px;border:1px solid rgba(148,163,184,0.2);border-radius:20px;font-size:13px;background:rgba(255,255,255,0.6);color:#334155;"
          />
          <datalist id="dataSourceList">
            <option v-for="src in dataSources" :key="src" :value="src"></option>
          </datalist>
          <span>语言</span>
          <label class="search-sr-only" for="search-language">语言</label>
          <select
            id="search-language"
            name="language"
            v-model="searchParams.language"
            :disabled="searchParams.searchType !== 'news'"
            :title="searchParams.searchType === 'news' ? '按新闻语言筛选' : '层级搜索暂不支持语言筛选'"
            @change="performSearch"
          >
            <option value="">全部</option>
            <option v-for="lang in languageOptions" :key="lang.id" :value="String(lang.id)">
              {{ lang.name }}
            </option>
          </select>
          <span class="analysis-surface">{{ activeSearchSurfaceLabel }}</span>
          <span class="data-total">{{ isSearchMode ? '搜索结果' : '数据总量' }}：{{ totalItems }} 条</span>
          <button type="button" class="assistant-btn" @click="sendSearchToAssistant">数据助手研判</button>
          <button type="button" class="export-btn" @click="exportData">导出</button>
          <p class="critical-action-status" role="status" aria-live="polite" aria-atomic="true">{{ criticalActionStatus }}</p>
        </div>
        </div>
      </div>

      <details v-if="queryExplain" class="query-explain" open>
        <summary>本次查询解释</summary>
        <div class="query-explain-grid">
          <div>
            <strong>实际解析</strong>
            <code>{{ queryExplain.normalized_terms.join(' · ') || '无主关键词' }}</code>
          </div>
          <div>
            <strong>实际检索词</strong>
            <code>{{ queryExplain.expanded_terms.join(' · ') || '无主关键词扩展' }}</code>
          </div>
          <div>
            <strong>Boolean AST（{{ queryExplain.query_language }}）</strong>
            <pre class="query-ast-value">{{ queryAstText(queryExplain.query_ast) }}</pre>
          </div>
          <div>
            <strong>实际执行表达式</strong>
            <code>{{ queryExplain.execution_expression || '无主关键词表达式' }}</code>
          </div>
          <div>
            <strong>实际模式</strong>
            <span>{{ queryExplain.mode_semantics }}</span>
          </div>
          <div>
            <strong>匹配字段</strong>
            <span>{{ queryExplain.effective_search_fields.join('、') }}。{{ queryExplain.hit_location_note }}</span>
          </div>
          <div>
            <strong>时间字段</strong>
            <span>{{ queryExplain.time.label }} · {{ queryExplain.time.predicate }}</span>
          </div>
          <div v-if="queryExplain.applied_filters.length">
            <strong>已应用筛选</strong>
            <span>{{ queryExplain.applied_filters.map(queryFilterText).join('；') }}</span>
          </div>
          <div>
            <strong>解析安全边界</strong>
            <span>{{ queryLimitText(queryExplain.limits) }}</span>
          </div>
        </div>
        <div v-if="queryExplain.entity_expansions.length" class="query-entity-list">
          <article
            v-for="entity in queryExplain.entity_expansions"
            :key="`${entity.query_field}:${entity.entity_id}`"
          >
            <strong>{{ entity.canonical_names['zh-Hans'] || entity.canonical_names.en }}</strong>
            <code>{{ entity.entity_id }}</code>
            <span>条件：{{ queryFieldLabel(entity.query_field) }}</span>
            <span>命中别名：{{ entity.matched_alias }}</span>
            <span v-if="entity.matched_alias_status !== 'active'">命中别名状态：需结合上下文复核</span>
            <span>实际展开：{{ entity.expanded_aliases.join('、') }}</span>
            <span>复核状态：{{ entity.review_status === 'approved' ? '已记录人工复核' : '待人工复核' }}</span>
            <span>实体有效期：{{ entity.valid_from || '未知' }} — {{ entity.valid_to || '未知' }}</span>
            <span v-if="entity.reviewed_at">复核记录：{{ entity.reviewed_at }} · {{ entity.reviewed_by }}</span>
            <span v-if="entity.review_note">{{ entity.review_note }}</span>
          </article>
        </div>
        <ol class="query-stage-list" aria-label="查询阶段">
          <li v-for="stage in queryExplain.stages" :key="stage.stage">
            <strong>{{ queryStageLabel(stage.stage) }}</strong>
            <span v-if="stage.matched_count != null">
              {{ stage.matched_count }} · {{ queryCountSemanticsLabel(stage.count_semantics) }}
            </span>
            <span v-else>{{ stage.status === 'not_run' ? '未执行' : '未提供计数' }}</span>
            <small>{{ stage.detail }}</small>
          </li>
        </ol>
        <div v-if="queryExplain.relaxation_suggestions.length" class="query-suggestions">
          <strong>{{ totalItems === 0 ? '零结果放宽建议' : '结果拆分建议' }}</strong>
          <ul>
            <li v-for="suggestion in queryExplain.relaxation_suggestions" :key="suggestion">{{ suggestion }}</li>
          </ul>
        </div>
        <p class="query-explain-footnote">
          别名目录 {{ queryExplain.alias_catalog_version }}；自动放宽：{{ queryExplain.automatic_relaxation ? '已执行' : '未执行' }}。
          实体种子不代表完整实体识别，也不声称 95% 准确率；采集时间和更新时间当前不可筛选。
        </p>
      </details>

      <details v-if="queryReceipt" class="query-receipt">
        <summary>本次查询执行收据</summary>
        <div class="query-receipt-grid">
          <div>
            <strong>收据标识 / 方法</strong>
            <code>{{ queryReceipt.receipt_id }}</code>
            <span>{{ queryReceipt.schema_version }} · {{ queryReceipt.method_version }}</span>
          </div>
          <div>
            <strong>规范化查询契约</strong>
            <code>{{ queryReceipt.normalized_contract_sha256 }}</code>
            <span>目录 {{ queryReceipt.entity_catalog_version }} · {{ queryReceipt.entity_catalog_review_status === 'approved' ? '已复核' : '待复核' }}</span>
          </div>
          <div>
            <strong>有序返回 ID</strong>
            <code>{{ queryReceipt.ordered_returned_ids_sha256 }}</code>
            <span>{{ queryReceipt.result_id_namespace }} · 第 {{ queryReceipt.page }} 页 · {{ queryReceipt.ordered_returned_ids.length }}/{{ queryReceipt.total }} 条</span>
          </div>
          <div>
            <strong>时间与实际覆盖</strong>
            <span>{{ queryReceipt.time_field.requested }} → {{ queryReceipt.time_field.applied }}</span>
            <span>
              {{ queryReceipt.result_coverage.status }} ·
              {{ queryReceipt.result_coverage.coverage_start || '起点未知' }} —
              {{ queryReceipt.result_coverage.coverage_end || '终点未知' }} ·
              cutoff {{ queryReceipt.result_coverage.cutoff || '未知' }}
            </span>
          </div>
          <div>
            <strong>收据完整性</strong>
            <code>{{ queryReceipt.receipt_sha256 }}</code>
            <span>已应用筛选 {{ queryReceipt.applied_filters.length }} 项；正文与排序输入未写入收据。</span>
          </div>
        </div>
        <p class="query-receipt-note">
          这是执行收据，不是冻结数据快照。它证明规范化查询契约与本页有序结果 ID；不冻结正文、底层语料或未来排序。
        </p>
      </details>

      <!-- 搜索结果区 -->
      <div class="search-results">
        <!-- 加载状态 -->
        <div v-if="isLoading" class="loading-indicator">
          <div class="spinner"></div>
          <span>正在检索，超过 8 秒将自动停止</span>
        </div>

        <!-- 错误状态 -->
        <div v-else-if="searchError" class="error-message">
          <span>{{ searchError }}</span>
          <button type="button" class="retry-btn" @click="retryLastDataRequest">重试</button>
        </div>

        <!-- cluster 分层结果：story (L2) → cluster (L1) → news -->
        <div
          v-else-if="isSearchMode && searchParams.mode === 'cluster' && clusterTree.length > 0"
          class="cluster-tree"
        >
          <div class="cluster-tree-header">
            <div class="cluster-tree-heading">
              <span class="cluster-tree-icon" aria-hidden="true" />
              <div class="cluster-tree-titles">
                <strong class="cluster-tree-title">按簇检索结果</strong>
                <span class="cluster-tree-sub">L2走势链 → L1事件 → 新闻 · 与当前检索条件一致</span>
              </div>
            </div>
            <div class="cluster-tree-stats" role="group" aria-label="簇统计">
              <span class="cluster-stat-pill cluster-stat-pill--macro">
                <span class="cluster-stat-k">故事线</span>
                <span class="cluster-stat-v">{{ clusterTreeSummary.stories }}</span>
              </span>
              <span class="cluster-stat-pill cluster-stat-pill--micro">
                <span class="cluster-stat-k">事件簇</span>
                <span class="cluster-stat-v">{{ clusterTreeSummary.clusters }}</span>
              </span>
              <span class="cluster-stat-pill cluster-stat-pill--news">
                <span class="cluster-stat-k">新闻</span>
                <span class="cluster-stat-v">{{ clusterTreeSummary.news }}</span>
              </span>
            </div>
          </div>
          <!-- L2 故事线 → L1 事件簇 → 新闻 -->
          <details
            v-for="story in clusterTree"
            :key="`story-${story.story_id}`"
            class="cluster-macro"
            open
          >
            <summary class="cluster-summary cluster-summary--macro">
              <span class="cluster-level-tag cluster-level-tag--macro">L2走势</span>
              <span class="cluster-title" :title="story.title">{{ story.title }}</span>
              <span class="cluster-meta-pills">
                <span class="cluster-mini-pill">事件簇 {{ story.cluster_count }}</span>
                <span class="cluster-mini-pill">新闻 {{ story.news_count }}</span>
              </span>
              <button
                type="button"
                class="cluster-data-btn"
                @click.stop.prevent="openClusterPanelStory(story)"
              >
                展示数据
              </button>
            </summary>
            <div class="cluster-micro-list">
              <!-- L1 事件簇 -->
              <details
                v-for="cluster in story.clusters"
                :key="`cluster-${cluster.cluster_id}`"
                class="cluster-micro"
              >
                <summary class="cluster-summary cluster-summary--micro">
                  <span class="cluster-level-tag cluster-level-tag--micro">事件簇</span>
                  <span class="cluster-title" :title="cluster.event_type || cluster.title">
                    {{ cluster.event_type || cluster.title || cluster.cluster_id }}
                  </span>
                  <span class="cluster-meta-pills">
                    <span v-if="cluster.initiator" class="cluster-mini-pill">发起方 {{ cluster.initiator }}</span>
                    <span v-if="cluster.target" class="cluster-mini-pill">目标 {{ cluster.target }}</span>
                    <span v-if="cluster.cluster_quality" class="cluster-mini-pill">{{ cluster.cluster_quality }}</span>
                    <span class="cluster-mini-pill cluster-mini-pill--accent">新闻 {{ cluster.news_count }}</span>
                  </span>
                  <button
                    type="button"
                    class="cluster-data-btn cluster-data-btn--micro"
                    @click.stop.prevent="openClusterPanelCluster(cluster)"
                  >
                    展示数据
                  </button>
                </summary>
                <ul class="cluster-news-list">
                  <li v-for="n in cluster.news" :key="`news-${n.id}`" class="cluster-news-row">
                    <span class="cluster-news-type">新闻</span>
                    <router-link :to="`/data-service/news/${n.id}`" class="cluster-news-link">
                      {{ n.title }}
                    </router-link>
                    <span class="cluster-news-time">新闻发布日期：{{ formatDateTime(publishedTimeValue(n)) }}</span>
                  </li>
                </ul>
              </details>
            </div>
          </details>
        </div>

        <!-- L1 事件簇 -->
        <div
          v-else-if="isSearchMode && eventCorefClusters.length > 0"
          class="cluster-tree"
        >
          <div class="cluster-tree-header">
            <div class="cluster-tree-heading">
              <span class="cluster-tree-icon" aria-hidden="true" />
              <div class="cluster-tree-titles">
                <strong class="cluster-tree-title">L1 事件检索结果</strong>
                <span class="cluster-tree-sub">事件聚类 → 代表性新闻 · 与当前检索条件一致</span>
              </div>
            </div>
            <div class="cluster-tree-stats" role="group" aria-label="簇统计">
              <span class="cluster-stat-pill cluster-stat-pill--micro">
                <span class="cluster-stat-k">事件簇</span>
                <span class="cluster-stat-v">{{ eventCorefSummary.clusters }}</span>
              </span>
              <span class="cluster-stat-pill cluster-stat-pill--news">
                <span class="cluster-stat-k">新闻</span>
                <span class="cluster-stat-v">{{ eventCorefSummary.news }}</span>
              </span>
            </div>
          </div>
          <details
            v-for="ec in eventCorefClusters"
            :key="`ec-${ec.cluster_id}`"
            class="cluster-micro"
            open
          >
            <summary class="cluster-summary cluster-summary--micro">
              <span class="cluster-level-tag cluster-level-tag--micro">L1事件</span>
              <span class="cluster-title" :title="ec.event_type || ec.cluster_id">
                {{ ec.event_type || ec.cluster_id }}
              </span>
              <span class="cluster-meta-pills">
                <span v-if="ec.initiator" class="cluster-mini-pill">发起方 {{ ec.initiator }}</span>
                <span v-if="ec.target" class="cluster-mini-pill">目标 {{ ec.target }}</span>
                <span v-if="ec.dominant_trigger" class="cluster-mini-pill">触发 {{ ec.dominant_trigger }}</span>
                <span v-if="ec.cluster_quality" class="cluster-mini-pill">{{ ec.cluster_quality }}</span>
                <span class="cluster-mini-pill cluster-mini-pill--accent">新闻 {{ ec.article_count }}</span>
              </span>
              <button
                type="button"
                class="cluster-data-btn cluster-data-btn--micro"
                @click.stop.prevent="openClusterPanelL1Event(ec)"
              >
                展示数据
              </button>
            </summary>
            <ul class="cluster-news-list">
              <li v-for="n in ec.articles" :key="`ecn-${n.id}`" class="cluster-news-row">
                <span class="cluster-news-type">新闻</span>
                <router-link :to="`/data-service/news/${n.id}`" class="cluster-news-link">
                  {{ n.title }}
                </router-link>
                <span class="cluster-news-time">新闻发布日期：{{ formatDateTime(publishedTimeValue(n)) }}</span>
              </li>
            </ul>
          </details>
        </div>

        <!-- L1 事件直接搜索结果 -->
        <div
          v-else-if="isSearchMode && searchParams.searchType === 'l1' && microStoryItems.length > 0"
          class="l1-results"
        >
          <div class="l1-header">
            <strong class="l1-header-title">L1 小事件检索结果</strong>
            <span class="l1-header-count">共 {{ totalItems }} 条</span>
          </div>
          <div v-for="item in microStoryItems" :key="'l1-'+item.id" class="l1-card">
            <button
              type="button"
              class="l1-card-header"
              :aria-expanded="!!expandedL1[item.id]"
              @click="toggleL1Expand(item)"
            >
              <span class="l1-expand-icon">{{ expandedL1[item.id] ? '▼' : '▶' }}</span>
              <div class="l1-card-title">{{ item.title }}</div>
            </button>
            <div class="l1-card-meta">
              <span v-if="item.event_type" class="l1-tag">{{ item.event_type }}</span>
              <span v-if="item.initiator" class="l1-actor">发起方: {{ item.initiator }}</span>
              <span v-if="item.target" class="l1-actor">目标: {{ item.target }}</span>
            </div>
            <div class="l1-card-stats">
              <span>文章数: {{ item.article_count }}</span>
              <span>簇数: {{ item.cluster_count }}</span>
            </div>
            <div v-if="l1LoadingChildren[item.id]" class="l1-children-loading">加载中...</div>
            <div v-else-if="expandedL1[item.id] && l1ClusterNews[item.id] && l1ClusterNews[item.id].length > 0" class="l1-news-list">
              <div
                v-for="news in l1ClusterNews[item.id]"
                :key="'l1n-'+news.id"
                class="l1-news-item"
              >
                <router-link :to="`/data-service/news/${news.id}`" class="l1-news-link">
                  {{ news.title }}
                </router-link>
                <span class="l1-news-time">新闻发布日期：{{ formatDateTime(publishedTimeValue(news)) }}</span>
              </div>
            </div>
            <div v-else-if="expandedL1[item.id]" class="l1-no-news">暂无相关新闻</div>
          </div>
        </div>

        <!-- L2 走势 / L3 大事件直接搜索结果 -->
        <div
          v-else-if="isSearchMode && ['l2', 'l3'].includes(searchParams.searchType) && macroEventItems.length > 0"
          class="cluster-tree l2-results"
        >
          <div class="cluster-tree-header">
            <div class="cluster-tree-heading">
              <span class="cluster-tree-icon" aria-hidden="true" />
              <div class="cluster-tree-titles">
                <strong class="cluster-tree-title">{{ hierarchyResultTitle }}</strong>
                <span class="cluster-tree-sub">
                  {{ searchParams.searchType === 'l3' ? 'L3大事件 → L2走势 → L1事件/新闻' : 'L2走势 → L1事件 → 新闻' }}
                </span>
              </div>
            </div>
            <div class="cluster-tree-stats" role="group" aria-label="层级统计">
              <span class="cluster-stat-pill cluster-stat-pill--macro">
                <span class="cluster-stat-k">{{ searchParams.searchType === 'l3' ? '大事件' : '走势' }}</span>
                <span class="cluster-stat-v">{{ macroEventItems.length }}</span>
              </span>
              <span class="cluster-stat-pill cluster-stat-pill--news">
                <span class="cluster-stat-k">总量</span>
                <span class="cluster-stat-v">{{ totalItems }}</span>
              </span>
            </div>
          </div>
          <div
            v-for="item in macroEventItems"
            :key="`${item.level || searchParams.searchType}-${item.id}`"
            class="l2-card hierarchy-card"
            :class="{ expanded: expandedL2[item.id] }"
          >
            <div class="l2-card-header">
              <button
                type="button"
                class="hierarchy-expand-btn"
                :aria-expanded="!!expandedL2[item.id]"
                @click.stop="toggleL2Expand(item)"
              >
                {{ expandedL2[item.id] ? '收起' : '展开' }}
              </button>
              <div class="l2-card-title">{{ item.title }}</div>
            </div>
            <p v-if="item.summary" class="l2-card-summary">{{ item.summary }}</p>
            <div class="l2-card-meta">
              <span v-if="item.initiator" class="l2-actor">发起方: {{ item.initiator }}</span>
              <span v-if="item.target" class="l2-actor">目标: {{ item.target }}</span>
              <span v-if="item.start_date || item.end_date" class="l2-actor">
                事件时间：{{ item.start_date || '—' }} 至 {{ item.end_date || '—' }}
              </span>
            </div>
            <div class="l2-card-stats">
              <span>文章数: {{ item.article_count }}</span>
              <span>{{ item.level === 'l3' || searchParams.searchType === 'l3' ? 'L2链数' : 'L1片段数' }}: {{ item.story_count }}</span>
              <span>链质量分: {{ graphMetricPresentation('story_graph.quality_score', { value: item.quality_score }).valueLabel }}</span>
              <details class="hierarchy-metric-disclosure">
                <summary>展开链质量公式、输入和证据</summary>
                <template v-for="explanation in [hierarchyQualityExplanation(item)]" :key="`${item.id}-${explanation.metric_id}`">
                  <p>方法：{{ explanation.method_version || '未建立' }}</p>
                  <p>公式：{{ explanation.formula }}</p>
                  <p>证据定位：{{ explanation.evidence.locator || '不可用' }}；{{ explanation.reason_code }}</p>
                  <ul>
                    <li v-for="input in explanation.inputs" :key="input.field">
                      <code>{{ input.field }}</code>：{{ input.state === 'provided_unverified' ? `已提供但未核验（${input.value}）` : '不可用' }}
                    </li>
                  </ul>
                </template>
              </details>
            </div>
            <div v-if="l2LoadingChildren[item.id]" class="l2-children-loading">加载中...</div>
            <div v-if="expandedL2[item.id] && l2Children[item.id]" class="l2-children">
              <div
                v-for="child in l2Children[item.id]"
                :key="'l2c-'+child.id"
                class="l2-child-card"
                :class="{ expanded: expandedL2[child.id], 'hierarchy-news-card': isHierarchyNews(child) }"
                @click.stop
              >
                <div v-if="isHierarchyNews(child)" class="result-item result-item--hierarchy-news">
                  <div class="result-content">
                    <h4 class="title">{{ child.title }}</h4>
                    <p class="desc">{{ child.displayDesc }}</p>
                    <div class="result-meta">
                      <span v-if="child.source" class="source">●{{ child.source }}</span>
                      <span class="time">新闻发布日期：{{ formatDateTime(child.displayTime) }}</span>
                      <span v-if="child.location" class="location">{{ formatLocation(child.location) }}</span>
                      <span v-if="child.cluster_title" class="cluster-ref">{{ child.cluster_title }}</span>
                    </div>
                    <div class="result-signals">
                      <span class="signal-pill signal-pill--id">ID {{ child.id }}</span>
                      <span v-if="child.value_tag" class="signal-pill signal-pill--value">{{ child.value_tag }}</span>
                      <span v-if="resolveLanguageName(child)" class="signal-pill signal-pill--lang">{{ resolveLanguageName(child) }}</span>
                      <span v-if="child.cluster_article_count" class="signal-pill signal-pill--cluster">簇 {{ child.cluster_article_count }} 篇</span>
                    </div>
                    <div class="actions-right">
                      <router-link :to="`/data-service/news/${child.id}`" class="action-btn">查看详情</router-link>
                      <a v-if="child.displayUrl" :href="child.displayUrl" target="_blank" rel="noopener noreferrer" class="action-btn">查看原文</a>
                      <button
                        type="button"
                        class="action-btn star-btn"
                        :class="{ active: isFavorited(child.id) }"
                        :title="`保存到「${themeName}」`"
                        @click.prevent.stop="toggleFavorite(child.id)"
                      >
                        {{ isFavorited(child.id) ? '★ 已入文件夹' : '☆ 星标' }}
                      </button>
                    </div>
                  </div>
                </div>
                <template v-else>
                <div class="l2-child-main">
                  <button type="button" class="hierarchy-expand-btn hierarchy-expand-btn--child" @click.stop="toggleL2Expand(child)">
                    {{ expandedL2[child.id] ? '收起' : '展开' }}
                  </button>
                  <div class="l2-child-title">{{ child.title || child.id }}</div>
                </div>
                <div class="l2-child-meta">
                  <span class="l1-tag">{{ hierarchyLevelLabel(child) }}</span>
                  <span v-if="child.event_type" class="l1-tag">{{ child.event_type }}</span>
                  <span v-if="child.initiator" class="l2-actor">{{ child.initiator }}</span>
                  <span v-if="child.target" class="l2-actor">{{ child.target }}</span>
                  <span v-if="child.start_date || child.end_date" class="l2-actor">
                    事件时间：{{ child.start_date || '—' }} 至 {{ child.end_date || '—' }}
                  </span>
                  <span v-if="child.source" class="l2-actor">{{ child.source }}</span>
                  <span v-if="childArticleCount(child)" class="l2-child-article-count">文章 {{ childArticleCount(child) }}</span>
                </div>
                </template>
                <div
                  v-if="l2LoadingChildren[child.id]"
                  class="l2-children-loading l2-children-loading--nested"
                >
                  加载中...
                </div>
                <div v-if="expandedL2[child.id] && l2Children[child.id]" class="l2-children l2-children--nested">
                  <div
                    v-for="grandchild in l2Children[child.id]"
                    :key="'l2gc-'+grandchild.id"
                    class="l2-child-card l2-child-card--nested"
                    :class="{ expanded: expandedL2[grandchild.id], 'hierarchy-news-card': isHierarchyNews(grandchild) }"
                    @click.stop
                  >
                    <div v-if="isHierarchyNews(grandchild)" class="result-item result-item--hierarchy-news">
                      <div class="result-content">
                        <h4 class="title">{{ grandchild.title }}</h4>
                        <p class="desc">{{ grandchild.displayDesc }}</p>
                        <div class="result-meta">
                          <span v-if="grandchild.source" class="source">●{{ grandchild.source }}</span>
                          <span class="time">新闻发布日期：{{ formatDateTime(grandchild.displayTime) }}</span>
                          <span v-if="grandchild.location" class="location">{{ formatLocation(grandchild.location) }}</span>
                          <span v-if="grandchild.cluster_title" class="cluster-ref">{{ grandchild.cluster_title }}</span>
                        </div>
                        <div class="result-signals">
                          <span class="signal-pill signal-pill--id">ID {{ grandchild.id }}</span>
                          <span v-if="grandchild.value_tag" class="signal-pill signal-pill--value">{{ grandchild.value_tag }}</span>
                          <span v-if="resolveLanguageName(grandchild)" class="signal-pill signal-pill--lang">{{ resolveLanguageName(grandchild) }}</span>
                          <span v-if="grandchild.cluster_article_count" class="signal-pill signal-pill--cluster">簇 {{ grandchild.cluster_article_count }} 篇</span>
                        </div>
                        <div class="actions-right">
                          <router-link :to="`/data-service/news/${grandchild.id}`" class="action-btn">查看详情</router-link>
                          <a v-if="grandchild.displayUrl" :href="grandchild.displayUrl" target="_blank" rel="noopener noreferrer" class="action-btn">查看原文</a>
                          <button
                            type="button"
                            class="action-btn star-btn"
                            :class="{ active: isFavorited(grandchild.id) }"
                            :title="`保存到「${themeName}」`"
                            @click.prevent.stop="toggleFavorite(grandchild.id)"
                          >
                            {{ isFavorited(grandchild.id) ? '★ 已入文件夹' : '☆ 星标' }}
                          </button>
                        </div>
                      </div>
                    </div>
                    <template v-else>
                    <div class="l2-child-main">
                      <button
                        type="button"
                        class="hierarchy-expand-btn hierarchy-expand-btn--child"
                        @click.stop="toggleL2Expand(grandchild)"
                      >
                        {{ expandedL2[grandchild.id] ? '收起' : '展开' }}
                      </button>
                      <div class="l2-child-title">{{ grandchild.title || grandchild.id }}</div>
                    </div>
                    <div class="l2-child-meta">
                      <span class="l1-tag">{{ hierarchyLevelLabel(grandchild) }}</span>
                      <span v-if="grandchild.source" class="l2-actor">{{ grandchild.source }}</span>
                      <span v-if="isHierarchyNews(grandchild)" class="l2-actor">新闻发布日期：{{ formatDateTime(publishedTimeValue(grandchild)) }}</span>
                      <span v-if="childArticleCount(grandchild)" class="l2-child-article-count">文章 {{ childArticleCount(grandchild) }}</span>
                    </div>
                    </template>
                    <div
                      v-if="l2LoadingChildren[grandchild.id]"
                      class="l2-children-loading l2-children-loading--nested"
                    >
                      加载中...
                    </div>
                    <div v-if="expandedL2[grandchild.id] && l2Children[grandchild.id]" class="l2-children l2-children--nested hierarchy-news-list">
                      <div
                        v-for="news in l2Children[grandchild.id]"
                        :key="'l2news-'+news.id"
                        class="result-item result-item--hierarchy-news"
                        @click.stop
                      >
                        <div class="result-content">
                          <h4 class="title">{{ news.title }}</h4>
                          <p class="desc">{{ news.displayDesc }}</p>
                          <div class="result-meta">
                            <span v-if="news.source" class="source">●{{ news.source }}</span>
                            <span class="time">新闻发布日期：{{ formatDateTime(news.displayTime) }}</span>
                            <span v-if="news.location" class="location">{{ formatLocation(news.location) }}</span>
                            <span v-if="news.cluster_title" class="cluster-ref">{{ news.cluster_title }}</span>
                          </div>
                          <div class="result-signals">
                            <span class="signal-pill signal-pill--id">ID {{ news.id }}</span>
                            <span v-if="news.value_tag" class="signal-pill signal-pill--value">{{ news.value_tag }}</span>
                            <span v-if="resolveLanguageName(news)" class="signal-pill signal-pill--lang">{{ resolveLanguageName(news) }}</span>
                            <span v-if="news.cluster_article_count" class="signal-pill signal-pill--cluster">簇 {{ news.cluster_article_count }} 篇</span>
                          </div>
                          <div class="actions-right">
                            <router-link :to="`/data-service/news/${news.id}`" class="action-btn">查看详情</router-link>
                            <a v-if="news.displayUrl" :href="news.displayUrl" target="_blank" rel="noopener noreferrer" class="action-btn">查看原文</a>
                            <button
                              type="button"
                              class="action-btn star-btn"
                              :class="{ active: isFavorited(news.id) }"
                              :title="`保存到「${themeName}」`"
                              @click.prevent.stop="toggleFavorite(news.id)"
                            >
                              {{ isFavorited(news.id) ? '★ 已入文件夹' : '☆ 星标' }}
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 空结果 -->
        <!-- 逻辑：非加载状态 && 显示列表为空 && (处于搜索模式 OR 有搜索关键词) -->
        <div
          v-else-if="
            !isLoading && filteredNewsList.length === 0 && eventCorefClusters.length === 0 && microStoryItems.length === 0 && macroEventItems.length === 0 && (isSearchMode || searchParams.topic)
          "
          class="no-results"
        >
          <span>未找到匹配的结果</span>
          <p>请尝试调整搜索条件或关键词</p>
        </div>

        <!-- 结果列表 -->
        <ul v-else class="result-list">
          <li class="result-item" v-for="item in filteredNewsList" :key="item.id">
            <span v-if="item.isFirstRelease" class="first-release">首发</span>

            <div class="result-content">
              <h4 class="title">
                <template
                  v-for="(segment, index) in buildHighlightedSegments(item.title, item.searchHit, 'title')"
                  :key="`title-${index}`"
                >
                  <mark v-if="segment.matched" class="search-hit-mark">{{ segment.text }}</mark>
                  <span v-else>{{ segment.text }}</span>
                </template>
              </h4>
              <p class="desc">
                <template
                  v-for="(segment, index) in buildHighlightedSegments(item.displayDesc, item.searchHit, 'abstract')"
                  :key="`abstract-${index}`"
                >
                  <mark v-if="segment.matched" class="search-hit-mark">{{ segment.text }}</mark>
                  <span v-else>{{ segment.text }}</span>
                </template>
              </p>
              <p
                v-if="item.searchHit?.status !== 'unavailable'"
                class="search-hit-boundary"
              >
                仅标记返回页显示文本中的正向原样词项；不代表相关性分数、别名命中或正文其他位置。
              </p>

              <div class="result-meta">
                <span v-if="item.source" class="source">●{{ item.source }}</span>
                <span class="time">
                  新闻发布日期：{{ formatDateTime(item.displayTime) }}
                </span>
                <span v-if="item.location" class="location">{{
                  formatLocation(item.location)
                }}</span>
                <span v-if="item.cluster_title" class="cluster-ref">{{ item.cluster_title }}</span>
              </div>

              <div class="result-signals">
                <span class="signal-pill signal-pill--id">ID {{ item.id }}</span>
                <span v-if="item.value_tag" class="signal-pill signal-pill--value">{{ item.value_tag }}</span>
                <span v-if="resolveLanguageName(item)" class="signal-pill signal-pill--lang">{{ resolveLanguageName(item) }}</span>
                <span v-if="item.isFirstRelease" class="signal-pill signal-pill--first">首发</span>
                <span v-if="item.is_favorited" class="signal-pill signal-pill--fav">已收藏</span>
                <span v-if="item.cluster_article_count" class="signal-pill signal-pill--cluster">
                  簇 {{ item.cluster_article_count }} 篇
                </span>
              </div>

              <div class="actions-right">
                <router-link :to="`/data-service/news/${item.id}`" class="action-btn">查看详情</router-link>
                <a v-if="item.displayUrl" :href="item.displayUrl" target="_blank" rel="noopener noreferrer" class="action-btn">查看原文</a>
                <button
                  type="button"
                  class="action-btn star-btn"
                  :class="{ active: isFavorited(item.id) }"
                  :title="`保存到「${themeName}」`"
                  @click.prevent="toggleFavorite(item.id)"
                >
                  {{ isFavorited(item.id) ? '★ 已入文件夹' : '☆ 星标' }}
                </button>
              </div>
            </div>
          </li>
        </ul>
      </div>

      <!-- 分页组件 -->
      <div class="pagination">
        <div class="pagination-info">
          <span>共{{ totalItems }}条</span>
          <label class="search-sr-only" for="search-page-size">每页结果数量</label>
          <select id="search-page-size" name="pageSize" v-model="pageSize" @change="handlePageSizeChange">
            <option value="10">10条/页</option>
            <option value="20">20条/页</option>
            <option value="50">50条/页</option>
          </select>
        </div>
        <div class="pagination-controls">
          <button
            type="button"
            class="page-btn"
            aria-label="上一页"
            :disabled="currentPage === 1"
            @click="goToPage(currentPage - 1)"
          >
            &lt;
          </button>
          <button
            v-for="page in visiblePages"
            :key="page"
            type="button"
            class="page-btn"
            :class="{ active: page === currentPage }"
            :aria-label="`第 ${page} 页`"
            :aria-current="page === currentPage ? 'page' : undefined"
            @click="goToPage(page)"
          >
            {{ page }}
          </button>
          <button
            type="button"
            class="page-btn"
            aria-label="下一页"
            :disabled="currentPage === totalPages"
            @click="goToPage(currentPage + 1)"
          >
            &gt;
          </button>
        </div>
        <div class="pagination-jump">
          <span>前往</span>
          <label class="search-sr-only" for="search-jump-page">跳转到页码</label>
          <input
            id="search-jump-page"
            name="jumpPage"
            type="number"
            v-model="jumpPage"
            :min="1"
            :max="totalPages"
            @keyup.enter="jumpToPage"
          />
          <span>页</span>
        </div>
      </div>
    </div>
  </div>

  <Teleport to="body">
    <div
      v-if="clusterPanelOpen"
      class="cluster-panel-overlay"
      role="presentation"
      @click.self="closeClusterPanel"
    >
      <div class="cluster-panel" role="dialog" aria-labelledby="cluster-panel-heading" @click.stop>
        <div class="cluster-panel-header">
          <h2 id="cluster-panel-heading" class="cluster-panel-heading">{{ clusterPanelTitle }}</h2>
          <div class="cluster-panel-actions">
            <button type="button" class="cluster-panel-btn cluster-panel-btn--ghost" @click="printClusterNote">
              打印
            </button>
            <button type="button" class="cluster-panel-btn cluster-panel-btn--primary" @click="downloadClusterNote">
              下载笔记
            </button>
            <button
              type="button"
              class="cluster-panel-close"
              aria-label="关闭"
              @click="closeClusterPanel"
            >
              ×
            </button>
          </div>
        </div>
        <p class="cluster-panel-hint">
          Obsidian 友好 Markdown（wikilink、frontmatter）；可下载 <code>.md</code> 入库，或使用「打印」另存为 PDF。
        </p>
        <pre class="cluster-panel-md" tabindex="0">{{ clusterPanelMarkdown }}</pre>
      </div>
    </div>
  </Teleport>

  <AssistantDrawer
    v-model="assistantDrawerOpen"
    :drawer-key="assistantDrawerKey"
    :page-skill="searchAssistantSkill"
    title="数据搜索助手"
    subtitle="基于当前检索条件、勾选结果和收藏文件夹协同研判"
    @page-action="handleAssistantPageAction"
  />
  <button
    type="button"
    class="assistant-fab"
    title="打开数据助手"
    @click="sendSearchToAssistant"
  >
    <span>AI</span>
    <strong>数据助手</strong>
  </button>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch, reactive } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { AssistantDrawer } from '@/features/assistant/index.js'
import {
  graphMetricExplanation,
  graphMetricPresentation,
} from '@/governance/graphMetrics.js'
import { safeExternalHttpUrl } from '@/utils/externalUrl.js'
import {
  DEFAULT_SEARCH_FOLDER_NAME as DEFAULT_FOLDER_NAME,
  SEARCH_DATA_OPERATION_KINDS,
  SEARCH_HIT_LOCATIONS as hitLocations,
  SEARCH_MODES as searchModes,
  SEARCH_SITUATION_PRESETS as situationPresets,
  SEARCH_TIME_RANGES as timeRanges,
  SEARCH_TYPES as searchTypes,
  activeSearchSurfaceLabel as formatActiveSearchSurfaceLabel,
  applySearchSituationPreset,
  buildHighlightedSegments,
  buildSearchHistoryKeyword,
  buildSearchRequestDto,
  buildVisiblePages,
  calculateTotalPages,
  createLatestSearchRequest,
  createSearchListOperation,
  createSearchOperationRunner,
  createSearchParams,
  createSearchQueryOperation,
  createSearchThemeName,
  createSearchThemeStorage,
  displaySearchFolderName as displayFolderName,
  isSearchAbortError,
  mergeFavoriteNewsIds,
  normalizeSearchParamsForType,
  normalizeDisplayNewsRows,
  normalizeNewsListResponse,
  normalizeNewsTimeSemantics,
  normalizeSearchFolderName as normalizeFolderName,
  normalizeSearchResponse,
  renameSearchThemeList,
  resolveLanguageDisplay,
  resolveJumpPage,
  searchApi,
  searchModeDisclosure,
  searchSortSemantics,
} from '@/features/search/index.js'
import {
  upsertReportFavorite,
  removeReportFavorite,
  getReportFavoritesForTopic,
  getFavoritesMap,
  setFavoritesMap,
  getWarningsForTopic,
  getWarningsMap,
  setWarningsMap,
  deleteThemeData,
} from '@/utils/reportFavorites'

const route = useRoute()
const workspacePanelExpanded = ref(false)
const filtersExpanded = ref(false)
const ASSISTANT_AUTORUN_CONTEXT_KEY = 'data_assistant_autorun_context_v1'
const PENDING_ASSISTANT_OPEN_KEY = 'data_search_pending_assistant_open_v1'
const assistantDrawerOpen = ref(false)
const assistantDrawerKey = ref(0)
const lastAssistantSearchKey = ref('')
const lastAssistantSearchAt = ref(0)
const criticalActionStatus = ref('')
let assistantPageActionChain = Promise.resolve()

function hasStoredAccessToken() {
  try {
    return Boolean(localStorage.getItem('access_token'))
  } catch {
    return false
  }
}

function readAssistantSessionValue(key) {
  try {
    return sessionStorage.getItem(key)
  } catch {
    return null
  }
}

function writeAssistantSessionValue(key, value) {
  try {
    sessionStorage.setItem(key, value)
    return true
  } catch {
    return false
  }
}

function removeAssistantSessionValue(key) {
  try {
    sessionStorage.removeItem(key)
  } catch {
    // Storage denial is surfaced when a context write is attempted.
  }
}

// 引入图标以避免报错 (如果在 main.js 注册了可删除)
// 已移除未使用的图标导入

// 搜索参数
const searchParams = reactive(createSearchParams())

// 状态管理
const isLoading = ref(false)
const searchError = ref('')
const searchResults = ref([])
const queryExplain = ref(null)
const queryReceipt = ref(null)
const newsList = ref([])
const isSearchMode = ref(false) // 核心状态：是否处于搜索结果展示模式
const favoriteNewsIds = ref([])
const favoriteCards = ref([])
const favoriteMapVersion = ref(0)
const themeName = ref(DEFAULT_FOLDER_NAME)
const isEditingTheme = ref(false)
const themeNameDraft = ref(themeName.value)
const themeList = ref([])

const themeStorage = createSearchThemeStorage(localStorage)
const dataRequest = createLatestSearchRequest()
const dataOperations = createSearchOperationRunner({ api: searchApi, request: dataRequest })
const auxiliaryRequestController = new AbortController()

const clusterTree = ref([])
const eventCorefClusters = ref([])
const microStoryItems = ref([])
const macroEventItems = ref([])

function hierarchyQualityExplanation(item) {
  return graphMetricExplanation('story_graph.quality_score', {
    inputs: {
      quality_score: item?.quality_score,
      chain_quality: item?.chain_quality,
      importance_score: item?.importance_score,
    },
  })
}

// L1/L2 展开状态
const expandedL1 = ref({})   // { [id]: true/false }
const expandedL2 = ref({})   // { [id]: true/false }
const l1Children = ref({})   // { [id]: [cluster items] }
const l2Children = ref({})   // { [id]: [micro_story items] }
const l1LoadingChildren = ref({})  // { [id]: true/false }
const l2LoadingChildren = ref({})  // { [id]: true/false }
const l1ClusterNews = ref({})       // { [l1_id]: [news items] }

/** 簇结果页眉统计：与后端 cluster_tree 汇总一致 */
const clusterTreeSummary = computed(() => {
  const stories = clusterTree.value.length
  let clusters = 0
  let news = 0
  for (const s of clusterTree.value) {
    clusters += Number(s.cluster_count) || 0
    news += Number(s.news_count) || 0
  }
  return { stories, clusters, news }
})

const eventCorefSummary = computed(() => {
  const clusters = eventCorefClusters.value.length
  let news = 0
  for (const ec of eventCorefClusters.value) {
    news += Number(ec.article_count) || 0
  }
  return { clusters, news }
})

const hierarchyResultTitle = computed(() => (
  searchParams.searchType === 'l3' ? 'L3 大事件检索结果' : 'L2 走势检索结果'
))

/** 簇数据 Obsidian 导出悬浮面板 */
const clusterPanelOpen = ref(false)
const clusterPanelTitle = ref('')
const clusterPanelMarkdown = ref('')
let clusterPanelEscHandler = null
let clusterPanelPrevBodyOverflow = ''

function removeClusterPanelEsc() {
  if (clusterPanelEscHandler) {
    window.removeEventListener('keydown', clusterPanelEscHandler)
    clusterPanelEscHandler = null
  }
}

function lockBodyScrollForClusterPanel() {
  clusterPanelPrevBodyOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
}

function unlockBodyScrollForClusterPanel() {
  document.body.style.overflow = clusterPanelPrevBodyOverflow
}

function closeClusterPanel() {
  removeClusterPanelEsc()
  unlockBodyScrollForClusterPanel()
  clusterPanelOpen.value = false
}

function attachClusterPanelEsc() {
  removeClusterPanelEsc()
  clusterPanelEscHandler = (e) => {
    if (e.key === 'Escape') closeClusterPanel()
  }
  window.addEventListener('keydown', clusterPanelEscHandler)
}

function sanitizeClusterFilename(name) {
  const s = String(name || 'cluster-note')
    .replace(/[/\\?%*:|"<>]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
  return s.slice(0, 80) || 'cluster-note'
}

function obsidianSafeWikiTitle(title) {
  return String(title || '无标题').replace(/\]\]/g, '] ')
}

function buildMarkdownStory(story) {
  const lines = []
  const now = new Date().toISOString().slice(0, 19).replace('T', ' ')
  lines.push('---')
  lines.push('type: cluster-export')
  lines.push('cluster_level: story')
  lines.push(`story_id: ${story.story_id}`)
  lines.push(`exported_at: ${now}`)
  lines.push(`search_theme: ${JSON.stringify(themeName.value)}`)
  lines.push(`search_topic: ${JSON.stringify(searchParams.topic || '')}`)
  lines.push(`search_hit_location: ${JSON.stringify(searchParams.hitLocation || '全文')}`)
  lines.push(`must_include: ${JSON.stringify(searchParams.mustInclude || '')}`)
  lines.push('tags: [簇检索, 故事线]')
  lines.push('---')
  lines.push('')
  lines.push(`# 故事线：${story.title}`)
  lines.push('')
  lines.push('## 统计')
  lines.push(`- 事件簇数量：${story.cluster_count}`)
  lines.push(`- 新闻总数：${story.news_count}`)
  lines.push('')
  lines.push('## 事件簇与新闻')
  const clusters = story.clusters || []
  for (const cluster of clusters) {
    const et = cluster.event_type || cluster.cluster_id
    lines.push(`### ${et}`)
    lines.push(`- **cluster_id**: \`${cluster.cluster_id}\``)
    if (cluster.initiator) lines.push(`- **发起方**: ${cluster.initiator}`)
    if (cluster.target) lines.push(`- **目标**: ${cluster.target}`)
    if (cluster.dominant_trigger) lines.push(`- **触发词**: ${cluster.dominant_trigger}`)
    if (cluster.cluster_quality) lines.push(`- **质量**: ${cluster.cluster_quality}`)
    lines.push(`- **新闻数**: ${cluster.news_count}`)
    lines.push('')
    for (const n of cluster.news || []) {
      const wiki = obsidianSafeWikiTitle(n.title)
      const url = n.request_url || ''
      const time = publishedTimeValue(n) || 'unknown'
      lines.push(
        `- [[${wiki}]] · 新闻发布日期 \`${time}\` · id \`${n.id}\`${url ? ` · [原文](${url})` : ''}`,
      )
    }
    lines.push('')
  }
  return lines.join('\n')
}

function buildMarkdownCluster(cluster) {
  const lines = []
  const now = new Date().toISOString().slice(0, 19).replace('T', ' ')
  lines.push('---')
  lines.push('type: cluster-export')
  lines.push('cluster_level: cluster')
  lines.push(`cluster_id: ${cluster.cluster_id}`)
  lines.push(`exported_at: ${now}`)
  lines.push(`search_theme: ${JSON.stringify(themeName.value)}`)
  lines.push(`search_topic: ${JSON.stringify(searchParams.topic || '')}`)
  lines.push('tags: [簇检索, 事件簇]')
  lines.push('---')
  lines.push('')
  const title = cluster.event_type || cluster.cluster_id
  lines.push(`# 事件簇：${title}`)
  lines.push('')
  lines.push('## 统计')
  lines.push(`- **cluster_id**: \`${cluster.cluster_id}\``)
  if (cluster.event_type) lines.push(`- **事件类型**: ${cluster.event_type}`)
  if (cluster.initiator) lines.push(`- **发起方**: ${cluster.initiator}`)
  if (cluster.target) lines.push(`- **目标**: ${cluster.target}`)
  if (cluster.dominant_trigger) lines.push(`- **触发词**: ${cluster.dominant_trigger}`)
  if (cluster.cluster_quality) lines.push(`- **质量**: ${cluster.cluster_quality}`)
  lines.push(`- **新闻数**: ${cluster.news_count}`)
  lines.push('')
  lines.push('## 新闻列表')
  for (const n of cluster.news || []) {
    const wiki = obsidianSafeWikiTitle(n.title)
    const url = n.request_url || ''
    const time = publishedTimeValue(n) || 'unknown'
    lines.push(
      `- [[${wiki}]] · 新闻发布日期 \`${time}\` · id \`${n.id}\`${url ? ` · [原文](${url})` : ''}`,
    )
  }
  lines.push('')
  return lines.join('\n')
}

function buildMarkdownL1Event(ec) {
  const lines = []
  const now = new Date().toISOString().slice(0, 19).replace('T', ' ')
  lines.push('---')
  lines.push('type: cluster-export')
  lines.push('cluster_level: l1_event')
  lines.push(`cluster_id: ${ec.cluster_id}`)
  lines.push(`exported_at: ${now}`)
  lines.push(`search_theme: ${JSON.stringify(themeName.value)}`)
  lines.push(`search_topic: ${JSON.stringify(searchParams.topic || '')}`)
  lines.push('tags: [簇检索, L1事件]')
  lines.push('---')
  lines.push('')
  const title = ec.event_type || ec.cluster_id
  lines.push(`# L1事件：${title}`)
  lines.push('')
  lines.push('## 统计')
  lines.push(`- **cluster_id**: \`${ec.cluster_id}\``)
  if (ec.event_type) lines.push(`- **事件类型**: ${ec.event_type}`)
  if (ec.initiator) lines.push(`- **发起方**: ${ec.initiator}`)
  if (ec.target) lines.push(`- **目标**: ${ec.target}`)
  if (ec.dominant_trigger) lines.push(`- **触发词**: ${ec.dominant_trigger}`)
  if (ec.cluster_quality) lines.push(`- **质量**: ${ec.cluster_quality}`)
  if (ec.start_date) lines.push(`- **开始日期**: ${ec.start_date}`)
  if (ec.end_date) lines.push(`- **结束日期**: ${ec.end_date}`)
  lines.push(`- **新闻数**: ${ec.article_count}`)
  lines.push('')
  lines.push('## 新闻列表')
  for (const n of ec.articles || []) {
    const wiki = obsidianSafeWikiTitle(n.title)
    const url = n.request_url || ''
    const time = publishedTimeValue(n) || 'unknown'
    lines.push(
      `- [[${wiki}]] · 新闻发布日期 \`${time}\` · id \`${n.id}\`${url ? ` · [原文](${url})` : ''}`,
    )
  }
  lines.push('')
  return lines.join('\n')
}

function openClusterPanelStory(story) {
  clusterPanelTitle.value = `故事线 · ${story.title || '未命名'}`
  clusterPanelMarkdown.value = buildMarkdownStory(story)
  lockBodyScrollForClusterPanel()
  clusterPanelOpen.value = true
  nextTick(() => attachClusterPanelEsc())
}

function openClusterPanelCluster(cluster) {
  const title = cluster.event_type || cluster.cluster_id
  clusterPanelTitle.value = `事件簇 · ${title}`
  clusterPanelMarkdown.value = buildMarkdownCluster(cluster)
  lockBodyScrollForClusterPanel()
  clusterPanelOpen.value = true
  nextTick(() => attachClusterPanelEsc())
}

function openClusterPanelL1Event(ec) {
  const title = ec.event_type || ec.cluster_id
  clusterPanelTitle.value = `L1事件 · ${title}`
  clusterPanelMarkdown.value = buildMarkdownL1Event(ec)
  lockBodyScrollForClusterPanel()
  clusterPanelOpen.value = true
  nextTick(() => attachClusterPanelEsc())
}

function downloadClusterNote() {
  const blob = new Blob([clusterPanelMarkdown.value], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${sanitizeClusterFilename(clusterPanelTitle.value)}.md`
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('已下载 Markdown 笔记')
}

function printClusterNote() {
  const w = window.open('', '_blank')
  if (!w) {
    ElMessage.warning('请允许弹出窗口以使用打印')
    return
  }
  const meta = w.document.createElement('meta')
  meta.setAttribute('charset', 'utf-8')
  w.document.head.appendChild(meta)
  const st = w.document.createElement('style')
  st.textContent = `
    body { font-family: "Segoe UI", system-ui, sans-serif; padding: 24px; color: #1e293b; }
    h1 { font-size: 18px; margin: 0 0 16px; color: #4338ca; }
    pre { font-family: ui-monospace, "Cascadia Code", Consolas, monospace; font-size: 12px;
          line-height: 1.55; white-space: pre-wrap; word-break: break-word; margin: 0;
          border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; background: #f8fafc; }
  `
  w.document.head.appendChild(st)
  w.document.title = clusterPanelTitle.value.slice(0, 80)
  const h1 = w.document.createElement('h1')
  h1.textContent = clusterPanelTitle.value
  const pre = w.document.createElement('pre')
  pre.textContent = clusterPanelMarkdown.value
  w.document.body.appendChild(h1)
  w.document.body.appendChild(pre)
  w.focus()
  setTimeout(() => w.print(), 200)
}

// 筛选后用于显示的最终列表
const filteredNewsList = ref([])

const activeSearchSurfaceLabel = computed(() => {
  return formatActiveSearchSurfaceLabel(searchParams, searchTypes)
})
const searchSortLabel = computed(() => searchSortSemantics(searchParams))

const timeFilterLabel = computed(() => (
  searchParams.searchType === 'news' ? '新闻发布日期' : '事件发生时间'
))

const timeFilterHint = computed(() => (
  searchParams.searchType === 'news'
    ? '筛选 public.news.published_at；不筛选事件发生、采集或更新时间。'
    : '筛选事件起止区间与所选范围是否重叠；不筛选新闻发布、采集或更新时间。'
))

function queryStageLabel(stage) {
  return {
    parse: '查询解析',
    entity_resolution: '实体解析',
    retrieval: '检索结果',
    relaxation: '自动放宽',
  }[stage] || stage
}

function queryCountSemanticsLabel(value) {
  return {
    parsed_term_count: '实际解析词数',
    resolved_entity_count: '稳定实体数',
    api_response_total: 'API 返回总数',
  }[value] || value || '计数语义未标注'
}

function queryFilterText(filter) {
  const label = {
    must_include: '必须包含',
    any_include: '任意包含',
    need_exclude: '需要排除',
    relative_time: `${timeFilterLabel.value}相对范围`,
    start_time: `${timeFilterLabel.value}起点`,
    end_time: `${timeFilterLabel.value}终点`,
    published_at: '新闻发布日期排序',
    data_source: '数据源',
    language: '语言',
  }[filter?.field] || filter?.field || '筛选'
  return `${label}：${filter?.value ?? '—'}`
}

function queryAstText(value) {
  try {
    return JSON.stringify(value || { type: 'unavailable' }, null, 2).slice(0, 4000)
  } catch {
    return '{ "type": "unavailable" }'
  }
}

function queryLimitText(limits) {
  const value = limits || {}
  return [
    `字符 ${value.observed_query_chars ?? 0}/${value.max_query_chars ?? '—'}`,
    `词元 ${value.observed_tokens ?? 0}/${value.max_tokens ?? '—'}`,
    `AST ${value.observed_ast_nodes ?? 0}/${value.max_ast_nodes ?? '—'}`,
    `条件 ${value.observed_terms ?? 0}/${value.max_terms ?? '—'}`,
    `括号深度 ${value.observed_nesting_depth ?? 0}/${value.max_nesting_depth ?? '—'}`,
    `数据库预算 ${value.statement_timeout_seconds ?? '—'} 秒`,
    `每个正向标题条件候选上限 ${value.max_title_candidates_per_positive_leaf ?? '—'}`,
  ].join('；')
}

function queryFieldLabel(value) {
  return {
    primary_query: '主关键词',
    must_include: '必须包含',
    any_include: '任意包含',
    need_exclude: '需要排除',
  }[value] || value || '未标注条件'
}

const searchAssistantSkill = computed(() => ({
  page: '数据搜索',
  path: '/data-service/data-search',
  summary: `检索 ${activeSearchSurfaceLabel.value}，当前工作夹 ${displayFolderName(themeName.value)}，结果 ${totalItems.value || searchResults.value.length || 0} 条，收藏 ${favoriteCards.value.length} 条。`,
  access: [
    '数据服务 > 数据搜索',
    '全局/页面搜索可带 topic 参数进入本页',
    '数据助手侧栏可从页面右下角 AI 按钮打开',
  ],
  sections: [
    '检索条件栏',
    '新闻/L1/L2/L3 结果列表',
    '工作夹收藏',
    'Obsidian Markdown 与 CSV 导出',
  ],
  actions: [
    '执行检索',
    '切换新闻/L1/L2/L3',
    '展开事件层级',
    '收藏或移除新闻',
    '导出当前素材',
  ],
  aiActions: [
    '同步助手检索关键词到当前页面',
    '根据工具调用切换新闻或事件层级搜索',
    '解释当前结果和收藏素材',
  ],
}))

const dataSources = ref([])
const languages = ref([])
const languageOptions = ref([])
const sites = ref(['新闻网站', '博客', '社交媒体', '论坛'])

// 分页相关状态
const currentPage = ref(1)
const pageSize = ref(10)
const totalItems = ref(0)
const jumpPage = ref(1)
const showOnlyFirst = ref(false)

const isInitialized = ref(false)

/** 登录用户：将本次搜索关键词写入后端 user_search_history */
const recordUserSearchHistory = async (params = searchParams) => {
  const keyword = buildSearchHistoryKeyword(params)
  if (!keyword) return
  const outcome = await searchApi.recordHistory(keyword, { signal: auxiliaryRequestController.signal })
  if (outcome.status === 'warning') console.warn('搜索历史未同步:', outcome.message)
  if (
    outcome.status === 'error'
    && !isSearchAbortError(outcome.error, auxiliaryRequestController.signal)
  ) {
    console.warn('搜索历史上报失败', outcome.error)
  }
}

const resolveLanguageName = (item) => {
  return resolveLanguageDisplay(item, languageOptions.value)
}

const syncFavoriteIdsFromList = (list) => {
  favoriteNewsIds.value = mergeFavoriteNewsIds(favoriteNewsIds.value, list)
}

// ================== 数据处理核心逻辑 ==================

/**
 * 核心筛选与字段映射函数
 */
const filterFirstRelease = () => {
  // 根据 isSearchMode 决定使用哪份数据
  const rawList = isSearchMode.value ? searchResults.value : newsList.value
  filteredNewsList.value = normalizeDisplayNewsRows(rawList, showOnlyFirst.value)
}

const prepareDataOperation = (operation) => {
  isLoading.value = true
  searchError.value = ''
  queryExplain.value = null
  queryReceipt.value = null
  if (operation.kind === SEARCH_DATA_OPERATION_KINDS.LIST) {
    isSearchMode.value = false
    clusterTree.value = []
    eventCorefClusters.value = []
    microStoryItems.value = []
    macroEventItems.value = []
  }
}

const settleDataOperation = (outcome) => {
  if (!outcome.latest || outcome.status === 'idle') return
  const operation = outcome.operation
  try {
    if (outcome.status !== 'success') {
      if (['error', 'timeout'].includes(outcome.status)) throw outcome.error
      return
    }

    if (operation.kind === SEARCH_DATA_OPERATION_KINDS.LIST) {
      const normalized = normalizeNewsListResponse(outcome.value)
      newsList.value = normalized.rows
      syncFavoriteIdsFromList(newsList.value)
      totalItems.value = normalized.total
      searchResults.value = []
      currentPage.value = Number(operation.query.page) || 1
      pageSize.value = Number(operation.query.size) || 10
      filterFirstRelease()
      return
    }

    const normalized = normalizeSearchResponse(outcome.value, operation.searchType, {
      page: operation.requestParams.page,
      pageSize: operation.requestParams.pageSize,
    })
    void recordUserSearchHistory(operation.requestParams)

    isSearchMode.value = true
    clusterTree.value = normalized.clusterTree
    eventCorefClusters.value = normalized.eventCorefClusters
    microStoryItems.value = normalized.microStoryItems
    macroEventItems.value = normalized.macroEventItems
    searchResults.value = normalized.searchResults
    queryExplain.value = normalized.queryExplain
    queryReceipt.value = normalized.queryReceipt
    if (operation.searchType === 'news') syncFavoriteIdsFromList(searchResults.value)
    totalItems.value = normalized.total
    currentPage.value = normalized.page
    pageSize.value = normalized.pageSize
    searchParams.page = currentPage.value
    searchParams.pageSize = pageSize.value

    if (operation.searchType === 'news') {
      filterFirstRelease()
    } else {
      filteredNewsList.value = []
    }
  } catch (error) {
    if (operation.kind === SEARCH_DATA_OPERATION_KINDS.LIST) {
      console.error('加载初始数据失败：', error)
      searchError.value = '加载数据失败，请检查网络或重试'
      newsList.value = []
    } else {
      console.error('搜索失败:', error)
      searchError.value = error.message || '搜索失败，请稍后重试'
      searchResults.value = []
      microStoryItems.value = []
      macroEventItems.value = []
      eventCorefClusters.value = []
      totalItems.value = 0
      isSearchMode.value = true
      filteredNewsList.value = []
    }
  } finally {
    isLoading.value = false
  }
}

const executeDataOperation = async (operation) => {
  prepareDataOperation(operation)
  const outcome = await dataOperations.execute(operation)
  settleDataOperation(outcome)
}

/**
 * 加载分页列表数据（单接口 GET /api/dashboard/news）
 */
const loadPageData = async (page) => {
  const operation = createSearchListOperation({
    query: {
      page,
      size: pageSize.value,
      sortBy: searchParams.sortBy,
      sortOrder: searchParams.sortOrder,
      favoriteScopeTopic: themeName.value,
    },
    origin: window.location.origin,
  })
  await executeDataOperation(operation)
}

/**
 * 重置搜索 - 恢复初始状态
 */
const resetSearch = async () => {
  isLoading.value = true

  // 1. 重置参数
  Object.assign(searchParams, createSearchParams())

  // 2. 清空状态并退出搜索模式
  isSearchMode.value = false
  searchResults.value = []
  eventCorefClusters.value = []
  microStoryItems.value = []
  macroEventItems.value = []
  queryExplain.value = null
  queryReceipt.value = null
  searchError.value = ''
  currentPage.value = 1
  pageSize.value = 10
  jumpPage.value = 1

  // 3. 重新加载初始数据
  await loadPageData(1)

  scrollToTop()
}

// ================== 搜索逻辑 ==================

const performSearch = async () => {
  const requestParams = normalizeSearchParamsForType(searchParams)
  Object.assign(searchParams, requestParams)
  const searchType = requestParams.searchType
  const params = buildSearchRequestDto(requestParams, themeName.value)
  const operation = createSearchQueryOperation({ params, requestParams, searchType })
  await executeDataOperation(operation)
}

const retryLastDataRequest = async () => {
  const operation = dataOperations.getFailedOperation()
  if (!operation) return
  prepareDataOperation(operation)
  const outcome = await dataOperations.retry()
  settleDataOperation(outcome)
}

// ================== 事件处理 ==================

const handleHitLocationChange = (val) => {
  if (searchParams.searchType !== 'news') return
  searchParams.hitLocation = val
  performSearch()
}

const setSortBy = (val) => {
  if (searchParams.searchType !== 'news') return
  searchParams.sortBy = val
  performSearch()
}

const setSearchMode = (mode) => {
  searchParams.mode = mode
  performSearch()
}

const setSearchType = (type) => {
  Object.assign(searchParams, normalizeSearchParamsForType({ ...searchParams, page: 1 }, type))
  performSearch()
}

const applySituationPreset = (preset) => {
  applySearchSituationPreset(searchParams, preset, pageSize.value)
  currentPage.value = 1
  jumpPage.value = 1
  performSearch()
}

const toggleL1Expand = async (item) => {
  const id = item.id
  if (expandedL1.value[id]) {
    expandedL1.value[id] = false
    return
  }
  if (l1Children.value[id]) {
    expandedL1.value[id] = true
    return
  }
  l1LoadingChildren.value[id] = true
  try {
    // Step 1: fetch clusters under this L1 event
    const data = await searchApi.getV11Children(id, 'micro', {
      signal: auxiliaryRequestController.signal,
    })
    const clusters = Array.isArray(data.items) ? data.items : []
    l1Children.value[id] = clusters

    // Step 2: fetch news for ALL clusters in parallel, then flatten
    if (clusters.length > 0) {
      const newsPromises = clusters.map(async (cluster) => {
        try {
          const ndata = await searchApi.getV11Children(cluster.id, 'cluster', {
            signal: auxiliaryRequestController.signal,
          })
          return Array.isArray(ndata.items) ? ndata.items : []
        } catch { return [] }
      })
      const newsArrays = await Promise.all(newsPromises)
      const allNews = newsArrays.flat()
      // Deduplicate by id
      const seen = new Set()
      l1ClusterNews.value[id] = allNews.filter(n => {
        if (seen.has(n.id)) return false
        seen.add(n.id)
        return true
      })
    } else {
      l1ClusterNews.value[id] = []
    }
    expandedL1.value[id] = true
  } catch (e) {
    if (!isSearchAbortError(e, auxiliaryRequestController.signal)) {
      console.error('展开L1失败:', e)
    }
  } finally {
    l1LoadingChildren.value[id] = false
  }
}

const normalizeHierarchyChildren = (items, childLevel) => (
  Array.isArray(items)
    ? items.map((child) => {
        const timeSemantics = normalizeNewsTimeSemantics(child)
        return {
          ...child,
          id: String(child.id),
          level: child.level || childLevel || 'news',
          displayDesc: child.displayDesc || child.abstract || child.desc || '—',
          displayTime: timeSemantics.publishedAt || undefined,
          timeSemantics,
          displayUrl: safeExternalHttpUrl(child.displayUrl || child.request_url || child.requestUrl),
          is_favorited: !!child.is_favorited,
          is_warned: !!child.is_warned,
        }
      })
    : []
)

const isHierarchyNews = (item) => {
  const level = String(item?.level || '').toLowerCase()
  return level === 'news'
}

const hierarchyLevelLabel = (item) => {
  const level = String(item?.level || '').toLowerCase()
  if (level === 'l3') return 'L3大事件'
  if (level === 'l2') return 'L2走势'
  if (level === 'cluster' || level === 'micro' || level === 'l1') return 'L1事件'
  return '新闻'
}

const childArticleCount = (item) => Number(item?.article_count || item?.cluster_article_count || item?.children_count || 0)

const toggleL2Expand = async (item) => {
  const id = item.id
  if (expandedL2.value[id]) {
    expandedL2.value[id] = false
    return
  }
  if (l2Children.value[id]) {
    expandedL2.value[id] = true
    return
  }
  l2LoadingChildren.value[id] = true
  try {
    const level = item.level || searchParams.searchType || 'l2'
    const data = await searchApi.getV11Children(id, level, {
      signal: auxiliaryRequestController.signal,
    })
    l2Children.value[id] = normalizeHierarchyChildren(data.items, data.child_level)
    expandedL2.value[id] = true
  } catch (e) {
    if (!isSearchAbortError(e, auxiliaryRequestController.signal)) {
      console.error('展开L2失败:', e)
    }
  } finally {
    l2LoadingChildren.value[id] = false
  }
}

const formatDateTime = (dateTime) => {
  if (!dateTime || dateTime === '—') return '—'
  try {
    const date = new Date(dateTime)
    if (isNaN(date.getTime())) return dateTime
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return dateTime
  }
}

const publishedTimeValue = (item) => (
  item?.timeSemantics?.schemaVersion === 'search-result-time-semantics-v1'
    ? item.timeSemantics.publishedAt
    : normalizeNewsTimeSemantics(item).publishedAt
)

const formatFolderDate = (dateTime) => {
  const text = formatDateTime(dateTime)
  if (!text || text === '—') return '暂无更新'
  return text.slice(0, 10)
}

const folderCards = computed(() => {
  favoriteMapVersion.value
  const map = getFavoritesMap()
  const names = themeList.value.length ? themeList.value : [themeName.value || DEFAULT_FOLDER_NAME]
  return names.map((name) => {
    const list = Array.isArray(map[name]) ? map[name] : []
    const latest = list[0] || null
    return {
      name,
      displayName: displayFolderName(name),
      count: list.length,
      latestTitle: latest?.title || '',
      latestLabel: latest ? formatFolderDate(publishedTimeValue(latest)) : '暂无收藏',
    }
  })
})

const formatLocation = (location) => {
  if (!location || location === '—') return '—'
  return `②${location}`
}

const setPublishTime = (range) => {
  searchParams.publishTime = range
  searchParams.startTime = ''
  searchParams.endTime = ''
  if (isInitialized.value) performSearch()
}

const onTimeRangeChange = () => {
  if (searchParams.startTime || searchParams.endTime) {
    searchParams.publishTime = ''
  }
  if (isInitialized.value) performSearch()
}

const goToPage = (page) => {
  if (page >= 1 && page <= totalPages.value) {
    currentPage.value = page
    jumpPage.value = page
    searchParams.page = page
    scrollToTop()
    // 根据当前模式决定调用哪个加载函数
    if (isSearchMode.value) {
      performSearch()
    } else {
      loadPageData(page)
    }
  }
}

const jumpToPage = () => {
  const requestedPage = parseInt(jumpPage.value)
  const page = resolveJumpPage(requestedPage, currentPage.value, totalPages.value)
  if (page === requestedPage) goToPage(page)
  else jumpPage.value = currentPage.value
}

const handlePageSizeChange = () => {
  currentPage.value = 1
  jumpPage.value = 1
  searchParams.page = 1
  searchParams.pageSize = pageSize.value
  scrollToTop()
  if (isSearchMode.value) {
    performSearch()
  } else {
    loadPageData(1)
  }
}

const scrollToTop = () => {
  nextTick(() => {
    window.scrollTo({ top: 0, behavior: 'smooth' })
  })
}

function csvCell(value) {
  const text = String(value ?? '').replace(/\r?\n/g, ' ').trim()
  return `"${text.replace(/"/g, '""')}"`
}

function exportRow(item) {
  const timeSemantics = normalizeNewsTimeSemantics(item)
  return {
    id: item.id ?? item.news_id ?? '',
    title: item.title || item.display_title || '',
    abstract: item.abstract || item.summary || '',
    source: item.source || item.source_name || item.domain || '',
    language: item.language || item.lang || '',
    published_at: timeSemantics.publishedAt || '',
    event_time_start: timeSemantics.eventTimeStart || '',
    event_time_end: timeSemantics.eventTimeEnd || '',
    collected_at: timeSemantics.collectedAt || '',
    updated_at: timeSemantics.updatedAt || '',
    url: item.request_url || item.url || item.link || '',
  }
}

function downloadTextFile(filename, content, mime = 'text/plain;charset=utf-8') {
  const blob = new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

const exportData = () => {
  const selectedItems = filteredNewsList.value.filter((item) => item.selected)
  if (selectedItems.length === 0) {
    criticalActionStatus.value = '导出未开始：请先选择至少一条数据。'
    ElMessage.warning('请先选择要导出的数据')
    return
  }
  try {
    const headers = [
      'id',
      'title',
      'abstract',
      'source',
      'language',
      'published_at',
      'event_time_start',
      'event_time_end',
      'collected_at',
      'updated_at',
      'url',
    ]
    const rows = selectedItems.map(exportRow)
    const csv = [
      headers.join(','),
      ...rows.map((row) => headers.map((key) => csvCell(row[key])).join(',')),
    ].join('\n')
    const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
    downloadTextFile(`globemind-news-export-${stamp}.csv`, `\ufeff${csv}`, 'text/csv;charset=utf-8')
    criticalActionStatus.value = `导出成功：已生成 ${selectedItems.length} 条数据的 CSV 文件。`
    ElMessage.success(`已导出 ${selectedItems.length} 条数据`)
  } catch (error) {
    console.error('导出失败:', error)
    criticalActionStatus.value = '导出失败：浏览器未能生成下载文件，请重试或检查下载权限。'
    ElMessage.error('导出失败，请重试或检查浏览器下载权限')
  }
}

function compactAssistantText(value, max = 220) {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  if (!text) return ''
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

function searchAssistantMaterial(item, index) {
  const row = exportRow(item)
  return [
    `${index + 1}. ${compactAssistantText(row.title || `新闻 ${row.id}`, 180)}`,
    row.source ? `来源：${row.source}` : '',
    `新闻发布日期：${row.published_at || 'unknown'}`,
    `事件时间：${[row.event_time_start, row.event_time_end].filter(Boolean).join(' 至 ') || 'unknown'}`,
    `采集时间：${row.collected_at || 'unknown'}`,
    `更新时间：${row.updated_at || 'unknown'}`,
    row.id ? `ID：${row.id}` : '',
    row.abstract ? `摘要：${compactAssistantText(row.abstract, 260)}` : '',
    row.url ? `URL：${row.url}` : '',
  ].filter(Boolean).join('；')
}

function buildSearchAssistantContext() {
  const selectedItems = filteredNewsList.value.filter((item) => item.selected)
  const materials = (selectedItems.length ? selectedItems : filteredNewsList.value.slice(0, 12)).slice(0, 24)
  const hasQuery = Boolean(searchParams.topic || searchParams.mustInclude || searchParams.anyInclude)
  const queryLines = [
    `主关键词：${searchParams.topic || '未指定'}`,
    `必须包含：${searchParams.mustInclude || '无'}`,
    `任意包含：${searchParams.anyInclude || '无'}`,
    `需要排除：${searchParams.needExclude || '无'}`,
    `${timeFilterLabel.value}：${searchParams.publishTime || '不限'}${searchParams.startTime || searchParams.endTime ? `（${searchParams.startTime || '起'} 至 ${searchParams.endTime || '止'}）` : ''}`,
    `命中位置：${searchParams.hitLocation || '全文'}`,
    `检索模式：${searchParams.mode || 'exact'}`,
    `搜索类型：${searchParams.searchType || 'news'}`,
    `数据源：${searchParams.dataSource || '全部'}`,
    `语言：${searchParams.language || '全部'}`,
    `排序：${searchSortLabel.value}`,
    `当前文件夹：${displayFolderName(themeName.value)}，收藏 ${favoriteCards.value.length} 条`,
    `当前结果：${totalItems.value} 条；发送素材：${materials.length} 条${selectedItems.length ? '（用户勾选）' : '（当前页前列）'}`,
  ]
  const materialLines = materials.map(searchAssistantMaterial)
  const visibleMessage = hasQuery || materials.length
    ? `基于当前检索「${searchParams.topic || searchParams.mustInclude || searchParams.anyInclude || activeSearchSurfaceLabel.value}」做研判`
    : '请协助我制定检索方向，并在本页发起搜索'
  const prompt = [
    '【数据搜索页上下文】',
    ...queryLines,
    '',
    '【侧栏联动能力】',
    '你现在嵌入在数据搜索页侧栏内。需要补充事实时，请直接调用平台新闻/事件检索工具；页面会接收你的检索关键词，同步填入主关键词并执行本页搜索。',
    '不要要求用户跳转到数据助手页面；请在当前页完成检索、解释结果、给出下一步关键词。',
    '',
    '【候选新闻/事件素材】',
    ...(materialLines.length ? materialLines : ['当前页面暂无结果素材。请先基于当前检索条件规划关键词并调用检索工具，必要时再向用户确认方向。']),
    '',
    '【用户问题】',
    '请基于当前检索条件和素材，判断主要事件脉络、关键主体、风险信号、可继续检索的关键词，并给出一段可用于简报/报告的结构化结论。',
  ].join('\n')
  return { visibleMessage, prompt, mode: 'expert' }
}

function pickAssistantInputValue(input, keys) {
  if (!input || typeof input !== 'object') return ''
  for (const key of keys) {
    const value = input[key]
    if (Array.isArray(value)) {
      const joined = value.map((item) => String(item || '').trim()).filter(Boolean).join(' ')
      if (joined) return joined
    } else if (value != null && String(value).trim()) {
      return String(value).trim()
    }
  }
  return ''
}

function waitForAssistantSearchIdle(maxTicks = 240) {
  if (!isLoading.value) return Promise.resolve(true)
  return new Promise((resolve) => {
    let ticks = 0
    const timer = window.setInterval(() => {
      ticks += 1
      if (!isLoading.value) {
        window.clearInterval(timer)
        resolve(true)
      } else if (ticks >= maxTicks) {
        window.clearInterval(timer)
        resolve(false)
      }
    }, 250)
  })
}

function handleAssistantPageAction(action) {
  assistantPageActionChain = assistantPageActionChain
    .then(() => runAssistantPageAction(action))
    .catch((error) => {
      console.warn('数据助手页面联动失败:', error)
      ElMessage.error('数据助手联动搜索失败')
    })
}

async function runAssistantPageAction(action) {
  if (!action || action.type !== 'search') return
  const query = String(action.query || '').replace(/\s+/g, ' ').trim()
  if (!query) return
  const input = action.input && typeof action.input === 'object' ? action.input : {}
  const tool = String(action.tool || '').toLowerCase()
  const level = pickAssistantInputValue(input, ['level']).toLowerCase()
  const key = `${tool}:${query}:${pickAssistantInputValue(input, ['search_type', 'searchType', 'type'])}:${level}`
  const now = Date.now()
  if (lastAssistantSearchKey.value === key && now - lastAssistantSearchAt.value < 3000) return
  lastAssistantSearchKey.value = key
  lastAssistantSearchAt.value = now

  const ready = await waitForAssistantSearchIdle()
  if (!ready) {
    ElMessage.warning('当前搜索仍在执行，请稍后重试数据助手联动检索')
    return
  }

  searchParams.topic = query
  searchParams.mustInclude = pickAssistantInputValue(input, ['must_include', 'mustInclude']) || ''
  searchParams.anyInclude = pickAssistantInputValue(input, ['any_include', 'anyInclude']) || ''
  searchParams.needExclude = pickAssistantInputValue(input, ['need_exclude', 'needExclude']) || ''
  searchParams.page = 1
  currentPage.value = 1
  jumpPage.value = 1

  let searchType = pickAssistantInputValue(input, ['search_type', 'searchType', 'type'])
  if (!searchType && tool === 'event_cluster_search') searchType = 'l1'
  if (!searchType && tool === 'macro_event_search') searchType = level === 'l3' ? 'l3' : 'l2'
  if (!searchType && tool === 'news_search') searchType = 'news'
  if (['news', 'l1', 'l2', 'l3'].includes(searchType)) searchParams.searchType = searchType
  const hitLocation = pickAssistantInputValue(input, ['hit_location', 'hitLocation'])
  if (hitLocation) searchParams.hitLocation = hitLocation
  const dataSource = pickAssistantInputValue(input, ['data_source', 'dataSource'])
  if (dataSource) searchParams.dataSource = dataSource
  const language = pickAssistantInputValue(input, ['language', 'lang'])
  if (language) searchParams.language = language
  const site = pickAssistantInputValue(input, ['site', 'domain'])
  if (site) searchParams.site = site

  await performSearch()
  if (searchError.value) {
    ElMessage.error(`数据助手检索失败：${searchError.value}`)
  } else {
    ElMessage.success(`已按数据助手检索：${query}`)
  }
}

function sendSearchToAssistant() {
  const context = buildSearchAssistantContext()
  if (!writeAssistantSessionValue(ASSISTANT_AUTORUN_CONTEXT_KEY, JSON.stringify(context))) {
    criticalActionStatus.value = '数据助手未打开：浏览器阻止了会话存储，无法安全保留当前研判上下文。'
    ElMessage.error('无法保留研判上下文，请允许站点会话存储后重试')
    return
  }
  if (!hasStoredAccessToken()) {
    if (!writeAssistantSessionValue(PENDING_ASSISTANT_OPEN_KEY, '1')) {
      criticalActionStatus.value = '研判上下文已保留，但无法标记登录后自动恢复；请允许会话存储后重试。'
      ElMessage.error('无法设置登录后自动恢复，请允许站点会话存储后重试')
      return
    }
    criticalActionStatus.value = '研判上下文已保留；登录成功后会在当前搜索页继续打开数据助手。'
    ElMessage.warning('请先登录；当前检索与勾选素材已保留')
    window.dispatchEvent(new CustomEvent('showLoginModal'))
    return
  }
  lastAssistantSearchKey.value = ''
  lastAssistantSearchAt.value = 0
  removeAssistantSessionValue(PENDING_ASSISTANT_OPEN_KEY)
  assistantDrawerKey.value += 1
  assistantDrawerOpen.value = true
  criticalActionStatus.value = '数据助手已打开，并已载入当前检索上下文。'
}

const loadFilterOptions = async () => {
  const signal = auxiliaryRequestController.signal
  try {
    const opt = await searchApi.getSearchOptions({ signal })
    languageOptions.value = Array.isArray(opt.language_options)
      ? opt.language_options
          .filter((x) => x && x.id !== undefined && x.name)
          .map((x) => ({ id: String(x.id), name: String(x.name) }))
      : []
    languages.value = languageOptions.value.map((x) => x.name)
    dataSources.value = Array.isArray(opt.data_sources) ? opt.data_sources : []
    sites.value = Array.isArray(opt.sites) ? opt.sites : []
    return
  } catch (optionsError) {
    if (isSearchAbortError(optionsError, signal)) return
  }

  // 兼容旧后端：兜底从 stats 衍生语言选项
  try {
    const data = await searchApi.getStats({ signal })
    const list = data.language_stats || []
    languageOptions.value = list
      .filter((x) => x && x.id !== undefined && x.name)
      .map((x) => ({ id: String(x.id), name: String(x.name) }))
    languages.value = languageOptions.value.map((x) => x.name)
  } catch (statsError) {
    if (!isSearchAbortError(statsError, signal)) {
      console.error('加载语言选项失败:', statsError)
    }
  }
}

const isFavorited = (newsId) => favoriteNewsIds.value.includes(Number(newsId))

const findNewsItemById = (newsId) => {
  const id = Number(newsId)
  if (!Number.isFinite(id)) return null
  const lists = [filteredNewsList.value || [], searchResults.value || [], newsList.value || []]
  for (const list of lists) {
    const found = list.find((x) => Number(x?.id) === id)
    if (found) return found
  }
  const hierarchyItems = Object.values(l2Children.value || {}).flat()
  return hierarchyItems.find((x) => Number(x?.id) === id) || null
}

const loadFavorites = async () => {
  try {
    loadFavoriteCards()
    if (!localStorage.getItem('access_token')) return

    const data = await searchApi.listFavorites({
      signal: auxiliaryRequestController.signal,
    })
    let items = Array.isArray(data.items) ? data.items : []
    if (items.length === 0 && Array.isArray(data.news_ids) && data.news_ids.length) {
      const tp0 = normalizeFolderName(themeName.value)
      items = data.news_ids
        .map((id) => ({
          news_id: Number(id),
          topic: tp0,
          kind: 'favorite',
        }))
        .filter((x) => Number.isFinite(x.news_id))
    }
    const favMap = { ...getFavoritesMap() }

    const mergeRow = (map, tp, nid, defaults) => {
      const arr = Array.isArray(map[tp]) ? [...map[tp]] : []
      if (!arr.some((x) => Number(x?.id) === nid)) {
        arr.push({ id: nid, title: '无标题', source: '', displayTime: '', ...defaults })
      }
      map[tp] = arr
    }

    for (const it of items) {
      const nid = Number(it.news_id)
      if (!Number.isFinite(nid)) continue
      const tp =
        it.topic !== undefined && it.topic !== null && String(it.topic).trim() !== ''
          ? String(it.topic).trim()
          : DEFAULT_FOLDER_NAME
      const kind = String(it.kind || 'favorite').toLowerCase()
      if (kind !== 'warning') {
        mergeRow(favMap, tp, nid, { topic: tp })
      }
    }
    setFavoritesMap(favMap)
    const extraTopics = new Set(themeList.value)
    for (const it of items) {
      const kind = String(it.kind || 'favorite').toLowerCase()
      if (kind === 'warning') continue
      const tp =
        it.topic !== undefined && it.topic !== null && String(it.topic).trim() !== ''
          ? String(it.topic).trim()
          : DEFAULT_FOLDER_NAME
      extraTopics.add(tp)
    }
    themeList.value = [...extraTopics].filter(Boolean)
    saveThemeListToStorage()
    loadFavoriteCards()
  } catch (e) {
    if (!isSearchAbortError(e, auxiliaryRequestController.signal)) {
      console.error('加载收藏失败:', e)
    }
  }
}

const toggleFavorite = async (newsId) => {
  try {
    const normalizedId = Number(newsId)
    const topic = normalizeFolderName(themeName.value)
    const signedIn = hasStoredAccessToken()
    const wasFavorited = favoriteNewsIds.value.includes(normalizedId)
    const nowFavorited = !wasFavorited

    if (nowFavorited) {
      if (!favoriteNewsIds.value.includes(normalizedId)) {
        favoriteNewsIds.value = [...favoriteNewsIds.value, normalizedId]
      }
      const item = findNewsItemById(normalizedId)
      if (item) {
        upsertReportFavorite(
          {
            id: Number(item.id),
            title: item.title,
            abstract: item.abstract,
            desc: item.desc,
            source: item.source,
            time_semantics: item.time_semantics,
            timeSemantics: normalizeNewsTimeSemantics(item),
            location: item.location,
            request_url: item.request_url || item.requestUrl || item.displayUrl,
            displayDesc: item.displayDesc,
            displayUrl: item.displayUrl,
            language: resolveLanguageName(item),
          },
          topic,
        )
      }
      loadFavoriteCards()
      if (signedIn) {
        criticalActionStatus.value = `已在本地收藏到「${topic}」；正在同步账户状态。`
        ElMessage.info(`已收藏到「${topic}」，正在同步`)
      } else {
        criticalActionStatus.value = `已临时收藏到「${topic}」；内容仅保存在此浏览器，登录后才可跨设备同步。`
        ElMessage.info(`已临时收藏到「${topic}」（仅此浏览器）`)
      }
    } else {
      favoriteNewsIds.value = favoriteNewsIds.value.filter((id) => id !== normalizedId)
      removeReportFavorite(normalizedId, topic)
      loadFavoriteCards()
      criticalActionStatus.value = signedIn
        ? `已从「${topic}」移除；正在同步账户状态。`
        : `已从本浏览器的临时收藏「${topic}」移除。`
      if (signedIn) ElMessage.info(`已从「${topic}」移除，正在同步`)
      else ElMessage.success(`已从「${topic}」移除`)
    }

    if (signedIn) {
      try {
        await searchApi.toggleFavorite(
          {
            news_id: normalizedId,
            topic,
            kind: 'favorite',
          },
          { signal: auxiliaryRequestController.signal },
        )
        criticalActionStatus.value = nowFavorited
          ? `收藏成功：已同步到账号文件夹「${topic}」。`
          : `移除成功：账号文件夹「${topic}」已同步。`
      } catch (syncError) {
        if (!isSearchAbortError(syncError, auxiliaryRequestController.signal)) {
          criticalActionStatus.value = '本地收藏已更新，但账号同步失败；请检查网络或重新登录后重试。'
          ElMessage.warning(syncError.message || '收藏同步请求失败，请检查网络或重新登录')
        }
      }
    }
  } catch (e) {
    console.error('收藏操作失败:', e)
    criticalActionStatus.value = '收藏操作失败：本地状态未能可靠更新，请稍后重试。'
    ElMessage.error('收藏操作失败，请稍后重试')
  }
}

const loadFavoriteCards = () => {
  const t = normalizeFolderName(themeName.value)
  const normalCards = getReportFavoritesForTopic(t).map((x) => ({ ...x, cardType: 'favorite' }))
  favoriteCards.value = normalCards

  favoriteNewsIds.value = normalCards
    .map((x) => Number(x?.id))
    .filter((id) => Number.isFinite(id))
  favoriteMapVersion.value += 1
}

const removeFavoriteCard = async (newsId) => {
  const normalizedId = Number(newsId)
  if (!Number.isFinite(normalizedId)) return
  const topic = normalizeFolderName(themeName.value)
  try {
    favoriteNewsIds.value = favoriteNewsIds.value.filter((id) => id !== normalizedId)
    removeReportFavorite(normalizedId, topic)
    loadFavoriteCards()
  } catch (error) {
    console.error('移除本地收藏失败:', error)
    criticalActionStatus.value = '移除失败：本浏览器的收藏状态未能可靠更新，请稍后重试。'
    ElMessage.error('移除收藏失败，请稍后重试')
    return
  }
  if (hasStoredAccessToken()) {
    criticalActionStatus.value = `已从本地文件夹「${topic}」移除；正在同步账户状态。`
    ElMessage.info(`已从「${topic}」移除，正在同步`)
    try {
      await searchApi.removeFavorite(
        {
          news_id: normalizedId,
          topic,
          kind: 'favorite',
        },
        { signal: auxiliaryRequestController.signal },
      )
      criticalActionStatus.value = `移除成功：账号文件夹「${topic}」已同步。`
    } catch (error) {
      if (isSearchAbortError(error, auxiliaryRequestController.signal)) return
      console.error('移除收藏同步失败:', error)
      criticalActionStatus.value = '已从本浏览器移除，但账号同步失败；请检查网络后重试。'
      ElMessage.warning('本地已移除，但账号同步失败，请检查网络后重试')
    }
  } else {
    criticalActionStatus.value = `已从本浏览器的临时收藏「${topic}」移除。`
    ElMessage.success(`已从临时收藏「${topic}」移除`)
  }
}

const saveThemeListToStorage = () => {
  themeStorage.saveThemes(themeList.value)
}

const loadThemeListFromStorage = () => {
  themeList.value = themeStorage.loadThemes(themeName.value)
}

const switchTheme = (t) => {
  const name = String(t || '').trim()
  if (!name) return
  themeName.value = name
  themeNameDraft.value = name
  themeStorage.saveCurrent(name)
  loadFavoriteCards()
}

const deleteTheme = async (topic) => {
  const t = String(topic || '').trim()
  if (!t) return
  if (themeList.value.length <= 1) {
    ElMessage.warning('至少保留一个文件夹')
    return
  }

  const favs = getReportFavoritesForTopic(t)
  const warns = getWarningsForTopic(t)
  if (localStorage.getItem('access_token')) {
    for (const x of favs) {
      const id = Number(x?.id)
      if (!Number.isFinite(id)) continue
      try {
        await searchApi.removeFavorite(
          { news_id: id, topic: t, kind: 'favorite' },
          { signal: auxiliaryRequestController.signal },
        )
      } catch {
        // Continue deleting the local folder if remote cleanup is unavailable.
      }
    }
    for (const x of warns) {
      const id = Number(x?.id)
      if (!Number.isFinite(id)) continue
      try {
        await searchApi.removeFavorite(
          { news_id: id, topic: t, kind: 'warning' },
          { signal: auxiliaryRequestController.signal },
        )
      } catch {
        // Continue deleting the local folder if remote cleanup is unavailable.
      }
    }
  }

  deleteThemeData(t)
  themeList.value = themeList.value.filter((x) => x !== t)
  saveThemeListToStorage()
  if (themeName.value === t) {
    const next = themeList.value[0] || DEFAULT_FOLDER_NAME
    themeName.value = next
    themeNameDraft.value = next
    themeStorage.saveCurrent(next)
  }
  loadFavoriteCards()
  ElMessage.success('已删除文件夹')
  if (isInitialized.value) {
    if (isSearchMode.value) void performSearch()
    else void loadPageData(currentPage.value)
  }
}

const addNewTheme = () => {
  const now = new Date()
  const name = createSearchThemeName(themeList.value, now)
  themeList.value = [...themeList.value, name]
  saveThemeListToStorage()
  themeName.value = name
  themeNameDraft.value = name
  themeStorage.saveCurrent(name)
  loadFavoriteCards()
  ElMessage.success(`已新增并切换到「${name}」，星标会保存到当前文件夹`)
  if (isInitialized.value) {
    if (isSearchMode.value) void performSearch()
    else void loadPageData(currentPage.value)
  }
}

const startEditThemeName = () => {
  themeNameDraft.value = displayFolderName(themeName.value)
  isEditingTheme.value = true
}

const saveThemeName = () => {
  const prev = normalizeFolderName(themeName.value)
  const next = normalizeFolderName(themeNameDraft.value)
  if (prev !== next) {
    const fav = { ...getFavoritesMap() }
    if (fav[prev]) {
      fav[next] = fav[prev]
      delete fav[prev]
      setFavoritesMap(fav)
    }
    const w = { ...getWarningsMap() }
    if (w[prev]) {
      w[next] = w[prev]
      delete w[prev]
      setWarningsMap(w)
    }
    themeList.value = renameSearchThemeList(themeList.value, prev, next)
    saveThemeListToStorage()
  }
  themeName.value = next
  themeStorage.saveCurrent(themeName.value)
  isEditingTheme.value = false
  loadFavoriteCards()
}

watch(themeName, () => {
  loadFavoriteCards()
  if (!isInitialized.value) return
  if (isSearchMode.value) void performSearch()
  else void loadPageData(currentPage.value)
})

// 监听 newsList 变化
watch(
  newsList,
  () => {
    if (!isSearchMode.value) {
      filterFirstRelease()
    }
  },
  { deep: true },
)

const totalPages = computed(() => {
  return calculateTotalPages(totalItems.value, pageSize.value)
})

const visiblePages = computed(() => {
  return buildVisiblePages(currentPage.value, totalPages.value)
})

// 监听登录成功事件，重新加载收藏
const handleLoginSuccess = () => {
  loadFavorites()
  loadFavoriteCards()
  if (readAssistantSessionValue(PENDING_ASSISTANT_OPEN_KEY) === '1') {
    removeAssistantSessionValue(PENDING_ASSISTANT_OPEN_KEY)
    assistantDrawerKey.value += 1
    assistantDrawerOpen.value = true
    criticalActionStatus.value = '登录成功：已恢复登录前保留的检索上下文并打开数据助手。'
  }
}

const handleReportFavoritesUpdated = () => {
  loadFavoriteCards()
}

onMounted(async () => {
  themeName.value = themeStorage.readCurrent()
  themeNameDraft.value = themeName.value
  loadThemeListFromStorage()
  jumpPage.value = currentPage.value
  const filterOptionsPromise = loadFilterOptions()
  const topicFromRoute = route.query.topic || route.query.q
  const topicStr = topicFromRoute != null ? String(topicFromRoute).trim() : ''
  let mainDataPromise
  if (topicStr) {
    searchParams.topic = topicStr
    mainDataPromise = performSearch()
  } else {
    mainDataPromise = loadPageData(1)
  }
  await Promise.allSettled([filterOptionsPromise, mainDataPromise])
  void loadFavorites()
  isInitialized.value = true
  if (hasStoredAccessToken() && readAssistantSessionValue(PENDING_ASSISTANT_OPEN_KEY) === '1') {
    removeAssistantSessionValue(PENDING_ASSISTANT_OPEN_KEY)
    assistantDrawerKey.value += 1
    assistantDrawerOpen.value = true
    criticalActionStatus.value = '已恢复登录前保留的检索上下文并打开数据助手。'
  }
  // 监听登录成功事件
  window.addEventListener('loginSuccess', handleLoginSuccess)
  window.addEventListener('reportFavoritesUpdated', handleReportFavoritesUpdated)
})

onUnmounted(() => {
  dataRequest.cancel()
  auxiliaryRequestController.abort()
  removeClusterPanelEsc()
  unlockBodyScrollForClusterPanel()
  // 移除事件监听
  window.removeEventListener('loginSuccess', handleLoginSuccess)
  window.removeEventListener('reportFavoritesUpdated', handleReportFavoritesUpdated)
})
</script>

<style src="./data-search.less" scoped lang="less"></style>
