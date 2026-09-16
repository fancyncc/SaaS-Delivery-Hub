<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, writeHeaders } from '../api'
import { useAuthStore } from '../auth'

const auth = useAuthStore()
const tab = ref('overview')
const error = ref('')
const message = ref('')
const latestInvitation = ref<{ id: string; email: string; url: string } | null>(null)
const dashboard = ref<any>({})
const tenants = ref<any[]>([])
const companyDirectory = ref<any[]>([])
const members = ref<any>({ members: [], invitations: [] })
const projects = ref<any[]>([])
const audit = ref<any[]>([])
const system = ref<any>({})
const selectedTenantId = ref('')
const selectedProject = ref<any>(null)
const projectMembers = ref<any[]>([])
const collaborations = ref<any[]>([])
const pendingCollaborations = ref<any[]>([])
const accessForm = ref({ user_id: '', primary_role_code: 'viewer' })
const collaborationTenantId = ref('')
const capabilityMember = ref<any>(null)
const capabilityGrants = ref<any[]>([])
const capabilityForm = ref({ permission_code: 'project.edit', reason: '', expires_at: '' })
const supportGrants = ref<any[]>([])
const supportTargets = ref<any[]>([])
const supportForm = ref({ target_tenant_id: '', target_project_id: '', reason: '', permission_codes: ['project.view'], expires_at: '' })
const tenantForm = ref({ name: '', slug: '', admin_email: '', admin_name: '租户管理员' })
const invite = ref({ email: '', display_name: '', company_role: 'tenant_member' })

const collaborationCandidates = computed(() => {
  if (!selectedProject.value) return []
  const unavailable = new Set(
    collaborations.value
      .filter(item => ['pending', 'active'].includes(item.status))
      .map(item => item.tenant_id),
  )
  return companyDirectory.value.filter(item =>
    item.id !== selectedProject.value.tenant_id && !unavailable.has(item.id),
  )
})
const collaborationEmptyMessage = computed(() => {
  if (!selectedProject.value) return ''
  const otherCompanies = companyDirectory.value.filter(
    item => item.id !== selectedProject.value.tenant_id,
  )
  return otherCompanies.length
    ? '其他公司均已在协作中或等待确认，无需重复邀请。'
    : '当前系统还没有其他有效公司。请先创建合作公司，再发起双方确认。'
})

const companyRoleNames: Record<string, string> = {
  tenant_admin: '公司管理员', tenant_member: '公司成员',
  company_admin: '公司管理员', company_member: '公司成员',
}
const projectRoleNames: Record<string, string> = {
  project_manager: '项目负责人', implementation_consultant: '实施顾问', approver: '审批人',
  customer_contact: '客户联系人', viewer: '只读成员',
}
const invitationStatusNames: Record<string, string> = {
  pending: '待接受', accepted: '已接受', revoked: '已撤销', expired: '已过期',
}

function formatDate(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN') : '—'
}

async function load() {
  error.value = ''
  try {
    dashboard.value = await api('/api/company/dashboard')
    tenants.value = await api('/api/company/tenants')
    companyDirectory.value = await api('/api/company/company-directory')
    if (!selectedTenantId.value) selectedTenantId.value = auth.user?.tenant_id || ''
    const scope = selectedTenantId.value ? `?tenant_id=${selectedTenantId.value}` : ''
    members.value = await api(`/api/company/members${scope}`)
    const [active, trash] = await Promise.all([
      api<any[]>(`/api/company/projects${scope}`), api<any[]>(`/api/company/projects/trash${scope}`),
    ])
    projects.value = [...active, ...trash]
    audit.value = await api(`/api/company/audit-events${scope}`)
    pendingCollaborations.value = await api('/api/project-collaborations/pending')
    supportGrants.value = await api('/api/company/support-access-grants')
  } catch (e: any) { error.value = e.message === 'Failed to fetch' ? '暂时无法连接服务，请确认服务已启动后重新加载。' : e.message }
}

async function createTenant() {
  error.value = ''; message.value = ''; latestInvitation.value = null
  tenantForm.value.name = tenantForm.value.name.trim()
  tenantForm.value.slug = tenantForm.value.slug.trim().toLowerCase()
  tenantForm.value.admin_email = tenantForm.value.admin_email.trim().toLowerCase()
  if (tenantForm.value.name.length < 2) { error.value = '企业名称至少需要 2 个字符。'; return }
  if (!/^[a-z0-9][a-z0-9-]+$/.test(tenantForm.value.slug)) {
    error.value = '企业标识只能使用小写字母、数字和连字符（-），例如 company-a；不能使用下划线。'
    return
  }
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(tenantForm.value.admin_email)) {
    error.value = '请填写有效的首位管理员邮箱。'
    return
  }
  try {
    const invitationEmail = tenantForm.value.admin_email
    const data = await api<any>('/api/platform/tenants', {
      method: 'POST', headers: writeHeaders(), body: JSON.stringify(tenantForm.value),
    })
    latestInvitation.value = data.invitation_url
      ? { id: data.invitation_id, email: invitationEmail, url: data.invitation_url } : null
    message.value = data.invitation_url ? '合作公司已创建，请复制下方链接给首位公司管理员。' : '合作公司已创建，邀请邮件已发送。'
    tenantForm.value = { name: '', slug: '', admin_email: '', admin_name: '租户管理员' }
    await load()
  } catch (e: any) { error.value = e.message }
}

async function inviteMember() {
  error.value = ''; latestInvitation.value = null
  try {
    const email = invite.value.email
    const data = await api<any>('/api/company/invitations', {
      method: 'POST', headers: writeHeaders(), body: JSON.stringify({ ...invite.value, tenant_id: selectedTenantId.value }),
    })
    latestInvitation.value = data.invitation_url ? { id: data.id, email, url: data.invitation_url } : null
    message.value = data.invitation_url ? '邀请已创建，请复制下方链接并发送给受邀人。' : '邀请邮件已发送。'
    invite.value = { email: '', display_name: '', company_role: 'tenant_member' }
    await load()
  } catch (e: any) { error.value = e.message }
}

async function copyInvitationLink() {
  if (!latestInvitation.value) return
  await navigator.clipboard.writeText(latestInvitation.value.url)
  message.value = `已复制 ${latestInvitation.value.email} 的接受邀请链接。`
}

async function resendInvitation(item: any) {
  error.value = ''; latestInvitation.value = null
  try {
    const data = await api<any>(`/api/company/invitations/${item.id}/resend`, {
      method: 'POST', headers: writeHeaders(),
    })
    latestInvitation.value = data.invitation_url ? { id: data.id, email: item.email, url: data.invitation_url } : null
    message.value = data.invitation_url ? '旧链接已失效，请复制新链接给受邀人。' : '新邀请邮件已发送。'
    await load()
  } catch (e: any) { error.value = e.message }
}

async function revokeInvitation(item: any) {
  if (!window.confirm(`确定撤销发给 ${item.email} 的邀请吗？撤销后原链接立即失效。`)) return
  try {
    await api(`/api/company/invitations/${item.id}/revoke`, { method: 'POST', headers: writeHeaders() })
    message.value = '邀请已撤销。'
    if (latestInvitation.value?.id === item.id) latestInvitation.value = null
    await load()
  } catch (e: any) { error.value = e.message }
}

async function deleteInvitation(item: any) {
  const impact = item.status === 'accepted'
    ? '只会删除这条邀请记录，不会删除已经加入的成员。'
    : '删除后该邀请链接将立即失效。'
  if (!window.confirm(`确定删除发给 ${item.email} 的邀请记录吗？\n${impact}`)) return
  try {
    await api(`/api/company/invitations/${item.id}`, { method: 'DELETE', headers: writeHeaders() })
    message.value = '邀请记录已删除；对应审计事件仍会保留。'
    if (latestInvitation.value?.id === item.id) latestInvitation.value = null
    await load()
  } catch (e: any) { error.value = e.message }
}

async function restore(id: string) {
  await api(`/api/company/projects/${id}/restore`, { method: 'POST', headers: writeHeaders() })
  message.value = '项目已恢复'
  await load()
}

async function selectProject(project: any) {
  selectedProject.value = project
  projectMembers.value = await api(`/api/projects/${project.id}/members`)
  collaborations.value = await api(`/api/projects/${project.id}/collaborating-companies`)
}

async function grantProjectMember() {
  if (!selectedProject.value || !accessForm.value.user_id) return
  try {
    await api(`/api/projects/${selectedProject.value.id}/members`, {
      method: 'POST', headers: writeHeaders(), body: JSON.stringify(accessForm.value),
    })
    message.value = '项目成员权限已保存。'
    accessForm.value.user_id = ''
    await selectProject(selectedProject.value)
  } catch (e:any) { error.value = e.message }
}

async function openCapabilities(member: any) {
  if (!selectedProject.value) return
  capabilityMember.value = member
  capabilityGrants.value = await api(`/api/projects/${selectedProject.value.id}/members/${member.id}/capability-grants`)
}

async function grantCapability() {
  if (!selectedProject.value || !capabilityMember.value) return
  try {
    await api(`/api/projects/${selectedProject.value.id}/members/${capabilityMember.value.id}/capability-grants`, {
      method: 'POST', headers: writeHeaders(), body: JSON.stringify(capabilityForm.value),
    })
    message.value = '临时能力已授予。'
    capabilityForm.value.reason = ''
    await openCapabilities(capabilityMember.value)
  } catch (e:any) { error.value = e.message }
}

async function revokeCapability(item: any) {
  if (!selectedProject.value || !capabilityMember.value) return
  await api(`/api/projects/${selectedProject.value.id}/members/${capabilityMember.value.id}/capability-grants/${item.id}`, {
    method: 'DELETE', headers: writeHeaders(),
  })
  message.value = '临时能力已撤销。'
  await openCapabilities(capabilityMember.value)
}

async function loadSupportTargets() {
  supportForm.value.target_project_id = ''
  supportTargets.value = supportForm.value.target_tenant_id
    ? await api(`/api/platform/support-access-grants/targets/${supportForm.value.target_tenant_id}`) : []
}

async function requestSupportAccess() {
  try {
    const payload = { ...supportForm.value, expires_at: supportForm.value.expires_at || null }
    await api('/api/platform/support-access-grants', {
      method: 'POST', headers: writeHeaders(), body: JSON.stringify(payload),
    })
    message.value = '支持访问申请已提交，等待目标公司管理员批准。'
    supportForm.value.reason = ''
    supportGrants.value = await api('/api/company/support-access-grants')
  } catch (e:any) { error.value = e.message }
}

async function decideSupport(item: any, decision: 'approve'|'reject') {
  try {
    await api(`/api/company/support-access-grants/${item.id}/${decision}`, {
      method: 'POST', headers: writeHeaders(), body: JSON.stringify({ comment: '' }),
    })
    message.value = decision === 'approve' ? '已批准只读支持访问。' : '已拒绝支持访问。'
    supportGrants.value = await api('/api/company/support-access-grants')
  } catch (e:any) { error.value = e.message }
}

async function revokeSupport(item: any) {
  await api(`/api/company/support-access-grants/${item.id}/revoke`, { method: 'POST', headers: writeHeaders() })
  message.value = '支持访问已撤销。'
  supportGrants.value = await api('/api/company/support-access-grants')
}

async function inviteCompany() {
  if (!selectedProject.value || !collaborationTenantId.value) return
  try {
    await api(`/api/projects/${selectedProject.value.id}/collaborating-companies`, {
      method: 'POST', headers: writeHeaders(), body: JSON.stringify({ tenant_id: collaborationTenantId.value }),
    })
    message.value = '协作邀请已发出，等待目标公司管理员确认。'
    collaborationTenantId.value = ''
    await selectProject(selectedProject.value)
  } catch (e:any) { error.value = e.message }
}

function openTenantCreation() {
  collaborationTenantId.value = ''
  tab.value = 'tenants'
  message.value = '请先创建合作公司并邀请首位管理员；创建完成后返回“项目与回收站”继续配置。'
}

async function decideCollaboration(item: any, decision: 'accept'|'reject') {
  try {
    await api(`/api/project-collaborations/${item.id}/${decision}`, { method: 'POST', headers: writeHeaders() })
    message.value = decision === 'accept' ? '已接受项目协作。' : '已拒绝项目协作。'
    await load()
  } catch (e:any) { error.value = e.message }
}

async function revokeCollaboration(item: any) {
  if (!window.confirm('撤销后该公司及其成员将立即失去项目访问权，历史审计仍会保留。确定继续吗？')) return
  try {
    await api(`/api/project-collaborations/${item.id}`, { method: 'DELETE', headers: writeHeaders() })
    message.value = '协作公司权限已撤销。'
    if (selectedProject.value) await selectProject(selectedProject.value)
  } catch (e:any) { error.value = e.message }
}

onMounted(load)
async function updateMember(item:any, values:any) {
  if (!window.confirm('确认调整成员身份或状态？权限将立即按新设置生效。')) return
  try { await api(`/api/company/members/${item.membership_id}`, {method:'PATCH', headers:writeHeaders(), body:JSON.stringify(values)}); await load() }
  catch(e:any) { error.value=e.message }
}
</script>

<template>
  <main class="admin-page page-wrap">
    <div class="admin-title">
      <div><span class="eyebrow">COMPANY SETTINGS</span><h1>公司设置</h1><p>管理本公司成员、项目权限、协作和审计。</p></div>
      <router-link class="primary" to="/app">项目管理 →</router-link>
    </div>
    <div v-if="error" class="alert alert-danger" role="alert">{{error}} <button class="secondary" @click="load">重新加载</button></div>
    <p v-if="message" class="alert alert-success">{{message}}</p>
    <nav class="admin-tabs">
      <button v-for="item in [['overview','总览'],['members','成员与邀请'],['projects','项目与回收站'],['support','支持访问'],['audit','审计']]" :key="item[0]" :class="{active:tab===item[0]}" @click="tab=item[0]">{{item[1]}}</button>
    </nav>

    <div v-if="tab==='overview'" class="overview-toolbar"><div><h2>公司概况</h2><p>当前空间的成员、项目与执行状态。</p></div><a class="secondary" href="/api/company/exports/tenant" download="company-export.json">导出公司数据 ↓</a></div>
    <section v-if="tab==='overview'" class="admin-grid">
      <article class="metric"><strong>{{dashboard.tenant_count ?? '—'}}</strong><span>公司空间</span></article>
      <article class="metric"><strong>{{dashboard.active_user_count ?? '—'}}</strong><span>有效成员</span></article>
      <article class="metric"><strong>{{dashboard.failed_run_count ?? '—'}}</strong><span>失败执行</span></article>
      <article class="metric"><strong>{{Object.values(dashboard.project_statuses||{}).reduce((a:any,b:any)=>a+b,0)}}</strong><span>项目</span></article>
    </section>

    <section v-if="tab==='members'" class="panel admin-section"><router-link to="/app/company/member-imports">批量导入公司成员</router-link>
      <div class="admin-section-heading"><div><h2>成员与邀请</h2><p>邀请链接 48 小时有效且只能使用一次。生产环境由邮件发送，本地开发请复制链接给受邀人。</p></div></div>
      <form class="inline-form" @submit.prevent="inviteMember">
        <input v-model="invite.email" type="email" placeholder="邮箱" required><input v-model="invite.display_name" placeholder="姓名（选填）">
        <select v-model="invite.company_role"><option value="tenant_admin">公司管理员：管理成员和项目权限，默认可查看项目详情</option><option value="tenant_member">公司成员：仅查看后续明确授权的项目</option></select>
        <button class="primary">发送邀请</button>
      </form>
      <div v-if="latestInvitation" class="invitation-delivery">
        <div><strong>接受邀请链接</strong><span>{{latestInvitation.email}}</span><code>{{latestInvitation.url}}</code></div>
        <button class="secondary" @click="copyInvitationLink">复制链接</button>
      </div>
      <p class="invitation-tip">受邀人打开链接后填写姓名和新密码，点击“接受邀请并登录”即可加入当前企业。管理员不要代替受邀人设置密码。</p>
      <h3>现有成员</h3>
      <table><thead><tr><th>成员</th><th>邮箱</th><th>公司身份</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="x in members.members" :key="x.membership_id"><td>{{x.display_name}}</td><td>{{x.email}}</td><td>{{companyRoleNames[x.role] || x.role}}</td><td>{{x.status}}</td><td><button class="secondary" @click="updateMember(x,{status:x.status==='active'?'disabled':'active'})">{{x.status==='active'?'停用':'启用'}}</button><button class="secondary" @click="updateMember(x,{role:x.role==='tenant_admin'?'tenant_member':'tenant_admin'})">切换公司身份</button></td></tr></tbody></table>
      <h3>邀请记录</h3>
      <div v-if="!members.invitations.length" class="empty-mini">当前没有邀请记录</div>
      <div v-else class="table-scroll"><table class="invitation-table"><thead><tr><th>受邀人</th><th>预设角色</th><th>发起人</th><th>邀请时间</th><th>有效期 / 接受时间</th><th>状态</th><th>操作</th></tr></thead><tbody>
        <tr v-for="x in members.invitations" :key="x.id">
          <td><strong>{{x.display_name || '未填写姓名'}}</strong><small>{{x.email}}</small><small>#{{x.id.slice(0,8)}}</small></td>
          <td>{{companyRoleNames[x.role] || x.role}}</td>
          <td><strong>{{x.invited_by_name}}</strong><small>{{x.invited_by_email || '账号已删除'}}</small></td>
          <td>{{formatDate(x.created_at)}}</td>
          <td><span>有效至 {{formatDate(x.expires_at)}}</span><small v-if="x.accepted_at">接受于 {{formatDate(x.accepted_at)}}</small></td>
          <td><span class="status-pill" :class="`invitation-${x.status}`"><i></i>{{invitationStatusNames[x.status] || x.status}}</span></td>
          <td class="table-actions"><button v-if="x.status !== 'accepted'" class="secondary" @click="resendInvitation(x)">重新生成</button><button v-if="x.status === 'pending'" class="secondary" @click="revokeInvitation(x)">撤销</button><button class="secondary danger-text" @click="deleteInvitation(x)">删除记录</button></td>
        </tr>
      </tbody></table></div>
    </section>

    <section v-if="tab==='projects'" class="panel admin-section">
      <h2>项目权限与协作公司</h2>
      <p>公司管理员默认可查看本公司项目及执行详情；修改项目需分配“项目负责人”或“实施顾问”，审批必须由未发起该 Run 的独立“审批人”处理。</p>
      <div v-if="pendingCollaborations.length" class="pending-collaborations"><h3>待确认的公司协作</h3><article v-for="x in pendingCollaborations" :key="x.id"><span>{{x.owner_tenant_name}} 发来的项目协作邀请</span><div><button class="secondary danger-text" @click="decideCollaboration(x,'reject')">拒绝</button><button class="primary" @click="decideCollaboration(x,'accept')">接受</button></div></article></div>
      <table><thead><tr><th>项目</th><th>客户</th><th>状态</th><th>权限管理</th></tr></thead><tbody><tr v-for="x in projects" :key="x.id"><td><router-link v-if="!x.deleted_at" :to="`/app/projects/${x.id}`">{{x.name}} →</router-link><span v-else>{{x.name}}</span></td><td>{{x.customer_name}}</td><td>{{x.deleted_at ? '回收站' : x.status}}</td><td><button v-if="x.deleted_at" @click="restore(x.id)">恢复</button><button v-else class="secondary" @click="selectProject(x)">成员与协作公司</button></td></tr></tbody></table>
      <div v-if="selectedProject" class="project-access-panel">
        <h3>{{selectedProject.name}} · 权限配置</h3>
        <div class="access-columns">
          <section><h4>项目成员</h4><form class="inline-form" @submit.prevent="grantProjectMember"><select v-model="accessForm.user_id" required><option value="" disabled>选择当前公司成员</option><option v-for="x in members.members" :key="x.user_id" :value="x.user_id">{{x.display_name}} · {{x.email}}</option></select><select v-model="accessForm.primary_role_code"><option v-for="(name,key) in projectRoleNames" :key="key" :value="key">{{name}}</option></select><button class="primary">保存主角色</button></form><table><thead><tr><th>成员</th><th>公司</th><th>项目主角色</th><th>状态</th><th>临时能力</th></tr></thead><tbody><tr v-for="x in projectMembers" :key="x.id"><td>{{x.display_name}}</td><td>{{x.tenant_name}}</td><td>{{projectRoleNames[x.primary_role_code] || x.primary_role_code}}</td><td>{{x.status}}</td><td><button class="secondary" @click="openCapabilities(x)">配置</button></td></tr></tbody></table></section>
          <section><h4>协作公司</h4><form v-if="collaborationCandidates.length" class="inline-form" @submit.prevent="inviteCompany"><select v-model="collaborationTenantId" required><option value="" disabled>选择需要协作的公司</option><option v-for="x in collaborationCandidates" :key="x.id" :value="x.id">{{x.name}}</option></select><button class="primary">发起双方确认</button></form><div v-else class="empty-mini"><p>{{collaborationEmptyMessage}}</p><small>如需新增公司，请联系平台运营人员。</small></div><table><thead><tr><th>公司</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="x in collaborations" :key="x.id"><td>{{x.tenant_name}}</td><td>{{x.status}}</td><td><button v-if="x.status==='active'" class="secondary danger-text" @click="revokeCollaboration(x)">撤销权限</button></td></tr></tbody></table></section>
        </div>
      </div>
      <div v-if="capabilityMember" class="project-access-panel">
        <h3>{{capabilityMember.display_name}} · 临时能力授权</h3>
        <p>临时能力不能包含审批、删除、成员管理或平台治理权限，有效期为 1 小时至 30 天。</p>
        <form class="inline-form" @submit.prevent="grantCapability">
          <select v-model="capabilityForm.permission_code"><option v-for="x in ['project.edit','run.start','run.cancel','run.retry','import.validate','import.submit','import.execute','acceptance.submit']" :key="x" :value="x">{{x}}</option></select>
          <input v-model="capabilityForm.reason" required minlength="5" placeholder="授权原因">
          <input v-model="capabilityForm.expires_at" required type="datetime-local">
          <button class="primary">授予临时能力</button>
        </form>
        <table><thead><tr><th>权限</th><th>原因</th><th>到期时间</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="x in capabilityGrants" :key="x.id"><td>{{x.permission_code}}</td><td>{{x.reason}}</td><td>{{formatDate(x.expires_at)}}</td><td>{{x.status}}</td><td><button v-if="x.status==='active'" class="secondary danger-text" @click="revokeCapability(x)">撤销</button></td></tr></tbody></table>
      </div>
    </section>
    <section v-if="tab==='support'" class="panel admin-section">
      <h2>支持访问</h2>
      <p>平台支持访问只允许读取指定项目，并由目标公司管理员批准；到期或撤销后立即失效。</p>
      <table><thead><tr><th>项目</th><th>权限</th><th>原因</th><th>申请/到期时间</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="x in supportGrants" :key="x.id"><td>#{{x.target_project_id.slice(0,8)}}</td><td>{{x.permission_codes.join('、')}}</td><td>{{x.reason}}</td><td>{{formatDate(x.requested_at)}}<br>{{formatDate(x.expires_at)}}</td><td>{{x.status}}</td><td class="table-actions"><button v-if="x.status==='pending'" class="primary" @click="decideSupport(x,'approve')">批准</button><button v-if="x.status==='pending'" class="secondary danger-text" @click="decideSupport(x,'reject')">拒绝</button><button v-if="x.status==='approved'" class="secondary danger-text" @click="revokeSupport(x)">撤销</button></td></tr></tbody></table>
    </section>
    <section v-if="tab==='audit'" class="panel admin-section"><h2>审计中心</h2><a href="/api/company/audit-events/export.csv">导出 CSV</a><table><thead><tr><th>时间</th><th>事件</th><th>操作者</th><th>结果</th></tr></thead><tbody><tr v-for="x in audit" :key="x.id"><td>{{new Date(x.created_at).toLocaleString()}}</td><td>{{x.event_type}}</td><td>{{x.actor}}</td><td>{{x.outcome}}</td></tr></tbody></table></section>
  </main>
</template>

