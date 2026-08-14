<script setup>
import '@/styles/user-center.css'

const navItems = [
  { path: '/user-center/personal-center', label: '个人中心', icon: '👤' },
  { path: '/user-center/my-applications', label: '我的报告', icon: '📊' },
  { path: '/user-center/help-docs', label: '帮助文档', icon: '📘' },
  { path: '/user-center/my-collections', label: '我的收录', icon: '⭐' },
  { path: '/user-center/logout', label: '退出登录', icon: '🚪', muted: true },
]

function openNewUserGuide() {
  window.dispatchEvent(new CustomEvent('openGlobeMindGuide'))
}
</script>

<template>
  <div class="uc-shell uc-theme">
    <aside class="uc-sidebar" aria-label="用户中心导航">
      <div class="uc-side-head">
        <span class="uc-side-title">用户中心</span>
        <span class="uc-side-sub">账号与偏好</span>
      </div>
      <nav class="uc-nav">
        <router-link
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="uc-nav-link"
          :class="{ 'is-muted': item.muted }"
        >
          <span class="uc-nav-ico" aria-hidden="true">{{ item.icon }}</span>
          <span class="uc-nav-label">{{ item.label }}</span>
        </router-link>
      </nav>
      <button type="button" class="uc-tour-entry" data-tour="account-guide-entry" @click="openNewUserGuide">
        <span class="uc-tour-entry__mark" aria-hidden="true">?</span>
        <span class="uc-tour-entry__copy">
          <strong>重看新手教程</strong>
          <small>逐页认识核心功能</small>
        </span>
      </button>
    </aside>

    <main class="uc-main">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.uc-shell {
  display: flex;
  min-height: calc(100vh - 64px);
  margin-top: 64px;
  width: 100%;
  box-sizing: border-box;
}

.uc-sidebar {
  width: 260px;
  flex-shrink: 0;
  background: linear-gradient(180deg, #ffffff 0%, #f8faff 100%);
  border-right: 1px solid var(--uc-border);
  padding: 0 0 24px;
  position: sticky;
  top: 64px;
  max-height: calc(100vh - 64px);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.uc-side-head {
  padding: clamp(22px, 3vw, 30px) 20px clamp(18px, 2.5vw, 22px);
  border-bottom: 1px solid var(--uc-border-soft);
  margin-bottom: 8px;
}

.uc-side-title {
  display: block;
  font-size: clamp(1.2rem, 2.4vw, 1.45rem);
  font-weight: 800;
  color: var(--uc-text);
  letter-spacing: 0.02em;
}

.uc-side-sub {
  display: block;
  margin-top: 6px;
  font-size: clamp(0.84rem, 1.6vw, 0.92rem);
  color: var(--uc-muted);
  font-weight: 500;
}

.uc-nav {
  display: flex;
  flex-direction: column;
  padding: 8px 12px;
  gap: 4px;
}

.uc-nav-link {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: var(--uc-radius-sm);
  text-decoration: none;
  color: var(--uc-muted);
  font-size: 0.95rem;
  font-weight: 600;
  transition:
    background 0.2s ease,
    color 0.2s ease,
    box-shadow 0.2s ease;
}

.uc-nav-link:hover {
  background: #eef2ff;
  color: var(--uc-text);
}

.uc-nav-link.router-link-active {
  background: linear-gradient(135deg, rgba(91, 114, 223, 0.12), rgba(102, 126, 234, 0.08));
  color: var(--uc-accent);
  box-shadow: inset 0 0 0 1px rgba(91, 114, 223, 0.22);
}

.uc-nav-link.is-muted {
  color: var(--uc-muted);
}

.uc-nav-link.is-muted:hover {
  background: #fff5f5;
  color: #b91c1c;
}

.uc-nav-ico {
  width: 1.5rem;
  text-align: center;
  font-size: 1.1rem;
  flex-shrink: 0;
}

.uc-nav-label {
  flex: 1;
}

.uc-tour-entry {
  display: flex;
  align-items: center;
  gap: 11px;
  margin: auto 12px 0;
  padding: 12px;
  border: 1px solid rgba(91, 114, 223, .24);
  border-radius: 12px;
  color: #26375f;
  background: linear-gradient(135deg, #f0f4ff, #ffffff);
  text-align: left;
  cursor: pointer;
}

.uc-tour-entry__mark { width: 31px; height: 31px; display: grid; place-items: center; flex: 0 0 auto; border-radius: 9px; color: #fff; background: #5b72df; font-weight: 900; }
.uc-tour-entry__copy { display: grid; gap: 2px; }
.uc-tour-entry strong { font-size: 13px; }
.uc-tour-entry small { color: #7180a8; font-size: 11px; }
.uc-tour-entry:hover { border-color: rgba(91, 114, 223, .45); box-shadow: 0 8px 22px rgba(91, 114, 223, .12); }

.uc-main {
  flex: 1;
  min-width: 0;
  padding: clamp(22px, 3.2vw, 36px) clamp(20px, 3vw, 40px) clamp(40px, 5vw, 64px);
  box-sizing: border-box;
}

@media (max-width: 900px) {
  .uc-shell {
    flex-direction: column;
  }

  .uc-sidebar {
    width: 100%;
    position: relative;
    top: 0;
    max-height: none;
    border-right: none;
    border-bottom: 1px solid var(--uc-border);
  }

  .uc-nav {
    flex-direction: row;
    flex-wrap: wrap;
    padding: 8px 10px 16px;
  }

  .uc-nav-link {
    flex: 1 1 auto;
    min-width: 140px;
    justify-content: center;
  }

  .uc-main {
    padding: 16px 16px 40px;
  }
}
</style>
