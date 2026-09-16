<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../auth'
const username = ref(''), password = ref(''), error = ref(''), loading = ref(false)
const auth = useAuthStore(), router = useRouter(), route = useRoute()
async function submit() {
  error.value = ''; loading.value = true
  try { await auth.login(username.value, password.value); await router.push(String(route.query.redirect || '/app')) }
  catch (e:any) { error.value = e.message } finally { loading.value = false }
}
</script>
<template>
  <main class="auth-page"><section class="auth-card modern-auth">
    <div class="auth-emblem" aria-hidden="true">A</div><span class="eyebrow">WELCOME BACK</span>
    <h1>欢迎回来</h1><p>登录你的账号，继续推进每一次实施。</p>
    <form @submit.prevent="submit">
      <label><span>账号</span><input v-model.trim="username" type="text" required autocomplete="username" placeholder="输入账号或已绑定邮箱" maxlength="160"></label>
      <label><span>密码</span><input v-model="password" type="password" required autocomplete="current-password" placeholder="输入你的密码" maxlength="128"></label>
      <div class="auth-helper"><span>账号密码登录</span><router-link class="text-link" to="/forgot-password">忘记密码</router-link></div>
      <p v-if="error" class="alert alert-danger" role="alert">{{error}}</p>
      <button class="primary auth-submit" :disabled="loading">{{loading ? '登录中…' : '登录'}}</button>
    </form>
    <div class="auth-footer"><span>还没有账号？</span><router-link class="text-link" to="/register">创建账号 <span aria-hidden="true">→</span></router-link></div>
  </section></main>
</template>
