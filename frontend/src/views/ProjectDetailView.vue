<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, writeHeaders } from '../api'
import ProjectDocumentsView from './ProjectDocumentsView.vue'
const tab = ref('overview')
const route = useRoute(), project = ref<any>(null), form = ref<any>({}), departments = ref('')
const error = ref(''), success = ref(''), editing = ref(false), busy = ref(false)
const labels: Record<string,string> = {draft:'草稿',ready:'待启动',in_progress:'实施中',blocked:'已阻塞',completed:'已完成',cancelled:'已取消',archived:'已归档'}
const fields = [
  ['name','项目名称','text'],['customer_contact','客户联系人','text'],['contact_email','联系邮箱','email'],
  ['employee_count','成员人数','number'],['target_go_live_date','计划上线日期','date'],['industry','所属行业','text'],
  ['contact_phone','联系电话','text'],['consultant_name','实施顾问','text'],
]
const areas = [['requirements_text','实施需求'],['migration_scope','迁移范围'],['acceptance_criteria','验收标准'],['notes','备注']]
const canEdit = computed(() => project.value?.permissions?.includes('project.edit') && !['completed','archived'].includes(project.value?.lifecycle_status))
const active = computed(() => ['pending','running','preparing_materials','waiting_approval','blocked'].includes(project.value?.latest_run?.status))
async function load() {
  error.value = ''; project.value = null; editing.value = false
  try {
    const p: any = await api(`/api/projects/${route.params.id}`)
    project.value = p; form.value = {...p.document, name:p.name, customer_name:p.customer_name, requirements_text:p.requirements_text || p.document?.requirements_text || ''}
    departments.value = (p.document?.departments || []).join('、')
  } catch(e:any) { error.value = e.message }
}
async function save() {
  busy.value = true; error.value = ''; success.value = ''
  try {
    if (active.value) {
      await api(`/api/runs/${project.value.latest_run.id}/cancel`, {method:'POST',headers:writeHeaders()})
      const refreshed: any = await api(`/api/projects/${project.value.id}`)
      project.value = refreshed
    }
    await api(`/api/projects/${project.value.id}`, {method:'PATCH',headers:writeHeaders(),body:JSON.stringify({...form.value,departments:departments.value.split(/[、，,]/).map(s=>s.trim()).filter(Boolean),expected_version:project.value.version})})
    await load(); success.value = '项目资料已保存，后续执行使用新版本。'
  } catch(e:any) { error.value = e.message }
  finally { busy.value = false }
}
watch(() => route.params.id, load, {immediate:true})
watch(() => route.query.tab, value => { tab.value = value === 'documents' ? 'documents' : 'overview' }, {immediate:true})
</script>
<template>
  <main class="page-wrap project-detail">
    <nav class="page-crumb"><router-link to="/app">项目管理</router-link><span>/</span><span>项目详情</span></nav>
    <p v-if="error" class="alert alert-danger" role="alert">{{error}}</p><p v-if="success" class="alert alert-success">{{success}}</p>
    <template v-if="project">
      <header class="detail-hero"><div><span class="eyebrow">PROJECT OVERVIEW</span><h1>{{project.name}}</h1><p>{{project.customer_name}} <span class="detail-badge">{{labels[project.lifecycle_status]}}</span></p></div>
        <div class="detail-actions"><router-link v-if="project.latest_run" class="secondary" :to="`/app/runs/${project.latest_run.id}`">执行记录</router-link><button v-if="canEdit && !editing" class="primary" @click="editing=true; tab='overview'">编辑项目</button></div>
      </header>
      <nav class="project-detail-tabs" aria-label="项目详情导航"><button :class="{active:tab==='overview'}" @click="tab='overview'">项目概览</button><button :class="{active:tab==='documents'}" @click="tab='documents'">项目文档</button></nav>
      <ProjectDocumentsView v-if="tab==='documents'" embedded />
      <form v-show="tab==='overview'" class="panel detail-sheet" @submit.prevent="save">
        <div class="detail-section-title"><h2>{{editing ? '编辑项目资料' : '基本信息'}}</h2><span>版本 {{project.version}}</span></div>
        <div class="detail-fields"><label v-for="[key,label,type] in fields" :key="key"><span>{{label}}</span><input v-if="editing" v-model="form[key]" :type="type" :required="['name','customer_contact','contact_email','employee_count','target_go_live_date'].includes(key)" :min="type==='number'?1:undefined" :disabled="busy"><strong v-else>{{form[key] || '未填写'}}</strong></label><label><span>部门</span><input v-if="editing" v-model="departments" required :disabled="busy"><strong v-else>{{departments || '未填写'}}</strong></label></div>
        <section v-for="[key,label] in areas" :key="key" class="detail-text"><h2>{{label}}</h2><textarea v-if="editing" v-model="form[key]" :required="key==='requirements_text'" :minlength="key==='requirements_text'?20:undefined" :maxlength="key==='requirements_text'?10000:2000" rows="5" :disabled="busy"></textarea><p v-else>{{form[key] || '未填写'}}</p></section>
        <footer v-if="editing" class="detail-footer"><p v-if="active">保存将取消当前执行与待审批项。已执行的操作不会自动撤销，修改后需重新启动执行。</p><div><button type="button" class="secondary" :disabled="busy" @click="load">放弃修改</button><button class="primary" :disabled="busy || (active && !project.permissions.includes('run.cancel'))">{{busy ? '正在保存…' : active ? '取消当前执行并保存修改' : '保存修改'}}</button></div><p v-if="active && !project.permissions.includes('run.cancel')">请有取消执行权限的成员先取消当前执行。</p></footer>
        <p v-if="!canEdit" class="muted">{{['completed','archived'].includes(project.lifecycle_status) ? '项目已完成或归档，资料以只读方式保留。' : '当前账号可查看项目，没有编辑权限。'}}</p>
      </form>
    </template>
  </main>
</template>
