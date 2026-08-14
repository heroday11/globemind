<script setup>
import { computed, nextTick, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'

defineOptions({ name: 'LegacyGroundNewsNotice' })

const route = useRoute()

const LEGACY_NOTICES = Object.freeze({
  '/data-service/ground-news-search': Object.freeze({
    code: 'ROUTE MIGRATION / GROUND-NEWS-SEARCH',
    title: '原「全球新闻搜索」入口已退役',
    legacyPath: '/data-service/ground-news-search',
    explanation:
      '旧入口不再对应一个可验证的独立搜索页面。为避免静默丢失旧链接中的任务语义或让用户误以为筛选条件已迁移，请手动选择下方目标。',
    options: Object.freeze([
      Object.freeze({
        to: '/data-service/data-search',
        title: '新闻与事件检索',
        description: '重新输入关键词、时间、来源和语言等条件；旧入口中的条件不会自动带入。',
      }),
      Object.freeze({
        to: '/data-service/ground-news',
        title: '全球新闻观察台',
        description: '浏览全球新闻概览；它不是旧搜索页的等价替代。',
      }),
    ]),
  }),
  '/data-service/ground-news-blindspot': Object.freeze({
    code: 'ROUTE MIGRATION / GROUND-NEWS-BLINDSPOT',
    title: '原「报道盲点」入口已退役',
    legacyPath: '/data-service/ground-news-blindspot',
    explanation:
      '旧盲点页面没有可确认的等价替代能力。为避免把专门分析任务静默改成普通首页浏览，请根据实际目的手动选择下方入口并重新核验来源。',
    options: Object.freeze([
      Object.freeze({
        to: '/data-service/ground-news-desk',
        title: '新闻分析工作台',
        description: '用于来源对比和事件分析；不会自动恢复旧盲点页的判断或筛选条件。',
      }),
      Object.freeze({
        to: '/data-service/ground-news',
        title: '全球新闻观察台',
        description: '用于浏览新闻概览；它不代表平台已识别或验证报道盲点。',
      }),
    ]),
  }),
})

const notice = computed(
  () => LEGACY_NOTICES[route.path] || LEGACY_NOTICES['/data-service/ground-news-search'],
)

async function resetPageFocus() {
  window.scrollTo(0, 0)
  await nextTick()
  document.querySelector('#legacy-ground-news-title')?.focus()
}

onMounted(resetPageFocus)
watch(() => route.path, resetPageFocus)
</script>

<template>
  <main class="legacy-page">
    <section class="legacy-card" aria-labelledby="legacy-ground-news-title">
      <p class="legacy-code">{{ notice.code }}</p>
      <h1 id="legacy-ground-news-title" tabindex="-1">{{ notice.title }}</h1>
      <p>
        <code>{{ notice.legacyPath }}</code>
        {{ notice.explanation }}
      </p>
      <div class="legacy-options">
        <router-link v-for="option in notice.options" :key="option.to" :to="option.to">
          <strong>{{ option.title }}</strong>
          <span>{{ option.description }}</span>
        </router-link>
      </div>
      <p class="legacy-help">
        不确定应该选哪个？<router-link to="/data-service/help-docs">查看帮助文档</router-link>，或查看
        <router-link to="/methodology">方法说明</router-link>与<router-link to="/sources">来源说明</router-link>。
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

h1:focus {
  outline: 3px solid #3475d8;
  outline-offset: 7px;
}

.legacy-card > p:not(.legacy-code) {
  margin: 20px 0 0;
  color: #526983;
  font-size: 1.03rem;
  line-height: 1.8;
}

.legacy-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 28px;
}

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

.legacy-options a:hover {
  border-color: #6996d5;
  background: #f1f6ff;
}

.legacy-options a:focus-visible,
.legacy-help a:focus-visible {
  outline: 3px solid rgba(52, 117, 216, 0.4);
  outline-offset: 3px;
}

.legacy-options span {
  color: #5c7089;
  line-height: 1.6;
}

.legacy-help a {
  color: #245dac;
  font-weight: 700;
}

@media (max-width: 650px) {
  .legacy-page {
    align-items: start;
    padding: 92px 12px 36px;
  }

  .legacy-card {
    border-radius: 18px;
  }

  .legacy-options {
    grid-template-columns: 1fr;
  }
}
</style>
