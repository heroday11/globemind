<template>
  <div class="auth-page">
    <router-link class="home-link" to="/">← 返回首页</router-link>
    <div class="auth-card" aria-labelledby="reset-password-title">
      <h1 id="reset-password-title">重置密码</h1>
      <p class="auth-subtitle">设置至少 8 位且同时包含字母和数字的新密码。</p>
      <form @submit.prevent="handleSubmit" :aria-busy="loading">
        <div class="form-item">
          <label for="reset-new-password">新密码</label>
          <input id="reset-new-password" ref="firstField" v-model="newPassword" name="new_password" type="password" required minlength="8" autocomplete="new-password" aria-describedby="reset-password-hint" placeholder="请输入新密码" />
          <p id="reset-password-hint" class="field-hint">至少 8 位，且同时包含字母和数字。</p>
        </div>
        <div class="form-item">
          <label for="reset-confirm-password">确认新密码</label>
          <input id="reset-confirm-password" v-model="confirmPassword" name="confirm_password" type="password" required minlength="8" autocomplete="new-password" placeholder="请再次输入新密码" />
        </div>
        <p v-if="error" ref="errorMessage" class="error" role="alert" aria-live="assertive" tabindex="-1">{{ error }}</p>
        <p v-if="message" ref="successMessage" class="success" role="status" aria-live="polite" tabindex="-1">{{ message }}</p>
        <button type="submit" :disabled="loading || !hasToken" :aria-disabled="loading || !hasToken">{{ loading ? '提交中...' : '确认重置' }}</button>
      </form>
      <p class="tips"><router-link to="/login">返回登录</router-link></p>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { API_PREFIX } from '@/config/api'
import { formatApiErrorDetail } from '@/utils/apiError'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const error = ref('')
const message = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const firstField = ref(null)
const errorMessage = ref(null)
const successMessage = ref(null)
const hasToken = computed(() => Boolean(String(route.query.token || '').trim()))

async function showError(value) {
  error.value = value
  await nextTick()
  errorMessage.value?.focus()
}

onMounted(async () => {
  if (!hasToken.value) {
    await showError('重置链接缺少 token，请重新申请密码重置链接')
    return
  }
  firstField.value?.focus()
})

async function handleSubmit() {
  const token = (route.query.token || '').toString()
  if (!token) {
    await showError('重置链接缺少 token，请重新申请密码重置链接')
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    await showError('两次输入密码不一致')
    return
  }
  if (newPassword.value.length < 8 || !/[A-Za-z]/.test(newPassword.value) || !/\d/.test(newPassword.value)) {
    await showError('密码至少 8 位，且需同时包含字母和数字')
    return
  }
  loading.value = true
  error.value = ''
  message.value = ''
  try {
    const res = await fetch(`${API_PREFIX}/auth/reset-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        token,
        new_password: newPassword.value,
        confirm_password: confirmPassword.value,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      await showError(formatApiErrorDetail(data))
      return
    }
    message.value = '密码重置成功，正在跳转登录页...'
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
.auth-page { position: relative; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 64px 24px 24px; box-sizing: border-box; background: #f0f2f5; }
.home-link { position: absolute; top: 22px; left: 28px; color: #245b9c; font-weight: 650; }
.auth-card { background: #fff; padding: 30px; width: 460px; max-width: 100%; border-radius: 12px; box-shadow: 0 8px 28px rgba(20,55,95,.12); }
.auth-card h1 { margin: 0; color: #16365c; }
.auth-subtitle { margin: 10px 0 22px; color: #5a6f89; line-height: 1.65; }
.form-item { margin-bottom: 12px; }
.form-item label { display: block; margin-bottom: 7px; font-size: 14px; font-weight: 650; color: #344f70; }
.form-item input { width: 100%; min-height: 46px; padding: 9px 11px; border: 1px solid #cdd9e8; border-radius: 8px; box-sizing: border-box; font-size: 16px; }
.form-item input:focus-visible { outline: 3px solid rgba(46, 112, 202, .25); border-color: #3974c4; }
.field-hint { margin: 6px 0 0; color: #657890; font-size: 13px; line-height: 1.5; }
button { width: 100%; margin-top: 6px; border: 0; background: #409eff; color: #fff; padding: 10px 0; border-radius: 4px; cursor: pointer; }
.error { color: #b42318; font-size: 14px; }
.success { color: #1f7a39; font-size: 14px; }
.error:focus, .success:focus { outline: 3px solid currentColor; outline-offset: 4px; }
.auth-card button:disabled { opacity: .62; cursor: not-allowed; }
.tips { margin: 12px 0 0; font-size: 13px; text-align: center; }

@media (max-width: 480px) {
  .auth-page { padding: 16px; }
  .auth-card { padding: 20px 16px; }
  .form-item input { min-height: 44px; font-size: 15px; }
  button { min-height: 46px; font-size: 15px; }
}
</style>
