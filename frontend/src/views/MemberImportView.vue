<script setup lang="ts">
import { ref } from 'vue'
import { api, writeHeaders } from '../api'

interface Row {row:number; display_name:string; email:string; department:string; employee_number:string|null; status:string; errors:string[]}
interface Batch {id:string; valid:boolean; rows:Row[]; new_count:number; skip_count:number}
const content = ref(''), batch = ref<Batch|null>(null), busy = ref(false), committed = ref(false)
const error = ref(''), message = ref('')
const previewLinks = ref<{email:string; invitation_url?:string}[]>([])
let commitHeaders: HeadersInit = {}
async function selectFile(event:Event) {
  batch.value = null; committed.value = false; error.value = ''; message.value = ''; previewLinks.value = []
  const file = (event.target as HTMLInputElement).files?.[0]
  content.value = ''
  if (!file) return
  if (file.size > 2_000_000) { error.value = '文件不能超过 2 MB'; return }
  content.value = await file.text()
}
async function validate() {
  busy.value = true; error.value = ''; message.value = ''; batch.value = null; committed.value = false
  try {
    batch.value = await api<Batch>('/api/company/member-imports/validate', {method:'POST', headers:writeHeaders(), body:JSON.stringify({csv_text:content.value})})
    commitHeaders = writeHeaders()
  } catch(e:any) { error.value = e.message } finally { busy.value = false }
}
async function commit() {
  if (!batch.value) return
  busy.value = true; error.value = ''
  try {
    const result = await api<Batch & {invitations:{email:string; invitation_url?:string}[]}>(`/api/company/member-imports/${batch.value.id}/commit`, {method:'POST', headers:commitHeaders})
    batch.value = result; previewLinks.value = result.invitations; committed.value = true
    message.value = `已提交 ${result.new_count} 名待激活成员，跳过 ${result.skip_count} 条。成员验证后加入公司。`
  } catch(e:any) { error.value = `${e.message}。可重试提交；若资料已变化，请重新校验查看逐行结果。` } finally { busy.value = false }
}
</script>

<template>
  <main class="import-page">
    <router-link to="/app/company">返回公司设置与激活状态</router-link>
    <h1>批量开通公司成员</h1>
    <p>上传 UTF-8 CSV，最多 1,000 名成员、2 MB。新成员默认为普通成员，需本人验证激活；已注册账号登录后确认加入。</p>
    <a href="/api/company/member-imports/template.csv" download>下载成员模板</a>
    <section class="panel controls">
      <label>选择 CSV 文件<input type="file" accept=".csv,text/csv" :disabled="busy" @change="selectFile"></label>
      <button class="secondary" :disabled="busy || !content" @click="validate">校验文件</button>
    </section>
    <p v-if="error" class="alert alert-danger" role="alert">{{error}}</p><p v-if="message" role="status">{{message}}</p>
    <section v-if="batch" class="panel">
      <h2>校验结果</h2><p>可新增 {{batch.new_count}} 条，跳过 {{batch.skip_count}} 条。{{batch.valid ? '校验通过，可确认提交。' : '存在错误，整批不会提交；请修正文件后重新校验。'}}</p>
      <div class="table-scroll"><table><thead><tr><th>行号</th><th>姓名</th><th>邮箱</th><th>部门</th><th>工号</th><th>结果</th><th>原因</th></tr></thead>
        <tbody><tr v-for="row in batch.rows" :key="row.row"><td>{{row.row}}</td><td>{{row.display_name}}</td><td>{{row.email}}</td><td>{{row.department}}</td><td>{{row.employee_number || '—'}}</td><td>{{row.status === 'new' ? '新增' : row.status === 'skip' ? '跳过' : '错误'}}</td><td>{{row.errors.join('；')}}</td></tr></tbody>
      </table></div>
      <button class="primary" :disabled="busy || !batch.valid || committed" @click="commit">{{committed ? '已提交' : '确认提交并发送激活邮件'}}</button>
      <p>重复记录不会覆盖已有信息，也不会重复发送激活邮件；过期邀请可在公司设置中重发。</p>
      <template v-for="item in previewLinks" :key="item.email"><p v-if="item.invitation_url"><a :href="item.invitation_url">开发模式：{{item.email}} 的激活链接</a></p></template>
    </section>
  </main>
</template>

<style scoped>
.import-page{max-width:1200px;margin:32px auto;padding:0 24px}.panel{padding:24px;margin:20px 0}.controls{display:flex;align-items:end;gap:24px;flex-wrap:wrap}label{display:grid;gap:10px}.table-scroll{overflow-x:auto;margin-bottom:20px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid #e5e7eb}
</style>
