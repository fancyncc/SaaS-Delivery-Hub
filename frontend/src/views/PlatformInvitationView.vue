<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useAuthStore } from '../auth'

const name = ref(''); const password = ref(''); const confirm = ref('')
const error = ref(''); const loading = ref(false)
const route = useRoute(); const router = useRouter(); const auth = useAuthStore()
async function submit() {
  error.value = ''
  if (password.value !== confirm.value) { error.value = '两次密码不一致'; return }
  loading.value = true
  try {
    await api(`/api/auth/platform-invitations/${String(route.query.token || '')}/accept`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({display_name: name.value, password: password.value}),
    })
    await auth.load(); await router.push('/platform')
  } catch (e:any) { error.value = e.message } finally { loading.value = false }
}
</script>

<template><main class="auth-page"><section class="auth-card">
  <span class="eyebrow">PLATFORM INVITATION</span><h1>接受平台人员邀请</h1>
  <p>平台账号不能同时加入客户公司。</p>
  <form @submit.prevent="submit"><label><span>姓名</span><input v-model.trim="name" required minlength="2"></label>
    <label><span>密码</span><input v-model="password" type="password" required minlength="10"></label>
    <label><span>确认密码</span><input v-model="confirm" type="password" required></label>
    <p v-if="error" class="alert alert-danger">{{error}}</p><button class="primary" :disabled="loading">接受邀请并登录</button>
  </form>
</section></main></template>
