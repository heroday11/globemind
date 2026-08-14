<script setup>
import { ArrowLeft, Home, Search } from 'lucide-vue-next'
import { useRouter } from 'vue-router'

const router = useRouter()

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/')
}
</script>

<template>
  <main class="not-found-page">
    <section class="not-found-card" aria-labelledby="not-found-title">
      <p class="not-found-code">404 / ROUTE NOT FOUND</p>
      <h1 id="not-found-title">这个页面不在情报地图中</h1>
      <p>地址可能已更新或输入有误。你可以返回上一页，或从首页和新闻检索重新进入。</p>
      <div class="not-found-actions">
        <button type="button" class="secondary" @click="goBack"><ArrowLeft :size="18" />返回上一页</button>
        <router-link to="/"><Home :size="18" />返回首页</router-link>
        <router-link to="/data-service/data-search"><Search :size="18" />新闻检索</router-link>
      </div>
    </section>
  </main>
</template>

<style scoped>
.not-found-page {
  min-height: 100vh;
  box-sizing: border-box;
  display: grid;
  place-items: center;
  padding: 104px 20px 40px;
  background:
    radial-gradient(circle at 16% 20%, rgba(52, 119, 255, 0.15), transparent 32%),
    linear-gradient(145deg, #f7faff, #edf3ff);
}
.not-found-card {
  width: min(100%, 700px);
  padding: clamp(30px, 6vw, 64px);
  border: 1px solid rgba(76, 111, 170, 0.18);
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.82);
  box-shadow: 0 26px 70px rgba(43, 76, 129, 0.14);
}
.not-found-code { margin: 0 0 14px; color: #2f6ee5; font: 700 0.78rem/1.4 ui-monospace, monospace; letter-spacing: 0.14em; }
h1 { margin: 0; color: #132b52; font-size: clamp(2rem, 5vw, 3.6rem); line-height: 1.08; letter-spacing: -0.04em; }
.not-found-card > p:not(.not-found-code) { max-width: 570px; margin: 20px 0 0; color: #526984; font-size: 1.05rem; line-height: 1.8; }
.not-found-actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 30px; }
.not-found-actions a,
.not-found-actions button { display: inline-flex; align-items: center; gap: 8px; min-height: 44px; box-sizing: border-box; padding: 10px 16px; border: 1px solid #2764d8; border-radius: 12px; background: #2764d8; color: white; font: inherit; font-weight: 650; text-decoration: none; cursor: pointer; }
.not-found-actions .secondary { border-color: #cbd7e8; background: white; color: #29476d; }
@media (max-width: 520px) {
  .not-found-page { align-items: start; padding-top: 92px; }
  .not-found-card { border-radius: 20px; }
  .not-found-actions { flex-direction: column; }
  .not-found-actions a,
  .not-found-actions button { justify-content: center; width: 100%; }
}
</style>
