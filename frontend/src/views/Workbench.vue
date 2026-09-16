<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, writeHeaders } from '../api'
import { useAuthStore } from '../auth'
const auth = useAuthStore()
const tasks = ref<any[]>([]), documents = ref<any[]>([]), error = ref('')
const projects = ref<any[]>([])
const file = ref<File | null>(null), uploading = ref(false), notice = ref(''), csvHeader = ref(true)
function chooseFile(event: Event) { file.value=(event.target as HTMLInputElement).files?.[0] || null }
const form = ref({project_id: null as string | null, title:'', version:1, module:'implementation', source:'', license:'', body:''})
async function load() {
  error.value=''
  try { [tasks.value, documents.value, projects.value] = await Promise.all([api<any[]>('/api/tasks'), api<any[]>('/api/knowledge'), api<any[]>('/api/projects')]) }
  catch(e:any) { error.value=e.message }
}
async function uploadFile() {
  if (!file.value || uploading.value) return
  uploading.value=true; error.value=''; notice.value=''
  try {
    const body=new FormData()
    body.append('file',file.value)
    for (const key of ['title','version','module','license'] as const) body.append(key,String(form.value[key]))
    if(form.value.project_id) body.append('project_id',form.value.project_id)
    body.append('csv_header',String(csvHeader.value))
    const headers=new Headers(writeHeaders()); headers.delete('Content-Type')
    const response=await api<any>('/api/knowledge/files',{method:'POST',headers,body})
    notice.value=response.indexing_enabled ? '已上传，正在等待解析。请在文档详情查看状态和告警。' : '已上传，V3 索引处理尚未开启；文档处于等待状态。'
    await load()
  } catch(e:any) {error.value=e.message} finally {uploading.value=false}
}
async function upload() {
  try { await api('/api/knowledge', {method:'POST', headers:writeHeaders(), body:JSON.stringify(form.value)}); await load() }
  catch(e:any) { error.value=e.message }
}
async function deactivate(id:string) {
  if (!window.confirm('停用后，该版本不再作为新执行的引用依据。是否继续？')) return
  try { await api(`/api/knowledge/${id}/deactivate`, {method:'POST', headers:writeHeaders()}); await load() }
  catch(e:any) { error.value=e.message }
}
async function prepareV3(id:string) {
  try { await api(`/api/knowledge/${id}/v3-index`,{method:'POST',headers:writeHeaders()}); notice.value='已排队准备 V3 旁路索引；原检索索引保持不变。'; await load() }
  catch(e:any) { error.value=e.message }
}
onMounted(load)
const indexLabels: Record<string,string> = {pending:'等待索引', ready:'可检索', failed:'索引失败', v3_pending:'V3 等待解析', v3_ready:'V3 索引就绪', v3_failed:'V3 解析或索引失败'}
async function reindex(id:string) {
  try { const d=documents.value.find(d=>d.id===id); await api(`/api/knowledge/${id}/${d?.index_status?.startsWith('v3_') ? 'v3-index' : 'reindex'}`, {method:'POST', headers:writeHeaders()}); await load() }
  catch(e:any) { error.value=e.message }
}
</script>
<template><main class="page-wrap workspace-workbench">
  <header class="workspace-heading"><div><span class="eyebrow">WORKSPACE HUB</span><h1>待办与知识库</h1><p>处理当前任务，沉淀每一次实施经验。</p></div><router-link class="secondary" to="/app">返回实施项目 →</router-link></header>
  <div v-if="error" class="alert alert-danger" role="alert">{{error === 'Failed to fetch' ? '暂时无法连接服务，请检查连接后重试。' : error}} <button class="secondary" @click="load">重新加载</button></div>
  <section class="panel"><h1>我的待办</h1><p v-if="!tasks.length">暂无需要你处理的任务。</p><article v-for="t in tasks" :key="t.run_id"><router-link :to="`/app/runs/${t.run_id}`">处理任务 {{t.run_id.slice(0,8)}}</router-link><p>{{t.reason || (t.status === 'waiting_approval' ? '等待审批' : '需要整改或补充材料')}}</p></article></section>
  <section class="panel"><h2>公司知识库</h2><p>仅使用有授权的资料。相同标题发布新版本时版本号必须递增。</p>
    <form v-if="auth.isCompanyAdmin" @submit.prevent="upload" class="form-grid cols-2">
      <label class="knowledge-wide">适用范围<select v-model="form.project_id"><option :value="null">公司通用（公司成员可检索）</option><option v-for="p in projects" :key="p.id" :value="p.id">{{p.name}}</option></select></label>
      <label>标题<input v-model="form.title" required minlength="2" maxlength="160"></label><label>版本<input v-model.number="form.version" type="number" min="1" required></label>
      <label>模块<input v-model="form.module" required></label><label>来源<input v-model="form.source" required minlength="3" maxlength="500"></label>
      <label class="knowledge-wide">授权说明<input v-model="form.license" required minlength="3" maxlength="500" placeholder="说明资料的使用授权或适用范围"></label><label class="knowledge-wide">资料正文<textarea v-model="form.body" required minlength="20" maxlength="30000" rows="6" placeholder="填写实施方法、操作指引或常见问题…"></textarea></label><div class="knowledge-wide knowledge-submit"><small>同标题的新版本号须递增。</small><button class="primary">发布知识版本</button></div>
    </form>
    <form v-if="auth.isCompanyAdmin" class="panel form-grid" @submit.prevent="uploadFile"><h3>上传结构化文件 · V3</h3><p>使用上方填写的标题、版本、模块、授权说明及范围。每份最多 2 MiB；只解析文字，图片与公式会显示告警。</p><input type="file" accept=".docx,.md,.txt,.csv,.json" required @change="chooseFile"><label><input v-model="csvHeader" type="checkbox">CSV 第一条记录是表头</label><button class="primary" :disabled="uploading || !file || form.title.length<2 || form.license.length<3">{{uploading ? '上传中…' : '上传文件并排队索引'}}</button><p role="status">{{notice}}</p></form>
    <button class="secondary" @click="load">刷新索引状态</button>
    <table><thead><tr><th>标题</th><th>版本</th><th>来源</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="d in documents" :key="d.id"><td><router-link :to="`/app/knowledge/${d.id}`">{{d.title}}</router-link><small> · {{d.project_id ? projects.find(p => p.id === d.project_id)?.name || '项目资料' : '公司通用'}}</small></td><td>{{d.version}}</td><td>{{d.source}}</td><td>{{d.active ? (indexLabels[d.index_status] || '等待索引') : '停用'}}<small v-if="d.index_error"> {{d.index_error}}</small></td><td><button v-if="auth.isCompanyAdmin && d.active" class="secondary" @click="deactivate(d.id)">停用</button><button v-if="auth.isCompanyAdmin && d.active" class="secondary" @click="reindex(d.id)">重建索引</button><button v-if="auth.isCompanyAdmin && d.active && !d.index_status?.startsWith('v3_')" class="secondary" @click="prepareV3(d.id)">准备 V3 索引</button></td></tr></tbody></table>
  </section>
</main></template>

