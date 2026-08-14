<template>
  <div class="login-page">
    <router-link class="home-link" to="/">← 返回首页</router-link>
    <div class="login-card glass-panel">
      <h1>{{ mfaChallenge ? '完成双因素验证' : '欢迎登录' }}</h1>
      <p class="auth-subtitle">
        {{
          mfaChallenge
            ? '密码已验证。请输入验证器中的 6 位动态码，或使用一枚恢复码。'
            : '登录后即可访问 GlobeMind 数据服务平台'
        }}
      </p>
      <form @submit.prevent="handleLogin" class="login-form" :aria-busy="loading">
        <div v-if="!mfaChallenge" class="form-item">
          <label for="login-username">用户名或邮箱</label>
          <input
            id="login-username"
            ref="firstField"
            v-model="username"
            name="username"
            type="text"
            required
            autocomplete="username"
            placeholder="用户名或邮箱"
          />
        </div>
        <div v-if="!mfaChallenge" class="form-item">
          <label for="login-password">密码</label>
          <input
            id="login-password"
            v-model="password"
            name="password"
            type="password"
            required
            autocomplete="current-password"
            placeholder="请输入密码"
          />
        </div>
        <template v-else>
          <fieldset class="mfa-methods">
            <legend>验证方式</legend>
            <label><input v-model="mfaMethod" type="radio" value="totp" />动态验证码</label>
            <label><input v-model="mfaMethod" type="radio" value="recovery" />恢复码</label>
          </fieldset>
          <div class="form-item">
            <label for="login-mfa-code">{{
              mfaMethod === 'totp' ? '6 位动态验证码' : '恢复码'
            }}</label>
            <input
              id="login-mfa-code"
              ref="mfaField"
              v-model="mfaValue"
              :inputmode="mfaMethod === 'totp' ? 'numeric' : 'text'"
              :autocomplete="mfaMethod === 'totp' ? 'one-time-code' : 'off'"
              :pattern="
                mfaMethod === 'totp' ? '[0-9]{6}' : '[A-Za-z2-9]{4}-[A-Za-z2-9]{4}-[A-Za-z2-9]{4}'
              "
              required
            />
          </div>
        </template>
        <p
          v-if="error"
          ref="errorMessage"
          class="error"
          role="alert"
          aria-live="assertive"
          tabindex="-1"
        >
          {{ error }}
        </p>
        <p
          v-if="success"
          ref="successMessage"
          class="success"
          role="status"
          aria-live="polite"
          tabindex="-1"
        >
          {{ success }}
        </p>
        <button type="submit" :disabled="loading" :aria-disabled="loading">
          {{ loading ? '验证中...' : mfaChallenge ? '验证并登录' : '登录' }}
        </button>
        <button
          v-if="mfaChallenge"
          type="button"
          class="restart-login"
          :disabled="loading"
          @click="restartLogin"
        >
          返回密码登录
        </button>
        <div v-if="!mfaChallenge" class="links">
          <router-link to="/register">注册账号</router-link>
          <router-link to="/forgot-password">忘记密码</router-link>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { API_PREFIX } from '@/config/api'
import { formatApiErrorDetail } from '@/utils/apiError'
import { getToken, setToken, setCurrentUser } from '@/utils/auth'
import { resolveSafeInternalRedirect } from '@/utils/internalRedirect.js'

// 定义组件名以符合 Vue 规范
defineOptions({
  name: 'LoginPage',
})

const router = useRouter()
const route = useRoute()
const username = ref('')
const password = ref('')
const error = ref('')
const success = ref('')
const loading = ref(false)
const firstField = ref(null)
const errorMessage = ref(null)
const successMessage = ref(null)
const mfaChallenge = ref('')
const mfaMethod = ref('totp')
const mfaValue = ref('')
const mfaField = ref(null)

async function showError(message) {
  error.value = message
  await nextTick()
  errorMessage.value?.focus()
}

async function handleLogin() {
  error.value = ''
  success.value = ''
  loading.value = true
  try {
    const completingMfa = Boolean(mfaChallenge.value)
    const payload = completingMfa
      ? {
          challenge: mfaChallenge.value,
          ...(mfaMethod.value === 'totp'
            ? { code: mfaValue.value }
            : {
                recovery_code: String(mfaValue.value || '')
                  .trim()
                  .toUpperCase(),
              }),
        }
      : {
          username: String(username.value || '').trim(),
          password: password.value,
        }
    const res = await fetch(`${API_PREFIX}${completingMfa ? '/auth/login/mfa' : '/auth/login'}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      await showError(formatApiErrorDetail(data))
      return
    }
    if (data.mfa_required === true && data.challenge && !data.access_token) {
      mfaChallenge.value = data.challenge
      password.value = ''
      mfaValue.value = ''
      success.value = '密码验证通过，请完成双因素验证。'
      await nextTick()
      mfaField.value?.focus()
    } else if (data.access_token) {
      setToken(data.access_token)
      if (data.user) {
        setCurrentUser(data.user)
      }
      success.value = '登录成功，正在跳转…'
      await nextTick()
      successMessage.value?.focus()
      await router.replace(resolveSafeInternalRedirect(route.query.redirect, router.resolve))
    } else {
      await showError('登录响应缺少身份凭据，请重试或联系支持')
    }
  } catch {
    await showError('网络错误，请稍后重试')
  } finally {
    loading.value = false
  }
}

async function restartLogin() {
  mfaChallenge.value = ''
  mfaValue.value = ''
  mfaMethod.value = 'totp'
  error.value = ''
  success.value = ''
  await nextTick()
  firstField.value?.focus()
}

watch(mfaMethod, async () => {
  mfaValue.value = ''
  await nextTick()
  mfaField.value?.focus()
})

onMounted(() => {
  if (getToken()) {
    router.replace(resolveSafeInternalRedirect(route.query.redirect, router.resolve))
    return
  }
  firstField.value?.focus()
})
</script>

<style scoped>
.login-page {
  position: relative;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  box-sizing: border-box;
  background-image:
    radial-gradient(circle at 20% 20%, rgba(66, 153, 225, 0.35), transparent 48%),
    radial-gradient(circle at 80% 10%, rgba(49, 130, 206, 0.28), transparent 45%),
    linear-gradient(145deg, #eef6ff 0%, #d7e8ff 45%, #d4e2f7 100%);
}
.home-link {
  position: absolute;
  top: 24px;
  left: 28px;
  color: #214d84;
  font-weight: 600;
  text-decoration: none;
}
.home-link:hover {
  text-decoration: underline;
}
.login-card {
  width: min(100%, 500px);
  padding: 36px 34px 30px;
  border-radius: 22px;
}
.glass-panel {
  background: rgba(255, 255, 255, 0.66);
  border: 1px solid rgba(255, 255, 255, 0.8);
  box-shadow:
    0 24px 60px rgba(22, 62, 121, 0.18),
    inset 0 1px 0 rgba(255, 255, 255, 0.55);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
}
.login-card h1 {
  margin: 0;
  font-size: 2rem;
  font-weight: 700;
  color: #14345d;
  text-align: left;
}
.auth-subtitle {
  margin: 10px 0 24px;
  font-size: 1.02rem;
  color: #3f5f87;
  line-height: 1.6;
}
.login-form .form-item {
  margin-bottom: 16px;
}
.login-form label {
  display: block;
  margin-bottom: 8px;
  font-size: 1rem;
  font-weight: 600;
  color: #1e3a61;
}
.login-form input {
  width: 100%;
  min-height: 48px;
  padding: 11px 14px;
  border: 1px solid rgba(47, 92, 145, 0.26);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.84);
  color: #17375f;
  font-size: 1rem;
  transition:
    border-color 0.2s ease,
    box-shadow 0.2s ease,
    background 0.2s ease;
  box-sizing: border-box;
}
.login-form input:focus {
  outline: none;
  border-color: rgba(34, 99, 182, 0.55);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.18);
  background: rgba(255, 255, 255, 0.96);
}
.login-form .error {
  color: #b42318;
  font-size: 0.95rem;
  margin: 4px 0 12px;
}
.login-form .success {
  color: #1f7a39;
  font-size: 0.95rem;
  margin: 4px 0 12px;
}
.login-form .error:focus,
.login-form .success:focus {
  outline: 3px solid currentColor;
  outline-offset: 4px;
}
.login-form button {
  width: 100%;
  min-height: 50px;
  padding: 12px;
  margin-top: 10px;
  background: linear-gradient(135deg, #1976d2, #0f56b3);
  color: #fff;
  border: none;
  border-radius: 12px;
  font-size: 1.05rem;
  font-weight: 600;
  letter-spacing: 0.02em;
  cursor: pointer;
  transition:
    transform 0.15s ease,
    box-shadow 0.2s ease;
}
.login-form button:not(:disabled):hover {
  transform: translateY(-1px);
  box-shadow: 0 12px 26px rgba(17, 80, 160, 0.35);
}
.login-form button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.login-form .restart-login {
  margin-top: 10px;
  color: #214d84;
  border: 1px solid rgba(47, 92, 145, 0.3);
  background: rgba(255, 255, 255, 0.72);
}
.mfa-methods {
  display: flex;
  gap: 18px;
  margin: 0 0 16px;
  border: 1px solid rgba(47, 92, 145, 0.26);
  border-radius: 12px;
  padding: 12px 14px;
}
.mfa-methods legend {
  padding: 0 6px;
  color: #1e3a61;
  font-weight: 600;
}
.mfa-methods label {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0;
}
.mfa-methods input {
  width: auto;
  min-height: 20px;
}
.links {
  margin-top: 16px;
  display: flex;
  justify-content: space-between;
  font-size: 0.96rem;
}
.links a {
  color: #1257ad;
  font-weight: 500;
  text-decoration: none;
}
.links a:hover {
  text-decoration: underline;
}
@media (max-width: 520px) {
  .home-link {
    top: 18px;
    left: 18px;
  }
}
</style>
