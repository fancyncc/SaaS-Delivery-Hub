<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, writeHeaders } from '../api'
import { useAuthStore } from '../auth'
const route = useRoute(), router = useRouter(), auth = useAuthStore()
const email = ref(''), password = ref(''), currentPassword = ref(''), confirmation = ref('')
const error = ref(''), message = ref(''), busy = ref(false)
const changing = computed(() => route.name === 'change-password')
const resetting = computed(() => route.name === 'reset')
async function submit() {
  error.value = ''; message.value = ''; busy.value = true
  try {
    if (changing.value || resetting.value) {
      if (password.value !== confirmation.value) throw new Error('两次输入的新密码不一致')
      await api(`/api/auth/password/${changing.value ? 'change' : 'reset'}`, {
        method: 'POST', headers: changing.value ? writeHeaders() : { 'Content-Type': 'application/json' },
        body: JSON.stringify(changing.value ? { current_password: currentPassword.value, password: password.value }
          : { token: String(route.query.token || ''), password: password.value }),
      })
      if (changing.value) { await auth.load(); await router.replace(auth.isPlatform ? '/platform' : '/app') }
      else { message.value = '密码已重置，旧会话与重置链接均已失效。请使用新密码登录。' }
      password.value = ''; currentPassword.value = ''; confirmation.value = ''
    } else {
      const data = await api<{message: string}>('/api/auth/password/forgot', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: email.value }) })
      message.value = data.message
    }
  } catch (e: unknown) { error.value = e instanceof Error ? e.message : '操作失败，请稍后重试' }
  finally { busy.value = false }
}
</script>
<template>
  <main class="auth-page"><section class="auth-card">
    <h1>{{ changing ? '修改初始密码' : resetting ? '设置新密码' : '找回密码' }}</h1>
    <p>{{ changing ? '为保护账号安全，请先修改初始密码后继续。' : resetting ? '保存后，所有旧会话与重置链接都会失效。' : '请输入账号邮箱，重置链接将通过邮件发送。' }}</p>
    <form @submit.prevent="submit">
      <label v-if="!changing && !resetting"><span>邮箱</span><input v-model.trim="email" type="email" autocomplete="email" required></label>
      <template v-else>
        <label v-if="changing"><span>当前密码</span><input v-model="currentPassword" type="password" autocomplete="current-password" maxlength="128" required></label>
        <label><span>新密码（10–128 位，包含字母和数字）</span><input v-model="password" type="password" autocomplete="new-password" minlength="10" maxlength="128" required></label>
        <label><span>确认新密码</span><input v-model="confirmation" type="password" autocomplete="new-password" minlength="10" maxlength="128" required></label>
      </template>
      <p v-if="error" class="alert alert-danger" role="alert">{{ error }}</p>
      <p v-if="message" class="alert alert-success" role="status">{{ message }}</p>
      <button class="primary" :disabled="busy">{{ busy ? '正在提交…' : '提交' }}</button>
      <RouterLink v-if="resetting && message" to="/login">返回登录</RouterLink>
    </form>
  </section></main>
</template>
