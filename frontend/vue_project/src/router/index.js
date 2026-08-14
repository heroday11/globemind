// src/router/index.js
import { createRouter, createWebHistory } from 'vue-router'

import { getToken } from '@/utils/auth'
import { resolveSafeInternalRedirect } from '@/utils/internalRedirect.js'
import { routeViewLoaders } from './routePreloaders.js'

const DEFAULT_TITLE = 'GlobeMind · 多语言地缘情报平台'
const PUBLIC_CANONICAL_ORIGIN = 'https://globemind.top'
const INDEXABLE_CANONICAL_PATHS = new Set([
  '/',
  '/about-us',
  '/academic-data',
  '/corrections',
  '/data-service/data-search',
  '/data-service/ground-news',
  '/data-service/ground-news-desk',
  '/data-service/help-docs',
  '/data-service/story-graph',
  '/financial-terminal',
  '/methodology',
  '/privacy',
  '/security',
  '/sentiment-analysis',
  '/sources',
  '/status',
  '/terms',
])
const ROUTE_TITLES = Object.freeze({
  '/': DEFAULT_TITLE,
  '/login': '登录 · GlobeMind',
  '/register': '创建账号 · GlobeMind',
  '/forgot-password': '找回密码 · GlobeMind',
  '/reset-password': '重置密码 · GlobeMind',
  '/privacy': '隐私说明 · GlobeMind',
  '/terms': '服务条款 · GlobeMind',
  '/security': '安全报告 · GlobeMind',
  '/corrections': '纠错与权利请求 · GlobeMind',
  '/methodology': '方法说明与解读边界 · GlobeMind',
  '/sources': '数据来源与许可说明 · GlobeMind',
  '/status': '数据与服务状态 · GlobeMind',
  '/data-service/data-search': '新闻与事件检索 · GlobeMind',
  '/data-service/help-docs': '帮助文档 · GlobeMind',
  '/data-service/story-graph': '事件故事脉络 · GlobeMind',
  '/data-service/ground-news': '全球新闻观察台 · GlobeMind',
  '/data-service/ground-news-desk': '新闻分析工作台 · GlobeMind',
  '/data-service/ground-news-blindspot': '旧盲点入口迁移说明 · GlobeMind',
  '/data-service/ground-news-search': '旧新闻搜索入口迁移说明 · GlobeMind',
  '/data-service/report-center': '报告中心 · GlobeMind',
  '/data-service/pipeline-monitor': '管线监控 · GlobeMind',
  '/sentiment-analysis': '涉华舆情分析 · GlobeMind',
  '/data-assistant': '数据助手 · GlobeMind',
  '/financial-terminal': '数值分析预警 · GlobeMind',
  '/academic-data': 'Agent 能力与连接卡 · GlobeMind',
  '/research-workspace': '可追溯研究工作台 · GlobeMind',
  '/model-assurance': '模型评测与发布保障 · GlobeMind',
  '/entity-governance': '时态实体治理 · GlobeMind',
  '/country-profiles': '国家档案目录（未配置）· GlobeMind',
  '/about-us': '关于我们 · GlobeMind',
  '/user-center/personal-center': '个人中心 · GlobeMind',
  '/user-center/my-applications': '我的报告 · GlobeMind',
  '/user-center/help-docs': '帮助文档 · GlobeMind',
  '/user-center/my-collections': '我的收录 · GlobeMind',
  '/data-statistics': '旧数据统计入口迁移说明 · GlobeMind',
})

const CHUNK_RELOAD_KEY = 'globemind_chunk_recovery_path'

function isChunkLoadError(error) {
  const message = String(error?.message || error || '')
  return /dynamically imported module|importing a module script failed|loading chunk|failed to fetch.*module/i.test(message)
}

function updateIndexingMetadata(path) {
  const normalized = path === '/' ? '/' : String(path || '').replace(/\/+$/, '')
  const indexable = INDEXABLE_CANONICAL_PATHS.has(normalized)
  let robots = document.head.querySelector('meta[name="robots"]')
  if (!robots) {
    robots = document.createElement('meta')
    robots.setAttribute('name', 'robots')
    document.head.appendChild(robots)
  }
  robots.setAttribute('content', indexable ? 'index,follow' : 'noindex,nofollow')

  let canonical = document.head.querySelector('link[rel="canonical"]')
  if (indexable) {
    if (!canonical) {
      canonical = document.createElement('link')
      canonical.setAttribute('rel', 'canonical')
      document.head.appendChild(canonical)
    }
    canonical.setAttribute('href', `${PUBLIC_CANONICAL_ORIGIN}${normalized}`)
  } else {
    canonical?.remove()
  }
}

// These routes are intentionally absent from the production route table. Keep
// experimental showcases available to local development without allowing the
// production SPA fallback to expose them as public product surfaces.
const DEVELOPMENT_ONLY_ROUTES = [
  {
    path: '/showcase',
    name: 'ShowcaseOrbis',
    component: () => import('@/views/ShowcaseOrbis.vue'),
    meta: { hideNavbar: false },
  },
  {
    path: '/showcase/delta-force',
    name: 'DeltaForceStudio',
    component: () => import('@/views/DeltaForceStudio.vue'),
    meta: { hideNavbar: true },
  },
  {
    path: '/story-graph-handle-debug',
    name: 'StoryGraphHandleDebug',
    component: () => import('@/views/StoryGraphHandleDebugView.vue'),
    meta: { hideNavbar: true },
  },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/login',
      name: 'Login',
      component: () => import('@/views/Login.vue'),
      meta: { hideNavbar: true, public: true }
    },
    {
      path: '/register',
      name: 'Register',
      component: () => import('@/views/Register.vue'),
      meta: { hideNavbar: true, public: true }
    },
    {
      path: '/forgot-password',
      name: 'ForgotPassword',
      component: () => import('@/views/ForgotPassword.vue'),
      meta: { hideNavbar: true, public: true }
    },
    {
      path: '/reset-password',
      name: 'ResetPassword',
      component: () => import('@/views/ResetPassword.vue'),
      meta: { hideNavbar: true, public: true }
    },
    {
      path: '/privacy',
      name: 'PrivacyNotice',
      component: () => import('@/views/PublicGovernance.vue'),
      meta: { hideNavbar: false, public: true }
    },
    {
      path: '/terms',
      name: 'ServiceTerms',
      component: () => import('@/views/PublicGovernance.vue'),
      meta: { hideNavbar: false, public: true }
    },
    {
      path: '/security',
      name: 'SecurityDisclosure',
      component: () => import('@/views/PublicGovernance.vue'),
      meta: { hideNavbar: false, public: true }
    },
    {
      path: '/corrections',
      name: 'CorrectionsIntake',
      component: () => import('@/views/PublicGovernance.vue'),
      meta: { hideNavbar: false, public: true }
    },
    {
      path: '/methodology',
      name: 'MethodologyNotice',
      component: () => import('@/views/PublicGovernance.vue'),
      meta: { hideNavbar: false, public: true }
    },
    {
      path: '/sources',
      name: 'SourcesNotice',
      component: () => import('@/views/PublicGovernance.vue'),
      meta: { hideNavbar: false, public: true }
    },
    {
      path: '/status',
      name: 'SystemStatus',
      component: () => import('@/views/SystemStatus.vue'),
      meta: { hideNavbar: false, public: true }
    },
    {
      path: '/',
      component: routeViewLoaders.appHome,
      meta: {
        keepAlive: false, // 不缓存首页
        hideNavbar: false
      }
    },
    {
      path: '/data-service',
      component: routeViewLoaders.dataServiceShell,
      children: [
        {
          path: '',
          redirect: (to) => ({
            path: '/data-service/data-search',
            query: to.query,
            hash: to.hash,
          })
        },
        {
          path: 'data-search',
          name: 'DataSearch',
          component: routeViewLoaders.dataSearch
        },
        {
          path: 'help-docs',
          name: 'PublicHelpDocs',
          component: () => import('@/views/user/HelpDocs.vue'),
          meta: { public: true }
        },
        {
          path: 'alert-center',
          name: 'LegacyAlertCenterNotice',
          component: () => import('@/views/LegacyDataServiceNotice.vue'),
          meta: { public: true, title: '旧告警中心入口迁移说明 · GlobeMind' }
        },
        {
          path: 'report-center',
          name: 'ReportCenter',
          component: routeViewLoaders.reportCenter
        },
        {
          path: 'open-computing',
          name: 'LegacyOpenComputingNotice',
          component: () => import('@/views/LegacyDataServiceNotice.vue'),
          meta: { public: true, title: '旧开放计算入口迁移说明 · GlobeMind' }
        },
        {
          path: 'algorithm-analysis',
          name: 'LegacyAlgorithmAnalysisNotice',
          component: () => import('@/views/LegacyDataServiceNotice.vue'),
          meta: { public: true, title: '旧算法分析入口迁移说明 · GlobeMind' }
        },
        {
          path: 'model-test/:modelId',
          name: 'LegacyModelTestNotice',
          component: () => import('@/views/LegacyDataServiceNotice.vue'),
          meta: { public: true, title: '旧模型测试入口迁移说明 · GlobeMind' }
        },
        {
          path: 'news/:id',
          name: 'NewsDetail',
          component: () => import('@/views/DataService/news-detail.vue')
        },
        {
          path: 'story-graph',
          name: 'StoryGraph',
          component: routeViewLoaders.storyGraph
        },
        {
          path: 'ground-news',
          name: 'GroundNewsHome',
          component: routeViewLoaders.groundNewsHome
        },
        {
          path: 'ground-news-desk',
          name: 'GroundNewsDesk',
          component: () => import('@/views/GroundNewsDesk.vue')
        },
        {
          path: 'ground-news-blindspot',
          name: 'LegacyGroundNewsBlindspotNotice',
          component: () => import('@/views/LegacyGroundNewsNotice.vue'),
          meta: { public: true }
        },
        {
          path: 'ground-news-topic/:topic',
          name: 'GroundNewsTopic',
          component: () => import('@/views/GroundNewsFeed.vue')
        },
        {
          path: 'ground-news-search',
          name: 'LegacyGroundNewsSearchNotice',
          component: () => import('@/views/LegacyGroundNewsNotice.vue'),
          meta: { public: true }
        },
        {
          path: 'ground-news-source/:domain',
          name: 'GroundNewsSource',
          component: () => import('@/views/GroundNewsSource.vue')
        },
        {
          path: 'ground-news-timeline/:chain_id',
          name: 'GroundNewsTimeline',
          component: () => import('@/views/GroundNewsTimeline.vue')
        },
        {
          path: 'pipeline-monitor',
          name: 'PipelineMonitor',
          component: routeViewLoaders.pipelineMonitor,
          meta: { requiresAuth: true, hideNavbar: false }
        }
      ]
    },
    {
      path: '/data-statistics',
      name: 'LegacyDataStatisticsNotice',
      component: () => import('@/views/LegacyDataStatisticsNotice.vue'),
      meta: { hideNavbar: false, public: true }
    },
    {
      path: '/academic-data',
      component: routeViewLoaders.academicData,
      meta: { hideNavbar: false }
    },
    {
      path: '/sentiment-analysis',
      component: routeViewLoaders.sentimentAnalysis,
      meta: { hideNavbar: false }
    },
    {
      path: '/data-assistant',
      name: 'DataAssistant',
      component: routeViewLoaders.dataAssistant,
      meta: { hideNavbar: false }
    },
    {
      path: '/financial-terminal',
      name: 'FinancialTerminal',
      component: routeViewLoaders.financialTerminal,
      meta: { hideNavbar: false }
    },
    {
      path: '/research-workspace',
      name: 'ResearchWorkspace',
      component: routeViewLoaders.researchWorkspace,
      meta: { requiresAuth: true, hideNavbar: false }
    },
    {
      path: '/model-assurance',
      name: 'ModelAssurance',
      component: routeViewLoaders.modelAssurance,
      meta: { requiresAuth: true, hideNavbar: false }
    },
    {
      path: '/entity-governance',
      name: 'EntityGovernance',
      component: routeViewLoaders.entityGovernance,
      meta: { requiresAuth: true, hideNavbar: false }
    },
    {
      path: '/country-profiles',
      name: 'CountryProfileCatalog',
      component: routeViewLoaders.countryProfileCatalog,
      meta: { hideNavbar: false, public: true }
    },
    ...(import.meta.env.DEV ? DEVELOPMENT_ONLY_ROUTES : []),
    {
      path: '/user-center',
      component: routeViewLoaders.userCenter,
      meta: { requiresAuth: true, hideNavbar: false },
      children: [
          {
            path: '',
            redirect: { name: 'UserPersonalCenter' }
          },
          {
            path: 'personal-center',
            name: 'UserPersonalCenter',
            component: routeViewLoaders.personalCenter
          },
          {
            path: 'my-applications',
            component: () => import('@/views/user/MyApplications.vue')
          },
          {
            path: 'help-docs',
            component: () => import('@/views/user/HelpDocs.vue')
          },
          {
            path: 'my-collections',
            component: () => import('@/views/user/MyCollections.vue')
          },
          {
            path: 'logout',
            component: () => import('@/views/user/Logout_.vue')
          }
      ]
    },
    {
      path: '/amazing-globe',
      name: 'AmazingGlobe',
      component: () => import('@/views/AmazingGlobe.vue'),
      meta: { hideNavbar: false }
    },
    {
      path: '/about-us',
      component: routeViewLoaders.aboutUs,
      meta: { hideNavbar: false }
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'NotFound',
      component: () => import('@/views/NotFound.vue'),
      meta: { hideNavbar: false }
    }
  ],
})

// 添加全局路由守卫
router.beforeEach((to, from, next) => {
  const token = getToken()
  const requiresAuth = to.matched.some((record) => record.meta?.requiresAuth)
  if (requiresAuth && !token) {
    next({ path: '/login', query: { redirect: to.fullPath } })
    return
  }
  if (to.path === '/login' && token) {
    next(resolveSafeInternalRedirect(to.query.redirect, router.resolve))
    return
  }
  if (to.path === '/' && from.path !== '/') {
    window.dispatchEvent(new CustomEvent('routeToHome'))
  }
  next()
})

router.afterEach((to) => {
  window.scrollTo(0, 0)
  document.title = to.meta?.title || ROUTE_TITLES[to.path] || (to.name === 'NotFound' ? '页面未找到 · GlobeMind' : DEFAULT_TITLE)
  updateIndexingMetadata(to.path)
  try {
    sessionStorage.removeItem(CHUNK_RELOAD_KEY)
  } catch {
    // Storage can be unavailable in privacy-restricted contexts.
  }
})

router.onError((error, to) => {
  if (!isChunkLoadError(error) || typeof window === 'undefined') return
  const target = to?.fullPath || window.location.pathname + window.location.search
  try {
    if (sessionStorage.getItem(CHUNK_RELOAD_KEY) === target) return
    sessionStorage.setItem(CHUNK_RELOAD_KEY, target)
  } catch {
    // Without durable attempt state, an automatic retry could become a reload loop.
    return
  }
  window.location.assign(target)
})

export default router
