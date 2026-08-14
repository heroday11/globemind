<script setup>
import { nextTick, onUnmounted, ref, watch } from 'vue'
import { API_PREFIX } from '@/config/api'
import { formatApiErrorDetail } from '@/utils/apiError'
import { setToken, setCurrentUser } from '@/utils/auth'
import { restoreDialogFocus, trapDialogTab } from '@/utils/dialogFocus'

const props = defineProps({ visible: Boolean })
const emit = defineEmits(['close', 'login-success'])

const loginUsername = ref('')
const loginPassword = ref('')
const loginError = ref('')
const loginLoading = ref(false)
const dialogCard = ref(null)
const usernameField = ref(null)
const errorMessage = ref(null)
const mfaChallenge = ref('')
const mfaMethod = ref('totp')
const mfaValue = ref('')
const mfaField = ref(null)
let returnFocusTarget = null

function closeDialog() {
  if (loginLoading.value) return
  emit('close')
}

function handleRegisterClick(event) {
  if (loginLoading.value) {
    event.preventDefault()
    return
  }
  emit('close')
}

function handleDialogKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeDialog()
    return
  }
  trapDialogTab(event, dialogCard.value, document.activeElement)
}

function restoreOpeningFocus() {
  restoreDialogFocus(returnFocusTarget)
  returnFocusTarget = null
}

function resetLoginState() {
  loginUsername.value = ''
  loginPassword.value = ''
  loginError.value = ''
  mfaChallenge.value = ''
  mfaMethod.value = 'totp'
  mfaValue.value = ''
}

async function showLoginError(message) {
  loginError.value = String(message || '登录失败')
  await nextTick()
  errorMessage.value?.focus()
}

watch(
  () => props.visible,
  async (visible) => {
    if (visible) {
      returnFocusTarget = document.activeElement
      loginError.value = ''
      await nextTick()
      usernameField.value?.focus()
      return
    }
    resetLoginState()
    restoreOpeningFocus()
  },
  { immediate: true },
)

onUnmounted(restoreOpeningFocus)

async function handleLogin() {
  loginError.value = ''
  loginLoading.value = true
  try {
    let res
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
        : { username: loginUsername.value, password: loginPassword.value }
      res = await fetch(`${API_PREFIX}${completingMfa ? '/auth/login/mfa' : '/auth/login'}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
    } catch {
      await showLoginError('网络错误，请稍后重试')
      return
    }
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      await showLoginError(formatApiErrorDetail(data))
      return
    }
    if (data.mfa_required === true && data.challenge && !data.access_token) {
      mfaChallenge.value = data.challenge
      loginPassword.value = ''
      mfaValue.value = ''
      await nextTick()
      mfaField.value?.focus()
    } else if (data.access_token) {
      try {
        setToken(data.access_token)
      } catch {
        await showLoginError('已收到登录响应，但浏览器无法保存登录状态。请允许站点存储后重试。')
        return
      }
      if (data.user) {
        try {
          setCurrentUser(data.user)
        } catch {
          console.warn('登录成功，但浏览器未能保存用户资料。')
        }
      }
      resetLoginState()
      window.dispatchEvent(new CustomEvent('loginSuccess'))
      emit('login-success')
      emit('close')
    } else {
      await showLoginError('登录响应无效：服务未返回访问令牌。')
    }
  } finally {
    loginLoading.value = false
  }
}

async function restartPasswordLogin() {
  mfaChallenge.value = ''
  mfaValue.value = ''
  loginError.value = ''
  await nextTick()
  usernameField.value?.focus()
}

watch(mfaMethod, async () => {
  mfaValue.value = ''
  await nextTick()
  mfaField.value?.focus()
})
</script>

<template>
  <div v-if="visible" class="login-modal-overlay" @click.self="closeDialog">
    <div
      ref="dialogCard"
      class="login-modal-card"
      role="dialog"
      aria-modal="true"
      aria-labelledby="login-modal-title"
      aria-describedby="login-modal-subtitle"
      tabindex="-1"
      @click.stop
      @keydown="handleDialogKeydown"
    >
      <h2 id="login-modal-title" class="login-modal-title">
        {{ mfaChallenge ? '完成双因素验证' : '登录 GlobeMind' }}
      </h2>
      <p id="login-modal-subtitle" class="login-modal-subtitle">
        {{
          mfaChallenge
            ? '密码已验证；动态码或单枚恢复码验证成功后才会签发访问令牌。'
            : '同步收藏、使用数据助手并管理你的研究报告'
        }}
      </p>
      <form class="login-modal-form" :aria-busy="loginLoading" @submit.prevent="handleLogin">
        <div v-if="!mfaChallenge" class="login-modal-item">
          <label for="login-modal-username">用户名或邮箱</label>
          <input
            id="login-modal-username"
            ref="usernameField"
            v-model.trim="loginUsername"
            name="username"
            type="text"
            required
            autocomplete="username"
            placeholder="请输入用户名或邮箱"
          />
        </div>
        <div v-if="!mfaChallenge" class="login-modal-item">
          <label for="login-modal-password">密码</label>
          <input
            id="login-modal-password"
            v-model="loginPassword"
            name="password"
            type="password"
            required
            autocomplete="current-password"
            placeholder="请输入密码"
          />
        </div>
        <template v-else>
          <fieldset class="login-modal-mfa-methods">
            <legend>验证方式</legend>
            <label><input v-model="mfaMethod" type="radio" value="totp" />动态验证码</label>
            <label><input v-model="mfaMethod" type="radio" value="recovery" />恢复码</label>
          </fieldset>
          <div class="login-modal-item">
            <label for="login-modal-mfa-code">{{
              mfaMethod === 'totp' ? '6 位动态验证码' : '恢复码'
            }}</label>
            <input
              id="login-modal-mfa-code"
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
        <div class="login-modal-buttons">
          <button type="submit" class="login-modal-submit" :disabled="loginLoading">
            {{ loginLoading ? '验证中...' : mfaChallenge ? '验证并登录' : '登录' }}
          </button>
          <button
            type="button"
            class="login-modal-cancel"
            @click="mfaChallenge ? restartPasswordLogin() : closeDialog()"
            :disabled="loginLoading"
          >
            {{ mfaChallenge ? '返回密码登录' : '取消' }}
          </button>
        </div>
        <div class="login-modal-register">
          <router-link
            to="/register"
            :aria-disabled="loginLoading"
            :tabindex="loginLoading ? -1 : undefined"
            @click="handleRegisterClick"
            >注册账号</router-link
          >
        </div>
        <p
          v-if="loginError"
          id="login-modal-error"
          ref="errorMessage"
          class="login-modal-error"
          role="alert"
          aria-live="assertive"
          tabindex="-1"
        >
          {{ loginError }}
        </p>
      </form>
    </div>
  </div>
</template>

<style scoped>
.login-modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  width: 100vw;
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 36, 67, 0.45);
  z-index: 9999;
  overflow: auto;
}

.login-modal-card {
  background: rgba(255, 255, 255, 0.68);
  border: 1px solid rgba(255, 255, 255, 0.8);
  padding: 28px 30px;
  border-radius: 20px;
  box-shadow: 0 24px 56px rgba(20, 57, 109, 0.26);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  width: 400px;
  min-height: 460px;
  max-width: 90vw;
  margin: auto;
  position: relative;
  display: flex;
  flex-direction: column;
}

.login-modal-title {
  margin: 0 0 7px 0;
  text-align: left;
  font-size: 34px;
  font-weight: 700;
  color: #173d6a;
}

.login-modal-subtitle {
  margin: 0 0 18px;
  color: #55708f;
  font-size: 13px;
  line-height: 1.55;
}

.login-modal-form {
  display: flex;
  margin-top: 10px;
  flex-direction: column;
  gap: 24px;
  flex: 1;
  position: relative;
}

.login-modal-item {
  display: flex;
  flex-direction: column;
}

.login-modal-item label {
  display: block;
  margin-bottom: 8px;
  font-size: 16px;
  font-weight: 600;
  color: #1f436e;
}

.login-modal-item input {
  width: 100%;
  min-height: 48px;
  padding: 10px 14px;
  border: 1px solid rgba(47, 92, 145, 0.3);
  border-radius: 12px;
  box-sizing: border-box;
  background: rgba(255, 255, 255, 0.84);
  outline: none;
  color: #17375f;
  font-size: 18px;
  transition:
    border-color 0.2s ease,
    box-shadow 0.2s ease,
    background 0.2s ease;
}

.login-modal-item input:focus {
  border-color: rgba(34, 99, 182, 0.58);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.18);
  background: rgba(255, 255, 255, 0.96);
  outline: none;
}

.login-modal-item input::placeholder {
  color: #999;
}
.login-modal-mfa-methods {
  display: flex;
  gap: 16px;
  border: 1px solid rgba(47, 92, 145, 0.3);
  border-radius: 12px;
  padding: 12px 14px;
}
.login-modal-mfa-methods legend {
  padding: 0 6px;
  color: #1f436e;
  font-weight: 600;
}
.login-modal-mfa-methods label {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #1f436e;
}

.login-modal-error {
  position: relative;
  top: 8px;
  color: #b42318;
  font-size: 14px;
  text-align: center;
  font-weight: 500;
}

.login-modal-error:focus-visible {
  outline: 3px solid rgba(180, 35, 24, 0.25);
  outline-offset: 3px;
}

.login-modal-buttons {
  position: absolute;
  bottom: 54px;
  left: 0;
  right: 0;
  display: flex;
  gap: 12px;
  padding: 0 10px;
}

.login-modal-submit {
  flex: 1;
  min-height: 46px;
  border: none;
  border-radius: 12px;
  background: linear-gradient(135deg, #667eea, #4e67d8);
  color: #fff;
  cursor: pointer;
  font-size: 16px;
  font-weight: 700;
}

.login-modal-submit:hover {
  background: linear-gradient(135deg, #5d74df, #435ecf);
}

.login-modal-submit:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.login-modal-cancel {
  flex: 1;
  min-height: 46px;
  border-radius: 12px;
  border: 1px solid rgba(88, 111, 196, 0.45);
  background: rgba(255, 255, 255, 0.72);
  cursor: pointer;
  font-size: 16px;
  color: #375b86;
  font-weight: 600;
}

.login-modal-cancel:hover {
  background: rgba(255, 255, 255, 0.92);
}

.login-modal-cancel:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.login-modal-register {
  position: absolute;
  bottom: 12px;
  left: 0;
  right: 0;
  text-align: center;
  padding: 0 20px;
}

.login-modal-register a {
  color: #355f95;
  text-decoration: none;
  font-size: 15px;
  font-weight: 600;
  transition: color 0.3s ease;
}

.login-modal-register a:hover {
  color: #1a4f95;
  text-decoration: underline;
}
</style>
