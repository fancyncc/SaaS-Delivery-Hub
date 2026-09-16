<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, writeHeaders } from '../api'
import { useAuthStore } from '../auth'

interface Profile {username:string; display_name:string; email:string|null; email_verified:boolean; phone:string|null; phone_verified:boolean; created_at:string}
const auth = useAuthStore(), profile = ref<Profile|null>(null)
const username = ref(''), name = ref(''), phone = ref(''), email = ref('')
const accountPassword = ref(''), phonePassword = ref(''), emailPassword = ref('')
const error = ref(''), message = ref(''), preview = ref(''), busy = ref(false)
async function load() {
  profile.value = await api<Profile>('/api/auth/profile')
  username.value = profile.value.username; name.value = profile.value.display_name
  phone.value = profile.value.phone || ''; email.value = profile.value.email || ''
}
onMounted(async () => {try {await load()} catch(e:any) {error.value=e.message}})
async function save(kind:'account'|'phone') {
  busy.value = true; error.value = ''; message.value = ''
  try {
    const body = kind === 'account' ? {username:username.value, display_name:name.value, current_password:accountPassword.value}
      : {phone:phone.value || null, current_password:phonePassword.value}
    await api('/api/auth/profile', {method:'PATCH', headers:writeHeaders(), body:JSON.stringify(body)})
    await load(); await auth.load(); accountPassword.value = ''; phonePassword.value = ''
    message.value = kind === 'account' ? '账号资料已更新。' : '手机号已保存，当前未进行短信验证。'
  } catch(e:any) {error.value=e.message} finally {busy.value=false}
}
async function bindEmail() {
  busy.value=true; error.value=''; message.value=''; preview.value=''
  try {
    const result = await api<{verification_url?:string; verified?:boolean}>('/api/auth/profile/email', {
      method:'POST', headers:writeHeaders(), body:JSON.stringify({email:email.value, current_password:emailPassword.value}),
    })
    emailPassword.value=''; preview.value=result.verification_url || ''
    message.value=result.verified ? '邮箱已验证。' : '验证邮件已发送，请打开邮件中的链接完成绑定。'
  } catch(e:any) {error.value=e.message} finally {busy.value=false}
}
</script>

<template>
  <main class="profile-page">
    <header class="profile-heading"><div><span class="eyebrow">PERSONAL CENTER</span><h1>个人主页</h1><p>管理你的账号和联系方式，随时回到自己的工作节奏。</p></div><router-link class="secondary" to="/app/spaces">管理我的空间 <span aria-hidden="true">↗</span></router-link></header>
    <p v-if="error" class="alert alert-danger" role="alert">{{error}}</p><p v-if="message" class="alert alert-success" role="status">{{message}}</p>
    <div v-if="profile" class="profile-layout">
      <aside class="profile-summary panel">
        <div class="profile-avatar" aria-hidden="true">{{profile.display_name.slice(0,1).toUpperCase()}}</div>
        <h2>{{profile.display_name}}</h2><p class="profile-handle">@{{profile.username}}</p>
        <span class="contact-status bound">账号已开通</span>
        <dl><div><dt>加入时间</dt><dd>{{new Date(profile.created_at).toLocaleDateString()}}</dd></div><div><dt>当前空间</dt><dd>{{auth.user?.tenant_name}}</dd></div></dl>
        <router-link class="text-link" to="/app">进入实施工作台 <span aria-hidden="true">→</span></router-link>
      </aside>
      <div class="profile-sections">
        <section class="panel profile-section">
          <div class="profile-section-heading"><div><span class="section-kicker">01 / ACCOUNT</span><h2>账号资料</h2></div><span class="contact-status">账号密码登录</span></div>
          <form @submit.prevent="save('account')" class="profile-form">
            <div class="form-grid cols-2"><label><span>账号</span><input v-model.trim="username" required minlength="3" maxlength="40" pattern="[A-Za-z][A-Za-z0-9_.-]*" autocomplete="username"></label><label><span>昵称</span><input v-model.trim="name" required maxlength="100" autocomplete="nickname"></label></div>
            <label><span>当前密码</span><input v-model="accountPassword" type="password" required autocomplete="current-password" placeholder="输入当前密码后保存账号资料"></label>
            <div class="profile-form-footer"><small>修改账号后，使用新账号名称登录；已绑定邮箱仍可使用。</small><button class="secondary" :disabled="busy">保存资料</button></div>
          </form>
        </section>
        <section class="panel profile-section">
          <div class="profile-section-heading"><div><span class="section-kicker">02 / EMAIL</span><h2>邮箱绑定</h2></div><span class="contact-status" :class="{bound:profile.email_verified}">{{profile.email_verified ? '已验证' : profile.email ? '待验证' : '未绑定'}}</span></div>
          <p class="profile-description">用于接收公司邀请、账号通知和找回密码。</p>
          <p v-if="profile.email_verified" class="contact-value">{{profile.email}} <span class="verified-mark" aria-label="已验证">✓</span></p>
          <form v-else @submit.prevent="bindEmail" class="profile-form">
            <label><span>邮箱</span><input v-model.trim="email" type="email" required :readonly="!!profile.email" maxlength="160" autocomplete="email" placeholder="name@example.com"></label>
            <label><span>邮箱绑定确认密码</span><input v-model="emailPassword" type="password" required autocomplete="current-password" placeholder="输入当前账号密码"></label>
            <div class="profile-form-footer"><small>验证链接 1 小时内有效，请在当前账号登录后打开。</small><button class="primary" :disabled="busy">发送绑定邮件</button></div>
          </form>
          <a v-if="preview" class="preview-link" :href="preview">开发模式：打开验证链接 <span aria-hidden="true">↗</span></a>
        </section>
        <section class="panel profile-section">
          <div class="profile-section-heading"><div><span class="section-kicker">03 / MOBILE</span><h2>手机号</h2></div><span class="contact-status">{{profile.phone ? '已填写 · 未验证' : '未填写'}}</span></div>
          <p class="profile-description">暂不发送短信验证码。手机号仅作为联系信息，不用于登录和找回密码。</p>
          <form @submit.prevent="save('phone')" class="profile-form">
            <label><span>手机号</span><input v-model.trim="phone" type="tel" maxlength="40" autocomplete="tel" placeholder="大陆手机号或带国际区号的号码"></label>
            <label><span>手机号保存确认密码</span><input v-model="phonePassword" type="password" required autocomplete="current-password" placeholder="输入当前账号密码"></label>
            <div class="profile-form-footer"><small>大陆手机号自动补全 +86，清空号码可移除。</small><button class="secondary" :disabled="busy">保存手机号</button></div>
          </form>
        </section>
      </div>
    </div>
    <p v-else-if="!error" class="empty-mini">正在加载个人资料…</p>
  </main>
</template>
