<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, writeHeaders } from '../api'
const route = useRoute()
defineProps<{ embedded?: boolean }>()
const project = ref<any>(null), documents = ref<any[]>([]), error = ref(''), busy = ref(false)
const includeShared = ref(false), query = ref('')
const submitting = ref(false), showSubmit = ref(false), message = ref(''), file = ref<File|null>(null)
const uploadMode = ref('file')
const submission = ref({title:'',version:1,module:'项目资料',license:'仅限项目成员内部协作使用',body:''})
const canSubmit = computed(() => project.value?.permissions?.includes('project.document.submit') && !['completed','archived','cancelled'].includes(project.value?.lifecycle_status))
function selectFile(event:Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] || null
  if (file.value && !submission.value.title) submission.value.title = file.value.name.replace(/\.[^.]+$/, '')
}
async function submit() {
  submitting.value = true; error.value = ''; message.value = ''
  try {
    if (uploadMode.value === 'file') {
      if (!file.value) throw new Error('请选择文档文件')
      const suffix = file.value.name.split('.').pop()?.toLowerCase()
      const limit = suffix === 'pdf' ? 10 : ['pptx', 'xlsx'].includes(suffix || '') ? 5 : 2
      if (file.value.size > limit * 1024 * 1024) throw new Error(`文件不能超过 ${limit} MiB`)
      const body = new FormData()
      body.append('file',file.value); body.append('project_id',String(route.params.id))
      for (const key of ['title','version','module','license'] as const) body.append(key,String(submission.value[key]))
      const headers = new Headers(writeHeaders()); headers.delete('Content-Type')
      await api('/api/knowledge/files',{method:'POST',headers,body})
      message.value = '文档已提交，正在等待后台解析和索引。'
    } else {
      await api('/api/knowledge',{method:'POST',headers:writeHeaders(),body:JSON.stringify({...submission.value,project_id:route.params.id,source:'项目成员提交'})})
      message.value = '文档已提交，项目成员可以查阅。'
    }
    showSubmit.value = false; file.value = null
    submission.value = {title:'',version:1,module:'项目资料',license:'仅限项目成员内部协作使用',body:''}
    await load()
  } catch(e:any) {error.value = e.message}
  finally {submitting.value = false}
}
async function retryIndex(id:string) {
  error.value = ''; message.value = ''
  try {
    await api(`/api/knowledge/${id}/v3-index`, {method:'POST',headers:writeHeaders()})
    message.value = '已重新加入解析和索引队列。'
    await load()
  } catch (e:any) { error.value = e.message }
}
const filtered = computed(() => documents.value.filter(d =>
  (d.project_id === route.params.id || includeShared.value && !d.project_id) && d.title.toLowerCase().includes(query.value.toLowerCase())))
const count = computed(() => documents.value.filter(d => d.project_id === route.params.id).length)
const labels: Record<string, string> = {v3_pending:'等待解析',v3_ready:'索引就绪',v3_failed:'解析或索引失败',v3_parsing:'解析中',v3_indexing:'索引中',v3_unsupported:'格式不支持',v3_missing_original:'需重新上传原文件'}
let revision = 0
async function load() {
  const current = ++revision
  busy.value = true; error.value = ''; documents.value = []; project.value = null
  try {
    const [p, docs] = await Promise.all([api(`/api/projects/${route.params.id}`), api<any[]>('/api/knowledge')])
    if (current === revision) { project.value = p; documents.value = docs }
  } catch (e: any) { if (current === revision) error.value = e.message }
  finally { if (current === revision) busy.value = false }
}
watch(() => route.params.id, () => {showSubmit.value=false; message.value=''; file.value=null; load()}, { immediate: true })
</script>
<template>
  <section class="project-library" :class="{'page-wrap': !embedded}">
    <header v-if="!embedded" class="library-heading"><div><span class="eyebrow">PROJECT LIBRARY</span><h1>项目文档</h1><p>{{ project?.name || '查看项目关联知识库' }}</p></div><router-link class="secondary" :to="`/app/projects/${route.params.id}?tab=documents`">项目详情 →</router-link></header>
    <p v-if="error" class="alert alert-danger" role="alert">{{ error }} <button @click="load">重试</button></p>
    <p v-if="message" class="alert alert-success" role="status">{{message}}</p>
    <section class="library-surface">
      <div class="library-summary"><div><h2>文档资料 <span>{{ count }}</span></h2><p>需求、设计与交付资料，集中查阅与追溯。</p></div><div><button v-if="canSubmit" class="primary" :disabled="busy || submitting" @click="showSubmit=!showSubmit">提交文档</button> <button class="secondary" :disabled="busy || submitting" @click="load">刷新列表</button></div></div>
      <form v-if="showSubmit" class="submission-form" @submit.prevent="submit"><fieldset :disabled="submitting">
        <label>提交方式<select v-model="uploadMode"><option value="file">上传文件</option><option value="text">填写文档正文</option></select></label>
        <label v-if="uploadMode==='file'">文档文件<input type="file" accept=".pdf,.pptx,.xlsx,.docx,.md,.txt,.csv,.json" required @change="selectFile"><small>支持 PDF（最大 10 MiB）、PPTX/XLSX（最大 5 MiB）及 DOCX/Markdown/TXT/CSV/JSON（最大 2 MiB）；文件需后台解析后查阅。</small></label>
        <label>文档标题<input v-model="submission.title" required minlength="2" maxlength="160"></label>
        <label>版本号<input v-model.number="submission.version" type="number" min="1" required><small>同一标题提交新版本时，请增加版本号。</small></label>
        <label>资料分类<input v-model="submission.module" required minlength="2" maxlength="60"></label>
        <label>使用授权说明<input v-model="submission.license" required minlength="3" maxlength="500"></label>
        <label v-if="uploadMode==='text'">文档正文<textarea v-model="submission.body" required minlength="20" maxlength="30000" rows="7"></textarea></label>
        <button type="button" class="secondary" @click="showSubmit=false">取消</button> <button class="primary">{{submitting ? '提交中…' : '确认提交'}}</button>
      </fieldset></form>
      <div class="library-tools"><input v-model="query" aria-label="搜索文档标题" placeholder="搜索文档名称…"><label class="shared-toggle"><input v-model="includeShared" type="checkbox">包含公司通用文档</label></div>
      <p v-if="busy">正在加载文档…</p>
      <template v-else><p class="library-count">显示 {{ filtered.length }} 份文档 · 含历史版本与停用资料</p>
        <p v-if="!filtered.length" class="library-empty">没有匹配的文档，请调整搜索或检索范围。</p>
        <div class="file-columns"><span>文档名称 / 来源</span><span>适用范围</span><span>状态</span><span></span></div>
        <article v-for="d in filtered" :key="d.id" class="document-row">
          <div class="file-identity"><span class="file-icon" aria-hidden="true">文</span><div><h3><router-link :to="`/app/knowledge/${d.id}`">{{ d.title }}</router-link><small>v{{d.version}}</small></h3><p :title="d.source">{{ d.source }}<template v-if="d.submitter_name"> · 提交人：{{d.submitter_name}}</template></p></div></div>
          <span class="file-scope">{{ d.project_id ? '项目私有' : '公司通用' }}</span>
          <span class="file-status" :class="{ready:d.active && d.index_status==='v3_ready',failed:d.active && d.index_status==='v3_failed'}"><i></i>{{ d.active ? labels[d.index_status] || '等待索引' : '已停用' }}</span>
          <span class="file-actions"><button v-if="project?.permissions?.includes('project.document.submit') && d.active && d.index_status==='v3_failed'" class="file-retry" @click="retryIndex(d.id)">重试索引</button><router-link class="file-open" :to="`/app/knowledge/${d.id}`">阅读全文 <span aria-hidden="true">↗</span></router-link></span>
        </article>
      </template>
    </section>
  </section>
</template>
<style scoped>
.file-actions{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap}.file-retry{border:0;background:none;color:#b55a42;padding:0;cursor:pointer;font-size:12px;white-space:nowrap}.file-retry:hover{text-decoration:underline}
.submission-form{margin:20px 0;padding:20px;background:#f7faf7;border:1px solid #dfe7e2;border-radius:12px}.submission-form fieldset{border:0;padding:0;min-width:0}.submission-form label{display:block;margin-bottom:14px}.submission-form small{display:block;color:#728378;margin-top:6px}
.project-library{max-width:1280px}.library-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:28px;gap:24px}.library-heading h1{font-size:30px;margin:10px 0}.library-heading p{color:#809087;font-size:14px}.library-surface{background:#fff;border:1px solid #dfe7e2;border-radius:16px;padding:28px 32px;box-shadow:0 8px 30px #173e3005}.library-summary{display:flex;align-items:center;justify-content:space-between;gap:16px}.library-summary h2{font-size:19px;margin:0}.library-summary h2 span{font-size:12px;padding:4px 9px;border-radius:7px;background:#edf4ef;color:#4e7563;margin-left:8px}.library-summary p{font-size:13px;color:#84948c;margin:10px 0}.library-tools{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:20px 0 6px}.library-tools>input{max-width:430px;font-size:13px;padding:12px 16px;background:#f8faf8}.shared-toggle{display:flex;align-items:center;gap:9px;white-space:nowrap;font-size:13px;color:#627b6e}.shared-toggle input{width:16px;height:16px;margin:0;accent-color:#28624e}.library-count{font-size:12px;color:#98a59e;margin:14px 0 20px}.file-columns,.document-row{display:grid;grid-template-columns:minmax(0,1fr) 100px 110px 100px;align-items:center;gap:20px}.file-columns{padding:12px 16px;background:#f7f9f7;border-radius:8px;color:#8b9a91;font-size:12px}.document-row{padding:20px 16px;border-bottom:1px solid #edf1ed;transition:background .15s}.document-row:hover{background:#fafcf9}.document-row:last-child{border-bottom:0}.file-identity{display:flex;align-items:center;gap:14px;min-width:0}.file-identity>div{min-width:0}.file-icon{display:grid;place-items:center;width:38px;height:44px;flex-shrink:0;border:1px solid #dce9df;border-radius:7px;background:#f0f6f1;color:#638773;font-size:15px}.file-identity h3{margin:0;display:flex;align-items:baseline;gap:8px;font-size:14px;line-height:1.6}.file-identity a{color:#284d3e;text-decoration:none}.file-identity a:hover{text-decoration:underline}.file-identity small{font-size:10px;color:#94a198;font-weight:400;white-space:nowrap}.file-identity p{font-size:11px;color:#97a39c;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin:5px 0 0}.file-scope{font-size:12px;color:#7a8b81}.file-status{font-size:11px;color:#87968d;display:flex;align-items:center;gap:6px}.file-status i{width:5px;height:5px;border-radius:50%;background:currentColor}.file-status.ready{color:#42856a}.file-status.failed{color:#b55a42}.file-open{font-size:12px;color:#477962;text-decoration:none;white-space:nowrap;text-align:right}.file-open span{margin-left:6px}.library-empty{text-align:center;padding:48px;color:#86968b;font-size:14px}@media(max-width:760px){.library-surface{padding:20px 16px}.library-heading,.library-tools{align-items:stretch;flex-direction:column}.library-tools>input{max-width:none}.file-columns{display:none}.document-row{grid-template-columns:1fr auto;gap:12px;padding:18px 0}.file-identity{grid-column:1/-1}.file-scope{display:none}.file-open{text-align:right}.library-heading h1{font-size:24px}}
</style>
