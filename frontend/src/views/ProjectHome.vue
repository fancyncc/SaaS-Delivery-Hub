<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, writeHeaders } from '../api'
import { useAuthStore } from '../auth'
import type { Project, ProjectDocument } from '../types'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const projects = ref<Project[]>([])
const projectQuery = ref(''), statusFilter = ref('')
const filteredProjects = computed(() => projects.value.filter(p => (!statusFilter.value || p.lifecycle_status === statusFilter.value) && `${p.name} ${p.customer_name}`.toLowerCase().includes(projectQuery.value.toLowerCase())))
const loading = ref(false)
const showCreate = ref(route.query.from === 'chat')
const error = ref('')
const success = ref('')
const departmentsText = ref('')
const companies = ref<{ id: string; name: string }[]>([])
const companyId = ref('')
const assistingCompanyId = ref('')
const initialForm = (): ProjectDocument => ({
  name: '', customer_name: '', customer_contact: '', contact_email: '', employee_count: 1,
  target_go_live_date: '', departments: [], requirements_text: '',
  industry: '', contact_phone: '', consultant_name: '', migration_scope: '',
  acceptance_criteria: '', notes: '',
})
const form = reactive<ProjectDocument>(initialForm())

const assistingCompanies = computed(() => companies.value.filter(company =>
  company.id !== companyId.value && company.id !== auth.user?.tenant_id,
))
watch(companyId, selectedId => {
  if (assistingCompanyId.value === selectedId) assistingCompanyId.value = ''
})

const counts = computed(() => ({
  total: projects.value.length,
  active: projects.value.filter(p => p.lifecycle_status === 'in_progress').length,
  rejected: projects.value.filter(p => p.lifecycle_status === 'blocked').length,
  completed: projects.value.filter(p => p.lifecycle_status === 'completed').length,
}))
const statusMeta: Record<string, { label: string; tone: string }> = {
  draft: { label: '草稿', tone: 'neutral' }, ready: { label: '待启动', tone: 'neutral' },
  in_progress: { label: '实施中', tone: 'info' }, blocked: { label: '已阻塞', tone: 'danger' },
  completed: { label: '已完成', tone: 'success' }, cancelled: { label: '已取消', tone: 'neutral' },
  archived: { label: '已归档', tone: 'neutral' },
}
const executionMeta: Record<string, string> = { preparing_materials:'等待成员材料', pending:'等待执行', running:'执行中', waiting_approval:'等待审批', succeeded:'执行成功', failed:'执行失败', cancelled:'已取消' }
const meta = (status: string) => statusMeta[status] || { label: status, tone: 'neutral' }

async function refresh() { projects.value = await api<Project[]>('/api/projects') }
async function loadCompanies() {
  if (!auth.isCompanyAdmin) {
    companies.value = auth.user?.tenant_id && auth.user.tenant_name
      ? [{ id: auth.user.tenant_id, name: auth.user.tenant_name }] : []
    companyId.value = auth.user?.tenant_id || ''
    return
  }
  companies.value = await api<{ id: string; name: string }[]>('/api/company/company-directory')
  if (!companyId.value) companyId.value = companies.value.find(company => company.id === auth.user?.tenant_id)?.id || ''
}
async function createProject() {
  error.value = ''; success.value = ''
  const company = companies.value.find(item => item.id === companyId.value)
  if (!company) { error.value = '请选择公司'; return }
  form.departments = departmentsText.value.split(/[，,、]/).map(v => v.trim()).filter(Boolean)
  if (!form.departments.length) { error.value = '请至少填写一个部门'; return }
  loading.value = true
  try {
    const hasAssistingCompany = Boolean(assistingCompanyId.value)
    const created = await api<{ id: string }>('/api/projects', {
      method: 'POST', headers: writeHeaders(), body: JSON.stringify({
        ...form,
        customer_name: company.name,
        company_id: company.id,
        assisting_company_id: assistingCompanyId.value || null,
      }),
    })
    if (route.query.from === 'chat') { await router.push({ path: '/app/chat', query: { project: created.id } }); return }
    Object.assign(form, initialForm()); departmentsText.value = ''
    assistingCompanyId.value = ''
    success.value = hasAssistingCompany
      ? '实施项目已创建，协助公司确认后即可参与项目。'
      : '实施项目已创建，可以在下方项目列表中启动 Agent。'
    await refresh()
  } catch (e: any) { error.value = e.message } finally { loading.value = false }
}
async function start(project: Project) {
  error.value = ''
  if (!project.can_start) return
  try {
    const run = await api<{ id: string }>(`/api/projects/${project.id}/runs`, { method: 'POST', headers: writeHeaders() })
    await router.push(`/app/runs/${run.id}`)
  } catch (e: any) { error.value = e.message; await refresh() }
}
function openRun(project: Project) { if (project.latest_run) router.push(`/app/runs/${project.latest_run.id}`) }
async function removeProject(project: Project) {
  error.value = ''; success.value = ''
  if (!window.confirm(`确定将实施项目“${project.name}”移入回收站吗？\n\n项目默认保留 30 天，租户管理员可在管理后台恢复。`)) return
  try {
    await api(`/api/projects/${project.id}`, { method: 'DELETE', headers: writeHeaders() })
    success.value = `项目“${project.name}”已移入回收站。`
    await refresh()
  } catch (e:any) { error.value = e.message }
}
onMounted(async () => {
  try { await Promise.all([refresh(), loadCompanies()]) }
  catch (e: any) { error.value = e.message }
})
</script>

<template>
  <main class="home-page page-wrap">
    <section class="intro-row">
      <div><span class="eyebrow">PROJECTS</span><h1>项目管理</h1><p>查看项目进展、维护实施资料，集中管理项目文档。</p><button v-if="auth.isCompanyAdmin" class="primary" @click="showCreate=!showCreate">{{showCreate ? '收起新建表单' : '+ 新建项目'}}</button></div>
      <div class="stats"><div><strong>{{counts.total}}</strong><span>全部项目</span></div><div><strong>{{counts.active}}</strong><span>进行中</span></div><div><strong>{{counts.rejected}}</strong><span>已驳回</span></div><div><strong>{{counts.completed}}</strong><span>已完成</span></div></div>
    </section>
    <p v-if="error" class="alert alert-danger">{{error}}</p><p v-if="success" class="alert alert-success">{{success}}</p>

    <section v-if="auth.user?.company_role_code === 'company_admin' && showCreate" id="new-project" class="panel intake-panel">
      <div class="section-heading"><div><span class="step-number">01</span><div><h2>新建实施项目</h2><p>带 <b class="required">*</b> 的字段为 Agent 启动前必须确认的信息。</p></div></div><span class="skill-note">遵循项目文书 Skill 规范</span></div>
      <form @submit.prevent="createProject">
        <div class="form-section"><h3>项目与客户</h3><div class="form-grid cols-2">
          <label><span>项目名称 <b>*</b></span><input v-model.trim="form.name" required minlength="2" placeholder="如：星河设计客户上线实施"><small>建议包含客户名和实施目标</small></label>
          <label><span>公司 <b>*</b></span><select v-model="companyId" required><option value="" disabled>请选择公司</option><option v-for="company in companies" :key="company.id" :value="company.id">{{company.name}}</option></select></label>
          <label><span>协助公司 <em>选填</em></span><select v-model="assistingCompanyId"><option value="">无</option><option v-for="company in assistingCompanies" :key="company.id" :value="company.id">{{company.name}}</option></select><small>所选公司确认邀请后可参与项目</small></label>
          <label><span>所属行业 <em>选填</em></span><input v-model.trim="form.industry" placeholder="如：创意设计、制造业"></label>
          <label><span>客户联系人 <b>*</b></span><input v-model.trim="form.customer_contact" required minlength="2" placeholder="项目主要协调人"></label>
          <label><span>联系邮箱 <b>*</b></span><input v-model.trim="form.contact_email" required type="email" placeholder="name@company.com"></label>
          <label><span>联系电话 <em>选填</em></span><input v-model.trim="form.contact_phone" placeholder="手机号或座机"></label>
          <label><span>实施顾问 <em>选填</em></span><input v-model.trim="form.consultant_name" placeholder="内部项目负责人"></label>
          <label><span>员工规模 <b>*</b></span><input v-model.number="form.employee_count" required type="number" min="1" max="100000"><small>用于评估导入量和实施周期</small></label>
          <label><span>目标上线日期 <b>*</b></span><input v-model="form.target_go_live_date" required type="date"></label>
        </div></div>

        <div class="form-section"><h3>实施范围与具体需求</h3><div class="form-grid cols-2 text-areas">
          <label><span>部门清单 <b>*</b></span><input v-model="departmentsText" required placeholder="设计部、市场部、财务部"><small>使用逗号或顿号分隔多个部门</small></label>
          <label class="wide"><span>具体实施需求 <b>*</b></span><textarea v-model.trim="form.requirements_text" required minlength="20" placeholder="请写清楚谁在什么场景下，需要完成什么操作，以及期望结果。例如：市场部负责人可创建项目并审批预算；采购部成员只能查看自己负责的采购任务；通过 CSV 导入 60 名员工并按部门分配角色。"></textarea><small>{{form.requirements_text.length}} / 10000，至少 20 个字符；请避免只写“权限管理”“数据导入”等模糊名称</small></label>
        </div></div>

        <div class="form-section"><h3>需求与验收</h3><div class="form-grid cols-2 text-areas">
          <label><span>数据迁移范围 <em>选填</em></span><textarea v-model.trim="form.migration_scope" placeholder="数据来源、类型、数量、质量问题和明确不迁移的内容"></textarea></label>
          <label><span>验收标准 <em>选填</em></span><textarea v-model.trim="form.acceptance_criteria" placeholder="如：80 名成员全部导入、权限抽查通过、关键模板可正常使用"></textarea></label>
          <label class="wide"><span>约束、风险与备注 <em>选填</em></span><textarea v-model.trim="form.notes" placeholder="记录审批依赖、时间约束、特殊风险或待确认问题"></textarea></label>
        </div></div>
        <div class="form-actions"><p>提交后项目先进入“待启动”，Agent 不会自动执行。</p><button class="primary" type="submit" :disabled="loading">{{loading ? '创建中…' : '创建实施项目'}}</button></div>
      </form>
    </section>

    <section class="panel projects-panel">
      <div class="section-heading projects-heading"><div><span class="step-number">02</span><div><h2>实施项目</h2><p>状态、审批结果和可执行动作集中展示。</p></div></div><button class="text-button" @click="refresh">刷新项目</button></div>
      <div class="inline-form"><input v-model="projectQuery" placeholder="按项目或客户名称筛选"><select v-model="statusFilter"><option value="">全部状态</option><option v-for="(item,code) in statusMeta" :key="code" :value="code">{{item.label}}</option></select></div>
      <div v-if="!filteredProjects.length" class="empty-state"><strong>没有匹配的实施项目</strong><p>请调整筛选或创建项目。</p></div>
      <div v-else class="project-list">
        <article v-for="project in filteredProjects" :key="project.id" class="project-card" :class="`project-${meta(project.lifecycle_status).tone}`">
          <button v-if="project.permissions?.includes('project.delete')" class="delete-project-button" @click="removeProject(project)">删除项目</button>
          <div class="project-main"><div class="project-top"><span class="status-pill" :class="`pill-${meta(project.lifecycle_status).tone}`"><i></i>{{meta(project.lifecycle_status).label}}</span><span v-if="project.execution_status" class="project-id">Run #{{project.latest_run?.run_number}} · {{executionMeta[project.execution_status] || project.execution_status}}</span><span class="project-id">#{{project.id.slice(0,8)}}</span></div><h3>{{project.name}}</h3><p class="customer">{{project.customer_name}}</p><div v-if="project.document" class="project-meta"><span>{{project.document.employee_count}} 人</span><span>计划 {{project.document.target_go_live_date}} 上线</span></div>
            <div v-if="project.lifecycle_status === 'blocked'" class="rejection-box"><strong>项目已阻塞</strong><p>{{project.latest_approval?.comment || '请进入执行详情查看失败原因并修订后重试。'}}</p></div>
          </div>
          <div class="project-actions">
            <router-link class="primary" :to="`/app/projects/${project.id}`">查看详情</router-link>
            <button v-if="project.latest_run" class="secondary" @click="openRun(project)">{{project.lifecycle_status === 'completed' ? '查看实施结果' : project.lifecycle_status === 'blocked' ? '查看阻塞详情' : '查看执行进度'}}</button>
            <button v-if="project.can_start" class="primary" @click="start(project)">{{project.lifecycle_status === 'blocked' ? '创建重试 Run' : '启动 Agent'}}</button>
          </div>
        </article>
      </div>
    </section>
  </main>
</template>
