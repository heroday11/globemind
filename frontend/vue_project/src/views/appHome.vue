<!-- src/views/appHome.vue -->
<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  buildHomeCapabilityCards,
  useFeatureFreshness,
} from '@/features/operations/index.js'

const mounted = ref(false)
const orbX = ref(0)
const orbY = ref(0)
const statusEvaluationNow = ref(new Date())
const { report, refresh } = useFeatureFreshness()
const cards = computed(() => buildHomeCapabilityCards(report.value, {
  now: statusEvaluationNow.value,
}))
let statusClock = null

onMounted(() => {
  requestAnimationFrame(() => {
    mounted.value = true
  })
  statusEvaluationNow.value = new Date()
  statusClock = window.setInterval(() => {
    statusEvaluationNow.value = new Date()
  }, 60_000)
  void refresh()
})

onBeforeUnmount(() => {
  if (statusClock !== null) window.clearInterval(statusClock)
})

function handleMouseMove(e) {
  orbX.value = (e.clientX / window.innerWidth - 0.5) * -40
  orbY.value = (e.clientY / window.innerHeight - 0.5) * -40
}

</script>

<template>
  <div class="home" @mousemove="handleMouseMove">
    <!-- 背景光斑 -->
    <div class="bg-layer" :style="{ transform: `translate(${orbX}px, ${orbY}px)` }">
      <div class="orb orb--1" />
      <div class="orb orb--2" />
      <div class="orb orb--3" />
      <div class="orb orb--4" />
    </div>

    <!-- 网格纹理 -->
    <div class="grid-texture" />

    <!-- 主体 -->
    <div class="home-content" :class="{ 'is-visible': mounted }">
      <!-- Hero -->
      <section class="hero">
        <p class="hero-tagline">全球资料探索 &middot; 可追溯证据 &middot; 人工复核</p>
        <div class="hero-line" />
      </section>

      <!-- Bento 卡片 -->
      <section class="bento" data-tour="home-capabilities">
        <router-link
          v-for="(card, i) in cards"
          :key="card.moduleId"
          :class="['card', { 'card--featured': card.featured }]"
          :data-module-id="card.moduleId"
          :data-module-state="card.disclosure.state"
          :data-tour="`home-capability-${i + 1}`"
          :style="{ '--i': i, '--bg-y': card.bgPosition }"
          :to="`/${card.path}`"
          :aria-label="`进入${card.title}`"
          :aria-describedby="`${card.moduleId}-disclosure`"
        >
          <div class="card-shimmer" />
          <div class="card-body">
            <div class="card-icon">
              <svg v-if="card.featured" width="32" height="32" viewBox="0 0 32 32" fill="none">
                <rect
                  x="2"
                  y="2"
                  width="28"
                  height="28"
                  rx="8"
                  stroke="currentColor"
                  stroke-width="2"
                />
                <circle cx="12" cy="12" r="3" fill="currentColor" opacity="0.6" />
                <path
                  d="M22 14l-4 4-2-2-4 4"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
                <circle cx="22" cy="22" r="1.5" fill="currentColor" />
                <circle cx="10" cy="22" r="1.5" fill="currentColor" opacity="0.5" />
              </svg>
              <svg v-else-if="i === 1" width="32" height="32" viewBox="0 0 32 32" fill="none">
                <circle cx="16" cy="16" r="12" stroke="currentColor" stroke-width="2" />
                <circle cx="16" cy="16" r="4" fill="currentColor" opacity="0.5" />
                <circle cx="8" cy="10" r="1.5" fill="currentColor" opacity="0.6" />
                <circle cx="24" cy="20" r="1.5" fill="currentColor" opacity="0.6" />
                <circle cx="20" cy="8" r="1" fill="currentColor" opacity="0.4" />
                <circle cx="10" cy="24" r="1" fill="currentColor" opacity="0.4" />
                <line
                  x1="14"
                  y1="14"
                  x2="9"
                  y2="10.5"
                  stroke="currentColor"
                  stroke-width="1"
                  opacity="0.4"
                />
                <line
                  x1="18"
                  y1="18"
                  x2="22.5"
                  y2="19"
                  stroke="currentColor"
                  stroke-width="1"
                  opacity="0.4"
                />
              </svg>
              <svg v-else-if="i === 2" width="32" height="32" viewBox="0 0 32 32" fill="none">
                <circle cx="16" cy="16" r="14" stroke="currentColor" stroke-width="2" />
                <circle
                  cx="16"
                  cy="14"
                  r="6"
                  stroke="currentColor"
                  stroke-width="1.5"
                  opacity="0.5"
                />
                <path
                  d="M9 22l3-4h3l2 3h3l3-5"
                  stroke="currentColor"
                  stroke-width="1.5"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  opacity="0.6"
                />
              </svg>
              <svg v-else width="32" height="32" viewBox="0 0 32 32" fill="none">
                <rect
                  x="3"
                  y="5"
                  width="12"
                  height="10"
                  rx="2"
                  stroke="currentColor"
                  stroke-width="2"
                />
                <rect
                  x="17"
                  y="9"
                  width="12"
                  height="14"
                  rx="2"
                  stroke="currentColor"
                  stroke-width="2"
                  opacity="0.6"
                />
                <rect
                  x="17"
                  y="5"
                  width="12"
                  height="6"
                  rx="2"
                  stroke="currentColor"
                  stroke-width="2"
                />
                <line
                  x1="9"
                  y1="19"
                  x2="9"
                  y2="24"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                />
                <line
                  x1="6"
                  y1="21.5"
                  x2="12"
                  y2="21.5"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                />
              </svg>
            </div>
            <div class="card-copy">
              <h3 class="card-title">{{ card.title }}</h3>
              <p class="card-desc">{{ card.desc }}</p>
            </div>
            <dl
              :id="`${card.moduleId}-disclosure`"
              class="card-disclosure"
              :data-state="card.disclosure.state"
            >
              <div>
                <dt>范围</dt>
                <dd>{{ card.disclosure.scope }}</dd>
              </div>
              <div>
                <dt>状态生成</dt>
                <dd>{{ card.disclosure.generatedAt }}</dd>
              </div>
              <div>
                <dt>数据截止</dt>
                <dd>{{ card.disclosure.cutoff }}</dd>
              </div>
              <div>
                <dt>新鲜度</dt>
                <dd>{{ card.disclosure.freshness }}</dd>
              </div>
              <div>
                <dt>方法 / 证据</dt>
                <dd>{{ card.disclosure.method }}；{{ card.disclosure.evidence }}</dd>
              </div>
              <div>
                <dt>状态来源</dt>
                <dd>{{ card.disclosure.statusSource }}</dd>
              </div>
            </dl>
            <div class="card-action">
              <span class="card-cta">
                进入
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" class="card-cta-arrow">
                  <path
                    d="M6 4l4 4-4 4"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                </svg>
              </span>
            </div>
          </div>
        </router-link>
      </section>
    </div>

    <!-- Footer — pinned to page bottom -->
    <footer class="home-footer">
      <span class="footer-text"
        >&copy; {{ new Date().getFullYear() }} GlobeMind. All rights reserved.</span
      >
    </footer>
  </div>
</template>

<style scoped>
/* ============================================================
   HOME — Premium redesign
   ============================================================ */

/* ---- Base ---- */
.home {
  position: relative;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding-top: 64px; /* fixed navbar height */
  background: url('/imgs/home/hero-bg.webp') center/cover no-repeat;
  overflow-x: hidden;
  overflow-y: auto;
  isolation: isolate;
}

/* ---- Background orbs ---- */
.bg-layer {
  position: fixed;
  inset: -10%;
  pointer-events: none;
  z-index: 0;
  transition: transform 0.6s cubic-bezier(0.25, 0.46, 0.45, 0.94);
}

.orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(100px);
  opacity: 0.45;
}

.orb--1 {
  width: 620px;
  height: 620px;
  top: 10%;
  left: 5%;
  background: radial-gradient(circle, rgba(59, 130, 246, 0.28), transparent 70%);
  animation: orbFloat1 18s ease-in-out infinite;
}

.orb--2 {
  width: 480px;
  height: 480px;
  top: 45%;
  right: -5%;
  background: radial-gradient(circle, rgba(139, 92, 246, 0.24), transparent 70%);
  animation: orbFloat2 22s ease-in-out infinite;
}

.orb--3 {
  width: 360px;
  height: 360px;
  bottom: 5%;
  left: 30%;
  background: radial-gradient(circle, rgba(6, 182, 212, 0.22), transparent 70%);
  animation: orbFloat3 20s ease-in-out infinite;
}

.orb--4 {
  width: 280px;
  height: 280px;
  top: 25%;
  left: 55%;
  background: radial-gradient(circle, rgba(245, 158, 11, 0.14), transparent 70%);
  animation: orbFloat4 24s ease-in-out infinite;
}

@keyframes orbFloat1 {
  0%,
  100% {
    transform: translate(0, 0) scale(1);
  }
  33% {
    transform: translate(40px, -30px) scale(1.08);
  }
  66% {
    transform: translate(-20px, 20px) scale(0.94);
  }
}

@keyframes orbFloat2 {
  0%,
  100% {
    transform: translate(0, 0) scale(1);
  }
  50% {
    transform: translate(-35px, -25px) scale(1.1);
  }
}

@keyframes orbFloat3 {
  0%,
  100% {
    transform: translate(0, 0) scale(1);
  }
  33% {
    transform: translate(25px, 30px) scale(0.92);
  }
  66% {
    transform: translate(-30px, -15px) scale(1.06);
  }
}

@keyframes orbFloat4 {
  0%,
  100% {
    transform: translate(0, 0) scale(1);
  }
  50% {
    transform: translate(-20px, -35px) scale(1.12);
  }
}

/* ---- Grid texture ---- */
.grid-texture {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  opacity: 0.25;
  background-image:
    linear-gradient(rgba(59, 130, 246, 0.06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(59, 130, 246, 0.06) 1px, transparent 1px);
  background-size: 64px 64px;
  mask-image: radial-gradient(ellipse 70% 70% at 50% 40%, black 30%, transparent 70%);
  -webkit-mask-image: radial-gradient(ellipse 70% 70% at 50% 40%, black 30%, transparent 70%);
}

/* ---- Main content wrapper ---- */
.home-content {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 1280px;
  padding: 0 32px;
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transform: translateY(20px);
  transition:
    opacity 0.8s ease,
    transform 0.8s ease;
}

.home-content.is-visible {
  opacity: 1;
  transform: translateY(0);
}

/* ============================================================
   Hero
   ============================================================ */
.hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: 40px;
}

.hero-tagline {
  margin: 0;
  font-size: 21px;
  font-weight: 500;
  letter-spacing: 0.08em;
  color: rgba(71, 85, 105, 0.75);
  animation: fadeInUp 0.8s 0.15s ease both;
}

.hero-line {
  width: 48px;
  height: 3px;
  margin-top: 16px;
  border-radius: 2px;
  background: linear-gradient(90deg, #3b82f6, #8b5cf6, #06b6d4);
  animation: fadeInUp 0.8s 0.3s ease both;
}

/* ============================================================
   Bento grid
   ============================================================ */
.bento {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 60px;
  width: 100%;
  margin-bottom: 32px;
}

@media (max-width: 860px) {
  .bento {
    grid-template-columns: 1fr;
    gap: 20px;
  }
}

/* ---- Card ---- */
.card {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-height: 390px;
  padding: 34px 40px 26px;
  border-radius: 20px;
  background-color: rgba(255, 255, 255, 0.12);
  background-image:
    linear-gradient(90deg, rgba(255, 255, 255, 0.98) 0%, rgba(255, 255, 255, 0.94) 60%, rgba(255, 255, 255, 0.78) 100%),
    url('/imgs/home/home-cards.webp');
  background-size: 100% 100%, 100% 400%;
  background-position: center, center var(--bg-y);
  background-repeat: no-repeat;
  border: 1px solid rgba(255, 255, 255, 0.55);
  cursor: pointer;
  overflow: hidden;
  transition:
    transform 0.4s cubic-bezier(0.25, 0.46, 0.45, 0.94),
    box-shadow 0.4s ease,
    border-color 0.4s ease;
  box-shadow:
    0 1px 3px rgba(15, 23, 42, 0.04),
    0 4px 16px rgba(15, 23, 42, 0.05);
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
  animation: fadeInUp 0.7s calc(0.4s + var(--i) * 0.1s) ease both;
  color: inherit;
  text-decoration: none;
}

.card::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  pointer-events: none;
  z-index: 0;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6);
}

.card:hover {
  transform: translateY(-6px);
  box-shadow:
    0 2px 6px rgba(15, 23, 42, 0.06),
    0 10px 32px rgba(15, 23, 42, 0.1),
    0 0 0 1px rgba(59, 130, 246, 0.12);
  border-color: rgba(59, 130, 246, 0.2);
}

.card:focus-visible {
  outline: 2px solid #3b82f6;
  outline-offset: 3px;
}

/* Featured card glow */
.card--featured {
  background-color: rgba(255, 255, 255, 0.18);
  border-color: rgba(59, 130, 246, 0.18);
  box-shadow:
    0 2px 8px rgba(59, 130, 246, 0.06),
    0 8px 24px rgba(15, 23, 42, 0.06),
    0 0 0 1px rgba(59, 130, 246, 0.08);
}

.card--featured:hover {
  border-color: rgba(59, 130, 246, 0.28);
  box-shadow:
    0 2px 8px rgba(59, 130, 246, 0.08),
    0 14px 40px rgba(59, 130, 246, 0.14),
    0 0 0 1px rgba(59, 130, 246, 0.16);
}

/* Shimmer — diagonal sweep */
.card-shimmer {
  position: absolute;
  inset: -40%;
  z-index: 1;
  pointer-events: none;
  opacity: 0;
  background:
    radial-gradient(circle at 30% 40%, rgba(255, 255, 255, 0.55) 0%, rgba(255, 255, 255, 0) 52%),
    linear-gradient(
      100deg,
      rgba(255, 255, 255, 0) 0%,
      rgba(56, 189, 248, 0.18) 42%,
      rgba(255, 255, 255, 0.42) 50%,
      rgba(37, 99, 235, 0.16) 58%,
      rgba(255, 255, 255, 0) 100%
    );
  transform: translateX(-65%) rotate(14deg);
  mix-blend-mode: overlay;
  transition: opacity 0.3s ease;
}

.card:hover .card-shimmer {
  opacity: 1;
  animation: cardShimmer 1.05s ease-in-out both;
}

@keyframes cardShimmer {
  0% {
    transform: translateX(-65%) rotate(14deg);
  }
  100% {
    transform: translateX(65%) rotate(14deg);
  }
}

/* ---- Card body ---- */
.card-body {
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.card-icon {
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  background: rgba(59, 130, 246, 0.08);
  color: #3b82f6;
  transition:
    background 0.3s ease,
    color 0.3s ease,
    transform 0.3s ease;
}

.card:hover .card-icon {
  background: rgba(59, 130, 246, 0.14);
  color: #2563eb;
  transform: scale(1.08);
}

.card--featured .card-icon {
  background: rgba(59, 130, 246, 0.12);
  color: #2563eb;
}

.card-copy {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.card-title {
  font-size: 20px;
  font-weight: 700;
  color: #1e293b;
  letter-spacing: -0.01em;
  margin: 0;
}

.card-desc {
  font-size: 14px;
  color: #64748b;
  line-height: 1.6;
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card-disclosure {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 0;
}

.card-disclosure > div {
  min-width: 0;
  padding: 8px 10px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.46);
}

.card-disclosure dt {
  margin-bottom: 3px;
  color: #64748b;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.05em;
}

.card-disclosure dd {
  margin: 0;
  color: #334155;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.card-disclosure[data-state='unknown'] > div,
.card-disclosure[data-state='not_configured'] > div {
  background: rgba(248, 250, 252, 0.62);
}

/* ---- Stat badge ---- */
.card-stat {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}

.card-stat-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.card-stat--multi {
  align-items: stretch;
  gap: 18px;
}

.card-stat--multi .card-stat-item {
  flex-direction: column;
  align-items: flex-start;
  gap: 5px;
}

.card-stat--multi .card-stat-item + .card-stat-item {
  padding-left: 18px;
  border-left: 1px solid rgba(148, 163, 184, 0.28);
}

.card-stat--multi .card-stat-label {
  font-weight: 700;
  color: #475569;
  letter-spacing: 0.04em;
}

.card-stat-num {
  font-size: 22px;
  font-weight: 800;
  color: #1e293b;
  letter-spacing: -0.02em;
  line-height: 1;
}

.card-stat-label {
  font-size: 12px;
  font-weight: 500;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

/* ---- Card action ---- */
.card-action {
  position: relative;
  z-index: 2;
  display: flex;
  justify-content: flex-start;
  margin-top: 14px;
}

.card-cta {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 3px;
  width: 82px;
  font-size: 12px;
  font-weight: 600;
  color: #fff;
  padding: 6px 0;
  border-radius: 999px;
  background: rgba(50, 125, 248, 1);
  transition: all 0.3s ease;
}

.card:hover .card-cta {
  background: #2563eb;
  color: #fff;
}

.card-cta-arrow {
  transition: transform 0.3s ease;
}

.card:hover .card-cta-arrow {
  transform: translateX(3px);
}

.card--featured .card-cta {
  background: rgba(50, 125, 248, 1);
  color: #fff;
}

.card--featured:hover .card-cta {
  background: #1d4ed8;
  color: #fff;
}

/* ============================================================
   Footer
   ============================================================ */
.home-footer {
  position: relative;
  z-index: 1;
  width: 100%;
  text-align: center;
  padding: 12px 24px;
  margin-top: auto;
  animation: fadeInUp 0.7s 0.8s ease both;
}

.footer-text {
  font-size: 12px;
  color: #94a3b8;
  font-weight: 500;
  letter-spacing: 0.02em;
}

/* ============================================================
   Shared keyframes
   ============================================================ */
@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(24px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* ============================================================
   Mobile responsive
   ============================================================ */
@media (max-width: 768px) {
  .home {
    padding-top: 56px;
  }

  .home-content {
    padding: 0 16px;
    justify-content: flex-start;
  }

  .hero {
    margin-bottom: 28px;
  }

  .hero-tagline {
    font-size: 16px;
    text-align: center;
    padding: 0 16px;
  }

  .hero-line {
    width: 40px;
    margin-top: 12px;
  }

  .bento {
    gap: 20px;
    margin-bottom: 28px;
  }

  .card {
    min-height: 360px;
    padding: 22px 20px 18px;
    border-radius: 16px;
  }

  .card-body {
    gap: 10px;
  }

  .card-icon {
    width: 36px;
    height: 36px;
  }

  .card-icon svg {
    width: 24px;
    height: 24px;
  }

  .card-title {
    font-size: 17px;
  }

  .card-desc {
    font-size: 13px;
  }

  .card-stat-num {
    font-size: 18px;
  }

  .card-stat-label {
    font-size: 11px;
  }

  .card-stat--multi {
    gap: 14px;
  }

  .card-stat--multi .card-stat-item + .card-stat-item {
    padding-left: 14px;
  }

  .card-action {
    margin-top: 8px;
  }

  .card-cta {
    width: 74px;
    font-size: 11px;
    padding: 5px 0;
  }

  .home-footer {
    padding: 10px 16px;
  }
}

@media (max-width: 480px) {
  .home {
    padding-top: 52px;
  }

  .home-content {
    padding: 0 12px;
    justify-content: flex-start;
  }

  .hero-tagline {
    font-size: 14px;
  }

  .card {
    min-height: 500px;
    padding: 18px 16px 14px;
  }

  .card-disclosure {
    grid-template-columns: 1fr;
  }

  .card-title {
    font-size: 15px;
  }

  .card-desc {
    font-size: 12px;
  }

  .card-stat-num {
    font-size: 16px;
  }

  .card-stat--multi {
    gap: 12px;
  }

  .card-stat--multi .card-stat-item + .card-stat-item {
    padding-left: 12px;
  }

  .home-footer {
    padding: 10px 12px;
  }

  .footer-text {
    font-size: 11px;
  }
}
</style>
