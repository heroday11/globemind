<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import {
  GOVERNANCE_CONTACT,
  GOVERNANCE_REVISION,
  PUBLIC_GOVERNANCE_PAGES,
  RESPONSIBILITY_MATRIX,
  buildGovernanceMailto,
  getGovernancePage,
} from '@/governance/publicGovernance.js'
import {
  catalogBlockerLabel,
  catalogStatusLabel,
  fetchDataCatalog,
  unavailableDataCatalog,
} from '@/governance/dataCatalog.js'

defineOptions({ name: 'PublicGovernance' })

const route = useRoute()
const page = computed(() => getGovernancePage(route.path))
const showDataCatalog = computed(() => route.path === '/sources')
const showSecurityContactDocument = computed(() => route.path === '/security')
const dataCatalog = ref(unavailableDataCatalog())
const dataCatalogLoading = ref(false)
let catalogRequestGeneration = 0
const contactHref = computed(() =>
  buildGovernanceMailto(route.path === '/security' ? '[SECURITY] GlobeMind 安全报告' : '[CORRECTION] GlobeMind 问题报告'),
)

function resetPageFocus() {
  window.scrollTo(0, 0)
  requestAnimationFrame(() => document.querySelector('#governance-title')?.focus())
}

async function loadDataCatalog() {
  const generation = ++catalogRequestGeneration
  dataCatalogLoading.value = true
  dataCatalog.value = unavailableDataCatalog('数据登记目录加载中。')
  try {
    const result = await fetchDataCatalog()
    if (generation === catalogRequestGeneration) dataCatalog.value = result
  } catch (error) {
    if (generation === catalogRequestGeneration) {
      dataCatalog.value = unavailableDataCatalog(error?.message)
    }
  } finally {
    if (generation === catalogRequestGeneration) dataCatalogLoading.value = false
  }
}

function syncGovernancePage() {
  resetPageFocus()
  if (showDataCatalog.value) loadDataCatalog()
  else catalogRequestGeneration += 1
}

onMounted(syncGovernancePage)
watch(() => route.path, syncGovernancePage)
</script>

<template>
  <main class="governance-page">
    <article class="governance-shell" aria-labelledby="governance-title">
      <header class="governance-hero">
        <p class="governance-eyebrow">{{ page.eyebrow }}</p>
        <h1 id="governance-title" tabindex="-1">{{ page.title }}</h1>
        <p class="governance-status">{{ page.status }}</p>
        <p class="governance-revision">
          版本 {{ GOVERNANCE_REVISION.version }} · 更新日期 {{ GOVERNANCE_REVISION.updatedAt }}
        </p>
        <p class="governance-summary">{{ page.summary }}</p>
      </header>

      <section
        v-for="(section, index) in page.sections"
        :key="section.title"
        class="governance-section"
        :aria-labelledby="`governance-section-${index}`"
      >
        <h2 :id="`governance-section-${index}`">{{ section.title }}</h2>
        <p v-for="paragraph in section.paragraphs" :key="paragraph">{{ paragraph }}</p>
      </section>

      <section
        v-if="showDataCatalog"
        class="governance-section catalog-section"
        aria-labelledby="data-catalog-title"
        aria-live="polite"
      >
        <div class="catalog-heading">
          <div>
            <p class="governance-eyebrow">DATA CATALOG / V1.0 FOUNDATION</p>
            <h2 id="data-catalog-title">数据集、数据源与模型登记</h2>
          </div>
          <span
            class="catalog-state"
            :class="{ 'catalog-state--ready': dataCatalog.summary.formalReleaseStatus === 'ready' }"
          >
            {{ dataCatalog.summary.formalReleaseStatus === 'ready' ? '正式门禁通过' : '正式门禁受阻' }}
          </span>
        </div>
        <p>
          技术可读、数据新鲜与研究可用是三个不同状态。下列卡片由正式登记契约重新验算；任何负责人、版本、时效、覆盖、许可、质量、溯源或 Schema 证据未知时，记录保持受阻。
        </p>
        <p v-if="dataCatalogLoading" class="catalog-message" role="status">正在读取登记目录…</p>
        <p v-else-if="!dataCatalog.available" class="catalog-message catalog-message--error" role="alert">
          {{ dataCatalog.error }}
        </p>
        <template v-else>
          <div class="catalog-summary" aria-label="数据登记汇总">
            <span>登记 {{ dataCatalog.summary.recordCount }} 项</span>
            <span>数据集 {{ dataCatalog.summary.datasetCount }}</span>
            <span>数据源 {{ dataCatalog.summary.sourceCount }}</span>
            <span>模型 {{ dataCatalog.summary.modelCount }}</span>
            <strong>正式可用 {{ dataCatalog.summary.eligibleCount }}</strong>
          </div>
          <div class="catalog-grid">
            <article v-for="record in dataCatalog.records" :key="record.recordId" class="catalog-card">
              <header>
                <span>{{ record.kindLabel }}</span>
                <strong :class="{ 'is-eligible': record.status.releaseEligible }">
                  {{ catalogStatusLabel(record.status.state) }}
                </strong>
              </header>
              <h3>{{ record.title }}</h3>
              <p>{{ record.description }}</p>
              <dl>
                <div><dt>负责人</dt><dd>{{ record.owner.name }} · {{ record.owner.assignmentLabel }}</dd></div>
                <div><dt>版本</dt><dd class="catalog-version">{{ record.version }}</dd></div>
                <div><dt>技术状态</dt><dd>{{ record.operational.label }}</dd></div>
                <div>
                  <dt>时效</dt>
                  <dd>{{ record.freshness.label }} · 截止 {{ record.freshness.cutoffAt || '未知' }}</dd>
                </div>
                <div><dt>覆盖</dt><dd>{{ catalogStatusLabel(record.coverage.status) }}</dd></div>
                <div><dt>许可</dt><dd>{{ catalogStatusLabel(record.license.status) }}</dd></div>
                <div><dt>质量</dt><dd>{{ catalogStatusLabel(record.quality.status) }}</dd></div>
                <div><dt>溯源</dt><dd>{{ catalogStatusLabel(record.provenanceStatus) }}</dd></div>
                <div><dt>Schema</dt><dd>{{ catalogStatusLabel(record.schemaStatus) }}</dd></div>
              </dl>
              <details v-if="record.status.reasonCodes.length">
                <summary>查看 {{ record.status.reasonCodes.length }} 项阻断原因</summary>
                <ul>
                  <li v-for="code in record.status.reasonCodes" :key="code">
                    {{ catalogBlockerLabel(code) }}
                  </li>
                </ul>
              </details>
            </article>
          </div>
        </template>
      </section>

      <section
        v-if="showSecurityContactDocument"
        class="governance-section"
        aria-labelledby="security-contact-document-title"
      >
        <h2 id="security-contact-document-title">机器可读安全联系入口</h2>
        <p>
          RFC 9116 文档只登记共享受理邮箱、公开政策、首选语言和到期时间；它不表示已有具名安全负责人或响应 SLA。
        </p>
        <a href="/.well-known/security.txt">查看 security.txt</a>
      </section>

      <section class="governance-section" aria-labelledby="responsibility-title">
        <h2 id="responsibility-title">责任矩阵</h2>
        <p>责任角色表示应承担的职能，不表示已任命具名人员。在完成任命前，所有问题先进入共享受理通道。</p>
        <div class="matrix-wrap" tabindex="0" aria-label="可水平滚动的责任矩阵">
          <table>
            <thead>
              <tr>
                <th scope="col">范围</th>
                <th scope="col">责任角色</th>
                <th scope="col">具名负责人</th>
                <th scope="col">当前入口</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in RESPONSIBILITY_MATRIX" :key="row.area">
                <th scope="row">{{ row.area }}</th>
                <td>{{ row.role }}</td>
                <td><span class="owner-pending">{{ row.owner }}</span></td>
                <td>{{ row.intake }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="governance-contact" aria-labelledby="contact-title">
        <div>
          <h2 id="contact-title">联系与受理</h2>
          <p>{{ GOVERNANCE_CONTACT.note }}</p>
        </div>
        <a class="contact-button" :href="contactHref">
          {{ GOVERNANCE_CONTACT.label }} · {{ GOVERNANCE_CONTACT.email }}
          <span aria-hidden="true">↗</span>
        </a>
      </section>

      <nav class="governance-related" aria-label="其他治理页面">
        <router-link to="/data-service/help-docs">帮助文档</router-link>
        <router-link to="/status">数据与服务状态</router-link>
        <router-link v-for="path in page.related" :key="path" :to="path">
          {{ PUBLIC_GOVERNANCE_PAGES[path].title }}
        </router-link>
        <router-link to="/about-us">关于我们</router-link>
      </nav>
    </article>
  </main>
</template>

<style scoped>
.governance-page {
  min-height: 100vh;
  box-sizing: border-box;
  padding: 112px 22px 64px;
  color: #183153;
  background:
    radial-gradient(circle at 86% 8%, rgba(54, 111, 210, 0.13), transparent 28rem),
    linear-gradient(155deg, #f7faff, #edf3fb);
}

.governance-shell {
  width: min(100%, 980px);
  margin: 0 auto;
}

.governance-hero,
.governance-section,
.governance-contact {
  border: 1px solid #dbe5f1;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 18px 52px rgba(42, 70, 111, 0.08);
}

.governance-hero {
  padding: clamp(28px, 6vw, 58px);
}

.governance-eyebrow {
  margin: 0 0 12px;
  color: #2766ca;
  font: 750 0.78rem/1.4 ui-monospace, monospace;
  letter-spacing: 0.13em;
}

h1 {
  margin: 0;
  color: #102b50;
  font-size: clamp(2.15rem, 6vw, 4.4rem);
  line-height: 1.08;
  letter-spacing: -0.04em;
}

h1:focus {
  outline: 3px solid #3678dd;
  outline-offset: 8px;
}

.governance-status {
  display: inline-flex;
  margin: 22px 0 0;
  padding: 7px 11px;
  border-radius: 999px;
  color: #764d08;
  background: #fff4d8;
  font-size: 0.88rem;
  font-weight: 700;
}

.governance-summary {
  max-width: 780px;
  margin: 22px 0 0;
  color: #4b617d;
  font-size: 1.08rem;
  line-height: 1.85;
}

.governance-revision {
  margin: 12px 0 0;
  color: #657892;
  font-size: 0.9rem;
}

.governance-section {
  margin-top: 20px;
  padding: clamp(24px, 4vw, 38px);
}

.governance-section h2,
.governance-contact h2 {
  margin: 0 0 14px;
  color: #16355f;
  font-size: 1.35rem;
}

.governance-section p,
.governance-contact p {
  margin: 10px 0 0;
  color: #516985;
  line-height: 1.85;
}

.matrix-wrap {
  margin-top: 20px;
  overflow-x: auto;
  border: 1px solid #dce5f1;
  border-radius: 14px;
}

.matrix-wrap:focus-visible {
  outline: 3px solid rgba(39, 102, 202, 0.35);
  outline-offset: 3px;
}

table {
  width: 100%;
  min-width: 720px;
  border-collapse: collapse;
  text-align: left;
}

th,
td {
  padding: 13px 15px;
  border-bottom: 1px solid #e4ebf4;
  vertical-align: top;
  line-height: 1.55;
}

thead th {
  color: #284565;
  background: #f2f6fc;
}

tbody th {
  color: #213c5d;
  font-weight: 700;
}

tbody tr:last-child > * {
  border-bottom: 0;
}

.owner-pending {
  display: inline-flex;
  padding: 3px 8px;
  border-radius: 999px;
  color: #855400;
  background: #fff2cc;
  font-weight: 750;
  white-space: nowrap;
}

.catalog-heading,
.catalog-card > header,
.catalog-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}

.catalog-heading .governance-eyebrow {
  margin-bottom: 8px;
}

.catalog-state,
.catalog-card > header strong {
  display: inline-flex;
  flex: 0 0 auto;
  padding: 5px 9px;
  border-radius: 999px;
  color: #8a4d08;
  background: #fff0d1;
  font-size: 0.78rem;
  font-weight: 760;
}

.catalog-state--ready,
.catalog-card > header strong.is-eligible {
  color: #17633b;
  background: #dcf7e7;
}

.catalog-message {
  margin-top: 18px !important;
  padding: 14px 16px;
  border-radius: 12px;
  background: #eef4fc;
}

.catalog-message--error {
  color: #7b4310 !important;
  background: #fff2d9;
}

.catalog-summary {
  justify-content: flex-start;
  flex-wrap: wrap;
  margin-top: 20px;
}

.catalog-summary span,
.catalog-summary strong {
  padding: 7px 10px;
  border: 1px solid #dce5f1;
  border-radius: 9px;
  color: #405a78;
  background: #f7faff;
}

.catalog-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 18px;
}

.catalog-card {
  min-width: 0;
  padding: 18px;
  border: 1px solid #dbe5f1;
  border-radius: 14px;
  background: #fbfdff;
}

.catalog-card > header > span {
  color: #2766ca;
  font-size: 0.78rem;
  font-weight: 800;
  letter-spacing: 0.08em;
}

.catalog-card h3 {
  margin: 15px 0 0;
  color: #18375e;
  font-size: 1.08rem;
}

.catalog-card p {
  margin-top: 8px;
  font-size: 0.9rem;
  line-height: 1.65;
}

.catalog-card dl {
  display: grid;
  gap: 8px;
  margin: 16px 0 0;
}

.catalog-card dl > div {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: 10px;
}

.catalog-card dt {
  color: #6a7e96;
}

.catalog-card dd {
  min-width: 0;
  margin: 0;
  color: #294867;
  overflow-wrap: anywhere;
}

.catalog-version {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.82rem;
}

.catalog-card details {
  margin-top: 16px;
  color: #73501a;
}

.catalog-card summary {
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  cursor: pointer;
  font-weight: 700;
}

.catalog-card li {
  margin-top: 7px;
  line-height: 1.5;
}

.governance-contact {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-top: 20px;
  padding: clamp(24px, 4vw, 38px);
}

.contact-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 48px;
  flex: 0 0 auto;
  padding: 10px 17px;
  border-radius: 12px;
  color: #fff;
  background: #2766ca;
  font-weight: 750;
  text-decoration: none;
}

.contact-button:hover {
  background: #1d52a6;
}

.contact-button:focus-visible,
.governance-related a:focus-visible {
  outline: 3px solid rgba(39, 102, 202, 0.42);
  outline-offset: 3px;
}

.governance-related {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 18px;
  margin-top: 24px;
  padding: 0 4px;
}

.governance-related a {
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  color: #245cac;
  font-weight: 650;
}

@media (max-width: 680px) {
  .governance-page {
    padding: 92px 12px 40px;
  }

  .governance-hero,
  .governance-section,
  .governance-contact {
    border-radius: 16px;
  }

  .governance-contact {
    align-items: stretch;
    flex-direction: column;
  }

  .contact-button {
    width: 100%;
    box-sizing: border-box;
  }

  .catalog-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .catalog-grid {
    grid-template-columns: 1fr;
  }
}
</style>
