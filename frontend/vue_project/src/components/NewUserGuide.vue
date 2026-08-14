<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ArrowRight, Check, List, LoaderCircle, X } from 'lucide-vue-next'
import { getAuthChangedEventName, getToken } from '@/utils/auth'

const STORAGE_KEY = 'globemind_new_user_guide_v3'
const router = useRouter()
const route = useRoute()
const visible = ref(false)
const stepIndex = ref(0)
const targetRect = ref(null)
const targetFound = ref(false)
const tooltip = ref(null)
const authenticated = ref(Boolean(getToken()))
const transitionState = ref('idle')
const transitionDirection = ref('forward')
const directoryOpen = ref(false)
const reducedMotion = ref(false)
let locateGeneration = 0
let resizeFrame = null
let trackingFrame = null

const commonSteps = [
  {
    chapter: '起步', route: '/', selector: '[data-tour="home-capabilities"]',
    eyebrow: '工作台 · 任务入口', title: '先按研究任务选择入口',
    description: '首页四张卡片分别通往舆情研判、全球新闻观察、新闻检索和知识库。你不需要从固定页面开始。',
    tips: ['追踪热点先进入全球新闻观察台', '已知主题时直接使用全局检索', '需要持续判断趋势时进入舆情分析'],
  },
  {
    chapter: '起步', route: '/', selector: '[data-tour="global-search"]', placement: 'left',
    eyebrow: '工作台 · 全局检索', title: '从任何页面快速发起检索',
    description: '输入实体、事件或主题并回车，系统会跳到数据检索页并自动带入关键词。',
    tips: ['先用宽主题建立候选集', '进入检索页后再限定时间、语言和搜索层级'],
  },
  {
    chapter: '发现', route: '/data-service/ground-news', selector: '[data-tour="ground-news-overview"]',
    eyebrow: '全球新闻观察台 · 总览', title: '先判断已收录事件中哪些值得关注',
    description: '事件首页按已收录时间和多源覆盖展示国际焦点，顶部同时给出故事卡、新闻量和最近有效日期。',
    tips: ['优先查看多源事件', '注意数据最新日期', '刷新事件流不会改变你的筛选偏好'],
  },
  {
    chapter: '发现', route: '/data-service/ground-news', selector: '[data-tour="ground-news-edition"]',
    eyebrow: '全球新闻观察台 · 新闻版面', title: '从快讯、多源报道和走势链交叉判断',
    description: '快讯列表用于查看已收录事件，多源版面帮助比较报道覆盖，L2 走势链用于理解连续演变。',
    tips: ['先看来源数量而不是只看标题', '打开事件后继续核对原始报道'],
  },
  {
    chapter: '发现', route: '/data-service/ground-news-desk', selector: '[data-tour="ground-desk-filters"]', placement: 'right',
    eyebrow: '新闻分析工作台 · 筛选', title: '用时间、信源和质量筛出可靠故事卡',
    description: '这里可以按事件家族、时间、新闻数、信源数与质量组合筛选，适合从大量热点中建立研究清单。',
    tips: ['先设时间，再提高信源门槛', '质量筛选用于减少单一来源噪声'],
  },
  {
    chapter: '发现', route: '/data-service/ground-news-desk', selector: '[data-tour="ground-desk-analysis"]', placement: 'left',
    eyebrow: '新闻分析工作台 · 事件档案', title: '在同一处核对脉络、价值与报道差异',
    description: '选中故事卡后，右侧会汇总事件脉络、研究价值、观察点、立场光谱和代表性证据。',
    tips: ['中立摘要用于导航，不替代原文', '可把当前故事交给数据助手继续研判'],
  },
  {
    chapter: '检索', route: '/data-service/data-search', selector: '[data-tour="search-workfolders"]', placement: 'right',
    eyebrow: '数据服务 · 工作文件夹', title: '先把资料归入一个研究主题',
    description: '星标新闻会进入当前文件夹，方便围绕项目、事件或地区持续积累证据。',
    tips: ['登录后收藏可跨设备同步', '按长期项目而不是临时关键词命名文件夹'],
  },
  {
    chapter: '检索', route: '/data-service/data-search', selector: '[data-tour="search-form"]',
    eyebrow: '数据服务 · 查询构造', title: '从模板开始，再组合关键词',
    description: '主关键词确定主题，必须包含和排除词控制边界；态势模板会自动选择较合适的搜索层级。',
    tips: ['首次查询保持条件简单', '同名实体用排除词去噪', '按 Enter 或点击搜索执行'],
  },
  {
    chapter: '检索', route: '/data-service/data-search', selector: '[data-tour="search-filters"]',
    eyebrow: '数据服务 · 精细筛选', title: '按时间、模式、层级、来源和语言收窄结果',
    description: '你可以检索新闻，也可以直接切换到 L1 事件、L2 走势和 L3 大事件，避免把不同粒度混在一起。',
    tips: ['新闻适合找原始证据', 'L1/L2/L3 适合研究事件结构', '相关度排序和时间排序回答不同问题'],
  },
  {
    chapter: '检索', route: '/data-service/data-search', selector: '[data-tour="search-result-tools"]',
    eyebrow: '数据服务 · 结果阅读', title: '从层级结果继续打开证据或交给助手',
    description: '结果会根据搜索类型显示新闻列表或事件层级。可打开详情、星标、导出，也可把当前条件发送给数据助手。',
    tips: ['先检查命中数量和数据截止时间', '事件摘要之后仍要回到相关新闻复核'],
  },
  {
    chapter: '脉络', route: '/data-service/story-graph', selector: '[data-tour="story-level-switch"]', placement: 'left',
    eyebrow: '事件故事脉络 · 层级', title: 'L2 看连续走势，L3 看宏观大事件',
    description: '用顶部切换器改变研究尺度：L2 强调时间上的连续演变，L3 强调多条走势之间的宏观关系。',
    tips: ['从 L3 建立全局认识', '进入 L2 核对具体演变和证据'],
  },
  {
    chapter: '脉络', route: '/data-service/story-graph', selector: '[data-tour="story-library"]', placement: 'right',
    eyebrow: '事件故事脉络 · 导航库', title: '从左侧选择主线并比较研究价值',
    description: '导航库展示候选大事件或走势链，以及研究价值、证据等级和时间范围。',
    tips: ['先选证据等级更高的主线', '搜索框可以快速定位主题'],
  },
  {
    chapter: '脉络', route: '/data-service/story-graph', selector: '[data-tour="story-canvas"]', placement: 'left',
    eyebrow: '事件故事脉络 · 关系画布', title: '点击节点查看关系与相关新闻',
    description: '主干、分支和影响关系会在画布中呈现。选择节点后可以打开详情、查看新闻证据或让助手研判。',
    tips: ['使用“适配视图”重新居中', '关系线是分析线索，不代表因果已经被证明'],
  },
  {
    chapter: '舆情', route: '/sentiment-analysis', selector: '[data-tour="sentiment-search"]', placement: 'right',
    eyebrow: '涉华舆情 · 主题检索', title: '先明确要观察的对象或议题',
    description: '顶部搜索用于切换分析主题，历史按钮可恢复之前的检索。',
    tips: ['名称尽量具体', '搜索结果可收藏并继续下钻'],
  },
  {
    chapter: '舆情', route: '/sentiment-analysis', selector: '[data-tour="sentiment-range"]', placement: 'left',
    eyebrow: '涉华舆情 · 时间范围', title: '时间窗口决定趋势是否可解释',
    description: '起止日期和视距共同控制趋势跨度。对比短期冲击与长期变化时，应分别选择不同窗口。',
    tips: ['短窗口用于突发事件', '长窗口用于结构性趋势'],
  },
  {
    chapter: '舆情', route: '/sentiment-analysis', selector: '[data-tour="sentiment-quality"]',
    eyebrow: '涉华舆情 · 数据质量', title: '先检查数据状态，再阅读分数',
    description: '质量栏说明最新评分日期、覆盖量和方法状态。若数据过期，应把页面视作历史快照。',
    tips: ['不要把旧日期结果描述为实时结论', '样本量不足时降低结论强度'],
  },
  {
    chapter: '舆情', route: '/sentiment-analysis', selector: '[data-tour="sentiment-overview"]',
    eyebrow: '涉华舆情 · 研判摘要', title: '用指数、事件和主题榜建立假设',
    description: '摘要区汇总综合指数、主要事件、研判简报和热点主题，适合快速发现异常方向。',
    tips: ['指数变化是线索', '点击摘要项可以继续查看解释和贡献新闻'],
  },
  {
    chapter: '舆情', route: '/sentiment-analysis', selector: '[data-tour="sentiment-chart"]',
    eyebrow: '涉华舆情 · 趋势图', title: '点击异常日期，核对推动变化的新闻',
    description: '趋势图用于观察方向和异常点。点击日期后会打开影响新闻抽屉，帮助追溯分数变化。',
    tips: ['关注变化幅度而不只是绝对值', '用贡献新闻验证异常原因'],
  },
  {
    chapter: '知识', route: '/academic-data', selector: '[data-tour="knowledge-navigation"]', placement: 'right',
    eyebrow: '智库信息汇聚 · 领域导航', title: '按专家领域筛选 Skill 和资料来源',
    description: '左侧可以检索领域、任务和来源，并切换专家领域或打开已下载索引。',
    tips: ['先选研究领域，再搜索具体任务', '注意每项能力的来源与风险等级'],
  },
  {
    chapter: '知识', route: '/academic-data', selector: '[data-tour="knowledge-skills"]',
    eyebrow: '智库信息汇聚 · Skill 库', title: '为 Agent 选择可审查的专家能力',
    description: 'Skill 卡片说明适用任务、知识输入、激活条件、运行时和安全边界；选择后会加入当前能力列表。',
    tips: ['先读来源与激活条件', '只选择当前任务需要的能力'],
  },
  {
    chapter: '知识', route: '/academic-data', selector: '[data-tour="knowledge-databases"]', placement: 'left',
    eyebrow: '智库信息汇聚 · 数据库卡片', title: '记录知识库连接说明，不在前端保存密钥',
    description: '数据库卡片用于管理 Host、库名、用途和标签。密码与 Token 应始终放在后端密钥环境。',
    tips: ['前端只填写连接描述', '不要在用途、标签或用户名中粘贴密钥'],
  },
  {
    chapter: '分析', route: '/financial-terminal', selector: '[data-tour="financial-terminal"]',
    eyebrow: '数值分析预警 · 终端', title: '在独立终端中查看指标与预警',
    description: '数值终端承载金融与风险指标。先确认数据时间和指标口径，再把异常信号带入其他研究页面交叉验证。',
    tips: ['预警是进一步研究的入口', '不要单独用一个指标形成结论'],
  },
]

const guestSteps = [
  {
    chapter: '产出', route: '/data-assistant', selector: '[data-tour="assistant-auth-gate"]',
    eyebrow: '数据助手 · 账户权限', title: '登录后进入完整研究工作台',
    description: '工作区、知识库、定时简报、历史会话和报告生成都属于账户数据。',
    tips: ['登录后会自动加载个人工作台', '没有账号可从当前页面免费注册'],
  },
  {
    chapter: '产出', route: '/data-service/report-center', selector: '[data-tour="report-auth-gate"]',
    eyebrow: '报告中心 · 账户权限', title: '报告会与个人项目和编辑记录绑定',
    description: '这里保存助手生成的报告、上下文和历史版本。未登录不会再被误显示为“暂无报告”。',
    tips: ['登录后可以跨设备同步', '报告可继续交给助手编辑'],
  },
  {
    chapter: '完成', route: '/', selector: '[data-tour="guest-access"]', placement: 'left',
    eyebrow: '完成 · 访客模式', title: '公开浏览与账户能力已经清楚区分',
    description: '新闻、搜索和公开分析可直接浏览；收藏同步、数据助手、报告和个人配置需要登录。',
    tips: ['顶栏问号可随时重看', '登录后可在个人中心再次启动完整教程'],
  },
]

const memberSteps = [
  {
    chapter: '产出', route: '/data-assistant', selector: '[data-tour="assistant-navigation"]', placement: 'right',
    eyebrow: '数据助手 · 工作区导航', title: '在对话、收藏、知识库和控制台之间切换',
    description: '侧栏组织助手的主要入口；顶部还可以进入定时简报、历史会话和智能体报告。',
    tips: ['新任务从新建对话开始', '收藏和知识库为回答提供个人证据'],
  },
  {
    chapter: '产出', route: '/data-assistant', selector: '[data-tour="assistant-main"]', placement: 'left',
    eyebrow: '数据助手 · 研究工作区', title: '把对象、时间、证据口径和输出格式写清楚',
    description: '高质量请求应说明研究范围、证据要求、是否区分事实与推断，以及最终交付格式。',
    tips: ['要求附证据链接和置信度', '明确列出待核实信息', '重要成果保存为报告'],
  },
  {
    chapter: '产出', route: '/data-service/report-center', selector: '[data-tour="report-library"]',
    eyebrow: '报告中心 · 成果归档', title: '集中管理报告并继续迭代',
    description: '助手生成的报告会在这里按类型归档，可查看历史成果并携带上下文继续编辑。',
    tips: ['在标题中保留项目或事件名称', '发布前记录数据截止时间'],
  },
  {
    chapter: '账户', route: '/user-center/personal-center', selector: '[data-tour="account-settings"]',
    eyebrow: '个人中心 · 账户设置', title: '管理资料、模型、记忆与显示偏好',
    description: '个人中心包含基本资料、密码、API 配置、助手记忆和全站显示设置。教程入口固定在左侧栏底部。',
    tips: ['API 密钥不会在页面回显', '显示设置会在当前浏览器自动保存'],
  },
  {
    chapter: '完成', route: '/user-center/personal-center', selector: '[data-tour="account-guide-entry"]', placement: 'right',
    eyebrow: '完成 · 教程入口', title: '以后可以从这里重新观看',
    description: '点击左侧“重看新手教程”即可再次从首页开始，也可以用顶栏问号启动。',
    tips: ['方向键可前后切换', 'Esc 可随时退出'],
  },
]

const steps = computed(() => [...commonSteps, ...(authenticated.value ? memberSteps : guestSteps)])
const currentStep = computed(() => steps.value[stepIndex.value] || steps.value[0])
const progress = computed(() => `${stepIndex.value + 1} / ${steps.value.length}`)
const progressWidth = computed(() => `${((stepIndex.value + 1) / steps.value.length) * 100}%`)
const isTransitioning = computed(() => ['leaving', 'navigating', 'entering', 'settling'].includes(transitionState.value))
const spotlightMoving = computed(() => ['leaving', 'navigating', 'entering'].includes(transitionState.value))
const nextStep = computed(() => steps.value[stepIndex.value + 1])
const nextLabel = computed(() => {
  if (!nextStep.value) return '完成教程'
  return nextStep.value.route === currentStep.value.route ? '下一步' : '前往下一站'
})
const chapters = computed(() => {
  const result = []
  steps.value.forEach((step, index) => {
    let chapter = result.find((item) => item.name === step.chapter)
    if (!chapter) {
      chapter = { name: step.chapter, steps: [] }
      result.push(chapter)
    }
    chapter.steps.push({ ...step, index })
  })
  return result
})

const spotlightStyle = computed(() => {
  const rect = targetRect.value
  if (!rect) return {}
  return {
    top: `${rect.top}px`, left: `${rect.left}px`, width: `${rect.width}px`, height: `${rect.height}px`,
    borderRadius: `${rect.radius || 14}px`,
  }
})

const tooltipStyle = computed(() => {
  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight
  const width = Math.min(420, viewportWidth - 32)
  const height = Math.min(tooltip.value?.offsetHeight || 360, viewportHeight - 32)
  const rect = targetRect.value
  if (!rect || transitionState.value === 'navigating') {
    return { width: `${width}px`, left: `${Math.max(16, (viewportWidth - width) / 2)}px`, top: `${Math.max(72, (viewportHeight - height) / 2)}px` }
  }

  const gap = 18
  const candidates = {
    right: { left: rect.right + gap, top: Math.min(Math.max(16, rect.top), viewportHeight - height - 16), fits: viewportWidth - rect.right >= width + gap + 16 },
    left: { left: rect.left - width - gap, top: Math.min(Math.max(16, rect.top), viewportHeight - height - 16), fits: rect.left >= width + gap + 16 },
    bottom: { left: Math.min(Math.max(16, rect.left), viewportWidth - width - 16), top: rect.bottom + gap, fits: viewportHeight - rect.bottom >= height + gap + 16 },
    top: { left: Math.min(Math.max(16, rect.left), viewportWidth - width - 16), top: rect.top - height - gap, fits: rect.top >= height + gap + 16 },
  }
  const preferred = currentStep.value.placement
  const ordered = [preferred, 'right', 'left', 'bottom', 'top'].filter((value, index, list) => value && list.indexOf(value) === index)
  const match = ordered.map((key) => candidates[key]).find((item) => item?.fits)
  if (match) return { width: `${width}px`, left: `${match.left}px`, top: `${match.top}px` }

  const targetOnRight = (rect.left + rect.right) / 2 > viewportWidth / 2
  const targetOnBottom = (rect.top + rect.bottom) / 2 > viewportHeight / 2
  return {
    width: `${width}px`,
    left: `${targetOnRight ? 16 : viewportWidth - width - 16}px`,
    top: `${targetOnBottom ? 16 : Math.max(72, viewportHeight - height - 16)}px`,
  }
})

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, reducedMotion.value ? 0 : ms))
}

function nextFrame() {
  return new Promise((resolve) => window.requestAnimationFrame(() => window.requestAnimationFrame(resolve)))
}

function isUsableTarget(element) {
  if (!element) return false
  const style = window.getComputedStyle(element)
  const rect = element.getBoundingClientRect()
  return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) > 0 && rect.width >= 18 && rect.height >= 18
}

function normalizedRect(element) {
  const rect = element.getBoundingClientRect()
  const padding = 9
  const left = Math.max(8, rect.left - padding)
  const top = Math.max(8, rect.top - padding)
  const right = Math.min(window.innerWidth - 8, rect.right + padding)
  const bottom = Math.min(window.innerHeight - 8, rect.bottom + padding)
  const radius = Math.min(18, Math.max(10, Number.parseFloat(window.getComputedStyle(element).borderRadius) || 14))
  return { left, top, right, bottom, width: Math.max(24, right - left), height: Math.max(24, bottom - top), radius }
}

function rectIsStable(a, b) {
  if (!a || !b) return false
  return ['left', 'top', 'width', 'height'].every((key) => Math.abs(a[key] - b[key]) < 1.5)
}

async function locateTarget(generation, useSmoothScroll = true) {
  targetFound.value = false
  targetRect.value = null
  let previousRect = null
  let stableFrames = 0

  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (!visible.value || generation !== locateGeneration) return false
    await nextTick()
    const element = document.querySelector(currentStep.value.selector)
    if (isUsableTarget(element)) {
      if (attempt === 0 || !rectIsStable(previousRect, normalizedRect(element))) {
        element.scrollIntoView({ behavior: useSmoothScroll && !reducedMotion.value ? 'smooth' : 'auto', block: 'center', inline: 'nearest' })
        await delay(useSmoothScroll ? 360 : 80)
      }
      const rect = normalizedRect(element)
      stableFrames = rectIsStable(previousRect, rect) ? stableFrames + 1 : 0
      previousRect = rect
      if (stableFrames >= 2) {
        targetRect.value = rect
        targetFound.value = true
        await nextTick()
        return true
      }
    }
    await delay(90)
  }
  return false
}

async function settleTarget(generation) {
  let previousRect = targetRect.value
  let stableSince = performance.now()
  const startedAt = stableSince
  while (performance.now() - startedAt < 2600) {
    if (!visible.value || generation !== locateGeneration) return
    await delay(80)
    const element = document.querySelector(currentStep.value.selector)
    if (!isUsableTarget(element)) continue
    const rect = normalizedRect(element)
    if (!rectIsStable(previousRect, rect)) stableSince = performance.now()
    previousRect = rect
    targetRect.value = rect
    if (performance.now() - stableSince >= 560) return
  }
}

function refreshTargetRect() {
  if (!visible.value || isTransitioning.value) return
  if (resizeFrame) window.cancelAnimationFrame(resizeFrame)
  resizeFrame = window.requestAnimationFrame(() => {
    const element = document.querySelector(currentStep.value.selector)
    if (isUsableTarget(element)) {
      targetRect.value = normalizedRect(element)
      targetFound.value = true
    }
  })
}

function trackTargetPosition() {
  if (!visible.value) return
  if (targetFound.value && !['leaving', 'navigating', 'entering'].includes(transitionState.value)) {
    const element = document.querySelector(currentStep.value.selector)
    if (isUsableTarget(element)) {
      const rect = normalizedRect(element)
      if (!rectIsStable(targetRect.value, rect)) targetRect.value = rect
    }
  }
  trackingFrame = window.requestAnimationFrame(trackTargetPosition)
}

async function activateStep(index, options = {}) {
  if (!visible.value || isTransitioning.value) return
  const boundedIndex = Math.min(Math.max(0, index), steps.value.length - 1)
  const previousIndex = stepIndex.value
  const previousRoute = currentStep.value?.route
  transitionDirection.value = boundedIndex >= previousIndex ? 'forward' : 'backward'
  directoryOpen.value = false
  const generation = ++locateGeneration

  if (!options.instant) {
    transitionState.value = 'leaving'
    await delay(180)
  }

  stepIndex.value = boundedIndex
  const nextRoute = currentStep.value.route
  const routeChanged = route.path !== nextRoute
  targetFound.value = false
  targetRect.value = null
  transitionState.value = 'navigating'

  try {
    if (routeChanged) await router.push(nextRoute)
    await nextFrame()
    await delay(routeChanged ? 180 : 40)
    const found = await locateTarget(generation, !options.instant)
    if (!visible.value || generation !== locateGeneration) return
    transitionState.value = found ? 'entering' : 'ready'
    await nextFrame()
    if (found) {
      transitionState.value = 'settling'
      await settleTarget(generation)
    }
    transitionState.value = 'ready'
    tooltip.value?.focus({ preventScroll: true })
  } catch {
    transitionState.value = 'ready'
  }

  if (!routeChanged && previousRoute === nextRoute) refreshTargetRect()
}

async function openGuide() {
  authenticated.value = Boolean(getToken())
  visible.value = true
  directoryOpen.value = false
  transitionState.value = 'idle'
  document.documentElement.classList.add('is-product-tour-active')
  if (!trackingFrame) trackingFrame = window.requestAnimationFrame(trackTargetPosition)
  await nextTick()
  await activateStep(0, { instant: true })
}

function closeGuide() {
  locateGeneration += 1
  visible.value = false
  targetFound.value = false
  targetRect.value = null
  transitionState.value = 'idle'
  directoryOpen.value = false
  if (trackingFrame) window.cancelAnimationFrame(trackingFrame)
  trackingFrame = null
  document.documentElement.classList.remove('is-product-tour-active')
  try { localStorage.setItem(STORAGE_KEY, 'completed') } catch { /* storage may be blocked */ }
}

async function next() {
  if (isTransitioning.value) return
  if (stepIndex.value >= steps.value.length - 1) {
    closeGuide()
    return
  }
  await activateStep(stepIndex.value + 1)
}

async function previous() {
  if (!isTransitioning.value && stepIndex.value > 0) await activateStep(stepIndex.value - 1)
}

function jumpTo(index) {
  if (!isTransitioning.value && index !== stepIndex.value) activateStep(index)
}

function onKeydown(event) {
  if (!visible.value) return
  if (event.key === 'Escape') closeGuide()
  if (event.key === 'ArrowRight') next()
  if (event.key === 'ArrowLeft') previous()
}

function syncAuth() {
  authenticated.value = Boolean(getToken())
  if (stepIndex.value >= steps.value.length) stepIndex.value = steps.value.length - 1
}

watch(() => route.fullPath, () => {
  if (visible.value && !isTransitioning.value) window.setTimeout(refreshTargetRect, 160)
})

onMounted(() => {
  reducedMotion.value = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  window.addEventListener('openGlobeMindGuide', openGuide)
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('resize', refreshTargetRect)
  window.addEventListener('scroll', refreshTargetRect, true)
  window.addEventListener(getAuthChangedEventName(), syncAuth)
})

onUnmounted(() => {
  locateGeneration += 1
  if (resizeFrame) window.cancelAnimationFrame(resizeFrame)
  if (trackingFrame) window.cancelAnimationFrame(trackingFrame)
  document.documentElement.classList.remove('is-product-tour-active')
  window.removeEventListener('openGlobeMindGuide', openGuide)
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('resize', refreshTargetRect)
  window.removeEventListener('scroll', refreshTargetRect, true)
  window.removeEventListener(getAuthChangedEventName(), syncAuth)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="tour-shell-fade">
      <div v-if="visible" class="product-tour" :class="`product-tour--${transitionState}`" aria-live="polite">
        <div class="tour-dimmer" :class="{ 'tour-dimmer--cutout': targetFound }" aria-hidden="true" />
        <div
          v-if="targetFound"
          class="tour-spotlight"
          :class="{ 'tour-spotlight--moving': spotlightMoving }"
          :style="spotlightStyle"
          aria-hidden="true"
        />

        <Transition name="tour-status-fade">
          <div v-if="transitionState === 'navigating'" class="tour-route-status" aria-hidden="true">
            <LoaderCircle :size="18" />
            <span>正在前往</span>
            <strong>{{ currentStep.chapter }} · {{ currentStep.title }}</strong>
          </div>
        </Transition>

        <section
          ref="tooltip"
          class="tour-tooltip"
          :class="[
            `tour-tooltip--${transitionState}`,
            `tour-tooltip--${transitionDirection}`,
            { 'tour-tooltip--directory': directoryOpen },
          ]"
          :style="tooltipStyle"
          role="dialog"
          aria-modal="false"
          aria-labelledby="tour-title"
          tabindex="-1"
        >
          <header class="tour-header">
            <div class="tour-progress-wrap">
              <div class="tour-progress-meta">
                <span class="tour-chapter">{{ currentStep.chapter }}</span>
                <span>完整教程 {{ progress }}</span>
              </div>
              <div class="tour-progress" aria-hidden="true"><i :style="{ width: progressWidth }" /></div>
            </div>
            <div class="tour-header-actions">
              <button type="button" class="tour-icon-button" :aria-expanded="directoryOpen" aria-label="查看教程目录" @click="directoryOpen = !directoryOpen">
                <List :size="18" />
              </button>
              <button type="button" class="tour-icon-button" aria-label="退出新手教程" @click="closeGuide"><X :size="18" /></button>
            </div>
          </header>

          <Transition :name="transitionDirection === 'forward' ? 'tour-content-forward' : 'tour-content-backward'" mode="out-in">
            <div v-if="directoryOpen" key="directory" class="tour-directory">
              <div class="tour-directory-head">
                <strong>教程目录</strong>
                <span>{{ steps.length }} 个关键操作</span>
              </div>
              <div class="tour-directory-scroll">
                <section v-for="chapter in chapters" :key="chapter.name" class="tour-directory-group">
                  <h3>{{ chapter.name }}</h3>
                  <button
                    v-for="item in chapter.steps"
                    :key="`${chapter.name}-${item.index}`"
                    type="button"
                    :class="{ 'is-current': item.index === stepIndex, 'is-past': item.index < stepIndex }"
                    :disabled="isTransitioning"
                    @click="jumpTo(item.index)"
                  >
                    <span>{{ item.index + 1 }}</span>
                    <strong>{{ item.title }}</strong>
                    <Check v-if="item.index < stepIndex" :size="13" />
                  </button>
                </section>
              </div>
            </div>

            <div v-else-if="transitionState === 'navigating'" :key="`loading-${stepIndex}`" class="tour-loading-content">
              <LoaderCircle :size="28" />
              <p>正在加载并定位真实控件</p>
              <strong>{{ currentStep.title }}</strong>
            </div>

            <div v-else :key="stepIndex" class="tour-content">
              <p class="tour-eyebrow">{{ currentStep.eyebrow }}</p>
              <h2 id="tour-title">{{ currentStep.title }}</h2>
              <p class="tour-description">{{ currentStep.description }}</p>
              <ul>
                <li v-for="tip in currentStep.tips" :key="tip"><Check :size="14" /> <span>{{ tip }}</span></li>
              </ul>
              <p v-if="!targetFound" class="tour-target-note">目标控件仍在加载。你可以继续浏览目录，教程会在控件稳定后再进行框选。</p>
            </div>
          </Transition>

          <footer class="tour-footer">
            <button type="button" class="tour-secondary" :disabled="stepIndex === 0 || isTransitioning" @click="previous">
              <ArrowLeft :size="15" /> 上一步
            </button>
            <span class="tour-keyboard-hint">← → 切换 · Esc 退出</span>
            <button type="button" class="tour-primary" :disabled="isTransitioning" @click="next">
              {{ nextLabel }}
              <ArrowRight v-if="stepIndex < steps.length - 1" :size="15" />
            </button>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.product-tour { position: fixed; inset: 0; z-index: 2100; pointer-events: none; color: #172033; }
.tour-dimmer { position: fixed; inset: 0; background: rgba(15, 23, 42, .6); backdrop-filter: blur(2px); transition: background .35s ease, backdrop-filter .35s ease; }
.tour-dimmer--cutout { background: transparent; backdrop-filter: none; }
.product-tour--navigating .tour-dimmer { background: rgba(15, 23, 42, .68); backdrop-filter: blur(4px); }
.tour-spotlight { position: fixed; border: 2px solid #60a5fa; background: transparent; box-shadow: 0 0 0 9999px rgba(15, 23, 42, .62), inset 0 0 0 1px rgba(255, 255, 255, .68), 0 0 0 6px rgba(96, 165, 250, .2), 0 14px 46px rgba(15, 23, 42, .3); transition: top .48s cubic-bezier(.22, 1, .36, 1), left .48s cubic-bezier(.22, 1, .36, 1), width .48s cubic-bezier(.22, 1, .36, 1), height .48s cubic-bezier(.22, 1, .36, 1), opacity .2s ease, transform .3s ease; }
.tour-spotlight::after { content: ''; position: absolute; inset: -7px; border: 1px solid rgba(147, 197, 253, .56); border-radius: inherit; animation: tour-breathe 2.4s ease-in-out infinite; }
.tour-spotlight--moving { opacity: 0; transform: scale(.985); }
.tour-route-status { position: fixed; left: 50%; top: 38px; z-index: 2; display: flex; align-items: center; gap: 8px; padding: 10px 14px; border: 1px solid rgba(191, 219, 254, .42); border-radius: 999px; color: #dbeafe; background: rgba(15, 23, 42, .76); box-shadow: 0 12px 40px rgba(15, 23, 42, .26); transform: translateX(-50%); }
.tour-route-status svg, .tour-loading-content svg { animation: tour-spin .9s linear infinite; }
.tour-route-status span { font-size: 12px; color: #93c5fd; }
.tour-route-status strong { max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.tour-tooltip { position: fixed; z-index: 3; max-height: calc(100vh - 32px); overflow: hidden; border: 1px solid rgba(148, 163, 184, .34); border-radius: 18px; outline: none; background: rgba(255, 255, 255, .985); box-shadow: 0 26px 88px rgba(15, 23, 42, .3); pointer-events: auto; transition: top .48s cubic-bezier(.22, 1, .36, 1), left .48s cubic-bezier(.22, 1, .36, 1), opacity .2s ease, transform .26s cubic-bezier(.22, 1, .36, 1); }
.tour-tooltip--leaving { opacity: 0; transform: translateY(7px) scale(.985); }
.tour-tooltip--entering { opacity: 0; transform: translateY(9px) scale(.985); }
.tour-tooltip--navigating { box-shadow: 0 24px 72px rgba(15, 23, 42, .34); }
.tour-header, .tour-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 13px 16px; }
.tour-header { border-bottom: 1px solid #e8edf5; }
.tour-progress-wrap { min-width: 0; display: grid; flex: 1; gap: 7px; color: #526077; font-size: 11px; font-weight: 850; }
.tour-progress-meta { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.tour-chapter { padding: 3px 8px; border-radius: 999px; color: #1d4ed8; background: #eff6ff; }
.tour-progress { width: 100%; height: 4px; overflow: hidden; border-radius: 999px; background: #e2e8f0; }
.tour-progress i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #2563eb, #0891b2); transition: width .44s cubic-bezier(.22, 1, .36, 1); }
.tour-header-actions { display: flex; gap: 7px; }
.tour-icon-button { width: 34px; height: 34px; display: grid; place-items: center; border: 0; border-radius: 9px; color: #526077; background: #f1f5f9; cursor: pointer; transition: color .18s ease, background .18s ease, transform .18s ease; }
.tour-icon-button:hover { color: #1d4ed8; background: #e8f0ff; transform: translateY(-1px); }
.tour-content { padding: 21px 22px 20px; }
.tour-eyebrow { margin: 0 0 7px; color: #2563eb; font-size: 11px; font-weight: 900; letter-spacing: .08em; }
.tour-content h2 { margin: 0; color: #172033; font-size: 24px; line-height: 1.22; letter-spacing: -.02em; }
.tour-description { margin: 11px 0 14px; color: #526077; font-size: 14px; line-height: 1.65; }
.tour-content ul { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
.tour-content li { display: flex; align-items: flex-start; gap: 8px; color: #334155; font-size: 13px; line-height: 1.5; }
.tour-content li :deep(svg) { flex: 0 0 auto; margin-top: 3px; color: #0f8a72; }
.tour-target-note { margin: 13px 0 0; padding: 9px 10px; border-radius: 9px; color: #92400e; background: #fff7ed; font-size: 12px; line-height: 1.5; }
.tour-loading-content { min-height: 210px; display: grid; place-items: center; align-content: center; gap: 10px; padding: 28px; text-align: center; }
.tour-loading-content svg { color: #2563eb; }
.tour-loading-content p { margin: 0; color: #64748b; font-size: 12px; }
.tour-loading-content strong { max-width: 330px; color: #172033; font-size: 17px; }
.tour-directory { min-height: 260px; }
.tour-directory-head { display: flex; align-items: center; justify-content: space-between; padding: 15px 18px 11px; }
.tour-directory-head strong { font-size: 16px; }
.tour-directory-head span { color: #64748b; font-size: 11px; }
.tour-directory-scroll { max-height: min(480px, calc(100vh - 190px)); overflow: auto; padding: 0 10px 14px; }
.tour-directory-group h3 { margin: 10px 8px 5px; color: #64748b; font-size: 10px; letter-spacing: .12em; }
.tour-directory-group button { width: 100%; display: grid; grid-template-columns: 26px 1fr 18px; align-items: center; gap: 8px; padding: 8px; border: 0; border-radius: 9px; color: #334155; background: transparent; text-align: left; cursor: pointer; }
.tour-directory-group button:hover { background: #f1f5f9; }
.tour-directory-group button.is-current { color: #1d4ed8; background: #eff6ff; }
.tour-directory-group button > span { width: 24px; height: 24px; display: grid; place-items: center; border-radius: 50%; color: #64748b; background: #e2e8f0; font-size: 10px; }
.tour-directory-group button.is-current > span { color: #fff; background: #2563eb; }
.tour-directory-group button.is-past > span { color: #047857; background: #d1fae5; }
.tour-directory-group button strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; font-weight: 700; }
.tour-directory-group button svg { color: #059669; }
.tour-footer { border-top: 1px solid #e8edf5; }
.tour-footer button { min-height: 39px; display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 0 13px; border-radius: 10px; font-weight: 800; cursor: pointer; transition: transform .18s ease, box-shadow .18s ease, background .18s ease; }
.tour-footer button:not(:disabled):hover { transform: translateY(-1px); }
.tour-secondary { border: 1px solid #cbd5e1; color: #334155; background: #fff; }
.tour-secondary:disabled, .tour-primary:disabled { opacity: .42; cursor: wait; }
.tour-primary { border: 1px solid #1d4ed8; color: #fff; background: #1d4ed8; box-shadow: 0 7px 18px rgba(37, 99, 235, .2); }
.tour-primary:not(:disabled):hover { background: #1e40af; box-shadow: 0 9px 24px rgba(37, 99, 235, .28); }
.tour-keyboard-hint { color: #94a3b8; font-size: 10px; white-space: nowrap; }
.tour-shell-fade-enter-active, .tour-shell-fade-leave-active { transition: opacity .28s ease; }
.tour-shell-fade-enter-from, .tour-shell-fade-leave-to { opacity: 0; }
.tour-status-fade-enter-active, .tour-status-fade-leave-active { transition: opacity .2s ease, transform .2s ease; }
.tour-status-fade-enter-from, .tour-status-fade-leave-to { opacity: 0; transform: translate(-50%, -8px); }
.tour-content-forward-enter-active, .tour-content-forward-leave-active, .tour-content-backward-enter-active, .tour-content-backward-leave-active { transition: opacity .18s ease, transform .22s cubic-bezier(.22, 1, .36, 1); }
.tour-content-forward-enter-from { opacity: 0; transform: translateX(14px); }
.tour-content-forward-leave-to { opacity: 0; transform: translateX(-10px); }
.tour-content-backward-enter-from { opacity: 0; transform: translateX(-14px); }
.tour-content-backward-leave-to { opacity: 0; transform: translateX(10px); }
@keyframes tour-breathe { 0%, 100% { opacity: .34; transform: scale(1); } 50% { opacity: .72; transform: scale(1.006); } }
@keyframes tour-spin { to { transform: rotate(360deg); } }

@media (max-width: 760px) {
  .tour-tooltip { left: 12px !important; right: 12px; top: auto !important; bottom: 12px; width: auto !important; max-height: 64vh; }
  .tour-keyboard-hint { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .tour-dimmer, .tour-spotlight, .tour-progress i, .tour-tooltip, .tour-shell-fade-enter-active, .tour-shell-fade-leave-active, .tour-status-fade-enter-active, .tour-status-fade-leave-active, .tour-content-forward-enter-active, .tour-content-forward-leave-active, .tour-content-backward-enter-active, .tour-content-backward-leave-active { transition: none; }
  .tour-spotlight::after, .tour-route-status svg, .tour-loading-content svg { animation: none; }
}
</style>
