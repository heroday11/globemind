<template>
  <!-- 舆情分析系统主容器 -->
  <div class="app-root">
    <h1 class="sentiment-sr-only">智能舆情分析</h1>
    <div class="bg-grid" />

    <!-- 顶部导航栏 -->
    <nav class="top-nav" data-tour="sentiment-workspace">
      <div class="nav-left" data-tour="sentiment-search">
        <button class="history-toggle" @click="leftDrawerOpen = !leftDrawerOpen" title="搜索记录">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <path d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span>搜索记录</span>
        </button>
        <div class="search-container">
          <input
            v-model="searchQuery"
            class="search-bar"
            placeholder="检索目标、事件、关键字..."
            @keyup.enter="handleSearch"
          />
          <button class="search-btn" @click="handleSearch" title="搜索">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <circle cx="11" cy="11" r="8"></circle>
              <path d="m21 21-4.35-4.35"></path>
            </svg>
          </button>
        </div>
      </div>

      <div class="nav-right" data-tour="sentiment-range">
        <div class="control-group date-picker-wrap" @wheel.prevent="onDateWheel">
          <span class="control-label">起始日:</span>
          <input type="date" v-model="startDate" class="control-date-input" />
        </div>
        <div class="control-group">
          <span class="control-label">结束日:</span>
          <input type="date" v-model="endDateModel" class="control-date-input" />
        </div>
        <div class="control-group">
          <span class="control-label">视距:</span>
          <select class="control-select" v-model="timeRange">
            <option value="7d">近 7 天</option>
            <option value="30d">近 1 个月</option>
            <option value="90d">近 3 个月</option>
            <option value="365d">全年 (365天)</option>
            <option value="custom">自定义</option>
          </select>
          <input
            v-if="timeRange === 'custom'"
            v-model.number="customDays"
            type="number"
            min="1"
            max="365"
            class="control-input control-days-input"
            title="自定义天数"
          />
          <span v-if="timeRange === 'custom'" class="control-label">天</span>
        </div>
      </div>
    </nav>

    <section
      class="quality-status-bar"
      :class="{ 'quality-status-bar--unavailable': !opinionTrust.computable }"
      data-tour="sentiment-quality"
      :aria-label="`数据质量摘要：${opinionTrust.headline}`"
      aria-live="polite"
    >
      <div class="quality-status-title">
        <span class="quality-dot" aria-hidden="true" />
        <b>{{ opinionTrust.headline }}</b>
      </div>
      <span v-if="!opinionTrust.computable" class="quality-trust-reason">
        {{ opinionTrust.detail }}
      </span>
      <dl class="quality-status-list">
        <div>
          <dt>数据截止</dt>
          <dd>{{ opinionTrust.cutoffDate }}</dd>
        </div>
        <div>
          <dt>最近日期覆盖</dt>
          <dd>{{ qualitySnapshot.coverageDate }} · {{ qualitySnapshot.latestCoverage }}</dd>
        </div>
        <div>
          <dt>近 30 天反馈</dt>
          <dd>{{ qualitySnapshot.pendingFeedback }}</dd>
        </div>
        <div class="quality-status-method">
          <dt>评分方法</dt>
          <dd>{{ opinionTrust.methodVersion !== '--' ? opinionTrust.methodVersion : qualitySnapshot.methodVersion }}</dd>
        </div>
      </dl>
      <button type="button" @click="openDiagnostics('quality')">质量明细</button>
    </section>

    <details class="semantic-method-card">
      <summary>三维语义方法卡 · {{ semanticMethod.contractVersion }}</summary>
      <div class="semantic-method-card__grid">
        <span><b>目标立场</b> 文章模型输出范围 [-1, 1]（无量纲）；聚合方法输出范围 [-100, 100]（指数点）；模型 {{ semanticMethod.stanceModel }}</span>
        <span><b>文本语气</b> 枚举 positive / neutral / negative / mixed / unknown；量纲与来源模型未建立</span>
        <span><b>现实影响</b> 方向 positive / neutral / negative / mixed / unknown；强度量纲与来源模型未建立</span>
      </div>
      <p>
        本合同仅约束响应投影：当前三维不在展示层组合；不从立场推断语气或现实影响，也不反向推断。
        上游轴独立性 {{ semanticMethod.upstreamAxisIndependenceState }}；质量状态
        {{ semanticMethod.qualityState }}；事实核验状态 {{ semanticMethod.factTruthState }}。
      </p>
    </details>

    <!-- 主内容区域 -->
    <div class="dashboard-layout">
      <!-- 宏观数据卡片区域：显示综合指标、情报短报、高频议题 -->
      <div class="macro-grid" data-tour="sentiment-overview">
        <!-- 卡片1：门禁与语义合同均通过后显示加权目标立场 -->
        <div class="macro-card risk-card">
          <div class="risk-card-head">
            <div class="card-title">
              <svg
                class="card-title-icon"
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <path
                  d="M12 2l3.09 6.26L22 9.27l-5 4.87L18.18 22 12 18.56 5.82 22 7 14.14 2 9.27l6.91-1.01L12 2z"
                />
              </svg>
              涉华目标立场概览
            </div>
            <span class="risk-card-date">{{ overviewLatestDate }}</span>
          </div>

          <div class="risk-card-hero">
            <div
              class="risk-score-panel insight-trigger"
              :class="overviewScoreClass"
              role="button"
              tabindex="0"
              title="查看目标立场相关新闻"
              @click="openRiskInsight()"
              @keydown.enter.prevent="openRiskInsight()"
            >
              <span class="risk-score-label">加权目标立场指数</span>
              <strong>{{ formatSemanticAxis(overviewSemantics.stance) }}</strong>
              <span class="risk-score-note">
                {{ overviewSemantics.stance.state === 'available' ? '指数点 · 非现实影响' : '未知' }}
              </span>
            </div>
            <div
              class="risk-event-panel insight-trigger"
              role="button"
              tabindex="0"
              title="查看主题事件相关新闻"
              @click="openEventInsight"
              @keydown.enter.prevent="openEventInsight"
            >
              <span class="risk-event-label">主题事件</span>
              <b class="risk-event-title">{{ overviewTopEventTitle }}</b>
              <div class="risk-event-trend">
                <span>立场走向</span>
                <b class="trend" :class="overviewTrendClass">{{ overviewSummary.trend_label }}</b>
              </div>
            </div>
          </div>

          <div class="semantic-axis-strip" aria-label="三维语义状态">
            <span><b>目标立场</b> {{ formatSemanticAxis(overviewSemantics.stance, { includeUnit: true }) }}</span>
            <span><b>文本语气</b> {{ formatSemanticAxis(overviewSemantics.tone) }}</span>
            <span><b>现实影响</b> {{ formatSemanticAxis(overviewSemantics.impact) }}</span>
          </div>

          <div class="target-indices-wrapper risk-indices-wrapper">
            <div class="target-indices">
              <span
                v-for="item in overviewReadableIndices"
                :key="item.label"
                class="index-item insight-trigger"
                :class="item.state"
                role="button"
                tabindex="0"
                :title="`查看${item.displayLabel}相关新闻`"
                @click="openRiskInsight(item)"
                @keydown.enter.prevent="openRiskInsight(item)"
              >
                <span class="index-badge">{{ item.displayLabel }}</span>
                <span class="index-value">{{ formatSemanticAxis(opinionSemanticSnapshot(item).stance) }}</span>
                <span class="index-copy">{{ item.description }}</span>
                <svg
                  v-if="opinionSemanticSnapshot(item).stance.state === 'available' && item.trend_values && item.trend_values.length > 1"
                  class="index-spark"
                  :class="item.state === 'negative' ? 'index-spark--neg' : 'index-spark--warn'"
                  viewBox="0 0 96 28"
                  aria-hidden="true"
                >
                  <polyline :points="sparklinePoints(item.trend_values, 96, 28)" />
                </svg>
              </span>
            </div>
          </div>
          <div class="macro-metrics">
            <div v-for="metric in overviewMetrics" :key="metric.label" class="metric">
              <span class="metric__label">{{ metric.label }}</span>
              <span
                class="metric__value"
                :class="{
                  'metric__value--neg': metric.display_tone === 'neg',
                  'metric__value--pos': metric.display_tone === 'pos',
                }"
                >{{ metric.value }}</span
              >
            </div>
          </div>
        </div>
        <!-- 卡片2：情报截获短报 - 显示最新的情报信息 -->
        <div class="macro-card">
          <div class="card-title">
            <svg
              class="card-title-icon"
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <rect x="2" y="3" width="20" height="14" rx="2" />
              <line x1="8" y1="21" x2="16" y2="21" />
              <line x1="12" y1="17" x2="12" y2="21" />
            </svg>
            情报截获短报
          </div>
          <ul class="intel-brief">
            <li
              v-for="brief in overviewBriefs"
              :key="brief.id"
              class="brief-item insight-trigger"
              :class="briefClass(brief.severity)"
              role="button"
              tabindex="0"
              title="查看短报详情"
              @click="openBriefInsight(brief)"
              @keydown.enter.prevent="openBriefInsight(brief)"
            >
              <span class="brief-time">{{ formatOverviewTime(brief.time) }}</span>
              <span class="brief-badge">{{ briefSeverityLabel(brief.severity) }}</span>
              <span class="brief-icon">{{ stanceGlyph(brief) }}</span>
              <span class="brief-body">
                <span class="brief-main" :title="brief.title">{{ brief.title }}</span>
                <span class="brief-meta"
                  >{{ brief.source || 'unknown' }} · {{ formatFamilyName(brief.event_family || 'general') }} ·
                  {{ brief.confidence == null ? '--' : `${Math.round(brief.confidence * 100)}%` }}</span
                >
              </span>
            </li>
          </ul>
        </div>
        <!-- 卡片3：高频敏感词 - 显示当前热门的敏感关键词标签 -->
        <div class="macro-card">
          <div class="card-title">
            <svg
              class="card-title-icon"
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path
                d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"
              />
              <line x1="7" y1="7" x2="7.01" y2="7" />
            </svg>
            高频敏感词
          </div>
          <div class="tags-container-wrapper">
            <div class="tags-container">
              <span
                v-for="(tag, index) in overviewTags"
                :key="tag.event_family"
                class="tag"
                :class="[
                  index === 0 ? 'tag--top1' : index === 1 ? 'tag--top2' : index === 2 ? 'tag--top3' : '',
                  stanceVariant(tag, 'tag--state-down', 'tag--state-up'),
                  'insight-trigger',
                ]"
                :style="tagStyle(index)"
                role="button"
                tabindex="0"
                :title="`查看${formatFamilyName(tag.event_family)}相关新闻`"
                @click="openTagInsight(tag)"
                @keydown.enter.prevent="openTagInsight(tag)"
              >
                <span class="tag-icon">{{ index + 1 }}</span>
                <span class="tag-body">
                  <span class="tag-text">{{ formatFamilyName(tag.event_family) }}</span>
                  <span class="tag-spark"
                    ><span
                      class="tag-spark-bar"
                      :style="{ width: tagBarWidth(tag.article_count) }"
                    ></span
                  ></span>
                </span>
                <span
                  class="tag-trend"
                  :class="stanceVariant(tag, 'tag-trend--down', 'tag-trend--up')"
                  >{{ formatSemanticAxis(opinionSemanticSnapshot(tag).stance) }}</span
                >
                <span class="tag-count">{{ formatCompactCount(tag.article_count) }}<span class="tag-unit"> 篇</span></span>
              </span>
            </div>
          </div>
          <div class="tags-summary">
            <span class="tags-summary__item">最新日期 <b>{{ overviewLatestDate }}</b></span>
            <span class="tags-summary__dot" aria-hidden="true">·</span>
            <span class="tags-summary__item">涉华报道 <b>{{ formatCompactCount(overviewSummary.article_count) }}</b></span>
            <span class="tags-summary__dot" aria-hidden="true">·</span>
            <span class="tags-summary__item">信源 <b>{{ formatCompactCount(overviewSummary.source_count) }}</b></span>
          </div>
        </div>
      </div>

      <!-- 图表容器：加权目标立场指数折线图 -->
      <div class="chart-container" data-tour="sentiment-chart" :class="{ 'chart-container--fullscreen': chartFullscreen }">
        <div v-if="opinionLoading" class="chart-loading">加载中…</div>
        <div
          v-else-if="opinionError && !opinionTrendData.dates.length"
          class="chart-loading chart-error"
        >
          {{ opinionError }}
        </div>
        <div
          v-else-if="!opinionTrust.computable"
          class="chart-loading chart-trust-unavailable"
          role="status"
        >
          <strong>目标立场趋势暂不出分</strong>
          <span>{{ opinionTrust.detail }}；历史证据仍可通过新闻与质量明细核验。</span>
        </div>
        <div ref="chartRef" class="chart-wrapper"></div>
        <button
          class="chart-fullscreen-btn"
          @click="toggleFullscreen"
          :title="chartFullscreen ? '退出全屏' : '全屏'"
          :style="{
            opacity: chartAnimating ? 0 : 1,
            pointerEvents: chartAnimating ? 'none' : 'auto',
          }"
        >
          <svg
            v-if="!chartFullscreen"
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
          >
            <path d="M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4" />
          </svg>
          <svg
            v-else
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
          >
            <path d="M9 9L4 4M15 9l5-5M9 15L4 20M15 15l5 5" />
          </svg>
        </button>
        <!-- 情感方向切换 -->
        <SentimentFilterToggle
          :model-value="sentimentFilter"
          @select="onSentimentFilterChange"
        />
        <div class="chart-diagnostic-tools">
          <button type="button" title="维度对比" @click="openDiagnostics('dimensions')">
            <span>维度</span>
          </button>
          <button type="button" title="数据质量" @click="openDiagnostics('quality')">
            <span>质量</span>
          </button>
        </div>
      </div>
    </div>

    <!-- 左侧栏：搜索记录 + 本次搜索结果 -->
    <div class="drawer drawer-left" :class="{ open: leftDrawerOpen }">
      <div class="drawer-header">
        <h2 class="drawer-title">搜索记录</h2>
        <button class="drawer-close" @click="leftDrawerOpen = false" title="关闭">✕</button>
      </div>
      <div class="left-drawer-body">
        <div
          class="drawer-content drawer-list search-history-panel"
          :class="{ 'search-history-panel--compact': searchResults.length > 0 || searchLoading || searchError }"
        >
          <div class="drawer-panel-head">
            <span>历史检索</span>
            <b>{{ searchHistory.length }}</b>
          </div>
          <p v-if="searchHistory.length === 0 && !searchLoading" class="drawer-empty">暂无搜索记录</p>
          <button
            v-for="(item, index) in searchHistory"
            :key="'h-' + index"
            type="button"
            class="search-history-item"
            :disabled="searchLoading"
            @click="handleHistorySearch(item)"
          >
            <span class="search-history-query">{{ item.query }}</span>
            <span class="search-history-time">{{ item.time }}</span>
          </button>
        </div>

        <section class="search-control-panel" aria-label="搜索设置">
          <div class="search-control-row">
            <label class="search-control-field">
              <span>时间范围</span>
              <select v-model="searchPublishTime" :disabled="searchLoading" @change="refreshSearchWithSettings">
                <option v-for="item in searchPublishTimeOptions" :key="item.value" :value="item.value">
                  {{ item.label }}
                </option>
              </select>
            </label>
            <label class="search-control-field">
              <span>搜索数量</span>
              <select v-model.number="searchPageSize" :disabled="searchLoading" @change="refreshSearchWithSettings">
                <option v-for="size in searchPageSizeOptions" :key="size" :value="size">
                  {{ size }} 条
                </option>
              </select>
            </label>
          </div>
          <div class="search-control-summary">
            <span :title="searchPanelQuery">{{ searchPanelQuery }}</span>
            <b>{{ searchPanelCountText }}</b>
          </div>
        </section>

        <!-- 本次搜索结果 -->
        <div
          v-if="searchLoading || searchResults.length > 0 || searchError"
          class="drawer-section drawer-section--results"
        >
          <div class="drawer-section-head">
            <h3 class="drawer-section-title">本次搜索结果</h3>
            <span v-if="searchResults.length">{{ searchResultRangeText }}</span>
          </div>
          <p v-if="searchLoading" class="drawer-empty">搜索中…</p>
          <p v-else-if="searchError" class="drawer-error">{{ searchError }}</p>
          <div v-else class="search-results-list">
            <article
              v-for="(item, index) in searchResults"
              :key="'r-' + item.id + '-' + index"
              class="search-result-item"
              @click="goToNewsDetail(item.id)"
            >
              <div class="search-result-header">
                <span class="search-result-title">{{ item.title || '无标题' }}</span>
                <button
                  class="favorite-btn"
                  :class="{ active: isFavorited(item.id) }"
                  @click.stop="toggleFavorite(item.id)"
                  :title="isFavorited(item.id) ? '取消收藏' : '收藏'"
                  :aria-label="isFavorited(item.id) ? '取消收藏' : '收藏'"
                >
                  {{ isFavorited(item.id) ? '★' : '☆' }}
                </button>
              </div>
              <p v-if="item.abstract || item.summary || item.body" class="search-result-snippet">
                {{ formatSearchSnippet(item) }}
              </p>
              <span class="search-result-meta"
                >{{ item.source || '' }} ·
                {{ item.pub_time ? String(item.pub_time).slice(0, 10) : '' }}</span
              >
            </article>
            <nav v-if="searchTotalPages > 1" class="search-pagination" aria-label="搜索结果分页">
              <button
                type="button"
                :disabled="searchLoading || searchPage <= 1"
                @click="changeSearchPage(searchPage - 1)"
              >
                上一页
              </button>
              <span>{{ searchPage }} / {{ searchTotalPages }}</span>
              <button
                type="button"
                :disabled="searchLoading || searchPage >= searchTotalPages"
                @click="changeSearchPage(searchPage + 1)"
              >
                下一页
              </button>
            </nav>
          </div>
        </div>

        <div v-else class="drawer-section drawer-section--empty-fill">
          <span>等待检索</span>
          <b>{{ overviewLatestDate }}</b>
        </div>
      </div>
    </div>

    <!-- 侧边栏：点击数据点时显示当天目标立场报道 -->
    <div
      class="drawer"
      :class="{ open: drawerOpen, 'drawer--beside-assistant': nodeDrawerAssistantOffset > 0 }"
      :style="nodeDrawerStyle"
      @scroll="onDrawerScroll"
    >
      <!-- 侧边栏头部：标题和关闭按钮 -->
      <div class="drawer-header">
        <h2 class="drawer-title" style="color: #0ea5e9">目标立场相关报道</h2>
        <button @click="drawerOpen = false" class="drawer-close" title="关闭">✕</button>
      </div>

      <!-- 当天目标立场报道：点击折线图节点时显示 -->
      <div v-if="drawerState === 'date_news'">
        <div class="drawer-meta">时间节点：{{ clickedDate }}</div>
        <div
          class="date-insight-panel"
          :class="{ 'date-insight-panel--anomaly': selectedDatePoint.anomaly }"
        >
          <div class="date-insight-head">
            <span>{{ dateInsightTitle }}</span>
            <b :class="selectedDatePoint.value == null ? null : selectedDatePoint.value >= 0 ? 'date-score--pos' : 'date-score--neg'">{{
              formatIndexValue(selectedDatePoint.value)
            }}</b>
          </div>
          <div class="date-insight-copy">
            <span>较前日 {{ selectedDatePoint.delta === null ? '--' : formatIndexValue(selectedDatePoint.delta) }}</span>
            <span>主导议题 {{ selectedDateExplanation.topFamily }}</span>
          </div>
          <div class="date-impact-balance" aria-hidden="true">
            <span
              class="date-impact-balance__neg"
              :style="{ width: `${selectedDateExplanation.criticalWidth}%` }"
            ></span>
            <span class="date-impact-balance__zero"></span>
            <span
              class="date-impact-balance__pos"
              :style="{ width: `${selectedDateExplanation.supportiveWidth}%` }"
            ></span>
          </div>
          <div class="date-insight-foot">
            <span>批评立场 {{ selectedDateExplanation.criticalCount }} 条</span>
            <span>支持立场 {{ selectedDateExplanation.supportiveCount }} 条</span>
            <span>未知 {{ selectedDateExplanation.unknownCount }} 条</span>
            <span>信源 {{ selectedDateExplanation.sourceCount }}</span>
            <button type="button" class="date-insight-export" @click.stop="exportDateBrief">导出</button>
          </div>
          <div v-if="selectedDateExplanation.leadingNews.length" class="date-insight-drivers">
            <span v-for="item in selectedDateExplanation.leadingNews" :key="item.id">
              {{ formatSemanticAxis(opinionSemanticSnapshot(item).stance) }} · {{ item.source || 'unknown' }}
            </span>
          </div>
        </div>
        <SentimentFilterToggle
          :model-value="eventsFilter"
          variant="drawer"
          @select="onDateNewsFilterChange"
        />
        <div v-if="dateNewsSummary" class="date-news-summary">
          <span>支持立场 {{ dateNewsSummary.positive_count || 0 }}</span>
          <span>批评立场 {{ dateNewsSummary.negative_count || 0 }}</span>
          <span>中性立场 {{ dateNewsSummary.neutral_count || 0 }}</span>
          <span>信源 {{ dateNewsSummary.source_count || 0 }}</span>
        </div>
        <p class="drawer-meta">
          结构化纠错仅作待人工复核记录，默认不用于模型训练；保留期限尚未批准，正式人审流程尚未配置。
        </p>
        <div v-if="eventNewsLoading" class="drawer-empty">加载中…</div>
        <div v-else-if="!eventNews.length" class="drawer-empty">该日期暂无涉华目标立场报道</div>
        <div v-else class="news-list">
          <div
            v-for="news in eventNews"
            :key="news.id"
            class="news-card impact-news-card"
            :class="stanceVariant(news, 'impact-news-card--pos', 'impact-news-card--neg', 'impact-news-card--unavailable')"
            @click="goToNewsDetail(news.id)"
          >
            <div class="news-card-title">{{ news.title }}</div>
            <div class="news-card-meta-row">
              <span class="news-card-date">{{ news.pub_date }}</span>
              <span class="news-card-source">{{ news.source || 'unknown' }}</span>
              <span
                class="news-card-sentiment"
                :class="stanceVariant(news, 'sent-pos', 'sent-neg')"
                >目标立场 {{ formatSemanticAxis(opinionSemanticSnapshot(news).stance) }}</span
              >
            </div>
            <div class="semantic-axis-grid">
              <span>
                <small>目标立场</small>
                <b>{{ formatSemanticAxis(opinionSemanticSnapshot(news).stance, { includeUnit: true }) }}</b>
              </span>
              <span>
                <small>文本语气</small>
                <b>{{ formatSemanticAxis(opinionSemanticSnapshot(news).tone) }}</b>
              </span>
              <span>
                <small>现实影响</small>
                <b>{{ formatSemanticAxis(opinionSemanticSnapshot(news).impact) }}</b>
              </span>
            </div>
            <div class="news-impact-meta">
              <span>{{ formatFamilyName(news.event_family) }}</span>
              <span>{{ news.initiator || '未知主体' }} → {{ news.target || '未知对象' }}</span>
              <span>置信 {{ news.confidence == null ? '--' : `${Math.round(news.confidence * 100)}%` }}</span>
            </div>
            <div class="news-correction-row" @click.stop>
              <button
                type="button"
                :class="{ active: news.feedback === 'irrelevant' }"
                :disabled="isCorrectionSubmitting(news, 'irrelevant')"
                @click="submitNewsCorrection(news, 'irrelevant')"
              >
                无关
              </button>
              <button
                type="button"
                :class="{ active: news.feedback === 'too_positive' }"
                :disabled="isCorrectionSubmitting(news, 'too_positive')"
                @click="submitNewsCorrection(news, 'too_positive')"
              >
                偏正
              </button>
              <button
                type="button"
                :class="{ active: news.feedback === 'too_negative' }"
                :disabled="isCorrectionSubmitting(news, 'too_negative')"
                @click="submitNewsCorrection(news, 'too_negative')"
              >
                偏负
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="correctionToast.open"
      class="correction-toast"
      :class="`correction-toast--${correctionToast.tone}`"
    >
      {{ correctionToast.message }}
    </div>

    <div
      v-if="insightModal.open"
      class="insight-modal-backdrop"
      @click.self="closeInsightModal"
    >
      <section class="insight-modal" role="dialog" aria-modal="true">
        <div class="insight-modal__header">
          <div>
            <span class="insight-modal__eyebrow">{{ insightModal.eyebrow }}</span>
            <h3>{{ insightModal.title }}</h3>
            <p>{{ insightModal.subtitle }}</p>
          </div>
          <button class="insight-modal__close" title="关闭" @click="closeInsightModal">✕</button>
        </div>

        <div v-if="insightModal.metrics.length" class="insight-metrics">
          <span v-for="metric in insightModal.metrics" :key="metric.label">
            <b>{{ metric.value }}</b>
            {{ metric.label }}
          </span>
        </div>

        <div v-if="insightModal.loading" class="insight-loading">加载中…</div>
        <div v-else-if="insightModal.error" class="insight-empty">{{ insightModal.error }}</div>
        <div v-else-if="!insightModal.news.length" class="insight-empty">暂无匹配新闻</div>
        <div v-else class="insight-news-list">
          <article
            v-for="news in insightModal.news"
            :key="news.id"
            class="insight-news"
            :class="stanceVariant(news, 'insight-news--pos', 'insight-news--neg')"
            @click="goToNewsDetail(news.id)"
          >
            <div class="insight-news__main">
              <h4>{{ news.title }}</h4>
              <p>
                {{ news.source || 'unknown' }} · {{ news.pub_date || formatOverviewTime(news.time) }} ·
                {{ formatFamilyName(news.event_family) }}
              </p>
            </div>
            <div class="insight-news__score">
              <b>{{ formatSemanticAxis(opinionSemanticSnapshot(news).stance) }}</b>
              <span>目标立场</span>
              <small>文本语气 {{ formatSemanticAxis(opinionSemanticSnapshot(news).tone) }}</small>
              <small>现实影响 {{ formatSemanticAxis(opinionSemanticSnapshot(news).impact) }}</small>
            </div>
          </article>
        </div>
      </section>
    </div>

    <div
      v-if="diagnosticModal.open"
      class="diagnostic-modal-backdrop"
      @click.self="closeDiagnostics"
    >
      <section class="diagnostic-modal" role="dialog" aria-modal="true">
        <div class="diagnostic-modal__header">
          <div>
            <span class="diagnostic-modal__eyebrow">舆情诊断</span>
            <h3>{{ diagnosticModal.tab === 'dimensions' ? '维度对比' : '数据质量' }}</h3>
          </div>
          <button class="diagnostic-modal__close" title="关闭" @click="closeDiagnostics">✕</button>
        </div>
        <div class="diagnostic-tabs">
          <button
            v-for="tab in diagnosticTabs"
            :key="tab.value"
            type="button"
            :class="{ active: diagnosticModal.tab === tab.value }"
            @click="switchDiagnosticTab(tab.value)"
          >
            {{ tab.label }}
          </button>
        </div>

        <div v-if="diagnosticModal.tab === 'dimensions'" class="diagnostic-content">
          <div v-if="dimensionsLoading" class="diagnostic-empty">加载中…</div>
          <div v-else-if="dimensionsError" class="diagnostic-empty">{{ dimensionsError }}</div>
          <div v-else class="dimension-grid">
            <section v-for="group in dimensionGroups" :key="group.key" class="dimension-panel">
              <div class="dimension-panel__head">
                <span>{{ group.label }}</span>
                <b>{{ group.items.length }}</b>
              </div>
              <article
                v-for="item in group.items"
                :key="group.key + '-' + item.key"
                class="dimension-row"
              >
                <div class="dimension-row__main">
                  <b>{{ formatDimensionLabel(group.key, item.key) }}</b>
                  <span>{{ formatCompactCount(item.article_count) }} 篇 · {{ formatCompactCount(item.source_count) }} 信源</span>
                </div>
                <div
                  class="dimension-row__score"
                  :class="stanceVariant(item, 'dimension-row__score--pos', 'dimension-row__score--neg')"
                >
                  {{ formatSemanticAxis(opinionSemanticSnapshot(item).stance) }}
                </div>
              </article>
            </section>
          </div>
        </div>

        <div v-else class="diagnostic-content">
          <div v-if="qualityLoading" class="diagnostic-empty">加载中…</div>
          <div v-else-if="qualityError" class="diagnostic-empty">{{ qualityError }}</div>
          <div v-else class="quality-panel">
            <div class="quality-summary">
              <span>
                <b>{{ qualityData?.freshness?.latest_score_date || '--' }}</b>
                最新评分
              </span>
              <span>
                <b>{{ qualityData?.pending_feedback_30d || 0 }}</b>
                待回看校正
              </span>
              <span>
                <b>{{ qualityData?.method_version || '--' }}</b>
                评分版本
              </span>
              <span>
                <b>{{ opinionTrust.schemaVersion }}</b>
                契约版本
              </span>
              <span>
                <b>{{ opinionTrust.modelVersion }}</b>
                模型版本
              </span>
              <span>
                <b>{{ opinionTrust.sourceStatus }}</b>
                数据源状态
              </span>
              <span>
                <b>{{ opinionTrust.snapshotId }}</b>
                快照标识
              </span>
            </div>
            <div class="quality-list">
              <article v-for="row in qualityCoverageRows" :key="row.date" class="quality-row">
                <span class="quality-row__date">{{ row.date }}</span>
                <span>新闻 {{ formatCompactCount(row.news_count) }}</span>
                <span>L1 {{ formatCompactCount(row.l1_count) }}</span>
                <span>得分 {{ formatCompactCount(row.scored_relevant) }}</span>
              </article>
            </div>
          </div>
        </div>
      </section>
    </div>
    <button
      type="button"
      class="sentiment-assistant-fab"
      :class="{ 'is-dragging': assistantFabDragging }"
      :style="assistantFabStyle"
      aria-label="打开舆情分析数据助手"
      title="拖动调整位置，点击打开数据助手"
      @pointerdown="startAssistantFabDrag"
      @click="handleAssistantFabClick"
    >
      <Bot class="sentiment-assistant-fab__icon" :size="25" :stroke-width="2.15" aria-hidden="true" />
      <span class="sentiment-assistant-fab__status" aria-hidden="true"></span>
    </button>
    <AssistantDrawer
      v-model="assistantDrawerOpen"
      :drawer-key="assistantDrawerKey"
      :page-skill="sentimentAssistantSkill"
      title="舆情分析数据助手"
      subtitle="读取目标立场指数、三维语义状态、质量诊断和下钻结果"
      @layout-change="handleAssistantLayoutChange"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Bot } from 'lucide-vue-next'
import { AssistantDrawer } from '@/features/assistant/index.js'
import {
  CORRECTION_LABELS,
  DIAGNOSTIC_TABS,
  OPINION_CACHE_TTL_MS,
  OPINION_REFRESH_INTERVAL_MS,
  OVERVIEW_CACHE_KEY,
  SEARCH_PAGE_SIZE_OPTIONS,
  SEARCH_PUBLISH_TIME_OPTIONS,
  SentimentFilterToggle,
  TREND_CACHE_KEY,
  addLocalSearchHistory as buildLocalSearchHistory,
  applyFavoriteResult,
  briefClass,
  briefSeverityLabel,
  buildDateExplanation,
  buildDateNewsQuery,
  buildFeedbackDto,
  buildInsightNewsQuery,
  buildMacroEventClustersQuery,
  buildOverviewQuery,
  buildSearchChartMarkPoints,
  buildSentimentChartOption,
  buildSentimentSearchDto,
  buildTrendQuery,
  createLatestSentimentRequest,
  createSentimentSnapshotCache,
  currentRangeDays,
  dataZoomRange as calculateDataZoomRange,
  dimensionGroups as buildDimensionGroups,
  findAnomalyPoints,
  formatCompactCount,
  formatDimensionLabel,
  formatFamilyName,
  formatIndexValue,
  formatOverviewTime,
  formatSearchSnippet,
  invalidateOpinionPayload,
  maxOverviewTagCount as calculateMaxOverviewTagCount,
  mergeFavoriteIds,
  normalizeDateNewsResponse,
  normalizeFavoriteIds,
  normalizeInsightNewsResponse,
  normalizeMacroEventClustersResponse,
  normalizeSearchHistoryResponse,
  normalizeSentimentSearchResponse,
  normalizeTrendResponse,
  overviewMetrics as buildOverviewMetrics,
  overviewReadableIndices as buildOverviewReadableIndices,
  overviewScoreClass as buildOverviewScoreClass,
  overviewSummary as buildOverviewSummary,
  overviewTopEventTitle as buildOverviewTopEventTitle,
  overviewTrendClass as buildOverviewTrendClass,
  opinionTrustSnapshot as buildOpinionTrustSnapshot,
  opinionSemanticMethodSnapshot,
  opinionSemanticSnapshot,
  qualitySnapshot as buildQualitySnapshot,
  rangeIndexes as calculateRangeIndexes,
  resolveAutoEndDate,
  resolveChartPointDate,
  sanitizeOpinionPayload,
  selectDatePoint,
  sentimentApi,
  semanticTrendProjection,
  formatSemanticAxis,
  sparklinePoints,
  tagBarWidth as calculateTagBarWidth,
  tagStyle,
  trimTrendData,
  yAxisRange as calculateYAxisRange,
} from '@/features/sentiment/index.js'

const router = useRouter()
const ASSISTANT_AUTORUN_CONTEXT_KEY = 'data_assistant_autorun_context_v1'
const ASSISTANT_FAB_POSITION_KEY = 'globemind_sentiment_assistant_fab_position_v1'
const assistantDrawerOpen = ref(false)
const assistantDrawerKey = ref(0)
const assistantDrawerLayout = ref({ open: false, docked: false, width: 0, mode: 'docked' })
const assistantFabDragging = ref(false)
const assistantFabPosition = ref({
  x: typeof window !== 'undefined' ? Math.max(16, window.innerWidth - 82) : 24,
  y: typeof window !== 'undefined' ? Math.max(16, window.innerHeight - 82) : 24,
})
let assistantFabStart = { x: 0, y: 0, left: 0, top: 0 }
let assistantFabMoved = false
let assistantFabSuppressClick = false

const sentimentAssistantSkill = computed(() => ({
  page: '智能舆情分析',
  path: '/sentiment-analysis',
  summary: `当前目标立场快照 ${overviewLatestDate.value || '未选择日期'}，搜索结果 ${searchTotal.value || searchResults.value?.length || 0} 条；语气与现实影响独立且当前未知。`,
  access: [
    '导航进入智能舆情分析',
    '搜索结果和日期下钻可打开新闻详情',
    '右下角悬浮按钮打开侧栏助手',
  ],
  sections: [
    '目标立场指数总览',
    '趋势图和日期下钻',
    '质量诊断',
    '舆情搜索结果',
    '反馈校正入口',
  ],
  actions: [
    '调整日期范围',
    '执行舆情搜索',
    '点击图表日期下钻',
    '切换情绪和主题过滤',
    '打开新闻详情',
  ],
  aiActions: [
    '解释指数变化和风险信号',
    '总结当前搜索结果',
    '建议后续检索词和报告提纲',
  ],
}))

const assistantFabStyle = computed(() => ({
  left: `${assistantFabPosition.value.x}px`,
  top: `${assistantFabPosition.value.y}px`,
}))

const nodeDrawerAssistantOffset = computed(() => {
  const width = Number(assistantDrawerLayout.value?.width || 0)
  if (!assistantDrawerLayout.value?.open || !assistantDrawerLayout.value?.docked || width <= 0) return 0
  const maxOffset = typeof window === 'undefined' ? width : Math.max(0, window.innerWidth - 424)
  return Math.round(Math.min(width, maxOffset))
})

const nodeDrawerStyle = computed(() => {
  if (!drawerOpen.value) return {}
  return {
    right: `${nodeDrawerAssistantOffset.value}px`,
  }
})

function handleAssistantLayoutChange(layout) {
  assistantDrawerLayout.value = {
    open: !!layout?.open,
    docked: !!layout?.docked,
    width: Number(layout?.width || 0),
    mode: layout?.mode || 'docked',
  }
}

function defaultAssistantFabPosition() {
  if (typeof window === 'undefined') return { x: 24, y: 24 }
  return {
    x: Math.max(16, window.innerWidth - 82),
    y: Math.max(16, window.innerHeight - 82),
  }
}

function clampAssistantFabPosition(pos) {
  if (typeof window === 'undefined') return pos
  const size = 58
  const margin = 12
  return {
    x: Math.min(Math.max(margin, Number(pos.x) || margin), Math.max(margin, window.innerWidth - size - margin)),
    y: Math.min(Math.max(margin, Number(pos.y) || margin), Math.max(margin, window.innerHeight - size - margin)),
  }
}

function readAssistantFabPosition() {
  if (typeof localStorage === 'undefined') return defaultAssistantFabPosition()
  try {
    const raw = localStorage.getItem(ASSISTANT_FAB_POSITION_KEY)
    if (!raw) return defaultAssistantFabPosition()
    return clampAssistantFabPosition(JSON.parse(raw))
  } catch {
    return defaultAssistantFabPosition()
  }
}

function persistAssistantFabPosition() {
  if (typeof localStorage === 'undefined') return
  localStorage.setItem(ASSISTANT_FAB_POSITION_KEY, JSON.stringify(assistantFabPosition.value))
}

function stopAssistantFabDrag() {
  if (!assistantFabDragging.value) return
  assistantFabDragging.value = false
  persistAssistantFabPosition()
  window.removeEventListener('pointermove', handleAssistantFabDrag)
  window.removeEventListener('pointerup', stopAssistantFabDrag)
  if (assistantFabMoved) {
    assistantFabSuppressClick = true
    window.setTimeout(() => {
      assistantFabSuppressClick = false
    }, 120)
  }
}

function handleAssistantFabDrag(event) {
  if (!assistantFabDragging.value) return
  const dx = event.clientX - assistantFabStart.x
  const dy = event.clientY - assistantFabStart.y
  if (Math.abs(dx) + Math.abs(dy) > 4) assistantFabMoved = true
  assistantFabPosition.value = clampAssistantFabPosition({
    x: assistantFabStart.left + dx,
    y: assistantFabStart.top + dy,
  })
}

function startAssistantFabDrag(event) {
  if (event.button !== 0) return
  assistantFabMoved = false
  assistantFabDragging.value = true
  assistantFabStart = {
    x: event.clientX,
    y: event.clientY,
    left: assistantFabPosition.value.x,
    top: assistantFabPosition.value.y,
  }
  window.addEventListener('pointermove', handleAssistantFabDrag)
  window.addEventListener('pointerup', stopAssistantFabDrag)
}

function handleAssistantFabClick() {
  if (assistantFabSuppressClick) return
  sendSentimentToAssistant('overview')
}

// ==================== 响应式数据 ====================
const chartRef = ref(null)
const chartFullscreen = ref(false)
const chartAnimating = ref(false)
const savedChartRect = ref(null)

async function toggleFullscreen() {
  if (chartAnimating.value) return
  chartAnimating.value = true
  const el = chartRef.value?.parentElement
  if (!el) {
    chartFullscreen.value = !chartFullscreen.value
    chartAnimating.value = false
    return
  }

  const DURATION = 500
  const EASING = 'cubic-bezier(0.22, 0.61, 0.36, 1)'

  let resizeTimer = null
  const startResizeLoop = () => {
    resizeTimer = setInterval(() => {
      if (chartInstance) chartInstance.resize()
    }, 16)
  }
  const stopResizeLoop = () => {
    if (resizeTimer) {
      clearInterval(resizeTimer)
      resizeTimer = null
    }
  }

  const cleanup = () => {
    el.style.position = ''
    el.style.top = ''
    el.style.left = ''
    el.style.width = ''
    el.style.height = ''
    el.style.borderRadius = ''
    el.style.padding = ''
    el.style.border = ''
    el.style.zIndex = ''
    el.style.transition = ''
  }

  if (!chartFullscreen.value) {
    const rect = el.getBoundingClientRect()
    savedChartRect.value = {
      top: rect.top,
      left: rect.left,
      width: rect.width,
      height: rect.height,
    }

    el.style.transition = 'none'
    el.style.position = 'fixed'
    el.style.zIndex = '200'
    el.style.top = rect.top + 'px'
    el.style.left = rect.left + 'px'
    el.style.width = rect.width + 'px'
    el.style.height = rect.height + 'px'
    el.style.borderRadius = '16px'
    el.style.padding = '20px'
    el.offsetHeight

    el.style.transition = 'all ' + DURATION + 'ms ' + EASING
    el.style.top = '64px'
    el.style.left = '0'
    el.style.width = '100vw'
    el.style.height = 'calc(100vh - 64px)'
    el.style.borderRadius = '0'
    el.style.padding = '24px 24px 24px 24px'

    startResizeLoop()
    setTimeout(() => {
      chartFullscreen.value = true
      stopResizeLoop()
      if (chartInstance) chartInstance.resize()
      chartAnimating.value = false
    }, DURATION + 50)
  } else {
    const r = savedChartRect.value || { top: 100, left: 200, width: 600, height: 400 }

    el.style.transition = 'all ' + DURATION + 'ms ' + EASING
    el.style.top = r.top + 'px'
    el.style.left = r.left + 'px'
    el.style.width = r.width + 'px'
    el.style.height = r.height + 'px'
    el.style.borderRadius = '16px'
    el.style.padding = '20px'

    startResizeLoop()
    setTimeout(() => {
      chartFullscreen.value = false
      stopResizeLoop()
      if (chartInstance) chartInstance.resize()
      // 先显示按钮，再清理容器样式避免跳动
      setTimeout(() => {
        cleanup()
        if (chartInstance) chartInstance.resize()
        chartAnimating.value = false
      }, 80)
    }, DURATION + 50)
  }
}
const drawerOpen = ref(false)
const leftDrawerOpen = ref(false)
const selectedData = ref(null)
const timeRange = ref('30d')
const customDays = ref(30)
const startDate = ref('')
const searchQuery = ref('')
const searchHistory = ref([]) // 搜索记录：{ query, time }
const searchResults = ref([])
const searchActiveQuery = ref('')
const searchPage = ref(1)
const searchPublishTime = ref('不限')
const searchPageSize = ref(10)
const searchTotal = ref(0)
const searchTotalPages = ref(0)
const searchLoading = ref(false)
const searchError = ref('')
const favoriteNewsIds = ref([])
const drawerState = ref('') // '' | 'date_news' | 'events' | 'l1_clusters' | 'event_detail'
const clickedDate = ref('')
const selectedEvent = ref(null)
const l1Clusters = ref([])
const l1ClustersLoading = ref(false)
const l1ClustersHasMore = ref(false)
const l1Page = ref(1)
const l1ParentEvent = ref(null)
const eventNews = ref([])
const eventNewsTotal = ref(0)
const eventNewsLoading = ref(false)
const dateNewsSummary = ref(null)
const selectedDatePoint = ref({ date: '', value: 0, delta: null, anomaly: false })
const correctionSubmitting = ref({})
const correctionToast = ref({ open: false, message: '', tone: 'info' })
const sentimentFilter = ref('all')
const eventsFilter = ref('all')
const dateNewsCache = new Map()
let correctionToastTimer = null
const insightModal = ref({
  open: false,
  eyebrow: '',
  title: '',
  subtitle: '',
  metrics: [],
  news: [],
  loading: false,
  error: '',
})
const insightNewsCache = new Map()
let overviewRefreshTimer = null
let overviewBackgroundRefreshTimer = null
let overviewRefreshPromise = null
let lastOverviewRefreshAt = 0
let opinionTrustRevalidationTimer = null
const diagnosticModal = ref({ open: false, tab: 'dimensions' })
const dimensionsData = ref(null)
const dimensionsLoading = ref(false)
const dimensionsError = ref('')
const qualityData = ref(null)
const qualityLoading = ref(false)
const qualityError = ref('')
const diagnosticTabs = DIAGNOSTIC_TABS
const searchPublishTimeOptions = SEARCH_PUBLISH_TIME_OPTIONS
const searchPageSizeOptions = SEARCH_PAGE_SIZE_OPTIONS
let lastFetchDays = 365 // 跟踪最近一次成功请求的天数参数
let chartInstance = null
let echartsModule = null
let echartsLoadPromise = null
const overviewRequest = createLatestSentimentRequest()
const trendRequest = createLatestSentimentRequest()
const insightRequest = createLatestSentimentRequest()
const dateNewsRequest = createLatestSentimentRequest()
const searchRequest = createLatestSentimentRequest()
const dimensionsRequest = createLatestSentimentRequest()
const qualityRequest = createLatestSentimentRequest()
const clusterRequest = createLatestSentimentRequest()
const snapshotCache = createSentimentSnapshotCache({ ttlMs: OPINION_CACHE_TTL_MS })

const loadEcharts = () => {
  if (echartsModule) return Promise.resolve(echartsModule)
  if (!echartsLoadPromise) {
    echartsLoadPromise = import('echarts').then((mod) => {
      echartsModule = mod
      return mod
    })
  }
  return echartsLoadPromise
}

// ==================== 数据生成 ====================
// 从后端 API 获取涉华目标立场指数（替代原 mock 数据）
const opinionTrendData = ref({ dates: [], values: [], meta: {} })
const opinionLoading = ref(true)
const opinionError = ref('')
const overviewData = ref(null)
const overviewLoading = ref(false)
const overviewError = ref('')
const opinionTrustEvaluationNow = ref(Date.now())
const safeOverviewData = computed(() => sanitizeOpinionPayload(overviewData.value, {
  now: opinionTrustEvaluationNow.value,
  requireClaimContract: true,
}))
const safeOpinionTrendData = computed(() => semanticTrendProjection(
  sanitizeOpinionPayload(opinionTrendData.value, {
    now: opinionTrustEvaluationNow.value,
  }),
))

const overviewSummary = computed(() => buildOverviewSummary(safeOverviewData.value))
const overviewSemantics = computed(() => opinionSemanticSnapshot(overviewSummary.value))
const semanticMethod = computed(() => opinionSemanticMethodSnapshot(safeOverviewData.value))
const opinionTrust = computed(() => buildOpinionTrustSnapshot(safeOverviewData.value, {
  now: opinionTrustEvaluationNow.value,
}))
const overviewLatestDate = computed(() => safeOverviewData.value?.latest_date || '--')
const overviewTopEventTitle = computed(() => buildOverviewTopEventTitle(safeOverviewData.value, {
  loading: overviewLoading.value,
  error: overviewError.value,
}))
const overviewTrendClass = computed(() => (
  overviewSemantics.value.stance.state === 'available'
    ? buildOverviewTrendClass({ current_index: overviewSemantics.value.stance.score })
    : 'trend--neutral'
))
const overviewScoreClass = computed(() => (
  overviewSemantics.value.stance.state === 'available'
    ? buildOverviewScoreClass({ current_index: overviewSemantics.value.stance.score })
    : 'risk-score-panel--neutral'
))
const overviewReadableIndices = computed(() => buildOverviewReadableIndices(safeOverviewData.value))
const overviewMetrics = computed(() => buildOverviewMetrics(safeOverviewData.value))
const overviewBriefs = computed(() => safeOverviewData.value?.briefs || [])
const overviewTags = computed(() => safeOverviewData.value?.families || [])
const maxOverviewTagCount = computed(() => calculateMaxOverviewTagCount(overviewTags.value))

const stanceVariant = (
  record,
  supportiveClass,
  criticalClass,
  unavailableClass = null,
) => {
  const stance = opinionSemanticSnapshot(record).stance
  if (stance.state !== 'available') return unavailableClass
  if (stance.category === 'supportive') return supportiveClass
  if (stance.category === 'critical') return criticalClass
  return null
}

const stanceGlyph = (record) => {
  const stance = opinionSemanticSnapshot(record).stance
  if (stance.state !== 'available') return '·'
  if (stance.category === 'supportive') return '+'
  if (stance.category === 'critical') return '-'
  return '0'
}

const revalidateVisibleOpinionComposites = (now = Date.now()) => {
  opinionTrustEvaluationNow.value = now
  const provenance = overviewData.value || {}
  const safeVisible = sanitizeOpinionPayload({
    news: eventNews.value,
    sub_events: l1Clusters.value,
    semantic_contract: safeOverviewData.value?.semantic_contract,
    trust: provenance.trust,
    meta: provenance.meta,
  }, { now })
  eventNews.value = safeVisible.news || []
  l1Clusters.value = safeVisible.sub_events || []
  const safeInsight = sanitizeOpinionPayload({
    news: insightModal.value.news,
    semantic_contract: safeOverviewData.value?.semantic_contract,
    trust: provenance.trust,
    meta: provenance.meta,
  }, { now })
  insightModal.value.news = safeInsight.news || []
  if (safeVisible.trust?.is_computable !== true) {
    selectedDatePoint.value = {
      ...selectedDatePoint.value,
      value: null,
      delta: null,
      anomaly: false,
    }
  }
}

const loadCachedOpinionSnapshot = ({ includeOverview = false, includeTrend = false } = {}) => {
  const cachedOverview = includeOverview ? snapshotCache.read(OVERVIEW_CACHE_KEY) : null
  if (cachedOverview) {
    overviewData.value = cachedOverview
    overviewLoading.value = false
  }
  const cachedTrend = includeTrend ? snapshotCache.read(TREND_CACHE_KEY) : null
  if (cachedTrend?.dates?.length) {
    opinionTrendData.value = normalizeTrendResponse(cachedTrend)
    lastFetchDays = Number(cachedTrend.meta?.filters?.days || cachedTrend.meta?.days || 365)
    opinionLoading.value = false
  }
  return {
    hasOverview: !!cachedOverview,
    hasTrend: !!cachedTrend?.dates?.length,
  }
}

const fetchOverview = async (days = 30, { showLoading = true, refresh = false } = {}) => {
  if (showLoading) overviewLoading.value = true
  overviewError.value = ''
  const outcome = await overviewRequest.run((signal) => (
    sentimentApi.getOverview(buildOverviewQuery(days, { refresh }), { signal })
  ))
  if (outcome.status === 'success') {
    overviewData.value = sanitizeOpinionPayload(outcome.value, { requireClaimContract: true })
    if (overviewData.value?.trust?.is_computable !== true) {
      opinionTrendData.value = normalizeTrendResponse(
        invalidateOpinionPayload(opinionTrendData.value, 'OVERVIEW_TRUST_UNAVAILABLE'),
      )
      snapshotCache.remove(TREND_CACHE_KEY)
    }
    if (refresh) lastOverviewRefreshAt = Date.now()
    snapshotCache.write(OVERVIEW_CACHE_KEY, overviewData.value)
  } else if (outcome.status === 'error') {
    overviewError.value = outcome.error?.message || '获取舆情概览失败'
    overviewData.value = invalidateOpinionPayload(overviewData.value, 'REFRESH_FAILED')
    opinionTrendData.value = normalizeTrendResponse(
      invalidateOpinionPayload(opinionTrendData.value, 'REFRESH_FAILED'),
    )
    snapshotCache.remove(OVERVIEW_CACHE_KEY)
    snapshotCache.remove(TREND_CACHE_KEY)
    console.warn('[opinion] 加载舆情概览失败:', overviewError.value)
  }
  if (outcome.latest) overviewLoading.value = false
  return outcome
}

const searchResultRangeText = computed(() => {
  const total = searchTotal.value || searchResults.value.length
  if (!total || !searchResults.value.length) return ''
  const start = (Math.max(1, searchPage.value) - 1) * searchPageSize.value + 1
  const end = Math.min(total, start + searchResults.value.length - 1)
  return `${start}-${end} / ${total} 条`
})

const searchPanelQuery = computed(() => {
  const q = searchActiveQuery.value || searchQuery.value.trim()
  return q ? `关键词：${q}` : '输入关键词后开始检索'
})

const searchPanelCountText = computed(() => {
  if (searchLoading.value) return '搜索中'
  const total = searchTotal.value || searchResults.value.length
  if (!total) return `${searchPublishTime.value} · ${searchPageSize.value} 条`
  return `命中 ${formatCompactCount(total)} 条`
})

const dimensionGroups = computed(() => buildDimensionGroups(dimensionsData.value))
const qualityCoverageRows = computed(() => qualityData.value?.coverage_by_date || [])
const qualitySnapshot = computed(() => buildQualitySnapshot(qualityData.value))

function compactAssistantText(value, max = 520) {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  return text.length > max ? `${text.slice(0, max)}…` : text
}

function buildSentimentSearchMaterial() {
  return searchResults.value.slice(0, 10).map((item, index) => {
    const title = compactAssistantText(item.title || '无标题', 180)
    const snippet = compactAssistantText(item.abstract || item.summary || item.body || '', 420)
    const meta = [
      item.source ? `来源: ${item.source}` : '',
      item.pub_time ? `时间: ${String(item.pub_time).slice(0, 10)}` : '',
      item.id ? `ID: ${item.id}` : '',
    ].filter(Boolean).join(' | ')
    return `${index + 1}. ${title}\n${meta}${snippet ? `\n摘要: ${snippet}` : ''}`
  }).join('\n\n')
}

function buildSentimentOverviewMaterial() {
  const summary = overviewSummary.value || {}
  const metrics = overviewMetrics.value.map((item) => `${item.label}: ${item.value}`).join(' | ')
  const targets = overviewReadableIndices.value.map((item) => (
    `${item.displayLabel}: ${formatSemanticAxis(opinionSemanticSnapshot(item).stance)} (${item.description})`
  )).join(' | ')
  const briefs = overviewBriefs.value.slice(0, 5).map((item, index) => {
    const title = compactAssistantText(item.title || item.text || item.summary || '', 220)
    const source = item.source ? `来源: ${item.source}` : ''
    return `${index + 1}. ${title}${source ? ` | ${source}` : ''}`
  }).filter((line) => line.trim()).join('\n')
  return [
    `时间范围: ${startDate.value || '--'} 至 ${endDateModel.value || endDate.value || '--'} (${timeRange.value})`,
    `最新评分日期: ${qualitySnapshot.value.latestScoreDate}`,
    overviewSemantics.value.stance.state === 'available'
      ? `加权目标立场指数: ${formatSemanticAxis(overviewSemantics.value.stance, { includeUnit: true })} | 趋势: ${summary.trend_label || '--'}`
      : `加权目标立场指数: 未知 | 原因: ${opinionTrust.value.detail}`,
    '文本语气: 未知（来源模型未建立） | 现实影响方向/强度: 未知（来源模型与量纲未建立）',
    '响应投影组合规则: 三维不组合，不从一个维度推断另一个维度 | 上游轴独立性: 未建立',
    `报道量: ${formatCompactCount(summary.article_count)} | 信源: ${formatCompactCount(summary.source_count)} | 主题数: ${formatCompactCount(summary.family_count)}`,
    opinionTrust.value.computable
      ? `支持/中性/批评立场占比: ${formatCompactCount(summary.positive_pct)} / ${formatCompactCount(summary.neutral_pct)} / ${formatCompactCount(summary.negative_pct)}`
      : '立场分布: 不可计算',
    metrics ? `指标: ${metrics}` : '',
    targets ? `分项指数: ${targets}` : '',
    overviewTopEventTitle.value ? `主题事件: ${overviewTopEventTitle.value}` : '',
    briefs ? `代表性简报:\n${briefs}` : '',
  ].filter(Boolean).join('\n')
}

function buildSentimentAssistantContext(kind = 'overview') {
  const isSearch = kind === 'search'
  const material = isSearch ? buildSentimentSearchMaterial() : buildSentimentOverviewMaterial()
  const subject = isSearch
    ? `舆情搜索「${searchQuery.value.trim() || '当前结果'}」`
    : `涉华舆情快照 ${overviewLatestDate.value || ''}`.trim()
  const prompt = [
    `请基于以下${isSearch ? '舆情搜索结果' : '舆情总览快照'}做协同研判。`,
    '请输出：1）核心结论；2）风险/机会信号；3）需要继续检索或补证的方向；4）可进入报告的结构化提纲。',
    '页面立场数值属于模型派生且未作事实核验，不得标为已知事实；解释性判断标为“分析推断”，缺失信息标为“待核实”。不得从立场推断文本语气或现实影响。',
    '后续检索建议最多 5 条，每条给出一个精确查询词和核验目标。正文控制在 1200 字以内。',
    '如果材料不足，请明确指出缺口，不要编造来源。',
    '',
    material,
  ].join('\n')
  return {
    confirmMessage: `${subject} 已准备好。要我基于当前页面材料开始研判吗？`,
    visibleMessage: `请基于${subject}做研判，并给出后续检索与报告提纲。`,
    prompt,
    mode: 'expert',
    extraBody: { tool_mode: 'context_only' },
    createNew: false,
  }
}

function sendSentimentToAssistant(kind = 'overview') {
  if (!sentimentApi.isAuthenticated()) {
    ElMessage.warning('请先登录后使用数据助手')
    return
  }
  if (kind === 'search' && !searchResults.value.length) {
    ElMessage.warning('请先完成检索并加载结果')
    return
  }
  const context = buildSentimentAssistantContext(kind)
  sessionStorage.setItem(ASSISTANT_AUTORUN_CONTEXT_KEY, JSON.stringify(context))
  assistantDrawerKey.value += 1
  assistantDrawerOpen.value = true
}

const tagBarWidth = (count) => calculateTagBarWidth(count, maxOverviewTagCount.value)

const setInsightModal = (payload) => {
  insightModal.value = {
    open: true,
    eyebrow: payload.eyebrow || '舆情洞察',
    title: payload.title || '',
    subtitle: payload.subtitle || '',
    metrics: payload.metrics || [],
    news: payload.news || [],
    loading: !!payload.loading,
    error: payload.error || '',
  }
}

const closeInsightModal = () => {
  insightRequest.cancel()
  insightModal.value.open = false
}

const fetchInsightNews = async ({ sentimentFilter = 'all', eventFamily = '', days = 30, pageSize = 10 } = {}) => {
  const cacheKey = `${days}:${sentimentFilter}:${eventFamily}:${pageSize}`
  const cached = insightNewsCache.get(cacheKey)
  if (cached) {
    const safeCached = sanitizeOpinionPayload(cached)
    insightNewsCache.set(cacheKey, safeCached)
    insightRequest.cancel()
    insightModal.value.news = safeCached.news
    insightModal.value.loading = false
    insightModal.value.error = ''
    return { latest: true, status: 'cached', value: safeCached }
  }

  insightModal.value.loading = true
  insightModal.value.news = []
  insightModal.value.error = ''
  const query = buildInsightNewsQuery({ days, eventFamily, pageSize, sentimentFilter })
  const outcome = await insightRequest.run((signal) => sentimentApi.getTopNews(query, { signal }))
  if (outcome.status === 'success') {
    const payload = normalizeInsightNewsResponse(sanitizeOpinionPayload(outcome.value))
    insightNewsCache.set(cacheKey, payload)
    insightModal.value.news = payload.news
  } else if (outcome.status === 'error') {
    insightNewsCache.delete(cacheKey)
    insightModal.value.news = []
    insightModal.value.error = outcome.error?.message || '获取相关新闻失败'
  }
  if (outcome.latest) insightModal.value.loading = false
  return outcome
}

const openRiskInsight = (item = null) => {
  const key = String(item?.label || 'CN').toUpperCase()
  const sentimentFilter = key === 'NEG' ? 'negative' : key === 'POS' ? 'positive' : 'all'
  const targetRecord = item || overviewSummary.value
  const title = item?.displayLabel || '加权目标立场指数'
  setInsightModal({
    eyebrow: '涉华目标立场指标',
    title,
    subtitle: `近 30 天 ${item?.description || '涉华目标立场报道'}；文本语气与现实影响不从立场推断`,
    loading: true,
    metrics: [
      { label: '目标立场', value: formatSemanticAxis(opinionSemanticSnapshot(targetRecord).stance, { includeUnit: true }) },
      { label: '文本语气', value: '未知' },
      { label: '现实影响', value: '未知' },
      { label: '信源', value: formatCompactCount(overviewSummary.value.source_count) },
    ],
  })
  fetchInsightNews({ sentimentFilter, pageSize: 10 })
}

const openEventInsight = () => {
  const event = safeOverviewData.value?.top_event
  setInsightModal({
    eyebrow: '主题事件',
    title: overviewTopEventTitle.value,
    subtitle: event?.event_family ? `${formatFamilyName(event.event_family)} · 近 30 天相关报道` : '近 30 天相关报道',
    loading: true,
    metrics: [
      { label: '事件报道', value: formatCompactCount(event?.article_count || 0) },
      { label: '涉华文章', value: formatCompactCount(event?.china_articles || 0) },
      { label: '平均目标立场', value: formatSemanticAxis(opinionSemanticSnapshot(event).stance, { includeUnit: true }) },
      { label: '现实影响', value: '未知' },
    ],
  })
  fetchInsightNews({ eventFamily: event?.event_family || '', pageSize: 10 })
}

const openBriefInsight = (brief) => {
  setInsightModal({
    eyebrow: '情报截获短报',
    title: brief.title || '无标题',
    subtitle: `${brief.source || 'unknown'} · ${formatFamilyName(brief.event_family)} · ${formatOverviewTime(brief.time)}`,
    loading: false,
    metrics: [
      { label: '目标立场', value: formatSemanticAxis(opinionSemanticSnapshot(brief).stance, { includeUnit: true }) },
      { label: '文本语气', value: '未知' },
      { label: '现实影响', value: '未知' },
      { label: '置信', value: brief.confidence == null ? '--' : `${Math.round(brief.confidence * 100)}%` },
      { label: '级别', value: briefSeverityLabel(brief.severity) },
    ],
    news: [brief],
  })
}

const openTagInsight = (tag) => {
  setInsightModal({
    eyebrow: '高频敏感词',
    title: formatFamilyName(tag.event_family),
    subtitle: `近 30 天 ${formatCompactCount(tag.article_count)} 篇涉华报道`,
    loading: true,
    metrics: [
      { label: '报道量', value: formatCompactCount(tag.article_count) },
      { label: '平均目标立场', value: formatSemanticAxis(opinionSemanticSnapshot(tag).stance, { includeUnit: true }) },
      { label: '现实影响', value: '未知' },
      { label: '最新日期', value: overviewLatestDate.value },
    ],
  })
  fetchInsightNews({ eventFamily: tag.event_family || '', pageSize: 10 })
}

const fetchDimensions = async () => {
  if (dimensionsData.value || dimensionsLoading.value) return
  dimensionsLoading.value = true
  dimensionsError.value = ''
  const outcome = await dimensionsRequest.run((signal) => (
    sentimentApi.getDimensions({ days: 30, limit: 8 }, { signal })
  ))
  if (outcome.status === 'success') dimensionsData.value = sanitizeOpinionPayload(outcome.value)
  else if (outcome.status === 'error') {
    dimensionsData.value = invalidateOpinionPayload(dimensionsData.value, 'REFRESH_FAILED')
    dimensionsError.value = outcome.error?.message || '维度数据加载失败'
  }
  if (outcome.latest) dimensionsLoading.value = false
}

const fetchQuality = async () => {
  if (qualityData.value || qualityLoading.value) return
  qualityLoading.value = true
  qualityError.value = ''
  const outcome = await qualityRequest.run((signal) => sentimentApi.getQuality({ signal }))
  if (outcome.status === 'success') qualityData.value = sanitizeOpinionPayload(outcome.value)
  else if (outcome.status === 'error') {
    qualityData.value = invalidateOpinionPayload(qualityData.value, 'REFRESH_FAILED')
    qualityError.value = outcome.error?.message || '质量数据加载失败'
  }
  if (outcome.latest) qualityLoading.value = false
}

const openDiagnostics = (tab = 'dimensions') => {
  diagnosticModal.value = { open: true, tab }
  if (tab === 'dimensions') fetchDimensions()
  else fetchQuality()
}

const switchDiagnosticTab = (tab) => {
  diagnosticModal.value.tab = tab
  if (tab === 'dimensions') fetchDimensions()
  else fetchQuality()
}

const closeDiagnostics = () => {
  diagnosticModal.value.open = false
}

const fetchOpinionTrend = async (days, filter, { showLoading = true, refresh = false } = {}) => {
  if (showLoading) opinionLoading.value = true
  opinionError.value = ''
  const activeFilter = filter || sentimentFilter.value
  const query = buildTrendQuery(days, activeFilter, { refresh })
  const outcome = await trendRequest.run((signal) => sentimentApi.getTrend(query, { signal }))
  if (outcome.status === 'success' && activeFilter === sentimentFilter.value) {
    opinionTrendData.value = normalizeTrendResponse(sanitizeOpinionPayload(outcome.value))
    lastFetchDays = days
    snapshotCache.write(TREND_CACHE_KEY, opinionTrendData.value)
  } else if (outcome.status === 'error') {
    opinionError.value = outcome.error?.message || '获取舆情数据失败'
    console.warn('[opinion] 加载目标立场指数失败:', opinionError.value)
    opinionTrendData.value = normalizeTrendResponse(
      invalidateOpinionPayload(opinionTrendData.value, 'REFRESH_FAILED'),
    )
    snapshotCache.remove(TREND_CACHE_KEY)
  }
  if (showLoading && outcome.latest) opinionLoading.value = false
  return outcome
}

const refreshOverviewSnapshot = ({ showLoading = false } = {}) => {
  revalidateVisibleOpinionComposites()
  if (overviewRefreshPromise) return overviewRefreshPromise
  overviewRefreshPromise = fetchOverview(30, { showLoading, refresh: true }).finally(() => {
    overviewRefreshPromise = null
  })
  return overviewRefreshPromise
}

const scheduleOverviewRefresh = (delay = 600) => {
  if (overviewBackgroundRefreshTimer) clearTimeout(overviewBackgroundRefreshTimer)
  overviewBackgroundRefreshTimer = setTimeout(() => {
    overviewBackgroundRefreshTimer = null
    refreshOverviewSnapshot({ showLoading: false })
  }, delay)
}

const onVisibilityRefresh = () => {
  if (document.visibilityState !== 'visible') return
  revalidateVisibleOpinionComposites()
  if (Date.now() - lastOverviewRefreshAt < 60 * 1000) return
  refreshOverviewSnapshot({ showLoading: false })
}

// 切换情感方向过滤
const onSentimentFilterChange = (value) => {
  if (sentimentFilter.value === value) return
  sentimentFilter.value = value
  fetchOpinionTrend(Math.min(lastFetchDays, 90), value, { showLoading: true }).then(() => {
    if (value === sentimentFilter.value && lastFetchDays < 365) {
      fetchOpinionTrend(365, value, { showLoading: false })
    }
  })
}

// 兼容下游 computed，保持与原有接口一致
// 同时剔除首尾零值，只展示有数据的时间段
const fullData = computed(() => trimTrendData(safeOpinionTrendData.value))
const anomalyPoints = computed(() => findAnomalyPoints(fullData.value))
const searchChartMarkPoints = computed(() => (
  buildSearchChartMarkPoints(searchResults.value, fullData.value)
))

const setSelectedDatePoint = (date) => {
  selectedDatePoint.value = selectDatePoint(date, fullData.value, anomalyPoints.value)
}

const dateInsightTitle = computed(() => {
  if (!selectedDatePoint.value.anomaly) return '目标立场指数解释'
  return Number(selectedDatePoint.value.delta || 0) >= 0
    ? '目标立场异常上行'
    : '目标立场异常下行'
})

const selectedDateExplanation = computed(() => (
  buildDateExplanation(eventNews.value, dateNewsSummary.value)
))

const endDate = computed(() => {
  const dates = fullData.value.dates
  return dates.length ? dates[dates.length - 1] : ''
})

// 结束日：允许在数据范围内调整，默认等于数据末日
const endDateModel = ref('')
watch(
  endDate,
  (val, previousDataEnd) => {
    endDateModel.value = resolveAutoEndDate(
      endDateModel.value,
      previousDataEnd,
      val,
    )
  },
  { immediate: true },
)

// ==================== 监听器 ====================
const getCurrentRangeDays = () => {
  return currentRangeDays(timeRange.value, customDays.value)
}

const getRangeIndexes = () => {
  return calculateRangeIndexes(fullData.value.dates, {
    customDays: customDays.value,
    endDate: endDateModel.value,
    timeRange: timeRange.value,
  })
}

// ==================== 图表配置 ====================
const dataZoomRange = computed(() => {
  return calculateDataZoomRange(getRangeIndexes())
})

const zoomStartPercent = ref(0)
const zoomEndPercent = ref(100)
let isSyncingRange = false
watch(
  [timeRange, customDays, fullData],
  () => {
    if (timeRange.value === 'custom') {
      const normalized = getCurrentRangeDays()
      if (normalized !== customDays.value) customDays.value = normalized
    }
    const dates = fullData.value.dates
    const { startIndex } = getRangeIndexes()
    isSyncingRange = true
    startDate.value = dates[startIndex] || ''
    const range = dataZoomRange.value
    zoomStartPercent.value = range.start
    zoomEndPercent.value = range.end
    isSyncingRange = false
  },
  { immediate: true },
)

// 动态 Y 轴范围：跟随 dataZoom 可视区间变化
const yAxisRange = computed(() => {
  return calculateYAxisRange(
    fullData.value.values,
    zoomStartPercent.value,
    zoomEndPercent.value,
  )
})

// ECharts 图表配置选项
const chartOption = computed(() => (
  buildSentimentChartOption({
    anomalyPoints: anomalyPoints.value,
    data: fullData.value,
    end: zoomEndPercent.value,
    searchMarkPoints: searchChartMarkPoints.value,
    start: zoomStartPercent.value,
    yRange: yAxisRange.value,
  })
))

// ==================== 图表初始化 ====================
// 初始化 ECharts 图表实例，绑定事件监听器
const initChart = async () => {
  if (!chartRef.value) {
    console.warn('图表容器未找到')
    return
  }
  if (chartInstance) return

  try {
    const echarts = await loadEcharts()
    if (!chartRef.value || chartInstance) return
    chartInstance = echarts.init(chartRef.value)
    chartInstance.setOption(chartOption.value)

    // 绑定点击事件 - 确保事件正确绑定
    chartInstance.off('click') // 先移除可能存在的旧监听器
    chartInstance.off('datazoom')
    chartInstance.on('click', onChartClick)
    chartInstance.on('datazoom', onDataZoom)
  } catch (error) {
    console.error('图表初始化失败:', error)
  }
}

// ==================== 事件处理 ====================
// 图表点击事件：点击数据点时获取该日关联事件
const onChartClick = async (params) => {
  const date = resolveChartPointDate(params, fullData.value)
  if (!date) return

  clickedDate.value = date
  drawerState.value = 'date_news'
  drawerOpen.value = true
  selectedEvent.value = null
  eventNews.value = []
  eventNewsTotal.value = 0
  dateNewsSummary.value = null
  setSelectedDatePoint(date)
  l1Clusters.value = []
  l1ParentEvent.value = null
  l1Page.value = 1
  l1ClustersHasMore.value = false

  // Reset selectedData to keep template reference valid
  selectedData.value = { date, score: 0, color: '#0ea5e9' }

  fetchDateNews(date)
}

// 按日期获取当天最有影响力的涉华新闻
const fetchDateNews = async (dateStr) => {
  const cacheKey = `${dateStr}:${eventsFilter.value}`
  const cached = dateNewsCache.get(cacheKey)
  if (cached) {
    const safeCached = sanitizeOpinionPayload(cached)
    dateNewsCache.set(cacheKey, safeCached)
    dateNewsRequest.cancel()
    eventNews.value = safeCached.news
    eventNewsTotal.value = safeCached.total
    dateNewsSummary.value = safeCached.summary
    eventNewsLoading.value = false
    return { latest: true, status: 'cached', value: safeCached }
  }

  eventNewsLoading.value = true
  eventNews.value = []
  eventNewsTotal.value = 0
  dateNewsSummary.value = null
  const query = buildDateNewsQuery(dateStr, eventsFilter.value)
  const outcome = await dateNewsRequest.run((signal) => (
    sentimentApi.getNewsByDate(query, { signal })
  ))
  if (outcome.status === 'success') {
    const payload = normalizeDateNewsResponse(sanitizeOpinionPayload(outcome.value))
    dateNewsCache.set(cacheKey, payload)
    eventNews.value = payload.news
    eventNewsTotal.value = payload.total
    dateNewsSummary.value = payload.summary
  } else if (outcome.status === 'error') {
    dateNewsCache.delete(cacheKey)
    eventNews.value = []
    eventNewsTotal.value = 0
    dateNewsSummary.value = null
    console.warn('[opinion] 获取日期新闻失败:', outcome.error?.message)
  }
  if (outcome.latest) eventNewsLoading.value = false
  return outcome
}

const onDateNewsFilterChange = (value) => {
  if (eventsFilter.value === value) return
  eventsFilter.value = value
  if (clickedDate.value) fetchDateNews(clickedDate.value)
}

const showCorrectionToast = (message, tone = 'info') => {
  correctionToast.value = { open: true, message, tone }
  if (correctionToastTimer) clearTimeout(correctionToastTimer)
  correctionToastTimer = setTimeout(() => {
    correctionToast.value = { open: false, message: '', tone: 'info' }
  }, 2400)
}

const isCorrectionSubmitting = (news, correction) => {
  return !!correctionSubmitting.value[`${news?.id || ''}:${correction}`]
}

const submitNewsCorrection = async (news, correction) => {
  const newsId = Number(news?.id)
  if (!Number.isFinite(newsId)) return
  const key = `${newsId}:${correction}`
  if (correctionSubmitting.value[key]) return
      correctionSubmitting.value = { ...correctionSubmitting.value, [key]: true }
      try {
        await sentimentApi.submitFeedback(buildFeedbackDto(news, correction))
        dimensionsRequest.cancel()
    qualityRequest.cancel()
    dimensionsData.value = null
    dimensionsLoading.value = false
    qualityData.value = null
    qualityLoading.value = false
        showCorrectionToast(`已记录为待复核：${CORRECTION_LABELS[correction] || '校正'}；不用于训练`)
  } catch (e) {
    showCorrectionToast(e.message || '反馈记录失败', 'error')
  } finally {
    const next = { ...correctionSubmitting.value }
    delete next[key]
    correctionSubmitting.value = next
  }
}

const exportDateBrief = () => {
  const date = clickedDate.value || selectedDatePoint.value.date
  if (!date) return
  const explanation = selectedDateExplanation.value
  const rows = eventNews.value.slice(0, 12)
  const lines = [
    `# ${date} 目标立场短报`,
    '',
    `加权目标立场指数：${formatIndexValue(selectedDatePoint.value.value)} 指数点`,
    `目标立场指数较前日：${selectedDatePoint.value.delta === null ? '--' : formatIndexValue(selectedDatePoint.value.delta)}`,
    '文本语气：未知（未建立独立来源模型）',
    '现实影响方向/强度：未知（未建立独立来源模型与量纲）',
    '响应投影组合规则：三维不组合，不从一个维度推断另一个维度',
    '上游轴独立性：未建立',
    '事实核验：未验证',
    `主导议题：${explanation.topFamily}`,
    `批评立场报道：${explanation.criticalCount}`,
    `支持立场报道：${explanation.supportiveCount}`,
    `立场未知报道：${explanation.unknownCount}`,
    `信源数：${explanation.sourceCount}`,
    '',
    '## 代表性目标立场报道',
    ...rows.map(
      (item, index) =>
        `${index + 1}. ${formatSemanticAxis(opinionSemanticSnapshot(item).stance, { includeUnit: true })} · ${item.source || 'unknown'} · ${item.title || '无标题'}`,
    ),
  ]
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `china-opinion-${date}.md`
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  showCorrectionToast('已导出当天短报')
}

// 加载更多 L1 聚类
const loadMoreL1 = async () => {
  if (!l1ParentEvent.value || l1ClustersLoading.value || !l1ClustersHasMore.value) return
  const nextPage = l1Page.value + 1
  l1Page.value = nextPage
  l1ClustersLoading.value = true
  const query = buildMacroEventClustersQuery(
    l1ParentEvent.value.macro_event_id,
    clickedDate.value,
    nextPage,
  )
  const outcome = await clusterRequest.run((signal) => (
    sentimentApi.getMacroEventClusters(query, { signal })
  ))
  if (outcome.status === 'success') {
    const payload = normalizeMacroEventClustersResponse(sanitizeOpinionPayload(outcome.value))
    l1Clusters.value = [...l1Clusters.value, ...payload.subEvents]
    l1ClustersHasMore.value = payload.hasMore
  } else if (outcome.status === 'error') {
    l1Clusters.value = []
    l1ClustersHasMore.value = false
    console.warn('[opinion] 加载更多 L1 聚类失败:', outcome.error?.message)
  }
  if (outcome.latest) l1ClustersLoading.value = false
}

// 抽屉滚动到底部自动加载更多 L1 聚类
const onDrawerScroll = (e) => {
  const el = e.target
  if (!el || drawerState.value !== 'l1_clusters') return
  const threshold = 80 // 距离底部 80px 触发
  if (el.scrollHeight - el.scrollTop - el.clientHeight < threshold) {
    loadMoreL1()
  }
}

// 起始日滚轮：上下滚动加减 1 天，边界限制在数据范围内
const onDateWheel = (e) => {
  const dates = fullData.value.dates
  if (!dates.length || !startDate.value) return
  let idx = dates.indexOf(startDate.value)
  if (idx === -1) idx = 0
  if (e.deltaY < 0) idx = Math.min(idx + 1, dates.length - 1)
  else idx = Math.max(idx - 1, 0)
  startDate.value = dates[idx]
}

watch(startDate, (val) => {
  const dates = fullData.value.dates
  if (!val || !dates.length || isSyncingRange) return
  let idx = dates.indexOf(val)
  if (idx === -1) idx = 0
  const endIndex = dates.length - 1
  const total = Math.max(1, endIndex)
  const days = Math.max(1, endIndex - idx + 1)
  if (timeRange.value !== 'custom') timeRange.value = 'custom'
  if (customDays.value !== days) customDays.value = days
  zoomStartPercent.value = (idx / total) * 100
  zoomEndPercent.value = 100
})

// 数据缩放事件：用户拖动时间条时仅更新百分比
const onDataZoom = () => {
  if (!chartInstance) return
  const option = chartInstance.getOption()
  const dz = option.dataZoom && option.dataZoom[0]
  if (dz && typeof dz.start === 'number' && typeof dz.end === 'number') {
    zoomStartPercent.value = dz.start
    zoomEndPercent.value = dz.end
  }
}

// 监听图表配置变化，重新设置选项并绑定事件（dataZoom 起止已由 chartOption 计算）
watch(
  chartOption,
  (newOption) => {
    if (chartInstance) {
      chartInstance.setOption(newOption, true)
      chartInstance.off('click')
      chartInstance.off('datazoom')
      chartInstance.on('click', onChartClick)
      chartInstance.on('datazoom', onDataZoom)
    }
  },
  { deep: true },
)

// ==================== 生命周期 ====================
// 组件挂载时初始化图表并绑定窗口大小变化监听
onMounted(() => {
  assistantFabPosition.value = readAssistantFabPosition()
  const cached = loadCachedOpinionSnapshot({ includeOverview: true, includeTrend: true })
  nextTick(() => {
    const overviewLoad = fetchOverview(30, { showLoading: !cached.hasOverview, refresh: false }).finally(() => {
      scheduleOverviewRefresh(cached.hasOverview ? 5000 : 8000)
    })
    overviewLoad.finally(() => {
      fetchOpinionTrend(cached.hasTrend ? 365 : 90, sentimentFilter.value, {
        showLoading: !cached.hasTrend,
      }).then(() => {
        if (!cached.hasTrend && sentimentFilter.value === 'all') {
          setTimeout(() => {
            fetchOpinionTrend(365, sentimentFilter.value, { showLoading: false })
          }, 120)
        }
      })
      setTimeout(() => {
        initChart()
      }, 80)
    })
    overviewRefreshTimer = setInterval(() => {
      refreshOverviewSnapshot({ showLoading: false })
    }, OPINION_REFRESH_INTERVAL_MS)
    opinionTrustRevalidationTimer = setInterval(() => {
      revalidateVisibleOpinionComposites()
    }, 60 * 1000)
    setTimeout(() => {
      fetchQuality()
    }, 200)
    document.addEventListener('visibilitychange', onVisibilityRefresh)
    window.addEventListener('resize', handleResize)
  })
  loadSearchHistory()
  loadFavorites()
})

// 组件卸载时清理图表实例和事件监听器
onUnmounted(() => {
  if (resizeTimer) clearTimeout(resizeTimer)
  if (correctionToastTimer) clearTimeout(correctionToastTimer)
  if (assistantFabDragging.value) stopAssistantFabDrag()
  if (overviewRefreshTimer) {
    clearInterval(overviewRefreshTimer)
    overviewRefreshTimer = null
  }
  if (overviewBackgroundRefreshTimer) {
    clearTimeout(overviewBackgroundRefreshTimer)
    overviewBackgroundRefreshTimer = null
  }
  if (opinionTrustRevalidationTimer) {
    clearInterval(opinionTrustRevalidationTimer)
    opinionTrustRevalidationTimer = null
  }
  if (chartInstance) {
    chartInstance.dispose()
    chartInstance = null
  }
  overviewRequest.cancel()
  trendRequest.cancel()
  insightRequest.cancel()
  dateNewsRequest.cancel()
  searchRequest.cancel()
  dimensionsRequest.cancel()
  qualityRequest.cancel()
  clusterRequest.cancel()
  document.removeEventListener('visibilitychange', onVisibilityRefresh)
  window.removeEventListener('resize', handleResize)
})

// 处理窗口大小变化：节流后调整图表尺寸，减轻卡顿
let resizeTimer = null
const handleResize = () => {
  if (resizeTimer) return
  resizeTimer = setTimeout(() => {
    resizeTimer = null
    assistantFabPosition.value = clampAssistantFabPosition(assistantFabPosition.value)
    if (chartInstance) chartInstance.resize()
  }, 180)
}

// ==================== 搜索功能 ====================
const addLocalSearchHistory = (query) => {
  searchHistory.value = buildLocalSearchHistory(searchHistory.value, query)
}

const fetchSearchPage = async ({ query, page = 1, recordHistory = false } = {}) => {
  const q = String(query || '').trim()
  if (!q) return
  searchActiveQuery.value = q
  searchPage.value = Math.max(1, Number(page) || 1)
  searchResults.value = []
  searchTotal.value = 0
  searchTotalPages.value = 0
  searchError.value = ''
  leftDrawerOpen.value = true

  searchLoading.value = true
  const request = buildSentimentSearchDto({
    keyword: q,
    page: searchPage.value,
    pageSize: searchPageSize.value,
    publishTime: searchPublishTime.value,
  })
  const outcome = await searchRequest.run((signal) => sentimentApi.search(request, { signal }))
  if (outcome.status === 'success') {
    const result = normalizeSentimentSearchResponse(outcome.value, {
      page: searchPage.value,
      pageSize: searchPageSize.value,
    })
    searchResults.value = result.results
    searchTotal.value = result.total
    searchPage.value = result.page
    searchPageSize.value = result.pageSize
    searchTotalPages.value = result.totalPages
    favoriteNewsIds.value = mergeFavoriteIds(favoriteNewsIds.value, result.results)
    if (recordHistory) {
      addLocalSearchHistory(q)
      void saveSearchHistory(q)
    }
  } else if (outcome.status === 'error') {
    searchError.value = outcome.error?.message || '搜索失败'
  }
  if (outcome.latest) searchLoading.value = false
  return outcome
}

const handleSearch = async () => {
  const q = searchQuery.value.trim()
  if (!q) return
  await fetchSearchPage({ query: q, page: 1, recordHistory: true })
}

const handleHistorySearch = async (item) => {
  const q = String(item?.query || '').trim()
  if (!q || searchLoading.value) return
  searchQuery.value = q
  await fetchSearchPage({ query: q, page: 1, recordHistory: false })
}

const changeSearchPage = async (page) => {
  const nextPage = Math.max(1, Math.min(Number(page) || 1, searchTotalPages.value || 1))
  if (nextPage === searchPage.value || searchLoading.value) return
  const q = searchActiveQuery.value || searchQuery.value
  await fetchSearchPage({ query: q, page: nextPage, recordHistory: false })
}

const refreshSearchWithSettings = async () => {
  if (searchLoading.value) return
  const q = (searchActiveQuery.value || searchQuery.value).trim()
  if (!q || (!searchResults.value.length && !searchError.value)) return
  await fetchSearchPage({ query: q, page: 1, recordHistory: false })
}

const goToNewsDetail = (id) => {
  if (id) router.push(`/data-service/news/${id}`)
}

const loadSearchHistory = async () => {
  try {
    if (!sentimentApi.isAuthenticated()) return
    searchHistory.value = normalizeSearchHistoryResponse(await sentimentApi.listSearchHistory())
  } catch {
    // 无需阻断主流程
  }
}

const saveSearchHistory = async (keyword) => {
  try {
    if (!sentimentApi.isAuthenticated()) return
    await sentimentApi.recordSearchHistory(keyword)
  } catch {
    // 无需阻断主流程
  }
}

const loadFavorites = async () => {
  try {
    if (!sentimentApi.isAuthenticated()) return
    favoriteNewsIds.value = normalizeFavoriteIds(await sentimentApi.listFavorites())
  } catch {
    favoriteNewsIds.value = []
  }
}

const isFavorited = (newsId) => {
  return favoriteNewsIds.value.includes(Number(newsId))
}

const toggleFavorite = async (newsId) => {
  if (!newsId) return
  try {
    if (!sentimentApi.isAuthenticated()) {
      ElMessage.warning('请先登录后再收藏')
      return
    }
    const result = await sentimentApi.toggleFavorite(newsId)
    favoriteNewsIds.value = applyFavoriteResult(
      favoriteNewsIds.value,
      newsId,
      Boolean(result?.favorited),
    )
  } catch {
    // ignore
  }
}
</script>

<style src="./sentimentAnalysis.css" scoped></style>
