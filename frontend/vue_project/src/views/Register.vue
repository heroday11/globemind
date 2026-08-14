<template>
  <div class="auth-page">
    <router-link class="home-link" to="/">← 返回首页</router-link>
    <div class="auth-card glass-panel">
      <h1>创建账号</h1>
      <p class="auth-subtitle">创建账号前，请先了解信息处理方式和实验性使用边界</p>
      <form @submit.prevent="handleRegister" :aria-busy="loading">
        <div class="form-item">
          <label for="register-username">用户名</label>
          <input id="register-username" ref="firstField" v-model.trim="form.username" name="username" required autocomplete="username" placeholder="请输入用户名" />
        </div>
        <div class="form-item">
          <label for="register-full-name">姓名（可选）</label>
          <input id="register-full-name" v-model.trim="form.full_name" name="full_name" autocomplete="name" placeholder="可稍后在个人中心填写" />
        </div>
        <div class="form-item">
          <label for="register-email">邮箱</label>
          <input id="register-email" v-model.trim="form.email" name="email" type="email" required autocomplete="email" placeholder="请输入邮箱" />
        </div>
        <div class="form-item">
          <label for="register-phone">手机号（可选）</label>
          <input id="register-phone" v-model.trim="form.phone" name="phone" type="tel" inputmode="tel" autocomplete="tel" pattern="1[3-9][0-9]{9}" placeholder="可稍后在个人中心填写" />
        </div>
        <div class="form-item">
          <label for="register-password">密码</label>
          <input id="register-password" v-model="form.password" name="password" type="password" required minlength="8" autocomplete="new-password" aria-describedby="register-password-hint" placeholder="至少 8 位，包含字母和数字" />
          <p id="register-password-hint" class="field-hint">至少 8 位，且同时包含字母和数字。</p>
        </div>
        <div class="form-item">
          <label for="register-confirm-password">确认密码</label>
          <input id="register-confirm-password" v-model="form.confirm_password" name="confirm_password" type="password" required minlength="8" autocomplete="new-password" placeholder="请再次输入密码" />
        </div>
        <div class="consent-item">
          <input
            id="register-consent"
            v-model="acceptedNotice"
            name="accepted_notice"
            type="checkbox"
            required
            aria-describedby="registration-data-note"
          />
          <label for="register-consent">
            我已阅读并同意
            <router-link to="/terms" target="_blank" rel="noopener noreferrer">《服务条款与使用边界》</router-link>，并已了解
            <router-link to="/privacy" target="_blank" rel="noopener noreferrer">《隐私说明》</router-link>。
          </label>
        </div>
        <p id="registration-data-note" class="registration-data-note">
          创建账号仅要求用户名、邮箱和密码；姓名与手机号为可选资料，可在个人中心留空或清除。分类保留期及完整自助删除流程仍待治理复核。
        </p>
        <p v-if="error" id="register-error" ref="errorMessage" class="error" role="alert" aria-live="assertive" tabindex="-1">{{ error }}</p>
        <p v-if="success" id="register-success" ref="successMessage" class="success" role="status" aria-live="polite" tabindex="-1">{{ success }}</p>
        <button type="submit" :disabled="loading" :aria-disabled="loading">{{ loading ? '提交中...' : '注册' }}</button>
      </form>
      <p class="tips">已有账号？<router-link to="/login">去登录</router-link></p>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { API_PREFIX } from '@/config/api'
import { formatApiErrorDetail } from '@/utils/apiError'

defineOptions({ name: 'UserRegister' })

const router = useRouter()
const loading = ref(false)
const error = ref('')
const success = ref('')
const acceptedNotice = ref(false)
const firstField = ref(null)
const errorMessage = ref(null)
const successMessage = ref(null)
const form = reactive({
  username: '',
  full_name: '',
  email: '',
  phone: '',
  password: '',
  confirm_password: '',
})

async function showError(message) {
  error.value = message
  await nextTick()
  errorMessage.value?.focus()
}

onMounted(() => firstField.value?.focus())

async function handleRegister() {
  error.value = ''
  success.value = ''
  if (form.password !== form.confirm_password) {
    await showError('两次输入密码不一致')
    return
  }
  if (form.password.length < 8 || !/[A-Za-z]/.test(form.password) || !/\d/.test(form.password)) {
    await showError('密码至少 8 位，且需同时包含字母和数字')
    return
  }
  if (!acceptedNotice.value) {
    await showError('请先阅读服务条款和隐私说明，并确认同意与了解')
    return
  }
  loading.value = true
  try {
    const res = await fetch(`${API_PREFIX}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      await showError(formatApiErrorDetail(data))
      return
    }
    success.value = '注册成功，正在跳转登录页...'
    await nextTick()
    successMessage.value?.focus()
    setTimeout(() => router.replace('/login'), 900)
  } catch {
    await showError('网络错误，请稍后重试')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-page {
  position: relative;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 36px 18px;
  box-sizing: border-box;
  background:
    linear-gradient(rgba(244, 248, 255, 0.78), rgba(239, 245, 255, 0.78)),
    url('/imgs/home/hero-bg.webp') center center / cover no-repeat;
}

.home-link {
  position: absolute;
  top: 22px;
  left: 28px;
  color: #214d84;
  font-weight: 600;
  text-decoration: none;
}

.home-link:hover { text-decoration: underline; }

.auth-card {
  width: min(100%, 620px);
  padding: 40px 40px 34px;
  border-radius: 24px;
}

.glass-panel {
  background: rgba(255, 255, 255, 0.65);
  border: 1px solid rgba(255, 255, 255, 0.84);
  box-shadow:
    0 26px 60px rgba(22, 62, 121, 0.2),
    inset 0 1px 0 rgba(255, 255, 255, 0.55);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
}

h1 {
  margin: 0;
  font-size: 2.15rem;
  line-height: 1.2;
  color: #14345d;
  font-weight: 700;
}

.auth-subtitle {
  margin: 10px 0 26px;
  font-size: 1.05rem;
  color: #3f5f87;
  line-height: 1.65;
}

form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px 16px;
}

.form-item {
  margin: 0;
}

.form-item:nth-last-child(-n + 2) {
  grid-column: span 1;
}

.form-item label {
  display: block;
  margin-bottom: 8px;
  font-size: 1rem;
  font-weight: 600;
  color: #1e3a61;
}

.form-item input {
  width: 100%;
  min-height: 50px;
  padding: 11px 14px;
  border: 1px solid rgba(47, 92, 145, 0.26);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.86);
  color: #17375f;
  font-size: 1rem;
  box-sizing: border-box;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, background 0.2s ease;
}

.form-item input:focus {
  outline: none;
  border-color: rgba(34, 99, 182, 0.55);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.18);
  background: rgba(255, 255, 255, 0.96);
}

.field-hint {
  margin: 6px 2px 0;
  color: #59708e;
  font-size: 0.82rem;
  line-height: 1.5;
}

.consent-item {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  color: #344f72;
  font-size: 0.92rem;
  line-height: 1.65;
}

.consent-item input {
  width: 20px;
  height: 20px;
  margin: 2px 0 0;
  accent-color: #1767c6;
}

.consent-item input:focus-visible {
  outline: 3px solid rgba(59, 130, 246, 0.35);
  outline-offset: 3px;
}

.consent-item a,
.registration-data-note a {
  color: #1257ad;
  font-weight: 650;
}

.registration-data-note {
  grid-column: 1 / -1;
  margin: -6px 0 0 32px;
  color: #647792;
  font-size: 0.82rem;
  line-height: 1.65;
}

button {
  grid-column: 1 / -1;
  width: 100%;
  margin-top: 2px;
  border: 0;
  background: linear-gradient(135deg, #1976d2, #0f56b3);
  color: #fff;
  min-height: 52px;
  border-radius: 12px;
  font-size: 1.08rem;
  font-weight: 600;
  letter-spacing: 0.02em;
  cursor: pointer;
  transition: transform 0.15s ease, box-shadow 0.2s ease;
}

button:not(:disabled):hover {
  transform: translateY(-1px);
  box-shadow: 0 12px 26px rgba(17, 80, 160, 0.35);
}

button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.error,
.success {
  grid-column: 1 / -1;
  font-size: 0.97rem;
  margin: -4px 0 0;
}

.error { color: #b42318; }
.success { color: #1f7a39; }

.error:focus,
.success:focus {
  outline: 3px solid currentColor;
  outline-offset: 4px;
}

.tips {
  margin: 16px 0 0;
  font-size: 0.98rem;
  text-align: center;
  color: #385578;
}

.tips a {
  color: #1257ad;
  font-weight: 600;
  text-decoration: none;
}

.tips a:hover {
  text-decoration: underline;
}

@media (max-width: 700px) {
  .auth-card {
    padding: 30px 20px 24px;
    border-radius: 18px;
  }

  h1 {
    font-size: 1.8rem;
  }

  form {
    grid-template-columns: 1fr;
    gap: 14px;
  }

  .registration-data-note {
    margin-left: 0;
  }
}

@media (max-width: 480px) {
  .auth-page {
    padding: 64px 12px 24px;
  }

  .home-link { top: 18px; left: 18px; }

  .auth-card {
    padding: 24px 16px 20px;
    border-radius: 14px;
  }

  h1 {
    font-size: 1.5rem;
  }

  .auth-subtitle {
    font-size: 0.92rem;
    margin: 8px 0 20px;
  }

  .form-item label {
    font-size: 0.9rem;
  }

  .form-item input {
    min-height: 44px;
    font-size: 0.95rem;
  }

  button {
    min-height: 46px;
    font-size: 1rem;
  }
}
</style>
