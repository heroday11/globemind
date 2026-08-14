export const routeViewLoaders = Object.freeze({
  appHome: () => import('@/views/appHome.vue'),
  dataServiceShell: () => import('@/views/DataSearch.vue'),
  groundNewsHome: () => import('@/views/GroundNewsHome.vue'),
  sentimentAnalysis: () => import('@/views/sentimentAnalysis.vue'),
  storyGraph: () => import('@/views/StoryGraphView.vue'),
  dataSearch: () => import('@/views/DataService/data-search.vue'),
  reportCenter: () => import('@/views/DataService/report-center.vue'),
  pipelineMonitor: () => import('@/views/PipelineMonitor.vue'),
  dataAssistant: () => import('@/views/DataAssistant.vue'),
  financialTerminal: () => import('@/views/FinancialTerminal.vue'),
  academicData: () => import('@/views/AcademicData.vue'),
  aboutUs: () => import('@/views/AboutUs.vue'),
  userCenter: () => import('@/views/UserCenter.vue'),
  personalCenter: () => import('@/views/user/PersonalCenter.vue'),
  researchWorkspace: () => import('@/views/ResearchWorkspace.vue'),
  modelAssurance: () => import('@/views/ModelAssurance.vue'),
  entityGovernance: () => import('@/views/EntityGovernance.vue'),
  countryProfileCatalog: () => import('@/views/CountryProfileCatalog.vue'),
})

function requireLoader(loaders, key) {
  const loader = loaders[key]
  if (typeof loader !== 'function') {
    throw new TypeError(`Missing route view loader: ${key}`)
  }
  return loader
}

export function createRoutePreloaders(loaders = routeViewLoaders) {
  const single = (key) => () => requireLoader(loaders, key)()
  const group = (...keys) => () => Promise.all(keys.map((key) => requireLoader(loaders, key)()))

  return Object.freeze({
    '/': single('appHome'),
    '/data-service/ground-news': group('dataServiceShell', 'groundNewsHome'),
    '/sentiment-analysis': single('sentimentAnalysis'),
    '/data-service/story-graph': group('dataServiceShell', 'storyGraph'),
    '/data-service/data-search': group('dataServiceShell', 'dataSearch'),
    '/data-service/report-center': group('dataServiceShell', 'reportCenter'),
    '/data-service/pipeline-monitor': group('dataServiceShell', 'pipelineMonitor'),
    '/data-assistant': single('dataAssistant'),
    '/financial-terminal': single('financialTerminal'),
    '/academic-data': single('academicData'),
    '/about-us': single('aboutUs'),
    '/user-center/personal-center': group('userCenter', 'personalCenter'),
    '/research-workspace': single('researchWorkspace'),
    '/model-assurance': single('modelAssurance'),
    '/entity-governance': single('entityGovernance'),
    '/country-profiles': single('countryProfileCatalog'),
  })
}

export const routePreloaders = createRoutePreloaders()
