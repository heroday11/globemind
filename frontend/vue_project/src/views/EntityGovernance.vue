<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  createLatestEntityGovernanceRequest,
  emptyEntityGovernanceState,
  entityGovernanceApi,
  readyEntityGovernanceState,
} from '@/features/entity-governance/index.js'

defineOptions({ name: 'EntityGovernance' })

const state = ref(emptyEntityGovernanceState())
const pageTitle = ref(null)
const errorAlert = ref(null)
const request = createLatestEntityGovernanceRequest()

const status = computed(() => state.value.data?.status || null)
const catalog = computed(() => state.value.data?.catalog || null)
const relations = computed(() => state.value.data?.relations?.items || [])
const history = computed(() => state.value.data?.history?.items || [])

function errorMessage(error) {
  if (error?.code === 'AUTH_REQUIRED') return '请先登录后查看实体治理账本。'
  if (error?.code === 'ENTITY_GOVERNANCE_CONTRACT_INVALID') return error.message
  return String(error?.message || '实体治理工作台暂时不可用')
}

async function loadWorkspace() {
  state.value = emptyEntityGovernanceState('loading')
  const outcome = await request.run((signal) =>
    entityGovernanceApi.loadWorkspace({ limit: 50, signal }),
  )
  if (outcome.status === 'stale' || outcome.status === 'aborted') return
  if (outcome.status === 'error') {
    state.value = emptyEntityGovernanceState('error', errorMessage(outcome.error))
    await nextTick()
    errorAlert.value?.focus()
    return
  }
  state.value = readyEntityGovernanceState(outcome.value)
}

function displayName(entity) {
  return entity?.canonicalNames?.['zh-Hans']
    || entity?.canonicalNames?.en
    || Object.values(entity?.canonicalNames || {})[0]
    || entity?.entityId
    || '未命名实体'
}

function formatInterval(item) {
  if (!item?.validFrom && !item?.validTo) return '时间未知（未推断）'
  return `${item.validFrom || '起始未知'} → ${item.validTo || '持续中/终止未知'}`
}

function formatTimestamp(value) {
  if (!value) return '未记录'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '未记录' : parsed.toLocaleString('zh-CN')
}

function shortId(value) {
  if (!value) return '无'
  if (value.length <= 38) return value
  return `${value.slice(0, 24)}…${value.slice(-10)}`
}

onMounted(async () => {
  await nextTick()
  pageTitle.value?.focus()
  await loadWorkspace()
})

onBeforeUnmount(() => request.cancel())
</script>

<template>
  <main class="governance-page" aria-labelledby="entity-governance-title">
    <header class="hero">
      <p class="eyebrow">ENTITY GOVERNANCE / READ ONLY</p>
      <h1 id="entity-governance-title" ref="pageTitle" tabindex="-1">时态实体治理工作台</h1>
      <p class="hero-copy">
        展示人工裁决后的实体、别名、时态关系与追加式审计记录。本页面不运行实体推断，
        不自动创建实体或关系，也不提供写入入口。
      </p>
      <div class="trust-boundary" role="note">
        <strong>可信边界</strong>
        <span>
          准确率为 not_measured；本地 SHA-256/HMAC 链不是 WORM 或数字签名，且尚未接入机构身份目录。
          未审阅种子始终显示为 review_required；审批到期复核策略尚未配置。
        </span>
      </div>
    </header>

    <section class="panel" aria-labelledby="governance-status-title" :aria-busy="state.phase === 'loading'">
      <div class="section-heading">
        <div>
          <p class="eyebrow">LEDGER STATUS</p>
          <h2 id="governance-status-title">账本与完整性状态</h2>
        </div>
        <button type="button" :disabled="state.phase === 'loading'" @click="loadWorkspace">
          {{ state.phase === 'loading' ? '正在读取…' : '重新读取' }}
        </button>
      </div>

      <p v-if="state.phase === 'loading'" class="state-message" role="status" aria-live="polite">
        正在读取服务端状态；旧数据已清除，避免把过期投影当作当前结果。
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
        {{ state.error }} 当前不展示任何旧目录、关系或历史。
      </p>

      <template v-else-if="state.phase === 'ready' && status">
        <div class="status-grid" role="group" aria-label="实体治理状态摘要">
          <div><span>存储</span><strong :class="`status-${status.storageStatus}`">{{ status.storageStatus }}</strong></div>
          <div><span>完整性</span><strong>{{ status.integrityStatus }}</strong></div>
          <div><span>事件数</span><strong>{{ status.eventCount ?? '不可用' }}</strong></div>
          <div><span>管理员裁决基础设施</span><strong>{{ status.mutationStatus }}</strong></div>
          <div><span>根目录</span><strong>{{ status.rootInitialized ? '已初始化' : '未初始化（零事件）' }}</strong></div>
          <div><span>准确率声明</span><strong>{{ status.accuracyClaim }}</strong></div>
          <div><span>审批到期复核</span><strong>{{ status.reviewExpiryPolicy }}</strong></div>
          <div><span>最新事件</span><strong :title="status.latestEventId || ''">{{ shortId(status.latestEventId) }}</strong></div>
        </div>
        <p class="boundary-note">
          HMAC key ID 未登记，在线轮换不可用；直接更换 key 会使既有链 fail closed，必须通过尚未实现的受控离线迁移处理。
        </p>
        <p
          v-if="status.storageStatus === 'unavailable'"
          class="state-message state-message--error"
          role="alert"
          aria-live="assertive"
          aria-atomic="true"
        >
          账本不可用：{{ status.reason }}。没有目录或关系降级结果。
        </p>
      </template>
    </section>

    <template v-if="state.phase === 'ready' && catalog">
      <section class="summary-grid" aria-label="实体治理目录摘要">
        <article class="summary-card">
          <span>待人工复核种子</span>
          <strong>{{ catalog.reviewRequiredEntities.length }}</strong>
          <small>不会进入 approved read model</small>
        </article>
        <article class="summary-card">
          <span>账本已批准实体</span>
          <strong>{{ catalog.approvedEntities.length }}</strong>
          <small>保留时态与裁决引用；到期复核未配置</small>
        </article>
        <article class="summary-card">
          <span>账本已批准且未撤回关系</span>
          <strong>{{ relations.length }}</strong>
          <small>仅批准端点且未撤回；不代表审批仍在有效期</small>
        </article>
        <article class="summary-card">
          <span>显示审计事件</span>
          <strong>{{ history.length }}</strong>
          <small>最多读取最近 50 条</small>
        </article>
      </section>

      <section class="panel" aria-labelledby="review-queue-title">
        <div class="section-heading">
          <div>
            <p class="eyebrow">REVIEW REQUIRED</p>
            <h2 id="review-queue-title">种子审核队列</h2>
          </div>
          <span class="pill pill--warning">{{ catalog.reviewRequiredEntities.length }} 条</span>
        </div>
        <div v-if="catalog.reviewRequiredEntities.length" class="table-wrap">
          <table>
            <thead><tr><th>实体</th><th>类型</th><th>稳定 ID</th><th>来源目录</th><th>状态</th></tr></thead>
            <tbody>
              <tr v-for="entity in catalog.reviewRequiredEntities" :key="entity.entityId">
                <td><strong>{{ displayName(entity) }}</strong></td>
                <td>{{ entity.entityType }}</td>
                <td><code>{{ entity.entityId }}</code></td>
                <td>{{ entity.sourceCatalogVersion }}</td>
                <td><span class="pill pill--warning">review_required</span></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="empty-state">当前没有待复核种子。</p>
      </section>

      <section class="panel" aria-labelledby="approved-entities-title">
        <div class="section-heading">
          <div>
            <p class="eyebrow">APPROVED PROJECTION</p>
            <h2 id="approved-entities-title">账本中已批准实体</h2>
          </div>
          <span class="pill pill--approved">{{ catalog.approvedEntities.length }} 条</span>
        </div>
        <div v-if="catalog.approvedEntities.length" class="entity-cards">
          <article v-for="entity in catalog.approvedEntities" :key="entity.entityId" class="entity-card">
            <div><span>{{ entity.entityType }}</span><strong>{{ displayName(entity) }}</strong></div>
            <code>{{ entity.entityId }}</code>
            <p>{{ formatInterval(entity) }}</p>
            <div v-if="entity.approvedAliases.length" class="alias-block">
              <span>已批准别名</span>
              <ul>
                <li v-for="alias in entity.approvedAliases" :key="alias.reviewEventId">
                  <code>{{ alias.value }}</code> · {{ alias.language }} · {{ alias.kind }} · {{ formatInterval(alias) }}
                </li>
              </ul>
            </div>
            <p v-else>已批准别名：无</p>
            <p v-if="entity.mergeTargetId">merge → <code>{{ entity.mergeTargetId }}</code></p>
            <p v-if="entity.splitIntoEntityIds.length">split → {{ entity.splitIntoEntityIds.join('、') }}</p>
          </article>
        </div>
        <p v-else class="empty-state">尚无已批准实体；空目录不代表种子已通过人工审阅。</p>
      </section>

      <section class="panel" aria-labelledby="approved-relations-title">
        <div class="section-heading">
          <div>
            <p class="eyebrow">TEMPORAL RELATIONS</p>
            <h2 id="approved-relations-title">账本中已批准且未撤回的关系</h2>
          </div>
          <span class="pill pill--approved">{{ relations.length }} 条</span>
        </div>
        <ol v-if="relations.length" class="relation-list">
          <li v-for="relation in relations" :key="relation.relationId">
            <div class="triple">
              <code>{{ relation.subjectId }}</code>
              <strong>{{ relation.predicate }}</strong>
              <code>{{ relation.objectId }}</code>
            </div>
            <p>{{ formatInterval(relation) }} · evidence {{ relation.evidence.snapshotId }}</p>
          </li>
        </ol>
        <p v-else class="empty-state">当前没有满足“端点已批准且关系未撤回”的活动关系。</p>
      </section>

      <section class="panel" aria-labelledby="governance-history-title">
        <div class="section-heading">
          <div>
            <p class="eyebrow">AUTHENTICATED AUDIT VIEW</p>
            <h2 id="governance-history-title">人工治理历史</h2>
          </div>
          <span class="pill">{{ history.length }} / {{ catalog.eventCount }}</span>
        </div>
        <p class="boundary-note">
          actor_ref 与裁决理由按合同向所有已登录用户可见；actor_ref 只是本地 canonical user_id 引用，未由机构目录验真。
        </p>
        <ol v-if="history.length" class="history-list">
          <li v-for="event in history" :key="event.eventId">
            <div class="history-heading">
              <strong>#{{ event.sequence }} · {{ event.eventType }}</strong>
              <time :datetime="event.occurredAt">{{ formatTimestamp(event.occurredAt) }}</time>
            </div>
            <p>{{ event.reason }}</p>
            <small>{{ event.actorRef }} · evidence {{ event.evidence.snapshotId }}</small>
            <code :title="event.recordSha256">record {{ shortId(event.recordSha256) }}</code>
          </li>
        </ol>
        <p v-else class="empty-state">账本当前为零事件；空链完整性已校验，但没有人工裁决可展示。</p>
      </section>
    </template>
  </main>
</template>

<style scoped>
.governance-page {
  min-height: 100vh;
  padding: 40px clamp(20px, 5vw, 72px) 72px;
  color: #e8edf7;
  background:
    radial-gradient(circle at 88% 4%, rgba(62, 179, 151, 0.14), transparent 30%),
    linear-gradient(160deg, #101725 0%, #0a101b 70%);
}

.hero,
.panel,
.summary-grid {
  width: min(1180px, 100%);
  margin-inline: auto;
}

.hero { margin-bottom: 24px; }
.eyebrow { margin: 0 0 8px; color: #76d7bc; font: 700 12px/1.4 ui-monospace, monospace; letter-spacing: 0.14em; }
h1 { margin: 0; font-size: clamp(30px, 5vw, 52px); }
h2 { margin: 0; font-size: 21px; }
.hero-copy { max-width: 760px; color: #b9c3d5; line-height: 1.75; }
.trust-boundary,
.boundary-note {
  display: flex;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid rgba(118, 215, 188, 0.28);
  border-radius: 12px;
  color: #c9d3e3;
  background: rgba(22, 38, 51, 0.76);
  line-height: 1.6;
}
.trust-boundary strong { color: #76d7bc; white-space: nowrap; }
.panel {
  margin-top: 18px;
  padding: 22px;
  border: 1px solid rgba(150, 165, 191, 0.18);
  border-radius: 16px;
  background: rgba(17, 25, 39, 0.92);
  box-shadow: 0 18px 44px rgba(0, 0, 0, 0.18);
}
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 18px; }
button {
  padding: 9px 14px;
  border: 1px solid rgba(118, 215, 188, 0.45);
  border-radius: 9px;
  color: #dffaf2;
  background: #17352f;
  cursor: pointer;
}
button:disabled { opacity: 0.55; cursor: progress; }
.status-grid,
.summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
.status-grid div,
.summary-card { padding: 15px; border: 1px solid rgba(150, 165, 191, 0.14); border-radius: 11px; background: #0d1522; }
.status-grid span,
.summary-card span { display: block; margin-bottom: 7px; color: #8491a7; font-size: 12px; }
.summary-grid { margin-top: 18px; }
.summary-card strong { display: block; color: #f4f7fc; font-size: 28px; }
.summary-card small { color: #8491a7; }
.status-available { color: #76d7bc; }
.status-unavailable,
.state-message--error { color: #ff9d9d; }
.state-message,
.empty-state { padding: 16px; color: #aeb9cc; background: #0d1522; border-radius: 10px; }
.boundary-note { margin: 16px 0 0; border-color: rgba(244, 189, 90, 0.22); font-size: 13px; }
.pill { display: inline-flex; padding: 4px 9px; border-radius: 999px; color: #cbd5e5; background: #293246; font: 700 12px/1.4 ui-monospace, monospace; }
.pill--warning { color: #ffd793; background: #3e2e18; }
.pill--approved { color: #8ee6cb; background: #173b32; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th,
td { padding: 11px 10px; border-bottom: 1px solid rgba(150, 165, 191, 0.12); text-align: left; vertical-align: top; }
th { color: #8f9bb0; font-size: 12px; }
code { color: #9dc6ff; overflow-wrap: anywhere; }
.entity-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px; }
.entity-card { padding: 16px; border: 1px solid rgba(118, 215, 188, 0.15); border-radius: 12px; background: #0d1522; }
.entity-card div { display: flex; justify-content: space-between; gap: 12px; }
.entity-card div span { color: #8491a7; }
.entity-card p { margin-bottom: 0; color: #aeb9cc; }
.entity-card .alias-block { display: block; margin-top: 12px; }
.alias-block ul { margin: 6px 0 0; padding-left: 20px; color: #aeb9cc; }
.relation-list,
.history-list { display: grid; gap: 12px; padding: 0; list-style: none; }
.relation-list li,
.history-list li { padding: 15px; border-left: 3px solid #397d6b; border-radius: 8px; background: #0d1522; }
.triple { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); gap: 12px; align-items: center; }
.triple strong { color: #76d7bc; text-align: center; }
.relation-list p,
.history-list p,
.history-list small { color: #aeb9cc; }
.history-list small,
.history-list code { display: block; margin-top: 7px; }
.history-heading { display: flex; justify-content: space-between; gap: 16px; }
.history-heading time { color: #8491a7; font-size: 13px; }

@media (max-width: 720px) {
  .governance-page { padding-inline: 16px; }
  .trust-boundary,
  .section-heading,
  .history-heading { align-items: flex-start; flex-direction: column; }
  .triple { grid-template-columns: 1fr; }
  .triple strong { text-align: left; }
}
</style>
