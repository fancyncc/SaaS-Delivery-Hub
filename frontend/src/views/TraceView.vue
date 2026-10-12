<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import type { AgentRun, AgentStep } from '../types'
const route=useRoute(), id=String(route.params.id)
type SnapshotSummary = {id:string; kind:string; sequence:number; content_hash:string; created_at:string}
const run=ref<AgentRun|null>(null), steps=ref<AgentStep[]>([]), error=ref(''), filter=ref('')
const snapshots=ref<{version:number;status:string;revisions:SnapshotSummary[]}|null>(null)
const selected=ref<Record<string,any>|null>(null)
const visible=computed(()=>steps.value.filter(step=>`${step.node} ${step.status}`.includes(filter.value.trim())))
async function load() {
  error.value=''
  selected.value=null
  try { const result=await Promise.all([api<AgentRun>(`/api/runs/${id}`),api<AgentStep[]>(`/api/runs/${id}/steps`),api<{version:number;status:string;revisions:SnapshotSummary[]}>(`/api/runs/${id}/snapshots`)]); run.value=result[0]; steps.value=result[1]; snapshots.value=result[2] }
  catch(e:any) { run.value=null; steps.value=[]; snapshots.value=null; error.value=e.message }
}
async function inspectSnapshot(snapshotId:string) {
  try { selected.value=await api<Record<string,any>>(`/api/runs/${id}/snapshots/${snapshotId}`) }
  catch(e:any) { error.value=e.message }
}
function download() {
  const url=URL.createObjectURL(new Blob([JSON.stringify({run_id:id,trace_id:run.value?.trace_id,status:run.value?.status,steps:steps.value,snapshots:snapshots.value},null,2)],{type:'application/json'}))
  const link=document.createElement('a'); link.href=url; link.download=`trace-${id}.json`; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000)
}
onMounted(load)
</script>
<template><main class="page-wrap"><router-link :to="`/app/runs/${id}`">← 返回实施执行</router-link><p v-if="error" class="alert alert-danger">{{error}}</p>
  <section v-if="run" class="panel"><h1>执行追踪</h1><p><code>{{run.trace_id}}</code></p><p>实施执行 #{{run.run_number}} · {{run.status}} · 当前节点 {{run.current_node}}</p><router-link v-if="run.retry_of_run_id" :to="`/app/runs/${run.retry_of_run_id}`">查看整改前的执行</router-link><p>节点显示实际执行耗时与追踪标识；异步执行可通过这些标识关联服务日志。历史记录没有耗时信息时显示“未采集”。</p><button class="secondary" @click="load">刷新轨迹</button><button class="secondary" @click="download">下载追踪 JSON</button><label>筛选节点或状态<input v-model="filter" placeholder="输入节点名称或状态"></label>
    <p v-if="!visible.length">暂无匹配的节点记录。</p><article v-for="step in visible" :key="step.id"><h2>{{step.sequence}}. {{step.node}}</h2><p>{{step.status}} · {{new Date(step.created_at).toLocaleString()}}</p><p>执行耗时：{{step.detail.execution ? `${step.detail.execution.duration_ms} ms` : "未采集"}}</p><p v-if="step.detail.execution"><code>{{step.detail.execution.trace_id}} / {{step.detail.execution.span_id}}</code></p><details><summary>节点执行详情</summary><pre>{{JSON.stringify(step.detail,null,2)}}</pre></details></article>
    <h2>执行材料快照</h2><p>{{snapshots?.version ? "本次执行已保存不可变输入与材料修订。" : "历史执行未固化完整输入，不能保证材料重放。"}}</p><article v-for="item in snapshots?.revisions" :key="item.id"><p>{{item.sequence}}. {{item.kind}} · {{new Date(item.created_at).toLocaleString()}}</p><code>{{item.content_hash}}</code><button class="secondary" @click="inspectSnapshot(item.id)">查看材料</button></article><details v-if="selected" open><summary>快照材料</summary><pre>{{JSON.stringify(selected,null,2)}}</pre></details>
  </section>
</main></template>
