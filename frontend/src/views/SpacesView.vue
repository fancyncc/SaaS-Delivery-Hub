<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, writeHeaders } from '../api'
import { useAuthStore } from '../auth'

const auth = useAuthStore()
const name = ref(''), slug = ref(''), error = ref(''), message = ref(''), preview = ref(''), busy = ref(false)
const checkedName = ref(''), sameName = ref(false)
const hasCompany = computed(() => auth.user?.memberships.some(space => space.kind === 'company'))
async function verify() {
  error.value = ''; busy.value = true
  try {
    const data = await api<{verification_url?:string}>('/api/auth/email-verification', {method:'POST', headers:writeHeaders()})
    message.value = '验证邮件已发送。'; preview.value = data.verification_url || ''
  } catch(e:any) { error.value = e.message } finally { busy.value = false }
}
async function create() {
  error.value = ''; busy.value = true
  try {
    if (checkedName.value !== name.value) {
      const data = await api<{similar_name_exists:boolean}>(`/api/companies/name-check?name=${encodeURIComponent(name.value)}`)
      checkedName.value = name.value; sameName.value = data.similar_name_exists
      if (sameName.value) return
    }
    const company = await api<{id:string}>('/api/companies', {method:'POST', headers:writeHeaders(), body:JSON.stringify({name:name.value, slug:slug.value})})
    await auth.load()
    message.value = '公司已创建，你已成为管理员。'
    await auth.switchSpace(company.id)
  } catch(e:any) { error.value = e.message } finally { busy.value = false }
}
async function switchTo(id:string) {
  busy.value = true; error.value = ''
  try { await auth.switchSpace(id) } catch(e:any) { error.value = e.message; busy.value = false }
}
</script>

<template>
  <main class="spaces-page">
    <span class="eyebrow">YOUR WORKSPACES</span><h1>空间与账号</h1>
    <p>个人空间与公司数据独立保存。每个账号最多加入一家企业。</p>
    <p v-if="error" class="alert alert-danger" role="alert">{{error}}</p>
    <p v-if="message" role="status">{{message}}</p>
    <section class="panel">
      <h2>我的空间</h2>
      <ul><li v-for="space in auth.user?.memberships" :key="space.tenant_id">
        <div class="space-identity"><span class="space-icon" aria-hidden="true">{{space.kind === 'personal' ? '个' : '企'}}</span><div><strong>{{space.tenant_name}}</strong><small>{{space.kind === 'personal' ? '个人空间 · 专注自己的项目' : '公司空间 · 与团队共同推进'}}</small></div></div>
        <button class="secondary" :disabled="busy || space.tenant_id === auth.user?.tenant_id" @click="switchTo(space.tenant_id)">{{space.tenant_id === auth.user?.tenant_id ? '当前空间' : '切换到此空间'}}</button>
      </li></ul>
    </section>
    <section v-if="!auth.user?.email_verified" class="panel">
      <span class="eyebrow">ACCOUNT SECURITY</span><h2>完善联系方式</h2><p>绑定并验证邮箱，用于公司协作和账号通知。</p><div class="space-actions"><router-link class="secondary" to="/app/profile">前往个人主页 →</router-link>
      <button v-if="auth.user?.email" class="secondary" :disabled="busy" @click="verify">重发验证邮件</button>
      </div>
      <a v-if="preview" :href="preview">开发模式：打开验证链接</a>
    </section>
    <section v-if="!hasCompany" class="panel">
      <h2>创建公司</h2><p>创建后你将成为公司管理员，可以批量导入成员并分配项目权限。</p>
      <form @submit.prevent="create">
        <label>公司名称<input v-model.trim="name" required minlength="2" maxlength="120"></label>
        <label>空间标识<input v-model.trim="slug" required minlength="2" maxlength="80" pattern="[a-z0-9][a-z0-9-]*"><small>小写字母、数字或连字符；每个空间标识唯一。</small></label>
        <p v-if="sameName && checkedName === name" role="status">已有同名公司。如果你是加入现有公司，请联系该公司管理员；若为另一家公司，可继续创建。同名检查不代表企业实名认证。</p>
        <button class="primary" :disabled="busy || (!!auth.user?.company_creation_requires_verified_email && !auth.user?.email_verified)">{{sameName && checkedName === name ? '确认创建同名公司' : '创建公司'}}</button>
      </form>
    </section>
  </main>
</template>

<style scoped>
.spaces-page{max-width:1080px;margin:40px auto;padding:0 28px 48px}.spaces-page h1{font-size:30px;margin:8px 0 10px;letter-spacing:-.03em}.spaces-page>p,.panel>p{color:var(--muted);font-size:14px;line-height:1.8}.panel{padding:28px;margin:24px 0}.panel h2{font-size:19px;margin:8px 0 16px}ul{padding:0;list-style:none;margin:22px 0 0}li{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:20px;border:1px solid var(--line);border-radius:12px;background:#fafcf9;margin-top:12px}.space-identity{display:flex;align-items:center;gap:16px;min-width:0}.space-identity strong{font-size:15px;overflow-wrap:anywhere}.space-identity small{display:block;margin-top:7px;color:var(--muted);font-size:12px}.space-icon{display:grid;place-items:center;flex:0 0 46px;height:46px;border-radius:13px;background:#e6efe8;color:var(--green);font-weight:700}.space-actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:20px}form{display:grid;gap:18px;max-width:640px;margin-top:24px}label{display:grid;gap:8px;font-size:13px;font-weight:600}label small{font-weight:400;color:var(--muted)}@media(max-width:640px){.spaces-page{padding:0 16px 24px;margin-top:26px}.panel{padding:20px}li{align-items:flex-start;flex-direction:column;padding:16px}li button{align-self:flex-end}}
</style>
