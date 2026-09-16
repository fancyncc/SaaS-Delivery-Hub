<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import type { AgentRun, AgentStep } from '../types'
const route=useRoute(), id=String(route.params.id)
const run=ref<AgentRun|null>(null), steps=ref<AgentStep[]>([]), error=ref(''), filter=ref('')
const visible=computed(()=>steps.value.filter(step=>`${step.node} ${step.status}`.includes(filter.value.trim())))
async function load() {
  error.value=''
  try { const result=await Promise.all([api<AgentRun>(`/api/runs/${id}`),api<AgentStep[]>(`/api/runs/${id}/steps`)]); run.value=result[0]; steps.value=result[1] }
  catch(e:any) { run.value=null; steps.value=[]; error.value=e.message }
}
function download() {
  const url=URL.createObjectURL(new Blob([JSON.stringify({run_id:id,trace_id:run.value?.trace_id,status:run.value?.status,steps:steps.value},null,2)],{type:'application/json'}))
  const link=document.createElement('a'); link.href=url; link.download=`trace-${id}.json`; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000)
}
onMounted(load)
</script>
<template><main class="page-wrap"><router-link :to="`/app/runs/${id}`">← 返回实施 Run</router-link><p v-if="error" class="alert alert-danger">{{error}}</p>
  <section v-if="run" class="panel"><h1>执行 Trace</h1><p><code>{{run.trace_id}}</code></p><p>Run #{{run.run_number}} · {{run.status}} · 当前节点 {{run.current_node}}</p><router-link v-if="run.retry_of_run_id" :to="`/app/runs/${run.retry_of_run_id}`">查看整改前的 Run</router-link><p>以下为数据库持久化节点记录，时间为记录创建时间，不是节点耗时；跨 Worker 分布式 Span 尚未在此聚合。</p><button class="secondary" @click="load">刷新轨迹</button><button class="secondary" @click="download">下载 Trace JSON</button><label>筛选节点或状态<input v-model="filter" placeholder="输入节点名称或状态"></label>
    <p v-if="!visible.length">暂无匹配的节点记录。</p><article v-for="step in visible" :key="step.id"><h2>{{step.sequence}}. {{step.node}}</h2><p>{{step.status}} · {{new Date(step.created_at).toLocaleString()}}</p><details><summary>节点执行详情</summary><pre>{{JSON.stringify(step.detail,null,2)}}</pre></details></article>
  </section>
</main></template>
