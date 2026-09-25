<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, writeHeaders } from '../api'
import { fragmentText } from '../fragmentText'
const route = useRoute(), document = ref<any>(null), error = ref(''), busy = ref(false)
const phaseLabels: Record<string,string> = {pending:'等待解析',parsing:'解析中',indexing:'索引中',ready:'索引就绪',failed:'解析或索引失败'}
let revision = 0
const sections = computed(() => (document.value?.body || '').split(/\n(?=#{1,6}\s)/).map((part: string, i: number) => {
  const lines = part.split('\n'), heading = lines[0].match(/^#{1,6}\s+(.+)/)
  const page = heading?.[1].match(/^第 (\d+) 页$/)
  return {id:page ? `page-${page[1]}` : `section-${i}`, title:heading ? fragmentText(heading[1]) : '正文', body:fragmentText(heading ? lines.slice(1).join('\n') : part)}
}).filter((s: any) => s.body))
watch([document, () => route.hash], async ([loaded]) => {
  if (!loaded || !route.hash) return
  await nextTick()
  window.document.getElementById(route.hash.slice(1))?.scrollIntoView()
}, {flush:'post'})
async function load() {
  const current = ++revision
  busy.value = true; error.value = ''; document.value = null
  try { const data = await api(`/api/knowledge/${route.params.id}`); if (current === revision) document.value = data }
  catch (e: any) { if (current === revision) error.value = e.message }
  finally { if (current === revision) busy.value = false }
}
async function retryIndex() {
  try {
    await api(`/api/knowledge/${route.params.id}/v3-index`, {method:'POST', headers:writeHeaders()})
    await load()
  } catch (e: any) { error.value = e.message }
}
function download() {
  const d = document.value
  const url = URL.createObjectURL(new Blob([d.body], { type: 'text/markdown;charset=utf-8' }))
  const link = window.document.createElement('a')
  link.href = url; link.download = `${d.title.replace(/[\\/:*?"<>|]/g, '_')}-v${d.version}.md`
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
}
watch(() => route.params.id, load, { immediate: true })
</script>
<template>
  <main class="page-wrap document-page">
    <header class="workspace-heading"><h1>{{ document?.title || '文档详情' }}</h1><router-link class="secondary" :to="document?.project_id ? `/app/projects/${document.project_id}/documents` : '/app/workbench'">返回文档列表</router-link></header>
    <p v-if="busy">正在加载全文…</p>
    <p v-if="error" class="alert alert-danger" role="alert">{{ error }} <button @click="load">重试</button></p>
    <div v-if="document" class="reader-layout"><aside class="reader-sidebar"><span class="eyebrow">DOCUMENT INFO</span><h2>文档信息</h2>
      <p>版本 {{ document.version }} · {{ document.project_id ? '项目私有文档' : '公司通用文档' }} · {{ document.active ? '启用中' : '已停用，不用于新检索' }}</p>
      <p>来源：{{ document.source }} · 模块：{{ document.module }}</p><p>授权说明：{{ document.license }}</p>
      <section v-if="document.v3"><h3>解析与索引</h3><p>{{phaseLabels[document.v3.phase] || '等待索引'}} · {{document.v3.filename}}</p><p v-if="document.v3.error" role="alert">{{document.v3.error}}</p><p v-for="warning in document.v3.warnings" :key="warning" role="status">{{warning}}</p><button v-if="document.can_reindex" class="secondary" @click="retryIndex">重新解析与索引</button> <button class="secondary" @click="load">刷新解析状态</button></section>
      <p v-else-if="document.index_error" role="alert">{{document.index_error}}</p>
      <button class="secondary" @click="download">下载提取文本（Markdown）</button>
      <nav class="reader-toc"><h3>内容目录</h3><a v-for="s in sections" :key="s.id" :href="`#${s.id}`">{{s.title}}</a></nav></aside><article class="panel reader-paper">
      <span class="eyebrow">DOCUMENT CONTENT</span><section v-for="s in sections" :id="s.id" :key="s.id" class="reader-section"><h2>{{s.title}}</h2><p class="document-body">{{s.body}}</p></section>
      <details><summary>查看原始文本</summary><pre class="document-body">{{ document.body }}</pre></details>
    </article></div>
  </main>
</template>
<style scoped>
.document-page { max-width: 1100px; overflow-wrap: anywhere; }
.document-body { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.9; font: inherit; }
summary { cursor: pointer; }
</style>
