<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, writeHeaders } from '../api'
import { useAuthStore } from '../auth'
type Experiment = {id:string; status:string; created_at:string; result:Record<string,any>}
const auth = useAuthStore()
const rows = ref<Experiment[]>([]), error = ref(''), running = ref(false), selected = ref(''), baseline = ref('')
const current = computed(()=>rows.value.find(row=>row.id===selected.value))
const comparison = computed(()=>rows.value.find(row=>row.id===baseline.value))
const compatible = computed(()=>current.value && comparison.value && current.value.result.dataset_checksum && current.value.result.corpus_checksum && current.value.result.dataset_checksum===comparison.value.result.dataset_checksum && current.value.result.corpus_checksum===comparison.value.result.corpus_checksum && current.value.result.evaluator_version===comparison.value.result.evaluator_version)
async function load() {
  try { rows.value=await api<Experiment[]>('/api/platform/evaluations'); if (!selected.value) selected.value=rows.value[0]?.id || '' }
  catch(e:any) { error.value=e.message }
}
async function execute() {
  running.value=true; error.value=''
  try { const result=await api<{id:string}>('/api/platform/evaluations/run', {method:'POST',headers:writeHeaders()}); selected.value=result.id; await load() }
  catch(e:any) { error.value=e.message }
  finally { running.value=false }
}
onMounted(load)
</script>
<template><main class="page-wrap">
  <router-link to="/platform">← 平台管理</router-link>
  <section class="panel"><h1>离线检索评测</h1><p>固定数据集的校验与本地检索 Recall@5。非 RAG 场景由自动化测试执行；本页面不代表真实模型、客户知识库或全部业务验收。</p>
    <p v-if="error" class="alert alert-danger">{{error}}</p>
    <button v-if="auth.user?.platform_roles.includes('platform_super_admin')" class="primary" :disabled="running" @click="execute">{{running?'评测中…':'运行离线评测'}}</button>
    <button class="secondary" :disabled="running" @click="load">刷新历史</button>
    <p v-if="!rows.length">暂无评测记录。</p>
    <table v-else><thead><tr><th>执行时间</th><th>状态</th><th>数据量</th><th>Recall@5</th><th>报告</th></tr></thead><tbody><tr v-for="row in rows" :key="row.id"><td><button class="secondary" @click="selected=row.id">{{new Date(row.created_at).toLocaleString()}}</button></td><td>{{row.status}}</td><td>{{row.result.dataset_size}}</td><td>{{row.result.rag_recall_at_5 ?? '未计算'}}</td><td><a :href="`/api/platform/evaluations/${row.id}/download`">下载 JSON</a></td></tr></tbody></table>
  </section>
  <section v-if="current" class="panel"><h2>评测详情</h2><p>执行 {{current.id}}</p><p>模式：{{current.result.mode || '历史记录未标注'}} · 评测器：{{current.result.evaluator_version || '未记录'}}</p><p>数据集 SHA-256：<code>{{current.result.dataset_checksum || '未记录'}}</code></p><p>数据集结构：{{current.result.schema_valid?'有效':'无效'}} · 重复 ID：{{current.result.duplicate_ids}}</p>
    <label>比较基线<select v-model="baseline"><option value="">不比较</option><option v-for="row in rows.filter(r=>r.id!==selected)" :key="row.id" :value="row.id">{{row.created_at}}</option></select></label>
    <template v-if="comparison"><p v-if="!compatible">数据集、知识语料或评测器版本不同（或历史记录缺少版本），不可直接比较。</p><p v-else>Recall@5：{{comparison.result.rag_recall_at_5 ?? '未计算'}} → {{current.result.rag_recall_at_5 ?? '未计算'}}</p></template>
    <table><thead><tr><th>用例</th><th>命中预期证据</th><th>召回证据 ID</th></tr></thead><tbody><tr v-for="item in current.result.rag_cases" :key="item.id"><td>{{item.id}}</td><td>{{item.passed?'通过':'失败'}}</td><td>{{item.retrieved.join('、')}}</td></tr></tbody></table>
  </section>
</main></template>
