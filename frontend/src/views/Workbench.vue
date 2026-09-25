<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, writeHeaders } from '../api'
import { useAuthStore } from '../auth'
import type { WorkbenchTask } from '../types'
const auth = useAuthStore()
const tasks = ref<WorkbenchTask[]>([]), documents = ref<any[]>([]), error = ref('')
const projects = ref<any[]>([])
const file = ref<File | null>(null), uploading = ref(false), notice = ref(''), csvHeader = ref(true)
function chooseFile(event: Event) { file.value=(event.target as HTMLInputElement).files?.[0] || null }
const form = ref({project_id: null as string | null, title:'', version:1, module:'implementation', source:'', license:'', body:''})
async function load() {
  error.value=''
  try { [tasks.value, documents.value, projects.value] = await Promise.all([api<WorkbenchTask[]>('/api/tasks'), api<any[]>('/api/knowledge'), api<any[]>('/api/projects')]) }
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
    notice.value=response.indexing_enabled ? '已上传，正在等待解析。请在文档详情查看状态和告警。' : '已上传，索引处理尚未开启；文档处于等待状态。'
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
onMounted(load)
const indexLabels: Record<string,string> = {v3_pending:'等待解析', v3_parsing:'解析中', v3_indexing:'索引中', v3_ready:'可检索', v3_failed:'解析或索引失败', v3_unsupported:'格式不支持', v3_missing_original:'需重新上传原文件'}
const taskNames: Record<string,string> = {
  create_project:'创建项目', collect_requirements:'提取客户需求', detect_missing_information:'检查历史资料', retrieve_product_knowledge:'检索产品知识', gap_analysis:'生成能力差距', generate_implementation_plan:'生成实施计划', plan_approval:'审批实施计划', inspect_tenant_configuration:'检查租户配置', generate_configuration_changes:'生成配置差异', configuration_approval:'审批配置变更', apply_configuration:'应用租户配置', validate_import_files:'上传并校验成员 CSV', import_approval:'审批数据导入', execute_import:'执行数据导入', generate_training_materials:'生成培训材料', run_go_live_checks:'运行上线检查', acceptance_approval:'审批上线验收', close_project:'关闭实施项目',
}
const statusNames: Record<string,string> = {blocked:'等待处理', preparing_materials:'等待材料', waiting_approval:'等待审批', failed:'执行失败', cancelled:'已取消'}
async function reindex(id:string) {
  try { await api(`/api/knowledge/${id}/v3-index`, {method:'POST', headers:writeHeaders()}); notice.value='已加入索引队列。'; await load() }
  catch(e:any) { error.value=e.message }
}
</script>
<template><main class="page-wrap workspace-workbench">
  <header class="workspace-heading"><div><span class="eyebrow">WORKSPACE HUB</span><h1>待办与知识库</h1><p>处理当前任务，沉淀每一次实施经验。</p></div><router-link class="secondary" to="/app">返回实施项目 →</router-link></header>
  <div v-if="error" class="alert alert-danger" role="alert">{{error === 'Failed to fetch' ? '暂时无法连接服务，请检查连接后重试。' : error}} <button class="secondary" @click="load">重新加载</button></div>
  <section class="panel"><h1>我的待办</h1><p v-if="!tasks.length">暂无需要你处理的任务。</p><article v-for="t in tasks" :key="t.run_id"><router-link :to="`/app/runs/${t.run_id}`">{{t.project_name}} · {{taskNames[t.current_node] || t.current_node}}</router-link><small>Run #{{t.run_number}} · {{statusNames[t.status] || t.status}}</small><p>{{t.reason || (t.status === 'waiting_approval' ? '请完成审批后继续执行' : '需要整改或补充材料')}}</p></article></section>
  <section class="panel"><h2>公司知识库</h2><p>仅使用有授权的资料。相同标题发布新版本时版本号必须递增。</p>
    <form v-if="auth.isCompanyAdmin" @submit.prevent="upload" class="form-grid cols-2">
      <label class="knowledge-wide">适用范围<select v-model="form.project_id"><option :value="null">公司通用（公司成员可检索）</option><option v-for="p in projects" :key="p.id" :value="p.id">{{p.name}}</option></select></label>
      <label>标题<input v-model="form.title" required minlength="2" maxlength="160"></label><label>版本<input v-model.number="form.version" type="number" min="1" required></label>
      <label>模块<input v-model="form.module" required></label><label>来源<input v-model="form.source" required minlength="3" maxlength="500"></label>
      <label class="knowledge-wide">授权说明<input v-model="form.license" required minlength="3" maxlength="500" placeholder="说明资料的使用授权或适用范围"></label><label class="knowledge-wide">资料正文<textarea v-model="form.body" required minlength="20" maxlength="30000" rows="6" placeholder="填写实施方法、操作指引或常见问题…"></textarea></label><div class="knowledge-wide knowledge-submit"><small>同标题的新版本号须递增。</small><button class="primary">发布知识版本</button></div>
    </form>
    <form v-if="auth.isCompanyAdmin" class="panel form-grid" @submit.prevent="uploadFile"><h3>上传知识文件</h3><p>使用上方填写的标题、版本、模块、授权说明及范围。PDF 最多 10 MiB，PPTX/XLSX 最多 5 MiB，其余格式最多 2 MiB；OCR 与未解析内容的告警见文档详情。</p><input type="file" accept=".pdf,.pptx,.xlsx,.docx,.md,.txt,.csv,.json" required @change="chooseFile"><label><input v-model="csvHeader" type="checkbox">CSV 第一条记录是表头</label><button class="primary" :disabled="uploading || !file || form.title.length<2 || form.license.length<3">{{uploading ? '上传中…' : '上传文件并排队索引'}}</button><p role="status">{{notice}}</p></form>
    <button class="secondary" @click="load">刷新索引状态</button>
    <table><thead><tr><th>标题</th><th>版本</th><th>来源</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="d in documents" :key="d.id"><td><router-link :to="`/app/knowledge/${d.id}`">{{d.title}}</router-link><small> · {{d.project_id ? projects.find(p => p.id === d.project_id)?.name || '项目资料' : '公司通用'}}</small></td><td>{{d.version}}</td><td>{{d.source}}</td><td>{{d.active ? (indexLabels[d.index_status] || '等待索引') : '停用'}}<small v-if="d.index_error"> {{d.index_error}}</small></td><td><button v-if="auth.isCompanyAdmin && d.active" class="secondary" @click="deactivate(d.id)">停用</button><button v-if="auth.isCompanyAdmin && d.active && !['v3_unsupported','v3_missing_original'].includes(d.index_status)" class="secondary" @click="reindex(d.id)">重建索引</button></td></tr></tbody></table>
  </section>
</main></template>

