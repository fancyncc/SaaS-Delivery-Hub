<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, writeHeaders } from '../api'

const props = defineProps<{ projectId?: string | null; conversationId?: string; projects: { id: string; name: string }[]; candidatesEnabled: boolean }>()
type Item = { id: string; scope: string; category: string; key: string; content: string; version: number }
type Candidate = { id: string; content: string; version: number; conflicts: Item[]; duplicates: Item[] }
const items = ref<Item[]>([]), candidates = ref<Candidate[]>([])
const preference = ref({ auto_extract: false, version: 0 })
const form = ref({ scope: 'workspace', category: 'preference', key: '', content: '', project: props.projectId || '' })
const busy = ref(false), error = ref('')
const labels: Record<string, string> = { user: '跨空间通用偏好', workspace: '当前空间', project: '指定项目', conversation: '当前对话' }
async function refresh() {
  items.value = await api<Item[]>('/api/chat/memory-items')
  if (props.candidatesEnabled) {
    candidates.value = await api<Candidate[]>('/api/chat/memory-candidates')
    preference.value = await api('/api/chat/memory-preferences')
  }
}
async function act(fn: () => Promise<unknown>) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await fn(); await refresh() } catch (e) { error.value = e instanceof Error ? e.message : '操作失败' }
  finally { busy.value = false }
}
function add() {
  void act(async () => {
    await api('/api/chat/memory-items', { method: 'POST', headers: writeHeaders(), body: JSON.stringify({
      scope: form.value.scope, category: form.value.category, key: form.value.key, content: form.value.content,
      scope_id: form.value.scope === 'project' ? form.value.project : form.value.scope === 'conversation' ? props.conversationId : null,
    }) })
    form.value.key = ''; form.value.content = ''
  })
}
function edit(item: Item) {
  void act(() => api(`/api/chat/memory-items/${item.id}`, { method: 'PATCH', headers: writeHeaders(), body: JSON.stringify({ expected_version: item.version, content: item.content }) }))
}
function remove(item: Item) {
  void act(() => api(`/api/chat/memory-items/${item.id}?expected_version=${item.version}`, { method: 'DELETE', headers: writeHeaders() }))
}
function optIn(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  void act(() => api('/api/chat/memory-preferences', { method: 'PUT', headers: writeHeaders(), body: JSON.stringify({ expected_version: preference.value.version, auto_extract: checked }) }))
}
function decide(candidate: Candidate, accept: boolean) {
  const conflict = candidate.conflicts[0]
  void act(() => api(`/api/chat/memory-candidates/${candidate.id}/${accept ? 'accept' : 'reject'}`, {
    method: 'POST', headers: writeHeaders(), body: JSON.stringify({ expected_version: candidate.version,
      replace_id: accept ? conflict?.id : undefined, replace_version: accept ? conflict?.version : undefined }),
  }))
}
onMounted(() => act(async () => {}))
</script>

<template>
  <section class="scoped-memory">
    <h3>分作用域记忆</h3>
    <p>所有条目仅你可见。选择项目或对话只限制适用范围，不会分享给团队。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <article v-for="item in items.filter(i => i.category !== 'legacy')" :key="item.id">
      <strong>{{ item.key }}</strong> · {{ labels[item.scope] }}
      <textarea v-model="item.content" :disabled="busy" maxlength="4000" :aria-label="`${item.key}的内容`" />
      <button type="button" :disabled="busy" @click="edit(item)">保存修改</button>
      <button type="button" :disabled="busy" @click="remove(item)">删除条目</button>
    </article>
    <form @submit.prevent="add">
      <label>适用范围<select v-model="form.scope" :disabled="busy"><option v-for="(label, scope) in labels" :key="scope" :value="scope" :disabled="scope === 'conversation' && !conversationId">{{ label }}</option></select></label>
      <p v-if="form.scope === 'user'">此条通用偏好将在你加入的各个空间中使用。</p>
      <label v-if="form.scope === 'project'">项目<select v-model="form.project" required :disabled="busy"><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option></select></label>
      <label>类别<select v-model="form.category" :disabled="busy"><option value="preference">偏好</option><option value="background" :disabled="form.scope === 'user'">个人背景</option><option value="constraint" :disabled="form.scope === 'user'">已确认约束</option></select></label>
      <label>主题<input v-model="form.key" required maxlength="100" placeholder="例如：回复语言" :disabled="busy" /></label>
      <label>内容<textarea v-model="form.content" required maxlength="4000" :disabled="busy" /></label>
      <button :disabled="busy">确认保存条目</button>
    </form>
    <template v-if="candidatesEnabled">
      <label><input type="checkbox" :checked="preference.auto_extract" :disabled="busy" @change="optIn" />从明确的个人陈述生成待确认候选</label>
      <p>候选不会自动成为长期记忆；确认后保存在当前空间，仅你可见。</p>
      <article v-for="candidate in candidates" :key="candidate.id">
        <blockquote>{{ candidate.content }}</blockquote>
        <p v-if="candidate.conflicts.length">将替换：{{ candidate.conflicts[0].content }}</p>
        <p v-if="candidate.duplicates.length">已存在相同内容，将合并。</p>
        <button type="button" :disabled="busy" @click="decide(candidate, true)">{{ candidate.conflicts.length ? '确认替换并保存' : '确认保存' }}</button>
        <button type="button" :disabled="busy" @click="decide(candidate, false)">拒绝</button>
      </article>
    </template>
  </section>
</template>

<style scoped>
.scoped-memory{border-bottom:1px solid #dce3d6;padding-bottom:18px;margin-bottom:18px}.scoped-memory article{padding:12px 0;border-top:1px solid #eee}.scoped-memory label{display:block;margin:10px 0}.scoped-memory select,.scoped-memory textarea,.scoped-memory input:not([type=checkbox]){display:block;width:100%;box-sizing:border-box}.scoped-memory button{margin:4px 6px 4px 0}.scoped-memory blockquote{margin:8px 0;white-space:pre-wrap}
</style>
