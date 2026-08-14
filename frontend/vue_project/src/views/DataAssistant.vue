<script setup>
import { defineAsyncComponent } from 'vue'

const AssistantExperience = defineAsyncComponent({
  loader: () => import('@/features/assistant/index.js').then((mod) => mod.AssistantExperience),
  delay: 80,
  timeout: 30000,
})

const props = defineProps({
  embedded: {
    type: Boolean,
    default: false,
  },
  pageSkill: {
    type: Object,
    default: () => ({}),
  },
})

const emit = defineEmits(['page-action'])
</script>

<template>
  <Suspense>
    <AssistantExperience
      :embedded="props.embedded"
      :page-skill="props.pageSkill"
      @page-action="emit('page-action', $event)"
    />
    <template #fallback>
      <div class="assistant-loading-shell" :class="{ 'assistant-loading-shell--embedded': props.embedded }">
        <aside v-if="!props.embedded" class="assistant-loading-rail">
          <span class="assistant-loading-logo">G</span>
          <span v-for="item in 5" :key="item" class="assistant-loading-rail-line" />
        </aside>
        <main class="assistant-loading-main">
          <div class="assistant-loading-topline" />
          <section class="assistant-loading-panel">
            <span class="assistant-loading-kicker" />
            <span class="assistant-loading-title" />
            <span class="assistant-loading-title assistant-loading-title--short" />
            <span class="assistant-loading-copy" />
            <span class="assistant-loading-copy assistant-loading-copy--wide" />
            <div class="assistant-loading-cards">
              <span v-for="item in 4" :key="item" />
            </div>
          </section>
          <div class="assistant-loading-composer" />
        </main>
      </div>
    </template>
  </Suspense>
</template>

<style scoped>
.assistant-loading-shell {
  margin-top: 64px;
  height: calc(100vh - 64px);
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  background: linear-gradient(180deg, #f8fbff, #eef5ff);
  overflow: hidden;
}

.assistant-loading-shell--embedded {
  margin-top: 0;
  height: 100%;
  grid-template-columns: minmax(0, 1fr);
}

.assistant-loading-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 18px;
  padding: 18px 10px;
  border-right: 1px solid rgba(37, 99, 235, 0.1);
  background: rgba(255, 255, 255, 0.72);
}

.assistant-loading-logo,
.assistant-loading-rail-line,
.assistant-loading-topline,
.assistant-loading-kicker,
.assistant-loading-title,
.assistant-loading-copy,
.assistant-loading-cards span,
.assistant-loading-composer {
  background: linear-gradient(90deg, rgba(226, 232, 240, 0.7), rgba(255, 255, 255, 0.95), rgba(226, 232, 240, 0.7));
  background-size: 220% 100%;
  animation: assistant-loading-shimmer 1.4s ease-in-out infinite;
}

.assistant-loading-logo {
  width: 42px;
  height: 42px;
  border-radius: 13px;
  display: grid;
  place-items: center;
  color: rgba(37, 99, 235, 0.28);
  font-weight: 900;
}

.assistant-loading-rail-line {
  width: 56px;
  height: 54px;
  border-radius: 14px;
}

.assistant-loading-main {
  min-width: 0;
  display: grid;
  grid-template-rows: 60px minmax(0, 1fr) auto;
}

.assistant-loading-topline {
  margin: 16px 20px;
  border-radius: 999px;
}

.assistant-loading-panel {
  width: min(980px, calc(100% - 48px));
  align-self: center;
  justify-self: center;
  display: grid;
  gap: 16px;
}

.assistant-loading-kicker {
  width: 180px;
  height: 12px;
  border-radius: 999px;
}

.assistant-loading-title {
  width: min(720px, 100%);
  height: 58px;
  border-radius: 12px;
}

.assistant-loading-title--short {
  width: min(460px, 74%);
}

.assistant-loading-copy {
  width: min(560px, 88%);
  height: 18px;
  border-radius: 999px;
}

.assistant-loading-copy--wide {
  width: min(720px, 100%);
}

.assistant-loading-cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0;
  margin-top: 18px;
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 18px;
  overflow: hidden;
}

.assistant-loading-cards span {
  min-height: 120px;
  border-right: 1px solid rgba(15, 23, 42, 0.07);
}

.assistant-loading-cards span:last-child {
  border-right: 0;
}

.assistant-loading-composer {
  width: min(900px, calc(100% - 48px));
  height: 94px;
  margin: 0 auto 26px;
  border-radius: 18px;
}

@keyframes assistant-loading-shimmer {
  0% { background-position: 120% 0; }
  100% { background-position: -120% 0; }
}

@media (max-width: 760px) {
  .assistant-loading-shell {
    grid-template-columns: minmax(0, 1fr);
  }

  .assistant-loading-rail {
    display: none;
  }

  .assistant-loading-panel,
  .assistant-loading-composer {
    width: calc(100% - 28px);
  }

  .assistant-loading-title {
    height: 44px;
  }

  .assistant-loading-cards {
    grid-template-columns: 1fr;
  }

  .assistant-loading-cards span {
    min-height: 76px;
    border-right: 0;
    border-bottom: 1px solid rgba(15, 23, 42, 0.07);
  }
}
</style>
