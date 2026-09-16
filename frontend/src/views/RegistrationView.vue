<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useAuthStore } from '../auth'
const router = useRouter(), auth = useAuthStore()
const username = ref(''), password = ref(''), confirm = ref(''), error = ref(''), busy = ref(false)
async function submit() {
  error.value = ''
  if (password.value !== confirm.value) { error.value = '两次密码不一致'; return }
  busy.value = true
  try {
    await api('/api/auth/register', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:username.value, password:password.value})})
    await auth.load(); await router.push('/app/profile')
  } catch(e:any) { error.value = e.message } finally { busy.value = false }
}
</script>
<template>
  <main class="auth-page"><section class="auth-card modern-auth">
    <div class="auth-emblem" aria-hidden="true">A</div><span class="eyebrow">YOUR WORKSPACE STARTS HERE</span>
    <h1>创建你的账号</h1><p>从个人空间开始，让 SaaS 实施更有条理。</p>
    <div class="auth-benefit"><span aria-hidden="true">✓</span> 自主注册，邮箱与手机号可稍后绑定</div>
    <form @submit.prevent="submit">
      <label><span>账号</span><input v-model.trim="username" aria-label="账号" required minlength="3" maxlength="40" pattern="[A-Za-z][A-Za-z0-9_.-]*" autocomplete="username" placeholder="给自己取一个账号名称"><small>3–40 位，以字母开头，可用数字、点、下划线和短横线。</small></label>
      <label><span>密码</span><input v-model="password" aria-label="密码" type="password" required minlength="10" maxlength="128" autocomplete="new-password" placeholder="至少 10 位，包含字母和数字"></label>
      <label><span>确认密码</span><input v-model="confirm" type="password" required autocomplete="new-password" placeholder="再次输入密码"></label>
      <p v-if="error" class="alert alert-danger" role="alert">{{error}}</p>
      <button class="primary auth-submit" :disabled="busy">{{busy ? '正在创建…' : '创建账号'}}</button>
    </form>
    <div class="auth-footer"><span>已有账号？</span><router-link class="text-link" to="/login">立即登录 <span aria-hidden="true">→</span></router-link></div>
  </section></main>
</template>
