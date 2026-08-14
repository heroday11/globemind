<script setup>
import { computed, nextTick, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'

defineOptions({ name: 'LegacyDataServiceNotice' })

const route = useRoute()

const LEGACY_NOTICES = Object.freeze({
  LegacyAlertCenterNotice: Object.freeze({
    code: 'ROUTE MIGRATION / ALERT-CENTER',
    title: '原「告警中心」入口已退役',
    legacyPath: '/data-service/alert-center',
    explanation:
      '旧入口中的告警筛选、选中项和处置上下文不会自动迁移。为避免把旧任务静默解释成新的数值分析页面，请手动选择目标并重新核验状态。',
    options: Object.freeze([
      Object.freeze({
        to: '/financial-terminal',
        title: '数值分析预警',
        description: '查看当前可用的金融数值与预警界面；旧入口参数不会自动带入。',
      }),
      Object.freeze({
        to: '/status',
        title: '数据与服务状态',
        description: '先确认数据截止时间、可用性和当前降级状态。',
      }),
    ]),
  }),
  LegacyOpenComputingNotice: Object.freeze({
    code: 'ROUTE MIGRATION / OPEN-COMPUTING',
    title: '原「开放计算」入口已退役',
    legacyPath: '/data-service/open-computing',
    explanation:
      '旧计算任务、输入参数和运行状态不会自动迁移。当前没有可验证的等价入口，请手动选择检索或能力说明，并重新建立任务。',
    options: Object.freeze([
      Object.freeze({
        to: '/data-service/data-search',
        title: '新闻与事件检索',
        description: '从可解释的检索条件重新开始，不继承旧计算参数。',
      }),
      Object.freeze({
        to: '/academic-data',
        title: 'Agent 能力与连接卡',
        description: '查看已登记能力及其边界；登记不等于外部能力已接通。',
      }),
    ]),
  }),
  LegacyAlgorithmAnalysisNotice: Object.freeze({
    code: 'ROUTE MIGRATION / ALGORITHM-ANALYSIS',
    title: '原「算法分析」入口已退役',
    legacyPath: '/data-service/algorithm-analysis',
    explanation:
      '旧页面的模型、数据集和分析参数不会自动迁移，也不能据此推定任何评测仍然有效。请手动选择目标并核验方法边界。',
    options: Object.freeze([
      Object.freeze({
        to: '/methodology',
        title: '方法说明与解读边界',
        description: '先确认当前方法、证据和不可计算条件。',
      }),
      Object.freeze({
        to: '/data-service/data-search',
        title: '新闻与事件检索',
        description: '重新建立检索任务；不会恢复旧算法页面的选择。',
      }),
    ]),
  }),
  LegacyModelTestNotice: Object.freeze({
    code: 'ROUTE MIGRATION / MODEL-TEST',
    title: '原「模型测试」入口已退役',
    legacyPath: '/data-service/model-test/:modelId',
    explanation:
      '旧链接中的模型标识、数据集和测试状态不会自动迁移。模型保障页面需要登录，且只有当前清单与审阅状态可作为入口。',
    options: Object.freeze([
      Object.freeze({
        to: '/model-assurance',
        title: '模型评测与发布保障',
        description: '登录后查看当前模型清单、评测证据与阻断状态。',
      }),
      Object.freeze({
        to: '/methodology',
        title: '方法说明与解读边界',
        description: '查看模型结果的解释边界和人工复核要求。',
      }),
    ]),
  }),
})

const notice = computed(
  () => LEGACY_NOTICES[String(route.name || '')] || LEGACY_NOTICES.LegacyOpenComputingNotice,
)
const legacyReference = computed(() => {
  if (route.name !== 'LegacyModelTestNotice') return notice.value.legacyPath
  const modelId = Array.from(String(route.params.modelId || ''))
    .map((character) => {
      const code = character.codePointAt(0)
      return code <= 31 || code === 127 ? ' ' : character
    })
    .join('')
    .trim()
    .slice(0, 80)
  return modelId ? `/data-service/model-test/${modelId}` : notice.value.legacyPath
})

async function resetPageFocus() {
  window.scrollTo(0, 0)
  await nextTick()
  document.querySelector('#legacy-data-service-title')?.focus()
}

onMounted(resetPageFocus)
watch(() => route.fullPath, resetPageFocus)
</script>

<template>
  <main class="legacy-page">
    <section class="legacy-card" aria-labelledby="legacy-data-service-title">
      <p class="legacy-code">{{ notice.code }}</p>
      <h1 id="legacy-data-service-title" tabindex="-1">{{ notice.title }}</h1>
      <p><code>{{ legacyReference }}</code> {{ notice.explanation }}</p>
      <div class="legacy-options">
        <router-link v-for="option in notice.options" :key="option.to" :to="option.to">
          <strong>{{ option.title }}</strong>
          <span>{{ option.description }}</span>
        </router-link>
      </div>
      <p class="legacy-help">
        不确定应该选哪个？请先查看<router-link to="/data-service/help-docs">帮助文档</router-link>、
        <router-link to="/methodology">方法说明</router-link>与<router-link to="/status">服务状态</router-link>。
      </p>
    </section>
  </main>
</template>

<style scoped>
.legacy-page {
  min-height: 100vh;
  box-sizing: border-box;
  display: grid;
  place-items: center;
  padding: 112px 20px 48px;
  color: #173455;
  background: linear-gradient(145deg, #f8fbff, #edf3fb);
}
.legacy-card {
  width: min(100%, 780px);
  box-sizing: border-box;
  padding: clamp(28px, 6vw, 58px);
  border: 1px solid #d9e4f1;
  border-radius: 24px;
  background: #fff;
  box-shadow: 0 24px 64px rgba(38, 70, 112, 0.12);
}
.legacy-code {
  margin: 0 0 13px;
  color: #2c68c5;
  font: 750 0.76rem/1.4 ui-monospace, monospace;
  letter-spacing: 0.12em;
}
h1 {
  margin: 0;
  color: #102b50;
  font-size: clamp(2rem, 5vw, 3.45rem);
  line-height: 1.12;
  letter-spacing: -0.035em;
}
h1:focus { outline: 3px solid #3475d8; outline-offset: 7px; }
.legacy-card > p:not(.legacy-code) { margin: 20px 0 0; color: #526983; font-size: 1.03rem; line-height: 1.8; }
.legacy-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin-top: 28px; }
.legacy-options a {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 132px;
  box-sizing: border-box;
  padding: 19px;
  border: 1px solid #cddbeb;
  border-radius: 15px;
  color: #214e89;
  background: #f8fbff;
  text-decoration: none;
}
.legacy-options a:hover { border-color: #6996d5; background: #f1f6ff; }
.legacy-options a:focus-visible,
.legacy-help a:focus-visible { outline: 3px solid rgba(52, 117, 216, 0.4); outline-offset: 3px; }
.legacy-options span { color: #5c7089; line-height: 1.6; }
.legacy-help a { color: #245dac; font-weight: 700; }
@media (max-width: 650px) {
  .legacy-page { align-items: start; padding: 92px 12px 36px; }
  .legacy-card { border-radius: 18px; }
  .legacy-options { grid-template-columns: 1fr; }
}
</style>
