<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  countryProfileCatalogApi,
  createLatestCountryProfileCatalogRequest,
} from '@/features/country-profiles/index.js'

defineOptions({ name: 'CountryProfileCatalog' })

const GENERIC_FAILURE = '国家档案目录暂不可用，请稍后重试。'
const pageTitle = ref(null)
const errorAlert = ref(null)
const request = createLatestCountryProfileCatalogRequest()
const state = ref(Object.freeze({ phase: 'idle', catalog: null, error: '' }))

const catalog = computed(() => state.value.catalog)
const sectionRows = computed(() => {
  if (!catalog.value) return []
  const fields = catalog.value.profileSchema.fields
  return catalog.value.profileSchema.sections.map((section) => Object.freeze({
    ...section,
    fields: Object.freeze(fields.filter((field) => field.sectionId === section.sectionId)),
  }))
})

const evidenceRows = computed(() => {
  const evidence = catalog.value?.profileSchema.minimumEvidence
  if (!evidence) return []
  return Object.freeze([
    ['来源定位', evidence.sourceLocator],
    ['来源权威性', evidence.sourceAuthority],
    ['来源抓取时间', evidence.sourceRetrievedAt],
    ['来源截止时间', evidence.sourceCutoff],
    ['未来截止时间策略', evidence.futureSourceCutoffPolicy],
    ['许可状态', evidence.licenseState],
    ['数据 owner 角色', evidence.ownerRole],
    ['owner 标识', evidence.ownerIdentifier],
    ['人工复核状态', evidence.reviewState],
    ['复核人标识', evidence.reviewerIdentifier],
    ['复核时间', evidence.reviewedAt],
    ['复核有效期', evidence.reviewExpiresAt],
    ['过期复核策略', evidence.expiredReviewPolicy],
    ['无效证据策略', evidence.invalidEvidencePolicy],
  ])
})

async function focusError() {
  await nextTick()
  errorAlert.value?.focus()
}

async function loadCatalog() {
  state.value = Object.freeze({ phase: 'loading', catalog: null, error: '' })
  const outcome = await request.run((signal) => countryProfileCatalogApi.fetchCatalog({ signal }))
  if (outcome.status === 'stale' || outcome.status === 'aborted') return
  if (outcome.status === 'error') {
    state.value = Object.freeze({ phase: 'error', catalog: null, error: GENERIC_FAILURE })
    await focusError()
    return
  }
  state.value = Object.freeze({ phase: 'ready', catalog: outcome.value, error: '' })
}

function formatTimestamp(value) {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '不可用' : parsed.toLocaleString('zh-CN')
}

onMounted(async () => {
  await nextTick()
  pageTitle.value?.focus()
  await loadCatalog()
})

onBeforeUnmount(() => request.cancel())
</script>

<template>
  <main class="country-catalog-page" aria-labelledby="country-catalog-title">
    <header class="hero">
      <p class="eyebrow">COUNTRY PROFILE / SCHEMA CATALOG ONLY</p>
      <h1 id="country-catalog-title" ref="pageTitle" tabindex="-1">国家档案目录</h1>
      <p class="hero-copy">
        这里公开未来标准国家档案的字段结构和最低证据门禁。当前目录不包含任何国家事实、
        国家页面或试点国家，也不会执行外部查询。
      </p>
      <div class="boundary" role="note">
        <strong>可信边界</strong>
        <span>
          当前只有 <code>schema_catalog_only</code>。没有具名 country owner、许可结论、来源与 cutoff
          证据或国家研究员验收，因此仍未完成 CD-01，也不能据此称国家档案能力已经上线。
        </span>
      </div>
    </header>

    <section
      class="panel status-panel"
      aria-labelledby="catalog-status-title"
      :aria-busy="state.phase === 'loading'"
    >
      <div class="section-heading">
        <div>
          <p class="eyebrow">READ-ONLY STATUS</p>
          <h2 id="catalog-status-title">目录状态</h2>
        </div>
        <button type="button" :disabled="state.phase === 'loading'" @click="loadCatalog">
          {{ state.phase === 'loading' ? '正在读取…' : '重新读取' }}
        </button>
      </div>

      <p v-if="state.phase === 'loading'" class="state-message" role="status" aria-live="polite">
        正在读取静态 schema；旧响应已清除，不会在刷新期间继续展示。
      </p>
      <p
        v-else-if="state.phase === 'error'"
        ref="errorAlert"
        class="state-message state-message--error"
        role="alert"
        aria-live="assertive"
        aria-atomic="true"
        tabindex="-1"
      >
        {{ state.error }} 当前不展示缓存或推测的目录内容。
      </p>

      <template v-else-if="state.phase === 'ready' && catalog">
        <div class="status-grid" aria-label="国家档案目录状态摘要">
          <div><span>可用性</span><strong>available=false</strong></div>
          <div><span>运行状态</span><strong>operational_state=not_configured</strong></div>
          <div><span>在线探测</span><strong>live_checked=false</strong></div>
          <div><span>实现范围</span><strong>schema_catalog_only</strong></div>
          <div><span>已发布档案</span><strong>profiles=[]</strong></div>
          <div><span>响应生成</span><strong>{{ formatTimestamp(catalog.generatedAt) }}</strong></div>
        </div>
        <p class="empty-state" role="status" aria-live="polite">
          <strong>发布状态：未配置</strong>
          空数组是明确的未配置状态，不代表覆盖了零个国家，也不代表任何国家数据通过审核。
        </p>
        <ul class="reason-list" aria-label="阻断原因代码">
          <li v-for="reason in catalog.reasonCodes" :key="reason"><code>{{ reason }}</code></li>
        </ul>
      </template>
    </section>

    <template v-if="state.phase === 'ready' && catalog">
      <section class="panel" aria-labelledby="schema-identity-title">
        <div class="section-heading">
          <div>
            <p class="eyebrow">VERSIONED IDENTITY</p>
            <h2 id="schema-identity-title">版本与标识规则</h2>
          </div>
          <span class="pill">{{ catalog.profileSchema.fields.length }} fields</span>
        </div>
        <dl class="definition-grid">
          <div><dt>Schema</dt><dd><code>{{ catalog.profileSchema.schemaVersion }}</code></dd></div>
          <div><dt>国家标识</dt><dd>{{ catalog.profileSchema.countryIdentifierStandard }}</dd></div>
          <div><dt>国家代码模式</dt><dd><code>{{ catalog.profileSchema.countryIdentifierPattern }}</code></dd></div>
          <div><dt>档案 ID</dt><dd><code>{{ catalog.profileSchema.profileIdentifierFormat }}</code></dd></div>
        </dl>
      </section>

      <section class="panel" aria-labelledby="schema-sections-title">
        <div class="section-heading">
          <div>
            <p class="eyebrow">STRUCTURE, NOT FACTS</p>
            <h2 id="schema-sections-title">档案字段结构</h2>
          </div>
          <span class="pill">{{ sectionRows.length }} sections</span>
        </div>
        <p class="boundary-copy">
          下列条目只是字段槽位和发布要求；页面不会为任何国家填入值，也不会从字段名推导事实。
        </p>
        <div class="section-grid">
          <article v-for="section in sectionRows" :key="section.sectionId" class="section-card">
            <header>
              <h3>{{ section.title }}</h3>
              <code>{{ section.sectionId }}</code>
            </header>
            <p>{{ section.purpose }}</p>
            <ul>
              <li v-for="field in section.fields" :key="field.fieldId">
                <div>
                  <strong>{{ field.title }}</strong>
                  <code>{{ field.fieldId }}</code>
                </div>
                <span>
                  {{ field.valueKind }} · {{ field.cardinality }} ·
                  {{ field.requiredForPublish ? '发布必填' : '可选' }} · evidence required
                </span>
              </li>
            </ul>
          </article>
        </div>
      </section>

      <section class="panel" aria-labelledby="evidence-gate-title">
        <div class="section-heading">
          <div>
            <p class="eyebrow">MINIMUM PUBLISH GATE</p>
            <h2 id="evidence-gate-title">最低证据门禁</h2>
          </div>
          <span class="pill pill--blocked">fail closed</span>
        </div>
        <p class="boundary-copy">
          这些是未来档案发布前必须满足的契约，不是本目录已经拥有的证据。
        </p>
        <dl class="evidence-grid">
          <div v-for="([label, value]) in evidenceRows" :key="label">
            <dt>{{ label }}</dt>
            <dd><code>{{ value }}</code></dd>
          </div>
        </dl>
      </section>
    </template>
  </main>
</template>

<style scoped>
.country-catalog-page {
  min-height: 100vh;
  padding: 42px clamp(18px, 5vw, 72px) 72px;
  color: #17233a;
  background:
    radial-gradient(circle at 90% 3%, rgb(50 122 171 / 12%), transparent 30%),
    linear-gradient(165deg, #f8fbff 0%, #edf4fa 72%);
}

.hero,
.panel {
  width: min(1180px, 100%);
  margin-inline: auto;
}

.hero { margin-bottom: 24px; }
.eyebrow { margin: 0 0 8px; color: #176a85; font: 800 12px/1.4 ui-monospace, monospace; letter-spacing: .13em; }
h1 { margin: 0; font-size: clamp(32px, 5vw, 54px); line-height: 1.08; }
h2 { margin: 0; font-size: 22px; }
h3 { margin: 0; font-size: 18px; }
.hero-copy,
.boundary-copy { max-width: 850px; color: #4b5e73; line-height: 1.75; }
.boundary {
  display: flex;
  gap: 14px;
  padding: 16px 18px;
  border: 1px solid #84adbf;
  border-radius: 14px;
  background: rgb(255 255 255 / 82%);
  line-height: 1.65;
}
.boundary strong { flex: 0 0 auto; color: #07566f; }
.panel {
  margin-bottom: 20px;
  padding: clamp(18px, 3vw, 28px);
  border: 1px solid #cbdbe6;
  border-radius: 18px;
  background: rgb(255 255 255 / 92%);
  box-shadow: 0 14px 35px rgb(41 73 97 / 8%);
}
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
button {
  min-width: 112px;
  min-height: 44px;
  padding: 9px 15px;
  border: 1px solid #176a85;
  border-radius: 10px;
  color: #07566f;
  background: #fff;
  font: inherit;
  font-weight: 750;
  cursor: pointer;
}
button:disabled { cursor: wait; opacity: .62; }
button:focus-visible,
[tabindex='-1']:focus { outline: 3px solid #ed9b27; outline-offset: 3px; }
.state-message,
.empty-state {
  margin: 20px 0 0;
  padding: 16px;
  border-radius: 12px;
  background: #edf6fa;
  line-height: 1.6;
}
.state-message--error { border: 1px solid #b74b54; color: #832c35; background: #fff3f4; }
.status-grid,
.definition-grid,
.evidence-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  margin-top: 20px;
}
.status-grid > div,
.definition-grid > div,
.evidence-grid > div {
  min-width: 0;
  padding: 14px;
  border: 1px solid #d8e4ec;
  border-radius: 12px;
  background: #f8fbfd;
}
.status-grid span,
dt { display: block; margin-bottom: 7px; color: #627486; font-size: 13px; }
.status-grid strong,
dd { margin: 0; overflow-wrap: anywhere; }
.reason-list { display: flex; flex-wrap: wrap; gap: 8px; padding: 0; list-style: none; }
.reason-list li,
.pill {
  padding: 6px 9px;
  border: 1px solid #b8ccd9;
  border-radius: 999px;
  background: #f3f8fb;
  font-size: 12px;
}
.pill--blocked { border-color: #d19b58; color: #7b4b0d; background: #fff8e8; }
.section-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(320px, 100%), 1fr)); gap: 14px; }
.section-card { min-width: 0; padding: 17px; border: 1px solid #d4e1e9; border-radius: 14px; }
.section-card header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.section-card p { min-height: 3em; color: #536779; line-height: 1.55; }
.section-card ul { margin: 0; padding: 0; list-style: none; }
.section-card li { padding: 11px 0; border-top: 1px solid #e2eaf0; }
.section-card li div { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; }
.section-card li span { display: block; margin-top: 5px; color: #627486; font-size: 12px; }
code { overflow-wrap: anywhere; color: #0c607a; font: 650 12px/1.5 ui-monospace, monospace; }

@media (max-width: 680px) {
  .country-catalog-page { padding-inline: 14px; }
  .section-heading,
  .boundary { align-items: stretch; flex-direction: column; }
  .section-heading button { width: 100%; }
  .status-grid,
  .definition-grid,
  .evidence-grid { grid-template-columns: 1fr; }
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after { scroll-behavior: auto !important; transition: none !important; }
}
</style>
