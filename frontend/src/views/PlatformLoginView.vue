<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../auth'

const email = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

async function submit() {
  error.value = ''; loading.value = true
  try {
    await auth.platformLogin(email.value, password.value)
    await router.push(String(route.query.redirect || '/platform'))
  } catch (e: any) { error.value = e.message } finally { loading.value = false }
}
</script>

<template>
  <main class="auth-page platform-auth">
    <section class="auth-card">
      <span class="eyebrow">PLATFORM STAFF ONLY</span>
      <h1>平台管理后台</h1>
      <p>仅限获授权的平台人员。客户公司账号请使用客户服务登录入口。</p>
      <form @submit.prevent="submit">
        <label><span>平台账号邮箱</span><input v-model.trim="email" type="email" required autocomplete="email"></label>
        <label><span>密码</span><input v-model="password" type="password" required autocomplete="current-password"></label>
        <p v-if="error" class="alert alert-danger">{{error}}</p>
        <button class="primary" :disabled="loading">{{loading ? '登录中…' : '进入平台后台'}}</button>
      </form>
      <router-link to="/login">返回客户服务登录</router-link>
    </section>
  </main>
</template>
