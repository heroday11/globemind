<script setup>
import { onMounted, ref } from 'vue'
import { getToken } from '@/utils/auth'
import { assistantApi } from '../api.js'
import {
  directoryLoadErrorText,
  formatDirectoryCount,
  formatDirectoryTimestamp,
  memberPresenceLabel,
  siteDirectoryStatus,
} from '../directory/model.js'

defineProps({ activeSideNav: { type: String, default: '' } })

// 站点
const monitoredSites = ref([])

// 成员
const teamMembers = ref([])
const panelLoading = ref(false)
const panelError = ref('')

async function fetchJsonList(path) {
  const json = await assistantApi.listDirectory(path)
  return Array.isArray(json?.data) ? json.data : []
}

async function loadPanelData() {
  if (!getToken()) {
    panelError.value = '请先登录后查看站点与成员配置。'
    return
  }
  panelLoading.value = true
  panelError.value = ''
  try {
    const [sites, members] = await Promise.all([
      fetchJsonList('/sites'),
      fetchJsonList('/members'),
    ])
    monitoredSites.value = sites
    teamMembers.value = members
  } catch (error) {
    panelError.value = directoryLoadErrorText(error)
  } finally {
    panelLoading.value = false
  }
}

function memberInitial(member) {
  return String(member?.name || '?').trim().slice(0, 1) || '?'
}

function memberRoleLabel(role) {
  if (role === 'admin') return '管理员'
  if (role === 'editor') return '编辑者'
  return '观察者'
}

onMounted(loadPanelData)
</script>

<template>
  <!-- ====== 侧导航页面：站点 ====== -->
  <div v-if="activeSideNav === 'sites'" class="ys-page">
    <div class="ys-page-head">
      <h1 class="ys-page-title">站点</h1>
      <div class="ys-page-head-row">
        <p class="ys-page-sub">只读站点目录与配置状态；不代表实时采集或可用性。</p>
        <button class="kb-upload-btn" disabled title="站点写入由管理员配置文件维护">只读配置</button>
      </div>
    </div>
    <div v-if="panelLoading" class="site-card">正在加载站点配置...</div>
    <div v-else-if="panelError" class="site-card site-card--empty">{{ panelError }}</div>
    <div v-else-if="!monitoredSites.length" class="site-card site-card--empty">暂无站点配置。</div>
    <div v-else class="sites-grid">
      <div v-for="site in monitoredSites" :key="site.id" class="site-card">
        <div class="site-card-top">
          <span class="site-card-icon">{{ site.icon || '◎' }}</span>
          <span class="site-card-status" :class="'site-status--' + siteDirectoryStatus(site.status).tone">
            {{ siteDirectoryStatus(site.status).label }}
          </span>
        </div>
        <div class="site-card-name">{{ site.name || '未命名站点' }}</div>
        <div class="site-card-url">{{ site.url || '-' }}</div>
        <div class="site-card-stats">
          <div class="site-stat">
            <span class="site-stat-num">{{ formatDirectoryCount(site.articles) }}</span>
            <span class="site-stat-label">目录登记文章数</span>
          </div>
          <div class="site-stat">
            <span class="site-stat-num">{{ formatDirectoryTimestamp(site.lastCrawl) }}</span>
            <span class="site-stat-label">目录记录时间</span>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ====== 侧导航页面：成员 ====== -->
  <div v-else-if="activeSideNav === 'members'" class="ys-page">
    <section class="members-hero">
      <div>
        <p class="members-eyebrow">Team Workspace</p>
        <h1>成员与权限</h1>
        <p>只读成员目录与角色配置；在线字段仅是目录标记，不代表实时在线。</p>
      </div>
      <button class="member-invite-btn" disabled title="成员写入由管理员配置文件维护">只读配置</button>
    </section>
    <div v-if="panelLoading" class="member-stat-card">正在加载成员配置...</div>
    <div v-else-if="panelError" class="member-stat-card">{{ panelError }}</div>

    <div v-else class="member-stat-grid">
      <div class="member-stat-card">
        <span>成员总数</span>
        <strong>{{ teamMembers.length }}</strong>
      </div>
      <div class="member-stat-card">
        <span>目录在线标记</span>
        <strong>{{ teamMembers.filter(m => m.online).length }}</strong>
      </div>
      <div class="member-stat-card">
        <span>管理员</span>
        <strong>{{ teamMembers.filter(m => m.role === 'admin').length }}</strong>
      </div>
    </div>

    <div v-if="!panelLoading && !panelError" class="members-board">
      <aside class="members-policy">
        <h2>权限说明</h2>
        <div class="policy-row">
          <strong>管理员</strong>
          <span>管理成员、工作区和关键配置</span>
        </div>
        <div class="policy-row">
          <strong>编辑者</strong>
          <span>创建报告、编辑资料和发起分析</span>
        </div>
        <div class="policy-row">
          <strong>观察者</strong>
          <span>查看结果、收藏资料和参与讨论</span>
        </div>
      </aside>

      <div class="members-list">
        <div v-if="!teamMembers.length" class="member-stat-card member-stat-card--empty">暂无成员配置。</div>
        <article v-for="m in teamMembers" :key="m.id" class="member-row-card">
          <div class="member-avatar" :class="{ 'directory-online': m.online === true }">
            {{ memberInitial(m) }}
          </div>
          <div class="member-row-main">
            <div class="member-row-top">
              <strong>{{ m.name || '未命名成员' }}</strong>
              <span class="member-role-tag">{{ memberRoleLabel(m.role) }}</span>
            </div>
          <div class="member-row-meta">
            <span>{{ m.title || '未填写职位' }}</span>
            <span>{{ m.dept || '未填写部门' }}</span>
            <span :class="{ 'directory-online': m.online === true }">{{ memberPresenceLabel(m.online) }}</span>
          </div>
        </div>
          <span class="member-card-readonly" title="成员写入由管理员配置文件维护">只读</span>
        </article>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ys-page { padding: 22px 22px 18px; }
.ys-page-head { border-bottom: 1px solid rgba(15, 23, 42, 0.08); padding-bottom: 14px; margin-bottom: 14px; }
.ys-page-title { margin: 0; font-size: 22px; font-weight: 950; letter-spacing: -0.02em; color: rgba(15, 23, 42, 0.92); }
.ys-page-head-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.ys-page-sub { margin: 8px 0 0; font-size: 13px; font-weight: 650; color: rgba(71, 85, 105, 0.86); line-height: 1.7; }
.ys-page-badge { font-size: 11px; font-weight: 700; color: rgba(59, 130, 246, 0.9); background: rgba(59, 130, 246, 0.08); padding: 4px 10px; border-radius: 999px; border: 1px solid rgba(59, 130, 246, 0.2); }

.kb-upload-btn { height: 36px; padding: 0 18px; border-radius: 10px; border: 1px solid rgba(37, 99, 235, 0.3); background: rgba(59, 130, 246, 0.08); color: rgba(37, 99, 235, 0.9); font-weight: 700; font-size: 13px; cursor: pointer; white-space: nowrap; }
.kb-upload-btn:hover { background: rgba(59, 130, 246, 0.14); }
.kb-upload-btn:disabled, .member-invite-btn:disabled { cursor: not-allowed; opacity: 0.72; box-shadow: none; }

.site-card { border-radius: 14px; border: 1px solid rgba(15, 23, 42, 0.08); background: #ffffff; padding: 16px; box-shadow: 0 4px 12px rgba(2, 6, 23, 0.04); transition: all 0.2s ease; }
.site-card--empty { color: rgba(100, 116, 139, 0.9); font-weight: 700; }
.site-card:hover { border-color: rgba(59, 130, 246, 0.2); box-shadow: 0 8px 20px rgba(2, 6, 23, 0.08); }
.site-card-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
.site-card-icon { font-size: 24px; }
.site-card-status { font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 999px; }
.site-status--configured { background: rgba(16, 185, 129, 0.1); color: rgba(4, 120, 87, 0.9); border: 1px solid rgba(16, 185, 129, 0.2); }
.site-status--paused { background: rgba(245, 158, 11, 0.1); color: rgba(180, 83, 9, 0.9); border: 1px solid rgba(245, 158, 11, 0.2); }
.site-status--error { background: rgba(239, 68, 68, 0.1); color: rgba(185, 28, 28, 0.9); border: 1px solid rgba(239, 68, 68, 0.2); }
.site-status--unknown { background: rgba(100, 116, 139, 0.08); color: rgba(71, 85, 105, 0.92); border: 1px solid rgba(100, 116, 139, 0.18); }
.site-card-name { font-size: 16px; font-weight: 700; color: rgba(15, 23, 42, 0.9); margin-bottom: 2px; }
.site-card-url { font-size: 12px; color: rgba(100, 116, 139, 0.85); margin-bottom: 12px; font-weight: 500; }
.site-card-stats { display: flex; gap: 20px; border-top: 1px solid rgba(15, 23, 42, 0.06); padding-top: 10px; }
.site-stat { display: flex; flex-direction: column; gap: 2px; }
.site-stat-num { font-size: 18px; font-weight: 800; color: rgba(15, 23, 42, 0.9); }
.site-stat-label { font-size: 11px; font-weight: 600; color: rgba(100, 116, 139, 0.85); }

.members-hero { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; padding: 24px; border-radius: 18px; border: 1px solid rgba(37, 99, 235, 0.13); background: linear-gradient(135deg, rgba(255,255,255,0.94), rgba(239,246,255,0.84)); box-shadow: 0 18px 42px rgba(37, 99, 235, 0.1); margin-bottom: 16px; }
.members-eyebrow { margin: 0 0 8px; color: #1d4ed8; font-size: 12px; font-weight: 900; letter-spacing: 0.08em; text-transform: uppercase; }
.members-hero h1 { margin: 0; color: #0f172a; font-size: 30px; line-height: 1.18; font-weight: 950; }
.members-hero p:last-child { max-width: 560px; margin: 10px 0 0; color: rgba(71, 85, 105, 0.9); font-size: 14px; line-height: 1.7; font-weight: 650; }
.member-invite-btn { height: 40px; padding: 0 18px; border: 0; border-radius: 11px; color: #fff; background: linear-gradient(135deg, #2563eb, #0891b2); font-size: 13px; font-weight: 850; cursor: pointer; box-shadow: 0 10px 22px rgba(37, 99, 235, 0.24); }
.member-stat-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin-bottom: 16px; }
.member-stat-card { padding: 16px; border-radius: 16px; border: 1px solid rgba(37, 99, 235, 0.12); background: #ffffff; box-shadow: 0 12px 28px rgba(37, 99, 235, 0.07); }
.member-stat-card--empty { margin: 16px; color: rgba(100, 116, 139, 0.9); font-size: 13px; font-weight: 760; box-shadow: none; }
.member-stat-card span { display: block; color: rgba(71, 85, 105, 0.88); font-size: 12px; font-weight: 850; }
.member-stat-card strong { display: block; margin-top: 6px; color: #0f172a; font-size: 30px; line-height: 1; font-weight: 950; }
.members-board { display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: 16px; }
.members-policy, .members-list { border: 1px solid rgba(37, 99, 235, 0.12); border-radius: 18px; background: rgba(255,255,255,0.88); box-shadow: 0 14px 34px rgba(37, 99, 235, 0.08); }
.members-policy { padding: 18px; align-self: start; }
.members-policy h2 { margin: 0 0 14px; color: #0f172a; font-size: 17px; font-weight: 950; }
.policy-row { padding: 12px 0; border-top: 1px solid rgba(37, 99, 235, 0.09); }
.policy-row strong { display: block; color: #1d4ed8; font-size: 13px; font-weight: 900; }
.policy-row span { display: block; margin-top: 4px; color: rgba(71, 85, 105, 0.88); font-size: 12px; line-height: 1.55; font-weight: 650; }
.members-list { display: grid; gap: 0; overflow: hidden; }
.member-row-card { display: grid; grid-template-columns: 48px minmax(0, 1fr) auto; align-items: center; gap: 12px; padding: 16px 18px; border-bottom: 1px solid rgba(37, 99, 235, 0.08); background: #ffffff; transition: background 0.15s ease; }
.member-row-card:last-child { border-bottom: 0; }
.member-row-card:hover { background: #f8fbff; }
.member-avatar { width: 48px; height: 48px; border-radius: 14px; display: flex; align-items: center; justify-content: center; color: #fff; background: #94a3b8; font-weight: 900; font-size: 19px; box-shadow: 0 10px 20px rgba(15, 23, 42, 0.08); }
.member-avatar.directory-online { background: linear-gradient(135deg, #2563eb, #10b981); }
.member-row-main { min-width: 0; }
.member-row-top { display: flex; align-items: center; gap: 10px; min-width: 0; }
.member-row-top strong { color: #0f172a; font-size: 15px; font-weight: 950; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.member-role-tag { display: inline-flex; align-items: center; flex: 0 0 auto; font-size: 11px; font-weight: 850; padding: 3px 9px; border-radius: 999px; background: rgba(37, 99, 235, 0.08); color: #1d4ed8; border: 1px solid rgba(37, 99, 235, 0.16); }
.member-row-meta { display: flex; flex-wrap: wrap; gap: 8px 12px; margin-top: 7px; color: rgba(100, 116, 139, 0.9); font-size: 12px; font-weight: 700; }
.member-row-meta .directory-online { color: #047857; }
.member-card-del { height: 32px; padding: 0 12px; border-radius: 9px; border: 1px solid rgba(239, 68, 68, 0.14); background: rgba(239, 68, 68, 0.06); color: rgba(185, 28, 28, 0.85); cursor: pointer; font-size: 12px; font-weight: 820; }
.member-card-del:hover { background: rgba(239, 68, 68, 0.12); }
.member-card-readonly { height: 28px; padding: 0 10px; display: inline-flex; align-items: center; border-radius: 999px; border: 1px solid rgba(100, 116, 139, 0.16); background: rgba(100, 116, 139, 0.06); color: rgba(71, 85, 105, 0.86); font-size: 12px; font-weight: 820; }

@media (max-width: 900px) {
  .members-hero { align-items: flex-start; flex-direction: column; }
  .member-stat-grid, .members-board { grid-template-columns: 1fr; }
}

.ws-preview-overlay { position: fixed; inset: 0; z-index: 1000; background: rgba(15, 23, 42, 0.45); backdrop-filter: blur(4px); display: flex; align-items: center; justify-content: center; }
.ws-create-modal { background: #ffffff; border-radius: 18px; box-shadow: 0 25px 50px -12px rgba(2, 6, 23, 0.25); width: min(540px, 94vw); max-height: 85vh; overflow: auto; }
.ws-create-modal--sm { width: min(380px, 94vw); }
.ws-preview-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 22px 24px; border-bottom: 1px solid rgba(15, 23, 42, 0.06); flex-wrap: wrap; }
.ws-preview-title { font-size: 18px; font-weight: 700; color: rgba(15, 23, 42, 0.92); }
.ws-preview-close { width: 36px; height: 36px; border-radius: 50%; border: 1px solid rgba(15, 23, 42, 0.08); background: transparent; color: rgba(71, 85, 105, 0.9); font-size: 20px; cursor: pointer; display: flex; align-items: center; justify-content: center; }
.ws-preview-close:hover { background: rgba(248, 250, 252, 0.9); color: rgba(15, 23, 42, 0.9); }
.ws-create-body { padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; }
.ws-create-field { display: flex; flex-direction: column; gap: 6px; }
.ws-create-label { font-size: 13px; font-weight: 600; color: rgba(51, 65, 85, 0.9); }
.ws-create-required { color: rgba(239, 68, 68, 0.9); }
.ws-create-input { width: 100%; box-sizing: border-box; padding: 10px 14px; border: 1px solid rgba(15, 23, 42, 0.12); border-radius: 10px; font-size: 14px; outline: none; font-family: inherit; }
.ws-create-input:focus { border-color: rgba(59, 130, 246, 0.4); box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.08); }
.ws-create-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 4px; }
.ws-create-cancel { height: 38px; padding: 0 18px; border-radius: 10px; border: 1px solid rgba(15, 23, 42, 0.1); background: transparent; color: rgba(71, 85, 105, 0.9); font-weight: 600; font-size: 13px; cursor: pointer; }
.ws-create-cancel:hover { background: rgba(248, 250, 252, 0.9); }
.ws-create-confirm { height: 38px; padding: 0 22px; border-radius: 10px; border: none; background: linear-gradient(135deg, #2563eb, #0891b2); color: #ffffff; font-weight: 700; font-size: 14px; cursor: pointer; }
.ws-create-confirm:hover:not(:disabled) { filter: brightness(1.05); }
.ws-create-confirm:disabled { cursor: not-allowed; opacity: 0.45; }
</style>
