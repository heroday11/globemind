<script setup>
import { onMounted } from 'vue'

defineOptions({ name: 'LegacyDataStatisticsNotice' })

onMounted(() => {
  window.scrollTo(0, 0)
  requestAnimationFrame(() => document.querySelector('#legacy-route-title')?.focus())
})
</script>

<template>
  <main class="legacy-page">
    <section class="legacy-card" aria-labelledby="legacy-route-title">
      <p class="legacy-code">ROUTE MIGRATION / DATA-STATISTICS</p>
      <h1 id="legacy-route-title" tabindex="-1">原「数据统计」入口已退役</h1>
      <p>
        <code>/data-statistics</code> 不再代表一个独立的统计模块。为避免把旧链接静默映射成不同语义的产品，请根据任务手动选择下方入口。
      </p>
      <div class="legacy-options">
        <router-link to="/data-service/ground-news-desk">
          <strong>新闻分析工作台</strong>
          <span>用于事件聚合、来源对比与报道分析；它不是旧统计页的等价替代。</span>
        </router-link>
        <router-link to="/data-service/data-search">
          <strong>新闻与事件检索</strong>
          <span>用于查找和筛选可用记录，并进入原始来源核验。</span>
        </router-link>
      </div>
      <p class="legacy-help">不确定应该选哪个？<router-link to="/data-service/help-docs">查看帮助文档</router-link>。</p>
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
