<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
const route = useRoute()
defineProps<{ embedded?: boolean }>()
const project = ref<any>(null), documents = ref<any[]>([]), error = ref(''), busy = ref(false)
const includeShared = ref(false), query = ref('')
const filtered = computed(() => documents.value.filter(d =>
  (d.project_id === route.params.id || includeShared.value && !d.project_id) && d.title.toLowerCase().includes(query.value.toLowerCase())))
const count = computed(() => documents.value.filter(d => d.project_id === route.params.id).length)
const labels: Record<string, string> = {ready: '索引就绪', pending: '等待索引', failed: '索引失败'}
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
watch(() => route.params.id, load, { immediate: true })
</script>
<template>
  <section class="project-library" :class="{'page-wrap': !embedded}">
    <header v-if="!embedded" class="library-heading"><div><span class="eyebrow">PROJECT LIBRARY</span><h1>项目文档</h1><p>{{ project?.name || '查看项目关联知识库' }}</p></div><router-link class="secondary" :to="`/app/projects/${route.params.id}?tab=documents`">项目详情 →</router-link></header>
    <p v-if="error" class="alert alert-danger" role="alert">{{ error }} <button @click="load">重试</button></p>
    <section v-if="!error" class="library-surface">
      <div class="library-summary"><div><h2>文档资料 <span>{{ count }}</span></h2><p>需求、设计与交付资料，集中查阅与追溯。</p></div><button class="secondary" :disabled="busy" @click="load">刷新列表</button></div>
      <div class="library-tools"><input v-model="query" aria-label="搜索文档标题" placeholder="搜索文档名称…"><label class="shared-toggle"><input v-model="includeShared" type="checkbox">包含公司通用文档</label></div>
      <p v-if="busy">正在加载文档…</p>
      <template v-else><p class="library-count">显示 {{ filtered.length }} 份文档 · 含历史版本与停用资料</p>
        <p v-if="!filtered.length" class="library-empty">没有匹配的文档，请调整搜索或检索范围。</p>
        <div class="file-columns"><span>文档名称 / 来源</span><span>适用范围</span><span>状态</span><span></span></div>
        <article v-for="d in filtered" :key="d.id" class="document-row">
          <div class="file-identity"><span class="file-icon" aria-hidden="true">文</span><div><h3><router-link :to="`/app/knowledge/${d.id}`">{{ d.title }}</router-link><small>v{{d.version}}</small></h3><p :title="d.source">{{ d.source }}</p></div></div>
          <span class="file-scope">{{ d.project_id ? '项目私有' : '公司通用' }}</span>
          <span class="file-status" :class="{ready:d.active && d.index_status==='ready',failed:d.active && d.index_status==='failed'}"><i></i>{{ d.active ? labels[d.index_status] || d.index_status : '已停用' }}</span>
          <router-link class="file-open" :to="`/app/knowledge/${d.id}`">阅读全文 <span aria-hidden="true">↗</span></router-link>
        </article>
      </template>
    </section>
  </section>
</template>
<style scoped>
.project-library{max-width:1280px}.library-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:28px;gap:24px}.library-heading h1{font-size:30px;margin:10px 0}.library-heading p{color:#809087;font-size:14px}.library-surface{background:#fff;border:1px solid #dfe7e2;border-radius:16px;padding:28px 32px;box-shadow:0 8px 30px #173e3005}.library-summary{display:flex;align-items:center;justify-content:space-between;gap:16px}.library-summary h2{font-size:19px;margin:0}.library-summary h2 span{font-size:12px;padding:4px 9px;border-radius:7px;background:#edf4ef;color:#4e7563;margin-left:8px}.library-summary p{font-size:13px;color:#84948c;margin:10px 0}.library-tools{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:20px 0 6px}.library-tools>input{max-width:430px;font-size:13px;padding:12px 16px;background:#f8faf8}.shared-toggle{display:flex;align-items:center;gap:9px;white-space:nowrap;font-size:13px;color:#627b6e}.shared-toggle input{width:16px;height:16px;margin:0;accent-color:#28624e}.library-count{font-size:12px;color:#98a59e;margin:14px 0 20px}.file-columns,.document-row{display:grid;grid-template-columns:minmax(0,1fr) 100px 110px 100px;align-items:center;gap:20px}.file-columns{padding:12px 16px;background:#f7f9f7;border-radius:8px;color:#8b9a91;font-size:12px}.document-row{padding:20px 16px;border-bottom:1px solid #edf1ed;transition:background .15s}.document-row:hover{background:#fafcf9}.document-row:last-child{border-bottom:0}.file-identity{display:flex;align-items:center;gap:14px;min-width:0}.file-identity>div{min-width:0}.file-icon{display:grid;place-items:center;width:38px;height:44px;flex-shrink:0;border:1px solid #dce9df;border-radius:7px;background:#f0f6f1;color:#638773;font-size:15px}.file-identity h3{margin:0;display:flex;align-items:baseline;gap:8px;font-size:14px;line-height:1.6}.file-identity a{color:#284d3e;text-decoration:none}.file-identity a:hover{text-decoration:underline}.file-identity small{font-size:10px;color:#94a198;font-weight:400;white-space:nowrap}.file-identity p{font-size:11px;color:#97a39c;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin:5px 0 0}.file-scope{font-size:12px;color:#7a8b81}.file-status{font-size:11px;color:#87968d;display:flex;align-items:center;gap:6px}.file-status i{width:5px;height:5px;border-radius:50%;background:currentColor}.file-status.ready{color:#42856a}.file-status.failed{color:#b55a42}.file-open{font-size:12px;color:#477962;text-decoration:none;white-space:nowrap;text-align:right}.file-open span{margin-left:6px}.library-empty{text-align:center;padding:48px;color:#86968b;font-size:14px}@media(max-width:760px){.library-surface{padding:20px 16px}.library-heading,.library-tools{align-items:stretch;flex-direction:column}.library-tools>input{max-width:none}.file-columns{display:none}.document-row{grid-template-columns:1fr auto;gap:12px;padding:18px 0}.file-identity{grid-column:1/-1}.file-scope{display:none}.file-open{text-align:right}.library-heading h1{font-size:24px}}
</style>
