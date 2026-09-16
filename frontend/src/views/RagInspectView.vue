<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, writeHeaders } from '../api'
import { fragmentText } from '../fragmentText'

type Chunk = { id: string; title: string; text: string; source?: string; heading?: string; version?: number | string; score?: number; rerank_score?: number; selection_reason?: string; disposition?: string }
type Result = { question: string; needs_rag: boolean; reason: string; status: string; chunks: Chunk[]; decision_mode: string; retrieval_mode: string; pipeline?: string; evidence_text?: string; coverage?: string; budget_limited?: boolean; conflict?: boolean; diagnostics?: { candidate_count: number; supplement_count: number; relevant_count: number; selected_count: number; original_tokens: number; final_tokens: number; dedup_saved_tokens: number; budget: number; tokenizer: string; baseline_note?: string; candidates: Chunk[] } }
const pipeline = ref('legacy')
const budget = ref(1200)
const question = ref(''), projectId = ref(''), busy = ref(false), error = ref('')
const projects = ref<{ id: string; name: string }[]>([]), result = ref<Result | null>(null)
onMounted(async () => {
  try { projects.value = await api('/api/projects') }
  catch (e: any) { error.value = e.message }
})
async function inspect() {
  if (busy.value || !question.value.trim()) return
  busy.value = true; error.value = ''; result.value = null
  try {
    result.value = await api<Result>('/api/knowledge/inspect', {
      method: 'POST', headers: writeHeaders(),
      body: JSON.stringify({ question: question.value.trim(), project_id: projectId.value || null, evidence_budget:budget.value, pipeline:pipeline.value }),
    })
  } catch (e: any) { error.value = e.message }
  finally { busy.value = false }
}
</script>

<template>
  <main class="page-wrap rag-inspect">
    <header class="workspace-heading"><div><span class="eyebrow">RAG CHECK</span><h1>RAG 查验</h1><p>输入问题，查看是否需要知识库检索及返回的原始片段。</p></div><router-link class="secondary" to="/app/workbench">管理知识库 →</router-link></header>
    <form class="panel inspect-form" @submit.prevent="inspect">
      <label for="pipeline">查验流程</label><select id="pipeline" v-model="pipeline" :disabled="busy" @change="result=null"><option value="legacy">当前流程</option><option value="v3">V3 · 实验流程（须通过发布门禁）</option></select>
      <label for="rag-scope">检索范围</label>
      <select id="rag-scope" v-model="projectId" :disabled="busy" @change="result = null"><option value="">当前空间内有权访问的知识库</option><option v-for="p in projects" :key="p.id" :value="p.id">{{ p.name }}及公司通用知识</option></select>
      <label for="rag-question">你的问题</label>
      <textarea id="rag-question" v-model="question" rows="5" maxlength="4000" required :disabled="busy" placeholder="例如：我们公司的成员导入有哪些要求？" @input="result = null"></textarea>
      <label for="evidence-budget">精选证据预算（新流程启用后生效）</label><select id="evidence-budget" v-model="budget" :disabled="busy" @change="result=null"><option :value="600">600 token</option><option :value="1200">1200 token · 默认</option><option :value="1800">1800 token</option></select>
      <div class="inspect-actions"><small>本地规则判断，不调用生成式 LLM。普通问题最多 8 个证据单元；枚举和比较问题按完整性组装，仍受所选预算限制。</small><button class="primary" :disabled="busy || !question.trim()">{{ busy ? '正在判断并检索…' : '开始查验' }}</button></div>
    </form>
    <p v-if="error" class="alert alert-danger" role="alert">查验失败：{{ error }}</p>
    <section v-if="result" class="panel" aria-live="polite">
      <h2>{{ result.needs_rag ? '这个问题需要知识库检索' : '这个问题无需知识库检索' }}</h2>
      <p>{{ result.reason }}</p>
      <p class="inspect-meta">判断方式：本地规则（不确定时尝试检索） · 检索模式：{{ result.retrieval_mode === 'real' ? '真实 RAG' : '离线关键词检索' }}</p>
      <p v-if="result.status === 'not_found'" class="empty-result">知识库中未检索到相关片段。请确认范围内资料已上传且索引就绪。</p>
      <p v-if="result.needs_rag && !result.pipeline" class="inspect-meta">当前为旧版检索流程；精选证据流程需校准验收通过后启用。</p>
      <p v-if="result.status==='insufficient_relevance'" class="empty-result">未找到足够相关的证据。</p>
      <p v-if="result.budget_limited || result.status==='budget_limited'" class="empty-result">部分相关内容因预算或片段数量限制省略，当前证据可能不完整。</p>
      <section v-if="result.diagnostics" class="empty-result"><strong>精选证据处理</strong><p>召回 {{result.diagnostics.candidate_count}} → 补充 {{result.diagnostics.supplement_count}} → 相关 {{result.diagnostics.relevant_count}} → 保留 {{result.diagnostics.selected_count}}</p><p>{{result.diagnostics.tokenizer}}：原候选 {{result.diagnostics.original_tokens}} → 最终 {{result.diagnostics.final_tokens}} / {{result.diagnostics.budget}}；去重节省 {{result.diagnostics.dedup_saved_tokens}}</p><p v-if="result.diagnostics.baseline_note">{{result.diagnostics.baseline_note}}</p><details><summary>查看候选及筛选原因（不属于最终证据）</summary><article v-for="c in result.diagnostics.candidates" :key="c.id"><h4>{{c.title}} · {{c.heading}}</h4><p>{{c.disposition}} · 重排分 {{c.rerank_score?.toFixed(4)}}（非置信度）</p><p class="chunk-text">{{c.text}}</p></article></details></section>
      <p v-if="result.status==='index_unavailable'">无可用资料或最新版本索引尚未就绪。</p><p v-if="result.coverage==='unknown'">当前仅返回相关证据，完整性未知。</p><p v-if="result.conflict || result.status==='source_conflict'">来源存在潜在冲突，请对照原文核实。</p><section v-if="result.pipeline==='v3'"><h3>精选原文证据 · 实际序列化内容</h3><pre class="chunk-text">{{result.evidence_text || '无最终证据'}}</pre><small>上方内容即后端 evidence_text；候选诊断不计入最终 token。</small></section>
      <template v-if="result.chunks.length && result.pipeline!=='v3'">
        <h3>返回 {{ result.chunks.length }} 个片段</h3>
        <p class="inspect-meta">{{ result.retrieval_mode === 'real' ? '按重排结果展示。' : '按检索排序展示。' }}正文已去除常见 Markdown 标记，可展开查看原文和排序信息。</p>
        <article v-for="(chunk, i) in result.chunks" :key="chunk.id" class="retrieved-chunk">
          <h3>{{ i + 1 }}. {{ chunk.title }}</h3>
          <p class="inspect-meta">{{ chunk.source || '知识库' }}<span v-if="chunk.version"> · 版本 {{ chunk.version }}</span><span v-if="chunk.heading"> · {{ chunk.heading }}</span></p>
          <p class="chunk-text">{{ fragmentText(chunk.text) }}</p>
          <p v-if="chunk.selection_reason" class="inspect-meta">{{chunk.selection_reason}} · 重排分 {{chunk.rerank_score?.toFixed(4)}}（非置信度）</p>
          <details class="fragment-details">
            <summary>查看原始片段与排序信息</summary>
            <p class="inspect-meta">片段 ID：{{ chunk.id }}</p>
            <template v-if="chunk.score != null">
              <p>RRF 融合分：{{ chunk.score.toFixed(4) }}</p>
              <p class="inspect-meta">按召回名次累加 1 / (60 + 名次)。单路第一名约 0.0164，两路都第一名约 0.0328；不是相似度或置信度，不能作为百分比理解。{{ result.retrieval_mode === 'real' ? '后续重排会调整展示顺序，此值不是重排模型分数。' : '' }}</p>
            </template>
            <pre class="chunk-text">{{ chunk.text }}</pre>
          </details>
        </article>
      </template>
    </section>
  </main>
</template>

<style scoped>
.rag-inspect { max-width: 1000px; }
.inspect-form { display: grid; gap: 14px; }
.inspect-actions { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.inspect-meta, .inspect-actions small { color: #64748b; overflow-wrap: anywhere; }
.retrieved-chunk { border-top: 1px solid #e2e8f0; padding: 18px 0; }
.chunk-text { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; }
.fragment-details summary { cursor: pointer; color: #64748b; }
.fragment-details pre { font: inherit; padding: 16px; background: #f8fafc; border-radius: 8px; }
.empty-result { padding: 20px; background: #f8fafc; border-radius: 10px; }
</style>
