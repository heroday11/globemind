<template>
  <div class="hd-root" :class="{ 'hd-root--public': isPublicHelp }">
    <!-- Header hero -->
    <header class="hd-hero">
      <div class="hd-hero-body">
        <div class="hd-hero-overline">PLATFORM GUIDE</div>
        <h1 class="hd-hero-title">帮助文档</h1>
        <p class="hd-hero-desc">平台操作说明与功能介绍，助您快速上手各项模块</p>
      </div>
      <div class="hd-hero-visual" aria-hidden="true">
        <div class="hd-hero-shape hd-hero-shape--1"></div>
        <div class="hd-hero-shape hd-hero-shape--2"></div>
      </div>
    </header>

    <!-- Content -->
    <div class="hd-body">
      <section class="hd-section">
        <div class="hd-section-head">
          <span class="hd-section-num">00</span>
          <div class="hd-section-head-text">
            <h2 class="hd-section-title">使用边界与数据状态</h2>
            <p class="hd-section-sub">先核验时效、覆盖和来源，再使用分析结果</p>
          </div>
        </div>
        <div class="hd-card hd-card--warning">
          <p class="hd-p">
            GlobeMind 当前用于历史新闻线索发现、试验性事件聚合和辅助浏览。它不应被直接当作实时预警、风险评级或无需复核的决策依据。
          </p>
          <p class="hd-p">
            使用关键结果前，请核对页面显示的数据状态、截止时间、覆盖率、来源状态和模型版本。这些信息缺失或输入降级时，请回到原文核验，不要仅依赖精确分数。
          </p>
        </div>
      </section>

      <section class="hd-section">
        <div class="hd-section-head">
          <span class="hd-section-num">01</span>
          <div class="hd-section-head-text">
            <h2 class="hd-section-title">首页与导航</h2>
            <p class="hd-section-sub">平台入口与全局导航结构</p>
          </div>
        </div>
        <div class="hd-card">
          <p class="hd-p">首页展示四大模块入口：<strong>舆情分析系统</strong>、<strong>全球新闻观察台</strong>、<strong>学术数据导航</strong>、<strong>数据服务系统</strong>。点击对应卡片即可进入。</p>
          <p class="hd-p">顶部导航栏提供：<strong>全球新闻观察台</strong>、<strong>数据服务</strong>（含数据搜索、管线监控）、<strong>报告中心</strong>、<strong>数据助手</strong>、<strong>数值分析预警</strong>、<strong>用户中心</strong>（需登录）与<strong>关于我们</strong>。右侧搜索可直接进入数据搜索。</p>
        </div>
      </section>

      <section class="hd-section">
        <div class="hd-section-head">
          <span class="hd-section-num">02</span>
          <div class="hd-section-head-text">
            <h2 class="hd-section-title">研究模块关系与任务交接</h2>
            <p class="hd-section-sub">这是用户显式操作地图，不是自动数据管线</p>
          </div>
        </div>
        <div class="hd-card hd-card--warning">
          <p class="hd-p">
            各模块保留自己的输入、回执、抽样和审阅边界；模块之间的交接不会把检索命中、模型输出或图上关系自动升级为已核验事实。
          </p>
          <p class="hd-p">
            下列关系只说明当前源码支持的任务步骤。所有交接的事实保障状态均为
            <code>not_established</code>，关键结论仍需回到原始来源和人工审阅。
          </p>
        </div>
        <ol class="hd-flow-list" aria-label="研究模块任务交接">
          <li v-for="flow in PRODUCT_TASK_FLOWS" :key="flow.id" class="hd-flow-item">
            <div class="hd-flow-path">
              <strong>{{ productModuleById(flow.from).label }}</strong>
              <span aria-hidden="true">→</span>
              <strong>{{ productModuleById(flow.to).label }}</strong>
            </div>
            <p>{{ flow.handoff }}</p>
            <small>
              {{ flow.automatic ? '自动' : '用户显式操作' }} ·
              provenance {{ flow.provenanceState }} · truth {{ flow.truthAssurance }}
            </small>
          </li>
        </ol>
        <details class="hd-module-details">
          <summary>查看各模块输入、输出与边界</summary>
          <dl>
            <template v-for="module in PRODUCT_MODULES" :key="module.id">
              <dt>{{ module.label }} · <code>{{ module.state }}</code></dt>
              <dd>
                输入：{{ module.input }}；输出：{{ module.output }}；边界：{{ module.boundary }}
              </dd>
            </template>
          </dl>
        </details>
      </section>

      <section class="hd-section">
        <div class="hd-section-head">
          <span class="hd-section-num">03</span>
          <div class="hd-section-head-text">
            <h2 class="hd-section-title">数据服务</h2>
            <p class="hd-section-sub">数据搜索、知识图谱与预警报告</p>
          </div>
        </div>
        <div class="hd-grid hd-grid--3">
          <div class="hd-card hd-card--feature">
            <div class="hd-feature-dot hd-feature-dot--purple"></div>
            <h3 class="hd-feature-title">数据搜索</h3>
            <p class="hd-feature-desc">
              支持按主题、必须包含、任意包含、需要排除等条件检索新闻；可筛选发布时间、命中位置、数据源、语言。结果列表中可查看详情或跳转原文。
            </p>
          </div>
          <div class="hd-card hd-card--feature">
            <div class="hd-feature-dot hd-feature-dot--blue"></div>
            <h3 class="hd-feature-title">全球新闻观察台</h3>
            <p class="hd-feature-desc">
              试验性地将候选新闻按 L1 事件聚合，并展示有界的 L2/L3 抽样、目录标签和代表性标题；它不等于完整事件图、来源真值或因果结论。
            </p>
          </div>
          <div class="hd-card hd-card--feature">
            <div class="hd-feature-dot hd-feature-dot--amber"></div>
            <h3 class="hd-feature-title">数值分析预警与报告中心</h3>
            <p class="hd-feature-desc">数值分析预警用于金融终端监控和规则预警；报告中心用于查看数据助手生成并保存到 report 工作区的分析报告。</p>
          </div>
        </div>
      </section>

      <section class="hd-section">
        <div class="hd-section-head">
          <span class="hd-section-num">04</span>
          <div class="hd-section-head-text">
            <h2 class="hd-section-title">舆情分析系统</h2>
            <p class="hd-section-sub">立场统计的历史可视化与下钻线索</p>
          </div>
        </div>
        <div class="hd-card">
          <p class="hd-p">提供历史立场统计折线，可设置起始日期与视距（近 7 天 / 1 个月 / 3 个月 / 全年）。点击数据点可查看候选下钻线索，并按目标、事件、关键字筛选。立场、语气和现实影响是不同维度；当前没有证据时，语气和现实影响保持未知。</p>
        </div>
      </section>

      <section class="hd-section">
        <div class="hd-section-head">
          <span class="hd-section-num">05</span>
          <div class="hd-section-head-text">
            <h2 class="hd-section-title">用户中心</h2>
            <p class="hd-section-sub">账号管理、个人资料与偏好设置</p>
          </div>
        </div>
        <div class="hd-grid hd-grid--2">
          <div class="hd-card hd-card--info">
            <h3 class="hd-info-label">登录与退出</h3>
            <div class="hd-info-body">
              <p class="hd-p">未登录时，导航栏显示<strong>「登录」</strong>按钮。输入用户名与密码提交即可登录，成功后跳转至原访问页或首页。</p>
              <p class="hd-p">登录后，导航栏显示「用户中心」下拉菜单，内含个人中心、我的报告、帮助文档、我的收录、<strong>退出登录</strong>。点击退出登录将清除登录状态并跳转至首页。</p>
            </div>
          </div>
          <div class="hd-card hd-card--info">
            <h3 class="hd-info-label">个人中心、我的报告、我的收录</h3>
            <div class="hd-info-body">
              <p class="hd-p">在用户中心侧栏进入对应子页，管理个人信息、报告存档与收藏的新闻收录。</p>
            </div>
          </div>
        </div>
      </section>

      <section class="hd-section">
        <div class="hd-section-head">
          <span class="hd-section-num">06</span>
          <div class="hd-section-head-text">
            <h2 class="hd-section-title">关于我们</h2>
            <p class="hd-section-sub">平台与实验室介绍</p>
          </div>
        </div>
        <div class="hd-card">
          <p class="hd-p">点击导航栏「关于我们」可查看平台与实验室介绍，并进入公开的治理、安全和纠错页面。</p>
        </div>
      </section>

      <section class="hd-section">
        <div class="hd-section-head">
          <span class="hd-section-num">07</span>
          <div class="hd-section-head-text">
            <h2 class="hd-section-title">治理、安全与纠错</h2>
            <p class="hd-section-sub">公开稳定路径，无需登录</p>
          </div>
        </div>
        <nav class="hd-governance-links" aria-label="治理页面">
          <router-link to="/privacy">隐私说明</router-link>
          <router-link to="/terms">服务条款与使用边界</router-link>
          <router-link to="/security">安全报告与披露</router-link>
          <router-link to="/methodology">方法说明与解读边界</router-link>
          <router-link to="/sources">数据来源与许可说明</router-link>
          <router-link to="/status">数据与服务状态</router-link>
          <router-link to="/corrections">纠错与权利请求</router-link>
        </nav>
        <div class="hd-card">
          <p class="hd-p">上述页面会如实标注尚未指定的负责人、尚未承诺的 SLA 以及尚未完成的许可/个人信息盘点，不以页面上线代替后续治理工作。</p>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onActivated, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import {
  PRODUCT_MODULES,
  PRODUCT_TASK_FLOWS,
  productModuleById,
} from '@/governance/productDataFlow.js'

const route = useRoute()
const isPublicHelp = computed(() => route.path === '/data-service/help-docs')

function resetScroll() {
  window.scrollTo(0, 0)
}

function onPopState() {
  setTimeout(resetScroll, 80)
}

onMounted(() => {
  if ('scrollRestoration' in history) {
    history.scrollRestoration = 'manual'
  }
  resetScroll()
  window.addEventListener('popstate', onPopState)
})

onActivated(() => {
  resetScroll()
})

onUnmounted(() => {
  window.removeEventListener('popstate', onPopState)
})
</script>

<style scoped>
/* ============================================
   DESIGN TOKENS
   ============================================ */
.hd-root {
  --hd-surface: #ffffff;
  --hd-border: #e8e5f0;
  --hd-border-light: #f0edf6;
  --hd-text: #1a1824;
  --hd-text-secondary: #5c5870;
  --hd-text-muted: #8b869e;
  --hd-accent-soft: rgba(91, 114, 223, 0.06);
  --hd-shadow-sm: 0 1px 2px rgba(26, 24, 36, 0.04);
  --hd-shadow: 0 1px 3px rgba(26, 24, 36, 0.04), 0 4px 16px rgba(26, 24, 36, 0.05);
  --hd-radius-sm: 8px;
  --hd-radius: 14px;
  --hd-radius-lg: 20px;
  --hd-transition: 0.22s cubic-bezier(0.33, 1, 0.68, 1);

  width: 100%;
  max-width: 860px;
  margin: 0 auto;
  animation: hd-fade-up 0.5s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.hd-root--public {
  max-width: 960px;
  box-sizing: border-box;
  padding: 104px 24px 64px;
}

.hd-card--warning {
  border-color: #e8cf8a;
  background: #fffaf0;
}

.hd-governance-links {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.hd-governance-links a {
  display: flex;
  align-items: center;
  min-height: 48px;
  box-sizing: border-box;
  padding: 11px 14px;
  border: 1px solid var(--hd-border);
  border-radius: 12px;
  color: #385cb4;
  background: var(--hd-surface);
  font-weight: 700;
  text-decoration: none;
}

.hd-flow-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin: 14px 0;
  padding: 0;
  list-style: none;
}

.hd-flow-item {
  padding: 14px;
  border: 1px solid var(--hd-border);
  border-radius: var(--hd-radius-sm);
  background: var(--hd-surface);
}

.hd-flow-item p {
  margin: 8px 0;
  color: var(--hd-text-secondary);
  line-height: 1.65;
}

.hd-flow-item small {
  color: var(--hd-text-muted);
}

.hd-flow-path {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.hd-module-details {
  padding: 14px;
  border: 1px solid var(--hd-border);
  border-radius: var(--hd-radius-sm);
}

.hd-module-details summary {
  min-height: 44px;
  cursor: pointer;
  font-weight: 650;
}

.hd-module-details dl {
  margin: 10px 0 0;
}

.hd-module-details dt {
  margin-top: 12px;
  font-weight: 650;
}

.hd-module-details dd {
  margin: 4px 0 0;
  color: var(--hd-text-secondary);
  line-height: 1.65;
}

.hd-governance-links a:hover {
  border-color: #9bacdf;
  background: #f6f8ff;
}

.hd-governance-links a:focus-visible {
  outline: 3px solid rgba(70, 94, 190, 0.3);
  outline-offset: 3px;
}

@keyframes hd-fade-up {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* ---- Hero ---- */
.hd-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 32px;
  padding: 32px 36px;
  background: linear-gradient(135deg, #f9f8fd 0%, #f3f1fa 30%, #faf9fd 100%);
  border-radius: var(--hd-radius-lg);
  border: 1px solid var(--hd-border);
  box-shadow: var(--hd-shadow);
  position: relative;
  overflow: hidden;
}

.hd-hero-body {
  position: relative;
  z-index: 1;
}

.hd-hero-overline {
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: var(--uc-accent, #5b72df);
  margin-bottom: 8px;
}

.hd-hero-title {
  margin: 0;
  font-size: 1.85rem;
  font-weight: 750;
  color: var(--hd-text);
  letter-spacing: -0.02em;
  line-height: 1.15;
}

.hd-hero-desc {
  margin: 6px 0 0;
  font-size: 0.92rem;
  color: var(--hd-text-muted);
  font-weight: 460;
}

.hd-hero-visual {
  position: relative;
  width: 80px;
  height: 80px;
  flex-shrink: 0;
}

.hd-hero-shape {
  position: absolute;
  border-radius: 12px;
}

.hd-hero-shape--1 {
  width: 48px;
  height: 48px;
  background: rgba(91, 114, 223, 0.08);
  border: 1px solid rgba(91, 114, 223, 0.15);
  top: 8px;
  right: 16px;
  transform: rotate(15deg);
}

.hd-hero-shape--2 {
  width: 32px;
  height: 32px;
  background: rgba(91, 114, 223, 0.12);
  border: 1px solid rgba(91, 114, 223, 0.22);
  bottom: 6px;
  right: 4px;
  border-radius: 8px;
  transform: rotate(-10deg);
}

/* ---- Sections ---- */
.hd-body {
  animation: hd-fade-up 0.5s cubic-bezier(0.22, 1, 0.36, 1) 0.06s both;
}

.hd-section {
  margin-bottom: 28px;
}

.hd-section-head {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 16px;
}

.hd-section-num {
  font-size: 1.35rem;
  font-weight: 800;
  color: var(--uc-accent, #5b72df);
  letter-spacing: -0.02em;
  line-height: 1;
  opacity: 0.75;
  flex-shrink: 0;
  margin-top: 2px;
}

.hd-section-title {
  margin: 0;
  font-size: 1.18rem;
  font-weight: 700;
  color: var(--hd-text);
  letter-spacing: -0.01em;
  line-height: 1.25;
}

.hd-section-sub {
  margin: 3px 0 0;
  font-size: 0.84rem;
  color: var(--hd-text-muted);
  font-weight: 450;
}

/* ---- Card ---- */
.hd-card {
  background: var(--hd-surface);
  border-radius: var(--hd-radius);
  border: 1px solid var(--hd-border);
  box-shadow: var(--hd-shadow-sm);
  padding: 20px 24px;
}

.hd-p {
  margin: 0 0 12px;
  font-size: 0.9rem;
  line-height: 1.75;
  color: var(--hd-text-secondary);
}

.hd-p:last-child {
  margin-bottom: 0;
}

.hd-p strong {
  color: var(--hd-text);
  font-weight: 640;
}

.hd-code {
  font-size: 0.88em;
  padding: 2px 7px;
  border-radius: 5px;
  background: #f3f1fa;
  color: #6358b6;
  font-weight: 540;
  font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
}

/* ---- Feature Grid ---- */
.hd-grid--3 {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}

.hd-grid--2 {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
}

.hd-card--feature {
  padding: 22px 22px 20px;
  transition:
    border-color var(--hd-transition),
    box-shadow var(--hd-transition);
}

.hd-card--feature:hover {
  border-color: rgba(91, 114, 223, 0.25);
  box-shadow: var(--hd-shadow);
}

.hd-feature-dot {
  width: 8px;
  height: 8px;
  border-radius: 3px;
  margin-bottom: 14px;
}

.hd-feature-dot--purple { background: #8b5cf6; }
.hd-feature-dot--blue   { background: #3b82f6; }
.hd-feature-dot--amber  { background: #f59e0b; }

.hd-feature-title {
  margin: 0 0 10px;
  font-size: 0.98rem;
  font-weight: 680;
  color: var(--hd-text);
  letter-spacing: -0.01em;
}

.hd-feature-desc {
  margin: 0;
  font-size: 0.85rem;
  line-height: 1.68;
  color: var(--hd-text-muted);
}

/* ---- Info cards ---- */
.hd-card--info {
  padding: 22px;
}

.hd-info-label {
  margin: 0 0 14px;
  padding-bottom: 12px;
  font-size: 0.95rem;
  font-weight: 680;
  color: var(--hd-text);
  border-bottom: 1px solid var(--hd-border-light);
}

.hd-info-body {
  font-size: 0.88rem;
}

/* ---- Responsive ---- */
@media (max-width: 768px) {
  .hd-root--public {
    padding: 92px 14px 44px;
  }

  .hd-hero {
    padding: 24px 20px;
  }

  .hd-hero-visual {
    display: none;
  }

  .hd-grid--3,
  .hd-grid--2,
  .hd-governance-links,
  .hd-flow-list {
    grid-template-columns: 1fr;
  }
}
</style>
