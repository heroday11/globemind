<template>
  <div class="logout-page">
    <div class="logout-card">
      <span class="logout-icon" aria-hidden="true">✓</span>
      <p class="message">已退出登录</p>
      <p class="hint">正在跳转到首页…</p>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { clearAuth } from '@/utils/auth'

const router = useRouter()
let timer = null

onMounted(() => {
  clearAuth()
  timer = setTimeout(() => {
    router.replace('/')
  }, 800)
})

onUnmounted(() => {
  if (timer) clearTimeout(timer)
})
</script>

<style scoped>
.logout-page {
  min-height: 320px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 16px;
}

.logout-card {
  background: var(--uc-surface, #fff);
  padding: 36px 40px;
  border-radius: var(--uc-radius, 12px);
  border: 1px solid var(--uc-border, #dbe3ff);
  box-shadow: var(--uc-shadow, 0 8px 24px rgba(91, 114, 223, 0.12));
  text-align: center;
  max-width: 360px;
}

.logout-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  margin-bottom: 16px;
  border-radius: 50%;
  background: linear-gradient(135deg, rgba(91, 114, 223, 0.15), rgba(102, 126, 234, 0.1));
  color: var(--uc-accent, #5b72df);
  font-size: 1.35rem;
  font-weight: 800;
}

.message {
  font-size: 1.08rem;
  font-weight: 800;
  color: var(--uc-text, #1f2a44);
  margin: 0 0 8px;
}

.hint {
  font-size: 0.9rem;
  font-weight: 500;
  color: var(--uc-muted, #60709b);
  margin: 0;
}
</style>
