<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, writeHeaders } from '../api'

type Chunk = {
  id: string; title: string; text: string; heading?: string; version?: number;
  rerank_score?: number; disposition?: string; evidence_role?: string; origin_id?: string;
  location?: { page?: number; slide?: number; sheet?: string; row?: number };
}
type Diagnostics = {
  candidate_count: number; rerank_count: number; supplement_count: number; relevant_count: number;
  selected_count: number; original_tokens: number; final_tokens: number; dedup_saved_tokens: number;
  budget: number; tokenizer: string; candidates: Chunk[]; routes?: Record<string, number>;
  timings_ms?: Record<string, number>;
}
type Result = {
  question: string; needs_rag: boolean; reason: string; status: string; chunks: Chunk[];
  retrieval_mode: string; retrieval_executed?: boolean; evidence_text?: string;
  budget_limited?: boolean; conflict?: boolean; pending_documents?: number; diagnostics?: Diagnostics;
  index_availability?: { visible_documents: number; ready_documents: number; states: Record<string, number> };
}
const budget = ref(1200), question = ref(''), projectId = ref('')
const busy = ref(false), error = ref(''), projectError = ref(''), copied = ref(false), elapsed = ref(0)
const projects = ref<{ id: string; name: string }[]>([]), result = ref<Result | null>(null)
const examples = ['成员迁移流程是怎样的', '触发回滚的情况有哪些']
let timer: ReturnType<typeof setInterval> | undefined
let copyTimer: ReturnType<typeof setTimeout> | undefined
const diagnostics = computed(() => result.value?.diagnostics)
const executed = computed(() => result.value?.retrieval_executed === true)
const hasEvidence = computed(() => !!result.value?.chunks.length)
const sourceCount = computed(() => new Set(result.value?.chunks.map(c => c.origin_id || c.title)).size)
const resultTitle = computed(() => {
  if (result.value?.status === 'not_needed') return '这次无需检索'
  if (result.value?.status === 'index_unavailable') return '资料暂不可检索'
  if (result.value?.conflict) return '发现需要核对的证据'
  if (!hasEvidence.value && result.value?.budget_limited) return '当前预算无法容纳证据'
  return hasEvidence.value ? '已找到相关证据' : '未找到足够相关的证据'
})
const statusLabel = computed(() => result.value?.status === 'not_needed' ? '已跳过' : executed.value ? '检索已执行' : '未执行召回')
const stateLabels: Record<string, string> = { ready: '可检索', stale: '需重建', missing: '待建索引', pending: '等待处理', failed: '处理失败', parsing: '解析中', indexing: '索引中' }
const dispositions: Record<string, string> = { direct: '直接证据', supporting: '补充证据', irrelevant: '相关性不足', incomplete_answerability: '不足以回答问题', token_budget: '超出证据预算', unit_limit: '达到片段上限', duplicate: '重复片段' }
const routeLabels: Record<string, string> = { bm25: '关键词', vector: '向量', exact: '精确匹配', structure: '结构补充' }
const stages = computed(() => [
  { label: '候选召回', count: diagnostics.value?.candidate_count ?? 0, time: diagnostics.value?.timings_ms?.recall_ms },
  { label: '模型重排', count: diagnostics.value?.rerank_count ?? 0, time: diagnostics.value?.timings_ms?.rerank_ms },
  { label: '相关筛选', count: diagnostics.value?.relevant_count ?? 0, time: diagnostics.value?.timings_ms?.answerability_ms },
  { label: '最终证据', count: diagnostics.value?.selected_count ?? 0, time: diagnostics.value?.timings_ms?.assembly_ms },
])
const budgetPercent = computed(() => Math.min(100, (diagnostics.value?.final_tokens || 0) / (diagnostics.value?.budget || budget.value) * 100))
function duration(ms?: number) { return ms == null ? '—' : ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms` }
function location(chunk: Chunk) {
  const p = chunk.location
  if (p?.page) return `第 ${p.page} 页`
  if (p?.slide) return `第 ${p.slide} 张幻灯片`
  if (p?.sheet) return `${p.sheet}${p.row ? ` · 第 ${p.row} 行` : ''}`
  return '文档原文'
}
function clearResult() { result.value = null; error.value = ''; copied.value = false }
function useExample(value: string) { question.value = value; clearResult() }
async function loadProjects() {
  projectError.value = ''
  try { projects.value = await api('/api/projects') }
  catch (e: any) { projectError.value = e.message }
}
onMounted(loadProjects)
onUnmounted(() => { clearInterval(timer); clearTimeout(copyTimer) })
async function inspect() {
  if (busy.value || !question.value.trim()) return
  busy.value = true; clearResult(); elapsed.value = 0
  const started = performance.now()
  timer = setInterval(() => { elapsed.value = performance.now() - started }, 100)
  try {
    result.value = await api<Result>('/api/knowledge/inspect', {
      method: 'POST', headers: writeHeaders(),
      body: JSON.stringify({ question: question.value.trim(), project_id: projectId.value || null, evidence_budget: budget.value }),
    })
  } catch (e: any) { error.value = e.message }
  finally { elapsed.value = performance.now() - started; clearInterval(timer); busy.value = false }
}
async function copyEvidence() {
  try {
    await navigator.clipboard.writeText(result.value?.evidence_text || '')
    copied.value = true; clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copied.value = false }, 2000)
  } catch { error.value = '复制失败，请展开原始证据后手动复制。' }
}
</script>

<template>
  <main class="page-wrap rag-inspect">
    <header class="inspect-heading">
      <div><span class="eyebrow">KNOWLEDGE RETRIEVAL</span><h1>让每个答案，有据可查<span>。</span></h1><p>检索已有知识，核对原文与来源，查看每一步的实际结果。</p></div>
      <router-link class="secondary library-link" to="/app/workbench"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M3 4h6v16H3zM9 4h6v16H9zM16 5l3-1 4 15-3 1z"/></svg>管理知识库 <span>↗</span></router-link>
    </header>
    <div class="inspect-layout">
      <aside class="query-column">
        <form class="panel inspect-form" @submit.prevent="inspect">
          <div class="form-heading"><span class="search-mark"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg></span><div><h2>查验一个问题</h2><p>从你已有的资料中寻找证据</p></div></div>
          <div class="field-group"><label for="rag-scope">检索范围</label><select id="rag-scope" v-model="projectId" :disabled="busy" @change="clearResult"><option value="">当前空间 · 全部授权知识</option><option v-for="p in projects" :key="p.id" :value="p.id">{{ p.name }}及公司通用知识</option></select><small>仅检索当前范围内有权访问的最新资料。</small></div>
          <p v-if="projectError" class="scope-error" role="alert">项目列表加载失败：{{ projectError }} <button type="button" class="text-button" @click="loadProjects">重试</button></p>
          <div class="field-group"><div class="field-label"><label for="rag-question">你的问题</label><span>{{ question.length }} / 4000</span></div><textarea id="rag-question" v-model="question" rows="6" maxlength="4000" required :disabled="busy" placeholder="描述你想查找的流程、规则或条件…" @input="clearResult" @keydown.ctrl.enter.prevent="inspect" @keydown.meta.enter.prevent="inspect"></textarea></div>
          <div class="examples"><span>试试这样问</span><button v-for="example in examples" :key="example" type="button" :disabled="busy" @click="useExample(example)">{{ example }} <span>↗</span></button></div>
          <div class="field-group budget-field"><label for="evidence-budget">证据预算</label><select id="evidence-budget" v-model="budget" :disabled="busy" @change="clearResult"><option :value="600">600 token · 精简</option><option :value="1200">1200 token · 标准</option><option :value="1800">1800 token · 更多上下文</option></select></div>
          <button class="primary inspect-submit" :disabled="busy || !question.trim()"><span v-if="busy" class="spinner" aria-hidden="true"></span><svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg>{{ busy ? '正在检索证据…' : '开始查验' }}<span v-if="!busy" aria-hidden="true">→</span></button>
          <p class="form-note">使用检索模型召回与重排，返回文档原文。<br>不调用生成式 LLM 编写答案。</p>
        </form>
        <div class="privacy-note"><span aria-hidden="true">◇</span><p>权限始终生效<span>项目资料按授权范围检索，停用和旧版资料不参与本次查验。</span></p></div>
      </aside>
      <section class="result-column" aria-live="polite" :aria-busy="busy">
        <div v-if="error" class="inspect-error" role="alert"><strong>本次操作未完成</strong><p>{{ error }}</p><button v-if="!result" type="button" class="secondary" :disabled="busy" @click="inspect">重新查验</button></div>
        <div v-if="busy" class="panel loading-panel"><span class="loading-orbit" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg></span><span class="eyebrow">SEARCH IN PROGRESS</span><h2>正在查找相关原文</h2><p>后台正在处理请求，完成后将展示实际执行的阶段与数量。</p><span class="elapsed">已等待 {{ (elapsed / 1000).toFixed(1) }} 秒</span></div>
        <template v-else-if="result">
          <section class="panel result-summary" :class="{ 'has-evidence': hasEvidence, 'needs-attention': result.status === 'index_unavailable' || result.conflict }">
            <div class="summary-top"><span class="result-state"><i></i>{{ statusLabel }}</span><span v-if="diagnostics?.timings_ms" class="total-time">耗时 {{ duration(diagnostics.timings_ms.total) }}</span></div>
            <h2>{{ resultTitle }}</h2>
            <p v-if="hasEvidence">来自 <strong>{{ sourceCount }}</strong> 份文档的 <strong>{{ result.chunks.length }}</strong> 条原文，可逐条打开来源核对。</p>
            <p v-else-if="result.status === 'index_unavailable' || result.status === 'not_needed'">{{ result.reason }}</p>
            <p v-else-if="result.budget_limited">已找到相关内容，但完整证据超出了当前预算。请提高预算后重试。</p>
            <p v-else>已完成检索，候选内容未通过相关性或可回答性检查。可以补充项目名称、对象或问题细节后重试。</p>
            <div class="summary-tags"><span>{{ result.retrieval_mode === 'real' ? '真实模型模式' : '离线模式' }}</span><span>本地规则判断</span><span v-if="hasEvidence">原文证据 · 未生成答案</span></div>
            <div v-if="result.status === 'index_unavailable' && result.index_availability" class="availability"><span v-for="(count, state) in result.index_availability.states" :key="state">{{ stateLabels[state] || state }} <b>{{ count }}</b></span><router-link to="/app/workbench">查看文档处理状态 →</router-link></div>
          </section>
          <section v-if="executed && diagnostics" class="panel pipeline-panel">
            <div class="section-title"><h3>本次检索路径</h3><span>实际执行统计</span></div>
            <ol class="pipeline-steps"><li v-for="(stage, i) in stages" :key="stage.label"><span class="stage-index">0{{ i + 1 }}</span><strong>{{ stage.count }}<small>条</small></strong><span class="stage-label">{{ stage.label }}</span><span class="stage-time">{{ duration(stage.time) }}</span></li></ol>
            <div class="recall-routes"><span v-for="(count, route) in diagnostics.routes" :key="route">{{ routeLabels[route] || route }} <b>{{ count }}</b></span><small>各路命中可能重叠；另补充 {{ diagnostics.supplement_count }} 条</small></div>
          </section>
          <div v-if="result.budget_limited || result.conflict || (executed && result.pending_documents)" class="result-notice" role="status"><p v-if="result.budget_limited">部分相关证据因预算或数量限制省略，可提高预算后再次查验。</p><p v-if="result.conflict">来源存在潜在冲突，请对照原文核实。</p><p v-if="executed && result.pending_documents">当前范围还有 {{ result.pending_documents }} 份资料尚不可检索，本次结果仅来自已就绪资料。</p></div>
          <section v-if="hasEvidence" class="evidence-section">
            <div class="evidence-heading"><div><h3>原文证据 <span>{{ result.chunks.length }}</span></h3><p>内容直接摘自文档，保留原始表述。</p></div><button type="button" class="secondary copy-evidence" @click="copyEvidence">{{ copied ? '已复制 ✓' : '复制证据' }}</button></div>
            <article v-for="(chunk, i) in result.chunks" :key="chunk.id" class="panel evidence-card">
              <div class="evidence-source"><span class="evidence-number">{{ String(i + 1).padStart(2, '0') }}</span><div><router-link v-if="chunk.origin_id" :to="`/app/knowledge/${chunk.origin_id}${chunk.location?.page ? '#page-' + chunk.location.page : ''}`">{{ chunk.title }} <span>↗</span></router-link><strong v-else>{{ chunk.title }}</strong><p>{{ location(chunk) }}<span v-if="chunk.version"> · v{{ chunk.version }}</span><span v-if="chunk.evidence_role === 'supporting'"> · 补充证据</span></p></div></div>
              <h4 v-if="chunk.heading">{{ chunk.heading }}</h4><p class="chunk-text evidence-body">{{ chunk.text }}</p>
              <div class="evidence-footer"><span>可追溯原文</span><span v-if="chunk.rerank_score != null">重排分 {{ chunk.rerank_score.toFixed(3) }} <small>非置信度</small></span></div>
            </article>
            <p class="coverage-note">本次展示相关证据，尚未证明覆盖知识库中的全部事实。</p>
          </section>
          <section v-if="diagnostics && executed" class="panel technical-panel">
            <div class="budget-summary"><div><h3>证据预算</h3><p>{{ diagnostics.tokenizer }} · 含来源与位置信息</p></div><strong>{{ diagnostics.final_tokens.toLocaleString() }} <span>/ {{ diagnostics.budget.toLocaleString() }}</span></strong></div><div class="budget-track"><div :style="{ width: `${budgetPercent}%` }"></div></div>
            <details v-if="result.evidence_text"><summary>查看原始序列化证据 <span>evidence_text</span></summary><pre class="chunk-text raw-evidence">{{ result.evidence_text }}</pre></details>
            <details><summary>查看候选与筛选诊断 <span>{{ diagnostics.candidates.length }} 条</span></summary><p class="diagnostic-note">候选不等于最终证据。原候选 {{ diagnostics.original_tokens.toLocaleString() }} token，去重节省 {{ diagnostics.dedup_saved_tokens }} token。</p><article v-for="(candidate, i) in diagnostics.candidates" :key="`${candidate.id}-${i}`" class="candidate"><h4>{{ candidate.title }}</h4><p>{{ dispositions[candidate.disposition || ''] || candidate.disposition }}<span v-if="candidate.rerank_score != null"> · 重排分 {{ candidate.rerank_score.toFixed(4) }}（非置信度）</span></p><p class="chunk-text">{{ candidate.text }}</p></article></details>
          </section>
        </template>
        <section v-else-if="!error" class="panel welcome-panel"><div class="welcome-art" aria-hidden="true"><div class="art-sheet"><span></span><span></span><span></span><i>✓</i></div><span class="art-dot dot-one"></span><span class="art-dot dot-two"></span></div><span class="eyebrow">EVIDENCE, BEFORE ANSWERS</span><h2>从一个问题开始</h2><p>让知识库中的资料变成可核对的证据。<br>在左侧输入问题，查看相关段落、来源和检索过程。</p><div class="welcome-steps"><div><span>01</span><strong>限定范围</strong><small>遵循项目与资料权限</small></div><div><span>02</span><strong>召回与筛选</strong><small>检索相关内容并重排</small></div><div><span>03</span><strong>核对原文</strong><small>追溯来源与证据位置</small></div></div><span class="welcome-footnote">每一次查验，都以实际执行结果为准</span></section>
      </section>
    </div>
  </main>
</template>

<style scoped src="../rag-inspect.css"></style>
