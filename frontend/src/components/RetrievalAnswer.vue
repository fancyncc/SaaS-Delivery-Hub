<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
const props = defineProps<{ answer: string; citations: { id: string; title: string; source: string; text: string; number?: number }[] }>()
const selected = ref<number | null>(null)
const root = ref<HTMLElement | null>(null)
const copied = ref('')
const sources = computed(() => props.citations.map((source, index) => ({ ...source, number: source.number || index + 1 })))
const lines = computed(() => props.answer.split('\n').map(text => ({
  heading: /^#{1,6}\s/.test(text),
  text: text.replace(/^#{1,6}\s+/, ''),
})))
function tokens(text: string) { return text.split(/(\[\d+\]|\*\*[^*]+\*\*|`[^`]+`)/g) }
function citation(token: string) { return /^\[\d+\]$/.test(token) ? sources.value.find(s => s.number === Number(token.slice(1, -1))) : undefined }
async function reveal(number: number) {
  selected.value = number
  await nextTick()
  const card = root.value?.querySelector<HTMLElement>(`[data-source-number="${number}"]`)
  if (card instanceof HTMLDetailsElement) card.open = true
  card?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  card?.focus({ preventScroll: true })
}
async function copy() {
  try { await navigator.clipboard.writeText(props.answer); copied.value = '已复制' }
  catch { copied.value = '复制失败，请选择文字复制' }
}
</script>

<template>
  <div ref="root" class="retrieval-result" :class="{ 'has-sources': sources.length }">
    <section class="result-answer" aria-label="回答内容">
      <header><h3>答案</h3><button type="button" @click="copy">复制答案</button></header>
      <small v-if="copied" role="status">{{ copied }}</small>
      <div class="answer-text">
        <template v-for="(line, index) in lines" :key="index">
          <component :is="line.heading ? 'h4' : 'p'" v-if="line.text.trim()">
            <template v-for="(token, i) in tokens(line.text)" :key="i">
              <button v-if="citation(token)" class="citation-link" :aria-label="`查看来源 ${citation(token)!.number}`" @click="reveal(citation(token)!.number)">{{ token }}</button>
              <strong v-else-if="token.startsWith('**') && token.endsWith('**')">{{ token.slice(2, -2) }}</strong>
              <code v-else-if="token.startsWith('`') && token.endsWith('`')">{{ token.slice(1, -1) }}</code>
              <template v-else>{{ token }}</template>
            </template>
          </component>
        </template>
      </div>
      <p v-if="!sources.length" class="no-sources">本次回答没有返回可核对的来源片段。</p>
    </section>
    <aside v-if="sources.length" class="result-sources" aria-label="检索来源">
      <header><h3>检索来源 <span>{{ sources.length }}</span></h3><small>点击卡片展开片段</small></header>
      <details v-for="source in sources" :key="`${source.id}-${source.number}`" class="source-card" :class="{ selected: selected === source.number }" :data-source-number="source.number" :open="selected === source.number" tabindex="-1">
        <summary><span class="source-number">{{ source.number }}</span><span class="source-title"><strong>{{ source.title }}</strong><small>{{ source.source }}</small><span class="source-preview">{{ source.text }}</span></span><span class="expand-icon">⌄</span></summary>
        <div class="source-full"><small>来源片段 · {{ source.number }}</small><p>{{ source.text || '此来源未提供原文片段。' }}</p></div>
      </details>
    </aside>
  </div>
</template>

<style scoped>
.retrieval-result{display:grid;gap:24px;min-width:0;margin-top:16px}.has-sources{grid-template-columns:minmax(0,1fr) minmax(260px,340px)}
.result-answer,.result-sources{min-width:0}header{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:14px}h3{font-size:13px;margin:0;color:#426252}h3 span{display:inline-block;background:#e8eee8;border-radius:5px;padding:1px 6px;margin-left:5px}header small,.no-sources{font-size:11px;color:#7a847c}header button{font-size:12px!important;color:#547461!important;padding:4px 8px!important;border:1px solid #e1e7df!important;border-radius:6px}
.answer-text{font-size:14px;line-height:1.9;overflow-wrap:anywhere}.answer-text p{white-space:pre-wrap;margin:0 0 10px}.answer-text h4{font-size:16px;margin:22px 0 10px}.answer-text h4:first-child{margin-top:0}.answer-text code{background:#f1f4ef;padding:2px 5px;border-radius:4px;font-size:.9em}.citation-link{display:inline!important;color:#33684d!important;background:#edf4eb!important;border-radius:4px;padding:0 4px!important;margin:0 2px;font-size:11px!important;vertical-align:baseline}.source-card{border:1px solid #e2e8df;border-radius:10px;background:#fafbf8;margin-bottom:10px;overflow:hidden;scroll-margin:20px}.source-card.selected{border-color:#779b84;box-shadow:0 0 0 2px #e9f1e8}.source-card summary{display:flex;gap:9px;padding:13px;cursor:pointer;list-style:none}.source-card summary::-webkit-details-marker{display:none}.source-number{background:#e6ede3;color:#41664c;border-radius:5px;min-width:23px;height:23px;text-align:center;font-size:12px;line-height:23px}.source-title{min-width:0;flex:1}.source-title strong{display:block;font-size:12px;line-height:1.6;overflow-wrap:anywhere}.source-title small{display:block;color:#7c877b;font-size:10px;overflow-wrap:anywhere;margin-top:3px}.expand-icon{color:#83917e}.source-preview{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-size:12px;color:#798375;line-height:1.8;margin:7px 0 0}.source-card:not([open])>.source-preview{display:-webkit-box}.source-card[open] .source-preview{display:none}.source-full{padding:0 13px 13px}.source-full small{font-size:10px;color:#688062}.source-full p{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;line-height:1.9;margin:8px 0 0}.no-sources{border-top:1px solid #edf0e9;padding-top:12px}
@media(max-width:1100px){.has-sources{grid-template-columns:minmax(0,1fr)}.result-sources{border-top:1px solid #e6eae2;padding-top:16px}}
</style>
