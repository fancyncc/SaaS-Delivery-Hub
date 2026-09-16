<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, writeHeaders } from '../api'
import type { AgentRun, AgentStep, Approval, Project } from '../types'
import DeliveryWorkspace from '../components/DeliveryWorkspace.vue'
import AgentProgress from '../components/AgentProgress.vue'

const route = useRoute()
const runId = String(route.params.id)
const run = ref<AgentRun | null>(null), project = ref<Project | null>(null)
const steps = ref<AgentStep[]>([]), approvals = ref<Approval[]>([])
const error = ref(''), message = ref(''), decisionComment = ref('')
const delivery = ref<any>({artifacts:[], imports:[], members:[], feedback:[]})
const csvText = ref(''), csvResult = ref<any>(null), feedbackText = ref('')
const failedCsv = ref<Record<string, string>>({})
const retryingImport = ref(false)
async function retryFailedImport(jobId: string) {
  retryingImport.value = true
  error.value = ''
  try {
    const result = await api<{run_id:string}>(`/api/imports/${jobId}/retry-failed`, {method:'POST', headers:writeHeaders(), body:JSON.stringify({csv_text:failedCsv.value[jobId]?.trim() || null})})
    window.location.assign(`/app/runs/${result.run_id}`)
  } catch(e:any) { error.value=e.message }
  finally { retryingImport.value=false }
}
const proposal = ref({departments:'', statuses:'', templates:'', custom_fields:'', due_date_notifications:true})
const materialForm = ref({requirements_text:'', migration_scope:'', acceptance_criteria:''})
let eventSource: EventSource | null = null
let loadSequence = 0
const pending = computed(() => approvals.value.filter(item => item.status === 'pending'))
const canApprove = computed(() => run.value?.allowed_actions?.includes('approve'))
const projectRoleNames: Record<string,string> = {
  project_manager: '项目负责人', implementation_consultant: '实施顾问', approver: '审批人',
  customer_contact: '客户联系人', viewer: '只读成员', company_admin: '公司管理员',
}
const approvalUnavailableMessage = computed(() => {
  const role = projectRoleNames[project.value?.my_project_role || ''] || project.value?.my_project_role || '当前角色'
  return `当前项目角色为“${role}”，不含审批决定权限。请由公司管理员为另一名成员设置“审批人”项目角色；启动本次 Run 的账号不能审批自己的请求。`
})
const progressedNodeCount = computed(() => run.value?.state.completed_nodes?.length || 0)
const gantt = computed(() => {
  const milestones:any[] = run.value?.state.plan?.milestones || []
  const end = new Map<string,number>()
  const resolve = (name:string, visiting=new Set<string>()):number => {
    if (end.has(name)) return end.get(name)!
    if (visiting.has(name)) return 0
    const m = milestones.find(m=>m.name===name)
    if (!m) return 0
    const next = new Set(visiting).add(name)
    const value = Math.max(0,...m.dependencies.map((d:string)=>resolve(d,next))) + m.days
    end.set(name,value); return value
  }
  const rows = milestones.map(m => ({...m, start:resolve(m.name)-m.days, end:resolve(m.name)}))
  return {rows, total:Math.max(1,...rows.map(m=>m.end))}
})
const waitingForApproval = computed(() => run.value?.status === 'waiting_approval')
const progressMessage = computed(() => {
  if (!run.value || !waitingForApproval.value) return ''
  const node = nodeNames[run.value.current_node] || run.value.current_node
  return `流程已推进到第 ${progressedNodeCount.value} / 17 个节点，当前暂停等待“${node}”。批准后 Agent 会自动继续，遇到下一个审批门时会再次暂停。`
})
const canCancel = computed(() => run.value?.allowed_actions?.includes('cancel'))
const statusNames: Record<string,string> = { blocked:'等待处理', preparing_materials:'等待成员材料', pending:'等待执行', running:'执行中', waiting_approval:'等待审批', failed:'执行终止', succeeded:'执行成功', cancelled:'已取消' }
const nodeNames: Record<string,string> = {
  create_project:'创建项目', collect_requirements:'提取客户需求', detect_missing_information:'历史资料检查（已移除）', retrieve_product_knowledge:'检索产品知识', gap_analysis:'生成能力差距', generate_implementation_plan:'生成实施计划', plan_approval:'实施计划审批', inspect_tenant_configuration:'检查租户配置', generate_configuration_changes:'生成配置差异', configuration_approval:'配置变更审批', apply_configuration:'应用租户配置', validate_import_files:'校验导入文件', import_approval:'数据导入审批', execute_import:'执行数据导入', generate_training_materials:'生成培训材料', run_go_live_checks:'运行上线检查', acceptance_approval:'上线验收审批', close_project:'关闭实施项目',
}
const approvalNames: Record<string,string> = { plan:'实施计划', configuration:'配置变更', import:'数据导入', acceptance:'上线验收', evidence:'证据确认' }
const approvalStatusNames: Record<string,string> = { pending:'待审批', approved:'已批准', rejected:'已驳回', expired:'已过期', cancelled:'已取消' }

async function load() {
  const sequence = ++loadSequence
  const runData = await api<AgentRun>(`/api/runs/${runId}`)
  const [projectData, stepData, approvalData, deliveryData] = await Promise.all([
    api<Project>(`/api/projects/${runData.project_id}`), api<AgentStep[]>(`/api/runs/${runId}/steps`), api<Approval[]>(`/api/approvals?run_id=${runId}`), api(`/api/projects/${runData.project_id}/delivery`),
  ])
  if (sequence !== loadSequence) return
  run.value = runData
  project.value = projectData; steps.value = stepData; approvals.value = approvalData
  delivery.value = deliveryData
}
async function uploadFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (file.size > 2_000_000) { error.value = 'CSV 不得超过 2 MB'; return }
  csvText.value = await file.text()
}
async function validateCsv() {
  try {
    csvResult.value = await api('/api/imports/validate', {method:'POST', headers:writeHeaders(), body:JSON.stringify({project_id:run.value?.project_id, run_id:runId, csv_text:csvText.value})})
    await load()
  } catch(e:any) { error.value=e.message }
}
async function submitFeedback() {
  try {
    await api(`/api/projects/${run.value?.project_id}/feedback`, {method:'POST', headers:writeHeaders(), body:JSON.stringify({run_id:runId, content:feedbackText.value})})
    feedbackText.value=''; await load()
  } catch(e:any) { error.value=e.message }
}
function editConfiguration() {
  const c = delivery.value.configuration || {}
  proposal.value = {departments:(c.departments||[]).join('、'), statuses:(c['workflow.statuses']||[]).join('、'), templates:(c.templates||[]).join('、'), custom_fields:(c.custom_fields||[]).join('、'), due_date_notifications:!!c['notifications.due_date']}
}
async function saveConfiguration() {
  const split = (s:string) => s.split(/[、,，]/).map(v=>v.trim()).filter(Boolean)
  try {
    await api(`/api/runs/${runId}/configuration`, {method:'PATCH', headers:writeHeaders(), body:JSON.stringify({expected_version:run.value?.version, departments:split(proposal.value.departments), statuses:split(proposal.value.statuses), templates:split(proposal.value.templates), custom_fields:split(proposal.value.custom_fields), due_date_notifications:proposal.value.due_date_notifications})})
    message.value='配置方案已修订，旧审批已作废，请独立审批人审核新材料。'; await load()
  } catch(e:any) { error.value=e.message }
}
function editMaterials() {
  materialForm.value = {requirements_text:project.value?.document?.requirements_text||'', migration_scope:project.value?.document?.migration_scope||'', acceptance_criteria:project.value?.document?.acceptance_criteria||''}
}
async function saveMaterials() {
  try {
    await api(`/api/projects/${run.value?.project_id}/materials`, {method:'PATCH', headers:writeHeaders(), body:JSON.stringify({...materialForm.value, expected_version:project.value?.version})})
    message.value='整改材料已保存，可返回项目列表创建新的 Run。'; await load()
  } catch(e:any) { error.value=e.message }
}
async function decide(item: Approval, decision: 'approved'|'rejected') {
  error.value = ''; message.value = ''
  if (decision === 'rejected' && !decisionComment.value.trim()) { error.value = '驳回时必须填写原因，便于项目团队修订。'; return }
  try {
    await api(`/api/approvals/${item.id}/decision`, { method:'POST', headers:writeHeaders(), body:JSON.stringify({ decision, comment:decisionComment.value.trim(), expected_version:item.version }) })
    decisionComment.value = ''; message.value = decision === 'approved' ? '审批已通过，Agent 已继续执行。' : '审批已驳回，项目已标记。'
    await load()
  } catch (e:any) { error.value = e.message }
}
async function cancelRun() {
  if (!run.value || !window.confirm(`确定取消 Run #${run.value.run_number} 吗？项目将进入阻塞状态，可随后创建重试 Run。`)) return
  error.value = ''; message.value = ''
  try {
    await api(`/api/runs/${run.value.id}/cancel`, { method:'POST', headers:writeHeaders() })
    message.value = '本次 Run 已取消，项目可以修订后重试。'
    await load()
  } catch (e:any) { error.value = e.message }
}
function connectEvents() {
  eventSource = new EventSource(`/api/runs/${runId}/events`)
  eventSource.addEventListener('run', () => load().catch(() => {}))
  // EventSource reconnects after transient transport errors and server timeouts.
}
onMounted(async () => { try { await load(); connectEvents() } catch(e:any) { error.value=e.message } })
onBeforeUnmount(() => eventSource?.close())
</script>

<template>
  <main class="run-page page-wrap">
    <div class="run-page-nav"><router-link class="back-link" to="/app">← 返回实施项目</router-link><router-link class="trace-link" :to="`/app/runs/${runId}/trace`">查看执行 Trace</router-link></div>
    <p v-if="error" class="alert alert-danger">{{error}}</p><p v-if="message" class="alert alert-success">{{message}}</p>
    <section v-if="run && project" class="run-hero">
      <div><span class="eyebrow">AGENT RUN #{{run.run_number}} · {{run.id.slice(0,8)}}</span><h1>{{project.name}}</h1><p>{{project.customer_name}} · {{project.document?.employee_count || '—'}} 人 · 目标上线 {{project.document?.target_go_live_date || '未填写'}}</p></div>
      <div class="run-status"><span :class="`run-status-${run.status}`">{{statusNames[run.status] || run.status}}</span><strong>{{nodeNames[run.current_node] || run.current_node}}</strong><small>Trace {{run.trace_id}}</small><button v-if="canCancel" class="secondary danger-text" @click="cancelRun">取消本次 Run</button></div>
    </section>

    <AgentProgress v-if="run" :run="run" @refresh="load" />
    <section v-if="run?.blocking_reason" class="panel"><h2>当前需要处理</h2><p>{{run.blocking_reason}}</p><router-link v-if="run.allowed_actions.includes('retry')" to="/app">返回项目列表创建整改 Run</router-link></section>
    <section v-if="run?.allowed_actions?.includes('upload_csv')" class="panel">
      <h2>成员 CSV</h2><p>字段：name、email、department、role。角色支持 admin、manager、member、viewer；请包含管理员并与项目人数一致。</p>
      <input type="file" accept=".csv" @change="uploadFile"><textarea v-model="csvText" aria-label="成员 CSV 内容" rows="6" placeholder="name,email,department,role"></textarea><button class="primary" :disabled="!csvText" @click="validateCsv">校验并提交审批</button>
      <pre v-if="csvResult">{{JSON.stringify(csvResult,null,2)}}</pre>
    </section>
    <section v-if="run" class="panel delivery-panel">
      <header class="delivery-title"><div><span class="eyebrow">DELIVERY WORKSPACE</span><h2>实施材料与交付</h2><p>从客户需求到上线验收，集中查看每一步的实施成果。</p></div><span class="delivery-scope">项目交付资料 · 当前 Run 检查</span></header>
      <details v-if="project?.lifecycle_status==='blocked' && project.permissions.includes('project.edit')" @toggle="editMaterials"><summary>修订整改材料</summary><form @submit.prevent="saveMaterials"><label>具体需求<textarea v-model="materialForm.requirements_text" minlength="20" required></textarea></label><label>迁移范围<textarea v-model="materialForm.migration_scope"></textarea></label><label>验收标准<textarea v-model="materialForm.acceptance_criteria"></textarea></label><button class="primary">保存整改材料</button></form></details>
      <details v-if="run.current_node==='configuration_approval' && run.status==='waiting_approval' && project?.permissions.includes('artifact.create')" @toggle="editConfiguration"><summary>修订配置方案</summary><form @submit.prevent="saveConfiguration" class="form-grid cols-2"><label>部门（顿号分隔）<input v-model="proposal.departments" required></label><label>状态流（顿号分隔）<input v-model="proposal.statuses" required></label><label>项目模板<input v-model="proposal.templates" required></label><label>自定义字段名称<input v-model="proposal.custom_fields"></label><label><input v-model="proposal.due_date_notifications" type="checkbox">启用到期提醒</label><button class="primary">提交新方案并重新审批</button></form></details>
      <DeliveryWorkspace :state="run.state" :delivery="delivery" :gantt="gantt" />
      <template v-if="project?.lifecycle_status==='blocked' && project?.permissions.includes('run.retry') && project?.permissions.includes('import.validate')"><section v-for="j in delivery.imports.filter((item:any)=>item.result.failed>0)" :key="j.id"><h3>失败行补偿导入</h3><p>仅处理此批次的 {{j.result.failed}} 条失败行；成功成员保留。创建新的 Run 并重新审批。可留空重试原失败内容，或粘贴仅含失败行的修正 CSV。</p><textarea v-model="failedCsv[j.id]" :aria-label="`失败行 CSV ${j.id}`" rows="4"></textarea><button :disabled="retryingImport" @click="retryFailedImport(j.id)">创建失败行整改 Run</button></section></template>
      <form v-if="project?.permissions.includes('acceptance.submit')" class="feedback-form" @submit.prevent="submitFeedback"><h3>客户验收反馈</h3><p>记录试用反馈与待改进事项，最终验收仍需独立审批。</p><textarea v-model="feedbackText" aria-label="客户验收反馈" placeholder="请描述使用情况、发现的问题或验收意见（至少 5 字）" required minlength="5" maxlength="4000"></textarea><button class="primary">提交反馈</button></form>
      <p v-for="f in delivery.feedback" :key="f.id" class="feedback-entry">{{f.content}}</p>
    </section>

    <div v-if="run" class="run-layout">
      <section class="panel execution-panel">
        <div class="section-heading"><div><span class="step-number">01</span><div><h2>Agent 执行轨迹</h2><p>节点完成后写入数据库，可在中断后恢复。</p></div></div><span class="live-indicator"><i></i>实时同步</span></div>
        <div class="timeline">
          <div v-for="(step,index) in steps" :key="step.id" class="timeline-item" :class="step.node === run.current_node && waitingForApproval ? 'current' : 'completed'"><span class="timeline-index">{{String(index+1).padStart(2,'0')}}</span><div><div class="timeline-title"><strong>{{nodeNames[step.node] || step.node}}</strong><span>{{step.node === run.current_node && waitingForApproval ? '等待审批' : '已完成'}}</span></div><small>{{new Date(step.created_at).toLocaleString()}}</small><div v-if="Object.keys(step.detail || {}).length" class="step-detail"><code>{{JSON.stringify(step.detail,null,2)}}</code></div></div></div>
          <div v-if="!['succeeded','failed','cancelled'].includes(run.status)" class="timeline-item current"><span class="timeline-index">→</span><div><div class="timeline-title"><strong>{{nodeNames[run.current_node] || run.current_node}}</strong><span>当前节点</span></div><small>{{run.status === 'waiting_approval' ? '等待人工审批后继续' : 'Agent 正在处理'}}</small></div></div>
        </div>
      </section>

      <aside class="run-sidebar">
        <section class="panel approval-panel">
          <div class="section-heading compact"><div><span class="step-number">02</span><div><h2>审批流程</h2><p>{{pending.length}} 项待处理</p></div></div></div>
          <div v-if="!approvals.length" class="empty-mini">尚未产生审批记录</div>
          <article v-for="item in approvals" :key="item.id" class="approval-card" :class="`approval-${item.status}`">
            <div class="approval-head"><strong>{{approvalNames[item.kind] || item.kind}}</strong><span>{{approvalStatusNames[item.status] || item.status}}</span></div>
            <details><summary>本次审批材料（不可变快照）</summary><pre>{{JSON.stringify(item.payload,null,2)}}</pre></details>
            <small>{{new Date(item.created_at).toLocaleString()}}</small><p v-if="item.comment" class="approval-comment">“{{item.comment}}”</p><p v-if="item.decided_by" class="decided-by">处理人：{{item.decided_by}}</p>
            <div v-if="item.status === 'pending' && canApprove" class="decision-box"><textarea v-model="decisionComment" placeholder="填写审批意见；驳回时必填"></textarea><div><button class="secondary danger-text" @click="decide(item,'rejected')">驳回并终止</button><button class="primary" @click="decide(item,'approved')">批准并继续</button></div></div>
            <p v-else-if="item.status === 'pending'" class="locked-action">{{approvalUnavailableMessage}}</p>
          </article>
        </section>
        <section class="panel summary-panel"><h2>本次执行摘要</h2><p v-if="progressMessage" class="locked-action">{{progressMessage}}</p><dl><div><dt>已推进节点</dt><dd>{{progressedNodeCount}} / 17</dd></div><div><dt>结构化需求</dt><dd>{{run.state.requirements?.length || 0}} 项</dd></div><div><dt>能力差距</dt><dd>{{run.state.gap_items?.length || 0}} 项</dd></div><div><dt>配置变更</dt><dd>{{run.state.configuration_changes?.length || 0}} 项</dd></div></dl><details><summary>查看结构化状态</summary><pre>{{JSON.stringify(run.state,null,2)}}</pre></details></section>
      </aside>
    </div>
  </main>
</template>
<style scoped>
.run-page-nav{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;gap:16px}.run-page-nav .back-link{margin:0}.trace-link{font-size:12px;font-weight:600;color:#3f725d;text-decoration:none;border:1px solid #ceded2;background:#fff;padding:8px 13px;border-radius:7px}.trace-link:hover{background:#edf5ee}.delivery-panel{padding:28px;margin:24px 0}.delivery-title{display:flex;align-items:center;justify-content:space-between;gap:20px}.delivery-title h2{font-size:23px;margin:6px 0;font-weight:600;letter-spacing:-.02em}.delivery-title p{font-size:12px;color:#819087;margin:0}.delivery-scope{font-size:11px;color:#7c9184;padding:6px 10px;border:1px solid #dfe8df;border-radius:6px;white-space:nowrap}.delivery-panel>details{margin-top:16px;border:1px solid #e2e9df;border-radius:8px;background:#fafcf8;padding:12px 16px;font-size:13px}.delivery-panel>details summary{cursor:pointer;color:#427258;font-weight:600}.delivery-panel>details form{margin-top:16px;display:grid;gap:14px}.delivery-panel>details label{font-size:12px;display:grid;gap:6px}.delivery-panel>details input[type=checkbox]{width:16px;height:16px}.feedback-form{margin-top:25px;padding-top:20px;border-top:1px solid #e4eae2}.feedback-form h3{font-size:15px;margin:0}.feedback-form p{font-size:12px;color:#859287}.feedback-form textarea{font-size:13px;margin-bottom:12px}.feedback-form button{font-size:12px}.feedback-entry{padding:14px 16px;background:#f6f9f4;border-radius:8px;font-size:13px;white-space:pre-wrap}.run-page>.panel:not(.delivery-panel){padding:24px;margin:20px 0}.run-page>.panel:not(.delivery-panel) h2{font-size:18px;margin-top:0}.run-page>.panel:not(.delivery-panel) p{font-size:13px;color:#718477}@media(max-width:760px){.delivery-panel{padding:18px}.delivery-title{align-items:flex-start;flex-direction:column;gap:12px}.delivery-title h2{font-size:21px}.delivery-scope{font-size:10px}.run-page-nav{gap:8px}.trace-link{white-space:nowrap}.run-page-nav .back-link{font-size:12px}}
</style>
