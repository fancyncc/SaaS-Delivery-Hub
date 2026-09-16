<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, writeHeaders } from '../api'
import { useAuthStore } from '../auth'
const route=useRoute(), router=useRouter(), auth=useAuthStore()
const error=ref(''), busy=ref(false)
async function verify() {
  busy.value=true; error.value=''
  try {
    await api('/api/auth/verify-email', {method:'POST', headers:writeHeaders(), body:JSON.stringify({token:String(route.query.token || '')})})
    await auth.load(); await router.push('/app/profile')
  } catch(e:any) {error.value=e.message} finally {busy.value=false}
}
</script>
<template><main class="auth-page"><section class="auth-card modern-auth">
  <div class="auth-emblem" aria-hidden="true">✓</div><span class="eyebrow">EMAIL VERIFICATION</span><h1>验证邮箱</h1><p>确认绑定后，可用此邮箱接收通知和找回密码。</p>
  <p v-if="auth.user" class="auth-benefit">当前账号：{{auth.user.username}}</p>
  <p v-if="error" class="alert alert-danger" role="alert">{{error}}</p>
  <button v-if="auth.user" class="primary auth-submit" :disabled="busy || !route.query.token" @click="verify">{{busy ? '验证中…' : '确认绑定邮箱'}}</button>
  <router-link v-else class="primary auth-submit" :to="{path:'/login',query:{redirect:route.fullPath}}">登录后验证邮箱</router-link>
  <div class="auth-footer"><router-link class="text-link" to="/app/profile">返回个人主页 <span aria-hidden="true">→</span></router-link></div>
</section></main></template>
