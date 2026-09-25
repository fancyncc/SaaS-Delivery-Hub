<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, writeHeaders } from '../api'
const props = defineProps<{ project: any }>()
type Document = {id:string;title:string;version:number;source:string;index_status:string}
type Review = {id:string;round:number;status:string;version:number;submission_comment:string;decision_comment:string;submitted_by:string;submitter_name:string;decider_name:string}
type Task = {id:string;title:string;description:string;status:string;blocking_reason:string;assignee_id:string|null;due_date:string|null;version:number;created_by:string;documents:Document[];latest_review:Review|null}
const tasks = ref<Task[]>([]), members = ref<{id:string;display_name:string}[]>([])
const error = ref(''), notice = ref(''), loading = ref(false), saving = ref(false), showForm = ref(false), filter = ref('')
const editing = ref<Task|null>(null), currentUserId = ref(''), canApprove = ref(false)
const comments = ref<Record<string,string>>({}), documentTask = ref<Task|null>(null), documentFile = ref<File|null>(null)
const documentForm = ref({title:'',version:1})
const blank = () => ({title:'',description:'',assignee_id:'',due_date:''})
const form = ref(blank())
const labels:Record<string,string> = {todo:'待开始',in_progress:'进行中',blocked:'已阻塞',pending_review:'待审批',changes_requested:'需修改',done:'已通过'}
const canWrite = computed(() => props.project.permissions.includes('project.task.write') && !['completed','archived','cancelled'].includes(props.project.lifecycle_status))
const filtered = computed(() => tasks.value.filter(t => !filter.value || t.status === filter.value))
const completed = computed(() => tasks.value.filter(t => t.status === 'done').length)
const managerRoles = ['company_admin','project_manager','implementation_consultant']
const canAdvance = (task:Task) => canWrite.value && (!task.assignee_id || task.assignee_id === currentUserId.value || task.created_by === currentUserId.value || managerRoles.includes(props.project.my_project_role))
let revision = 0
async function load() {
  const current = ++revision; loading.value = true; error.value = ''
  try {
    const data = await api<{tasks:Task[];members:{id:string;display_name:string}[];current_user_id:string;can_approve:boolean}>(`/api/projects/${props.project.id}/tasks`)
    if (current === revision) {tasks.value=data.tasks; members.value=data.members; currentUserId.value=data.current_user_id; canApprove.value=data.can_approve}
  } catch(e:any) {if (current === revision) error.value=e.message}
  finally {if (current === revision) loading.value=false}
}
function edit(task:Task|null) {
  editing.value=task
  form.value=task ? {title:task.title,description:task.description,assignee_id:task.assignee_id||'',due_date:task.due_date||''} : blank()
  showForm.value=true
}
async function save() {
  saving.value=true; error.value=''; notice.value=''
  try {
    const payload={...form.value,assignee_id:form.value.assignee_id||null,due_date:form.value.due_date||null,...(editing.value?{expected_version:editing.value.version}:{})}
    await api(`/api/projects/${props.project.id}/tasks${editing.value?`/${editing.value.id}`:''}`,{method:editing.value?'PUT':'POST',headers:writeHeaders(),body:JSON.stringify(payload)})
    showForm.value=false; notice.value=editing.value?'任务资料已更新。':'任务已创建，负责人需要点击“开始任务”后推进。'; await load()
  } catch(e:any) {error.value=e.message} finally {saving.value=false}
}
async function action(task:Task, name:string) {
  saving.value=true; error.value=''; notice.value=''
  try {
    await api(`/api/projects/${props.project.id}/tasks/${task.id}/actions/${name}`,{method:'POST',headers:writeHeaders(),body:JSON.stringify({expected_version:task.version,comment:comments.value[task.id]||''})})
    comments.value[task.id]=''; notice.value=name==='submit_review'?'已提交审批，等待审批人处理。':'任务状态已按流程推进。'; await load()
  } catch(e:any) {error.value=e.message} finally {saving.value=false}
}
async function decide(task:Task, decision:'approved'|'rejected') {
  saving.value=true; error.value=''; notice.value=''
  try {
    await api(`/api/projects/${props.project.id}/tasks/${task.id}/reviews/${task.latest_review?.id}/decision`,{method:'POST',headers:writeHeaders(),body:JSON.stringify({decision,expected_version:task.version,comment:comments.value[task.id]||''})})
    comments.value[task.id]=''; notice.value=decision==='approved'?'审批通过，任务已自动完成。':'已驳回，任务进入需修改状态。'; await load()
  } catch(e:any) {error.value=e.message} finally {saving.value=false}
}
function chooseDocument(task:Task) {documentTask.value=task;documentFile.value=null;documentForm.value={title:'',version:1}}
function fileChanged(event:Event) {
  documentFile.value=(event.target as HTMLInputElement).files?.[0]||null
  if(documentFile.value) documentForm.value.title=documentFile.value.name.replace(/\.[^.]+$/,'')
}
async function uploadDocument() {
  if(!documentTask.value||!documentFile.value)return
  saving.value=true;error.value='';notice.value=''
  try {
    const body=new FormData();body.append('file',documentFile.value);body.append('project_id',props.project.id);body.append('task_id',documentTask.value.id)
    body.append('title',documentForm.value.title);body.append('version',String(documentForm.value.version));body.append('module','任务交付');body.append('license','仅限项目成员内部协作使用')
    const headers=new Headers(writeHeaders());headers.delete('Content-Type')
    await api('/api/knowledge/files',{method:'POST',headers,body});documentTask.value=null;notice.value='任务文档已提交；解析可稍后完成，不影响发起审批。';await load()
  } catch(e:any){error.value=e.message}finally{saving.value=false}
}
watch(()=>props.project.id,()=>{tasks.value=[];members.value=[];showForm.value=false;documentTask.value=null;load()},{immediate:true})
</script>
<template>
  <section class="panel task-workspace">
    <header><div><h2>协作任务</h2><p>任务需提交文档并通过审批后完成。已通过 {{completed}} / {{tasks.length}}</p></div><button v-if="canWrite" class="primary" :disabled="loading||saving" @click="edit(null)">新建任务</button></header>
    <div class="workflow"><span>1 创建任务</span><i>→</i><span>2 开始执行</span><i>→</i><span>3 提交文档</span><i>→</i><span>4 发起审批</span><i>→</i><span>5 审批通过</span></div>
    <progress :value="completed" :max="tasks.length||1" aria-label="任务完成进度"></progress>
    <p v-if="error" class="alert alert-danger" role="alert">{{error}}</p><p v-if="notice" class="alert alert-success" role="status">{{notice}}</p>
    <form v-if="showForm" class="task-form" @submit.prevent="save"><h3>{{editing?'编辑任务资料':'新建任务'}}</h3><fieldset :disabled="saving">
      <label>任务名称<input v-model="form.title" required maxlength="160"></label><label>任务说明<textarea v-model="form.description" rows="3" maxlength="10000" placeholder="说明目标、分工与审批标准"></textarea></label>
      <div class="task-options"><label>负责人<select v-model="form.assignee_id"><option value="">暂不分配</option><option v-if="form.assignee_id&&!members.some(m=>m.id===form.assignee_id)" :value="form.assignee_id">原负责人（已不可分配）</option><option v-for="m in members" :key="m.id" :value="m.id">{{m.display_name}}</option></select></label><label>截止日期<input v-model="form.due_date" type="date"></label><div class="status-explain"><b>状态由流程自动推进</b><small>不能手动改为完成</small></div></div>
      <div class="actions"><button type="button" class="secondary" @click="showForm=false">取消</button><button class="primary">{{saving?'保存中…':'保存任务'}}</button></div></fieldset></form>
    <form v-if="documentTask" class="task-form" @submit.prevent="uploadDocument"><h3>向“{{documentTask.title}}”提交文档</h3><fieldset :disabled="saving"><label>选择文档<input type="file" accept=".pdf,.pptx,.xlsx,.docx,.md,.txt,.csv,.json" required @change="fileChanged"><small>支持 PDF（10 MiB）、PPTX/XLSX（5 MiB）及现有文档格式（2 MiB）</small></label><div class="task-options"><label>文档标题<input v-model="documentForm.title" minlength="2" maxlength="160" required></label><label>版本号<input v-model.number="documentForm.version" type="number" min="1" required></label></div><div class="actions"><button type="button" class="secondary" @click="documentTask=null">取消</button><button class="primary" :disabled="!documentFile">提交任务文档</button></div></fieldset></form>
    <div class="task-toolbar"><label>筛选状态<select v-model="filter"><option value="">全部任务</option><option v-for="(label,key) in labels" :key="key" :value="key">{{label}}</option></select></label><button class="secondary" :disabled="loading||saving" @click="load">刷新</button></div>
    <p v-if="loading" role="status">正在加载任务…</p><p v-else-if="!filtered.length" class="muted">{{tasks.length?'该状态下暂无任务。':'暂无协作任务，创建第一个任务开始分工。'}}</p>
    <article v-for="task in filtered" :key="task.id" class="task-row"><div class="task-content"><h3>{{task.title}} <span :class="`status-${task.status}`">{{labels[task.status]}}</span></h3><p class="description">{{task.description||'未填写任务说明'}}</p><small>负责人：{{members.find(m=>m.id===task.assignee_id)?.display_name||(task.assignee_id?'原成员（已不可分配）':'未分配')}} · 截止：{{task.due_date||'未设置'}}</small>
      <p v-if="task.blocking_reason" class="review-note"><b>阻塞原因：</b>{{task.blocking_reason}}</p><p v-if="task.latest_review?.decision_comment" class="review-note"><b>{{task.latest_review.status==='rejected'?'驳回意见':'审批意见'}}：</b>{{task.latest_review.decision_comment}}</p>
      <div class="task-documents"><b>任务文档（{{task.documents.length}}）</b><router-link v-for="doc in task.documents" :key="doc.id" :to="`/app/knowledge/${doc.id}`">{{doc.title}} v{{doc.version}}</router-link><span v-if="!task.documents.length">尚未提交</span></div>
      <div v-if="canAdvance(task)&&['todo','in_progress','changes_requested','blocked'].includes(task.status)" class="task-actions"><button v-if="task.status==='todo'||task.status==='changes_requested'" class="primary" :disabled="saving" @click="action(task,'start')">{{task.status==='changes_requested'?'继续修改':'开始任务'}}</button><button v-if="['in_progress','changes_requested'].includes(task.status)" class="secondary" :disabled="saving" @click="chooseDocument(task)">提交文档</button><button v-if="['in_progress','changes_requested'].includes(task.status)" class="primary" :disabled="saving||!task.documents.length" @click="action(task,'submit_review')">提交审批</button><button v-if="['todo','in_progress','changes_requested'].includes(task.status)" class="secondary" :disabled="saving" @click="action(task,'block')">报告阻塞</button><button v-if="task.status==='blocked'" class="primary" :disabled="saving" @click="action(task,'resume')">恢复任务</button><button class="secondary" :disabled="saving" @click="edit(task)">编辑资料</button></div>
      <div v-if="task.status==='pending_review'" class="approval-box"><p>第 {{task.latest_review?.round}} 轮审批 · 由 {{task.latest_review?.submitter_name||'项目成员'}} 提交</p><textarea v-model="comments[task.id]" rows="2" :placeholder="canApprove?'填写审批意见；驳回时必填':'等待有审批权限且非提交人的成员处理'"></textarea><div v-if="canApprove&&task.latest_review?.submitted_by!==currentUserId" class="actions"><button class="secondary danger-text" :disabled="saving" @click="decide(task,'rejected')">驳回修改</button><button class="primary" :disabled="saving" @click="decide(task,'approved')">审批通过</button></div></div>
      <textarea v-if="canAdvance(task)&&['todo','in_progress','changes_requested'].includes(task.status)" v-model="comments[task.id]" class="action-comment" rows="2" placeholder="提交审批说明，或填写阻塞原因"></textarea>
    </div></article><p v-if="!canWrite" class="muted">当前项目或账号为只读状态。</p>
  </section>
</template>
<style scoped>
.task-workspace{padding:28px}.task-workspace header,.task-toolbar,.actions{display:flex;align-items:center;justify-content:space-between;gap:16px}.task-workspace h2{margin:0}.task-workspace header p,.task-row small{color:#728378}.workflow{display:flex;align-items:center;justify-content:center;gap:10px;padding:14px;margin:18px 0 8px;border-radius:10px;background:#f1f7f3;color:#376b57;font-size:12px}.workflow i{color:#9aaba2}.task-workspace progress{width:100%;height:8px;accent-color:#28624e;margin:12px 0 20px}.task-form{background:#f6f9f6;border:1px solid #dfe7e2;padding:20px;border-radius:12px;margin:12px 0}.task-form fieldset{border:0;padding:0;min-width:0}.task-form label{display:block;margin-bottom:14px}.task-options{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.status-explain{padding:12px;background:#e9f2ec;border-radius:8px}.status-explain b,.status-explain small{display:block}.status-explain small{margin-top:6px;color:#728378}.actions{justify-content:flex-end}.task-toolbar{margin:16px 0}.task-toolbar label{display:flex;align-items:center;gap:12px}.task-toolbar select{width:auto}.task-row{border-top:1px solid #e5ece7;padding:22px 0}.task-content{min-width:0}.task-row h3{font-size:16px;overflow-wrap:anywhere}.task-row h3 span{font-size:12px;font-weight:400;background:#edf4ef;color:#28624e;padding:4px 8px;border-radius:5px}.task-row h3 .status-pending_review{background:#fff0d5;color:#8c6418}.task-row h3 .status-changes_requested,.task-row h3 .status-blocked{background:#fde9e5;color:#9c463d}.task-row h3 .status-done{background:#e1f2e8;color:#347254}.description{white-space:pre-wrap;overflow-wrap:anywhere;color:#596f61}.task-documents{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin:14px 0;padding:12px;background:#f7f9f7;border-radius:8px;font-size:12px}.task-documents a{color:#2f7259}.task-documents span{color:#8a9891}.task-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.task-actions button{padding:8px 12px}.action-comment,.approval-box textarea{margin-top:10px;min-height:58px}.review-note{padding:9px 12px;background:#fff4e3;color:#795d2d;border-radius:7px;font-size:12px}.approval-box{margin-top:14px;padding:14px;border:1px solid #e1d2ae;background:#fffaf0;border-radius:10px}.approval-box p{margin:0;color:#705b2e;font-size:13px}.approval-box .actions{margin-top:10px}@media(max-width:700px){.task-options{grid-template-columns:1fr}.task-workspace header{align-items:flex-start;flex-direction:column}.task-workspace{padding:18px}.workflow{align-items:flex-start;flex-direction:column}.workflow i{display:none}}
</style>
