<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../auth'
import RetrievalAnswer from '../components/RetrievalAnswer.vue'
import ScopedMemory from '../components/ScopedMemory.vue'
import { api, streamApi, writeHeaders } from '../api'

type Citation = { id: string; title: string; source: string; text: string; number?: number }
type Message = { request_id: string; question: string; answer: string; mode: string; citations: Citation[]; steps?: { stage: string; summary?: string; query?: string; source_count?: number; round?: number; action?: string }[] }
type Conversation = { next_before?: number | null; message_count?: number; id: string; title: string; project_id: string | null; version: number; archived: boolean; pinned: boolean; unread: boolean; messages: Message[]; documents: { id: string; name: string; characters: number }[] }
type History = { version: number; archived: boolean; pinned: boolean; unread: boolean; id: string; title: string; project_id: string | null; message_count: number; preview: string; updated_at: string | null }
const conversations = ref<History[]>([])
const features = ref({ memory_items: false, memory_candidates: false })
const route = useRoute(), router = useRouter(), auth = useAuthStore()
const search = ref(''), historyLoading = ref(false), sidebarOpen = ref(false)
const archiveView = ref(false)
const projectFilter = ref<string | null>(null)
const manageDialog = ref<HTMLDialogElement | null>(null)
const managementTarget = ref<{ id: string; title: string; version: number; archived: boolean; pinned: boolean; unread: boolean } | null>(null)
const managementMode = ref<'menu' | 'rename' | 'delete'>('menu')
const renameTitle = ref(''), managementError = ref('')
const memoryDialog = ref<HTMLDialogElement | null>(null)
let searchTimer: ReturnType<typeof setTimeout> | undefined
let historyRequest = 0
const scopeName = (id: string | null) => projects.value.find(p => p.id === id)?.name || (id ? '项目资料' : '当前空间')
const historyGroups = computed(() => {
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const yesterday = new Date(today); yesterday.setDate(yesterday.getDate() - 1)
  const week = new Date(today); week.setDate(week.getDate() - 7)
  const groups: Record<string, History[]> = {}
  for (const c of conversations.value) {
    if (projectFilter.value !== null && c.project_id !== projectFilter.value) continue
    const date = c.updated_at ? new Date(c.updated_at).getTime() : 0
    const label = c.pinned ? '置顶' : search.value.trim() ? '搜索结果' : date >= +today ? '今天' : date >= +yesterday ? '昨天' : date >= +week ? '最近 7 天' : '历史对话'
    ;(groups[label] ||= []).push(c)
  }
  return Object.entries(groups).sort(([a], [b]) => a === '置顶' ? -1 : b === '置顶' ? 1 : 0)
})
watch([search, archiveView], () => { clearTimeout(searchTimer); historyRequest++; searchTimer = setTimeout(() => { void refreshList().catch(e => { error.value = e.message }) }, 250) })
onBeforeUnmount(() => { clearTimeout(searchTimer); historyRequest++ })
const projects = ref<{ id: string; name: string }[]>([])
const active = ref<Conversation | null>(null)
const projectId = ref('')
const question = ref('')
const busy = ref(false)
const streamingAnswer = ref(''), streamingQuestion = ref(''), executionStep = ref('')
const error = ref('')
const status = ref('')
const mode = ref('retrieval')
const assistant = ref<{ name: string; version: string; knowledge_topics: string[]; tools: string[] } | null>(null)
const memory = ref({ content: '', version: 0 })
const useMemory = ref(true)
const memoryOpen = ref(false)
const messagesElement = ref<HTMLElement | null>(null)
let pendingRequest: { question: string; id: string } | null = null

async function refreshList() {
  const request = ++historyRequest
  historyLoading.value = true
  try {
  const result = await api<{ conversations: typeof conversations.value; mode: string; assistant: NonNullable<typeof assistant.value>; features?: typeof features.value }>(`/api/chat/conversations?q=${encodeURIComponent(search.value.trim())}&archived=${archiveView.value}`)
  if (request !== historyRequest) return
  conversations.value = result.conversations
  mode.value = result.mode
  assistant.value = result.assistant
  features.value = result.features || features.value
  } finally { if (request === historyRequest) historyLoading.value = false }
}
async function scrollBottom() {
  await nextTick()
  messagesElement.value?.scrollTo({ top: messagesElement.value.scrollHeight, behavior: 'smooth' })
}
async function perform(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''; status.value = ''
  try { await action() } catch (e) { error.value = e instanceof Error ? e.message : '操作失败，请重试' }
  finally { busy.value = false }
}
function create() {
  active.value = null; question.value = ''; pendingRequest = null; sidebarOpen.value = false
  archiveView.value = false; search.value = ''
  error.value = ''; status.value = ''
  void router.push({ path: '/app/chat' })
}
async function openConversation(id: string) {
  let loaded = await api<Conversation>(`/api/chat/conversations/${id}`)
  if (loaded.unread) {
    loaded = await api<Conversation>(`/api/chat/conversations/${id}`, { method: 'PATCH', headers: writeHeaders(), body: JSON.stringify({ expected_version: loaded.version, unread: false }) })
    await refreshList()
  }
  active.value = loaded; projectId.value = loaded.project_id || ''; archiveView.value = loaded.archived
  question.value = ''; pendingRequest = null; sidebarOpen.value = false
  await scrollBottom()
}
async function select(id: string) {
  await perform(async () => {
    await openConversation(id)
    await router.push({ query: { conversation: id } })
  })
}
function openMemory() { memoryOpen.value = true; sidebarOpen.value = false; memoryDialog.value?.showModal() }
watch(() => route.query.conversation, (id) => {
  if (busy.value || id === active.value?.id) return
  if (typeof id === 'string') void perform(() => openConversation(id))
  else { active.value = null; question.value = ''; pendingRequest = null }
})
async function send() {
  if (busy.value || active.value?.archived || !question.value.trim()) return
  await perform(async () => {
    const text = question.value.trim()
    if (pendingRequest?.question !== text) pendingRequest = { question: text, id: crypto.randomUUID() }
    const requestId = pendingRequest.id
    question.value = ''
    streamingQuestion.value = text; streamingAnswer.value = ''; executionStep.value = '正在理解问题'
    try {
      if (!active.value) active.value = await api<Conversation>('/api/chat/conversations', { method: 'POST', headers: writeHeaders(), body: JSON.stringify({ project_id: projectId.value || null }) })
      const id = active.value.id
      await router.replace({ query: { conversation: id } })
      active.value = await streamApi<Conversation>(`/api/chat/conversations/${id}/messages`, { method: 'POST', headers: writeHeaders(), body: JSON.stringify({ question: text, expected_version: active.value.version, request_id: pendingRequest!.id, use_memory: useMemory.value, stream: true }) }, (kind, data) => {
        if (kind === 'delta') streamingAnswer.value += data.text
        if (kind === 'step') executionStep.value = data.summary || (data.stage === 'observe' ? `已取得 ${data.source_count} 条来源` : data.action === 'rewrite' ? `检索问题：${data.query}` : '问题判断完成')
        void scrollBottom()
      })
    } catch (e) {
      if (active.value) active.value = await api<Conversation>(`/api/chat/conversations/${active.value.id}`).catch(() => active.value)
      if (active.value?.messages.some(message => message.request_id === requestId)) pendingRequest = null
      else question.value = text
      throw e
    } finally {
      streamingQuestion.value = ''; streamingAnswer.value = ''; executionStep.value = ''
    }
    pendingRequest = null
    await refreshList(); await scrollBottom()
  })
}
async function loadOlder() {
  await perform(async () => {
    if (!active.value?.next_before) return
    const previousHeight = messagesElement.value?.scrollHeight || 0
    const page = await api<{ messages: Message[]; next_before: number | null }>(`/api/chat/conversations/${active.value.id}/messages?before=${active.value.next_before}`)
    active.value.messages = [...page.messages, ...active.value.messages]
    active.value.next_before = page.next_before
    await nextTick()
    if (messagesElement.value) messagesElement.value.scrollTop += messagesElement.value.scrollHeight - previousHeight
  })
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  await perform(async () => {
    if (file.size > 2 * 1024 * 1024) throw new Error('文件不能超过 2 MB')
    if (!active.value) active.value = await api<Conversation>('/api/chat/conversations', { method: 'POST', headers: writeHeaders(), body: JSON.stringify({ project_id: projectId.value || null }) })
    const data = new FormData(); data.append('file', file)
    const headers = new Headers(writeHeaders()); headers.delete('Content-Type')
    active.value = await api<Conversation>(`/api/chat/conversations/${active.value.id}/documents?expected_version=${active.value.version}`, { method: 'POST', headers, body: data })
    await router.replace({ query: { conversation: active.value.id } })
    status.value = '文档已解析，可以开始提问。'; await refreshList()
  })
  input.value = ''
}
async function removeDocument(id: string) {
  await perform(async () => {
    if (!active.value) return
    active.value = await api<Conversation>(`/api/chat/conversations/${active.value.id}/documents/${id}?expected_version=${active.value.version}`, { method: 'DELETE', headers: writeHeaders() })
    status.value = '附件已删除，后续回答不再检索它；历史回答仍保留。'
  })
}
function manage(c: { id: string; title: string; version: number; archived: boolean; pinned: boolean; unread: boolean }) {
  managementTarget.value = { id: c.id, title: c.title, version: c.version, archived: c.archived, pinned: c.pinned, unread: c.unread }
  renameTitle.value = c.title; managementMode.value = 'menu'; managementError.value = ''
  manageDialog.value?.showModal()
}
function removeConversation() { if (active.value) { manage(active.value); managementMode.value = 'delete' } }
async function updateConversation(changes: { title?: string; archived?: boolean; pinned?: boolean; unread?: boolean }) {
  if (busy.value || !managementTarget.value) return
  busy.value = true; managementError.value = ''; error.value = ''
  const target = managementTarget.value
  try {
    const updated = await api<Conversation>(`/api/chat/conversations/${target.id}`, { method: 'PATCH', headers: writeHeaders(), body: JSON.stringify({ ...changes, expected_version: target.version }) })
    if (active.value?.id === updated.id) active.value = updated
    manageDialog.value?.close()
    status.value = changes.pinned !== undefined ? (changes.pinned ? '已置顶。' : '已取消置顶。') : changes.unread !== undefined ? (changes.unread ? '已标记为未读。' : '已标记为已读。') : changes.title !== undefined ? '对话已重命名。' : changes.archived ? '对话已归档，可在“已归档”中查看或恢复。' : '对话已恢复，可以继续提问。'
    await refreshList()
  } catch (e) {
    managementError.value = e instanceof Error ? e.message : '操作失败'
    const current = await api<Conversation>(`/api/chat/conversations/${target.id}`).catch(() => null)
    if (current) { managementTarget.value = current; if (active.value?.id === current.id) active.value = current }
    await refreshList().catch(() => {})
  } finally { busy.value = false }
}
async function deleteConversation() {
  if (busy.value || !managementTarget.value) return
  busy.value = true; managementError.value = ''
  const id = managementTarget.value.id
  try {
    await api(`/api/chat/conversations/${id}`, { method: 'DELETE', headers: writeHeaders() })
    if (active.value?.id === id) { active.value = null; question.value = ''; pendingRequest = null; await router.replace({ path: '/app/chat' }) }
    manageDialog.value?.close(); status.value = '对话及其附件已删除。'; await refreshList()
  } catch (e) { managementError.value = e instanceof Error ? e.message : '删除失败' }
  finally { busy.value = false }
}
async function saveMemory(clear = false) {
  await perform(async () => {
    memory.value = await api('/api/chat/memory', { method: 'PUT', headers: writeHeaders(), body: JSON.stringify({ content: clear ? '' : memory.value.content, expected_version: memory.value.version }) })
    status.value = clear ? (features.value.memory_items ? '原有记忆已清空。' : '长期记忆已清空。') : '偏好已保存，可在本空间的新对话中使用。'
  })
}
onMounted(() => perform(async () => {
  await refreshList()
  projects.value = await api('/api/projects')
  if (typeof route.query.project === 'string' && projects.value.some(p => p.id === route.query.project)) projectId.value = route.query.project
  memory.value = await api('/api/chat/memory')
  if (typeof route.query.conversation === 'string') await openConversation(route.query.conversation)
}))

async function copyConversation() {
  if (!managementTarget.value || busy.value) return
  try {
    const c = await api<Conversation>(`/api/chat/conversations/${managementTarget.value.id}`)
    while (c.next_before) {
      const page = await api<{ messages: Message[]; next_before: number | null }>(`/api/chat/conversations/${c.id}/messages?before=${c.next_before}`)
      c.messages = [...page.messages, ...c.messages]; c.next_before = page.next_before
    }
    await navigator.clipboard.writeText(c.messages.map(m => `你：${m.question}\n\nAI 助手：${m.answer}`).join('\n\n'))
    status.value = '对话内容已复制。'; manageDialog.value?.close()
  } catch { managementError.value = '复制失败，请重试或手动选择内容复制。' }
}
function openConversationWindow() {
  if (managementTarget.value) window.open(router.resolve({ path: '/app/chat', query: { conversation: managementTarget.value.id } }).href, '_blank', 'noopener,noreferrer')
  manageDialog.value?.close()
}

</script>

<template>
  <main class="chat-workspace" @keydown.esc="sidebarOpen = false">
    <button v-if="sidebarOpen" class="sidebar-backdrop" aria-label="关闭侧栏" @click="sidebarOpen = false" />
    <aside class="chat-sidebar" :class="{ 'is-open': sidebarOpen }" aria-label="项目与历史对话">
      <div class="sidebar-heading"><strong>AI 助手</strong><button class="mobile-toggle" aria-label="关闭侧栏" @click="sidebarOpen = false">×</button></div>
      <button class="primary new-chat" :disabled="busy" @click="create">＋ 新建对话</button>
      <input v-model="search" class="history-search" maxlength="200" placeholder="搜索历史对话" aria-label="搜索历史对话" />
      <div class="sidebar-scroll">
        <details class="project-list" open><summary>项目</summary><button :class="{ selected: projectFilter === null }" @click="projectFilter = null">所有对话</button><div v-for="p in projects" :key="p.id" class="project-row"><button :class="{ selected: projectFilter === p.id }" @click="projectFilter = p.id">▱ {{ p.name }}</button><button :disabled="busy" :aria-label="`在 ${p.name} 中新建对话`" @click="create(); projectId = p.id; projectFilter = p.id">＋</button></div><p v-if="!projects.length" class="muted">暂无可访问的项目</p><router-link to="/app?from=chat">创建项目 ↗</router-link></details>
        <div class="history-tabs"><button :class="{ selected: !archiveView }" @click="archiveView = false">对话</button><button :class="{ selected: archiveView }" @click="archiveView = true">已归档</button></div>
        <p v-if="historyLoading" class="muted">正在查找…</p>
        <section v-for="[label, rows] in historyGroups" :key="label" class="history-group"><h3>{{ label }}</h3><div v-for="c in rows" :key="c.id" class="history-row" :class="{ selected: active?.id === c.id }"><button class="history-item" :disabled="busy" :title="c.title" @click="select(c.id)"><span><i v-if="c.unread" class="unread-dot" aria-label="未读" />{{ c.title }}</span><small>{{ scopeName(c.project_id) }} · {{ c.message_count }} 轮</small></button><button class="history-menu" :disabled="busy" :aria-label="`管理对话：${c.title}`" aria-haspopup="dialog" @click="manage(c)">⋯</button></div></section>
        <p v-if="!historyLoading && !historyGroups.length" class="muted">暂无匹配的对话</p>
      </div>
      <button class="memory-link" :disabled="busy" @click="openMemory">☷ 管理长期记忆</button>
    </aside>
    <section class="chat-main">
      <header class="chat-toolbar"><button class="mobile-toggle" aria-label="打开历史对话" @click="sidebarOpen = true">☰</button><h1>{{ active?.title || '新对话' }}</h1><select v-model="projectId" aria-label="检索范围" :disabled="busy || !!active"><option value="">当前空间</option><option v-for="p in projects" :key="p.id" :value="p.id">{{ p.name }}</option></select><button v-if="active" class="toolbar-menu" :disabled="busy" aria-label="管理当前对话" @click="manage(active)">⋯</button></header>
      <p v-if="error" class="notice danger" role="alert">{{ error }}</p><p v-if="status && !memoryOpen" class="notice" role="status">{{ status }}</p>
      <div v-if="active?.archived" class="notice">此对话已归档。<button :disabled="busy" @click="manage(active); updateConversation({ archived: false })">恢复对话</button></div>
      <div ref="messagesElement" class="chat-messages" aria-live="polite">
        <div v-if="!active?.messages.length && !streamingQuestion" class="chat-empty"><span class="empty-mark">✳</span><h2>有什么可以帮你？</h2><p>提问、了解项目进展，或从资料中寻找答案。</p><div class="suggestions"><button :disabled="busy" @click="question = '我的项目当前进行到哪一步？'">查看项目进展 ↗</button><button :disabled="busy" @click="question = '总结我上传的文档，列出关键要求'">总结文档要点 ↗</button></div></div>
        <div class="conversation-content"><button v-if="active?.next_before" :disabled="busy" @click="loadOlder">加载更早的消息</button><article v-for="message in active?.messages || []" :key="message.request_id" class="chat-turn"><div class="chat-question">{{ message.question }}</div><div class="answer-label">✳ AI 助手</div><RetrievalAnswer :answer="message.answer" :citations="message.citations" /><details v-if="message.steps?.length" class="execution-details"><summary>查看执行步骤</summary><p v-for="(step, i) in message.steps" :key="i">{{ step.summary || (step.stage === 'observe' ? '取得 ' + step.source_count + ' 条来源' : step.query || '正在处理问题') }}</p></details></article><article v-if="streamingQuestion" class="chat-turn"><div class="chat-question">{{ streamingQuestion }}</div><div class="answer-label">✳ AI 助手</div><p class="stream-answer">{{ streamingAnswer }}</p><p class="muted" role="status">{{ executionStep || '正在处理…' }}</p></article></div>
      </div>
      <footer class="chat-composer"><form @submit.prevent="send"><div v-if="active?.documents.length" class="chat-files"><span v-for="doc in active.documents" :key="doc.id">{{ doc.name }} <button type="button" :disabled="busy || active?.archived" :aria-label="`移除 ${doc.name}`" @click="removeDocument(doc.id)">×</button></span></div><textarea v-model="question" rows="2" maxlength="4000" required :disabled="busy || active?.archived" placeholder="向 AI 助手提问…" aria-label="输入问题" @keydown.ctrl.enter.prevent="send" @keydown.meta.enter.prevent="send" /><div class="compose-actions"><label class="upload-button">＋ 上传文档<input type="file" accept=".txt,.md,.csv,.json,.docx,.pdf" :disabled="busy || active?.archived" @change="upload" /></label><label class="memory-toggle"><input v-model="useMemory" type="checkbox" :disabled="busy" />长期记忆</label><small class="keyboard-hint">Ctrl + Enter</small><button class="primary send-button" :disabled="busy || active?.archived || !question.trim()" aria-label="发送消息">{{ busy ? '…' : '↑' }}</button></div></form><p class="composer-note">回答可能有误，请结合来源核对重要信息。</p></footer>
    </section>
    <dialog ref="manageDialog" class="memory-dialog conversation-dialog" aria-labelledby="management-heading" @cancel="busy && $event.preventDefault()"><div class="memory-dialog-heading"><h2 id="management-heading">{{ managementMode === 'rename' ? '重命名对话' : managementMode === 'delete' ? '删除对话？' : '管理对话' }}</h2><button class="icon-button" :disabled="busy" aria-label="关闭对话管理" @click="manageDialog?.close()">×</button></div><p class="managed-title">{{ managementTarget?.title }}</p><p v-if="managementError" role="alert">{{ managementError }}</p><div v-if="managementMode === 'menu'" class="conversation-actions"><button :disabled="busy" @click="managementMode = 'rename'">✎ 重命名</button><button :disabled="busy" @click="updateConversation({ pinned: !managementTarget?.pinned })">{{ managementTarget?.pinned ? '取消置顶' : '置顶' }}</button><button :disabled="busy" @click="updateConversation({ unread: !managementTarget?.unread })">{{ managementTarget?.unread ? '标记为已读' : '标记为未读' }}</button><button :disabled="busy" @click="copyConversation">复制对话内容</button><button :disabled="busy" @click="openConversationWindow">在新窗口中打开</button><button :disabled="busy" @click="updateConversation({ archived: !managementTarget?.archived })">{{ managementTarget?.archived ? '↶ 恢复对话' : '▤ 归档对话' }}</button><button class="danger-action" :disabled="busy" @click="managementMode = 'delete'">删除对话</button></div><form v-else-if="managementMode === 'rename'" @submit.prevent="updateConversation({ title: renameTitle.trim() })"><label for="conversation-title">对话名称</label><input id="conversation-title" v-model="renameTitle" :disabled="busy" required maxlength="120" /><div class="memory-dialog-actions"><button type="button" :disabled="busy" @click="manageDialog?.close()">取消</button><button class="save-memory" :disabled="busy || !renameTitle.trim()">保存名称</button></div></form><template v-else><p>这将永久删除此对话的全部消息、附件和引用记录，无法恢复。项目资料和长期记忆不会删除。</p><div class="memory-dialog-actions"><button :disabled="busy" @click="manageDialog?.close()">取消</button><button class="delete-confirm" :disabled="busy" @click="deleteConversation">确认删除</button></div></template></dialog>
    <dialog ref="memoryDialog" class="memory-dialog" @close="memoryOpen = false"><div class="memory-dialog-heading"><h2>长期记忆</h2><button class="icon-button" aria-label="关闭长期记忆" @click="memoryDialog?.close()">×</button></div><ScopedMemory v-if="features.memory_items && memoryOpen" :project-id="active?.project_id || projectId" :conversation-id="active?.id" :projects="projects" :candidates-enabled="features.memory_candidates" /><p>原有记忆：保存你的偏好和背景，仅在当前空间使用。文档不会自动保存到记忆。</p><textarea v-model="memory.content" :disabled="busy" maxlength="4000" rows="6" placeholder="例如：请用中文回答，先给结论。我是项目实施负责人。" aria-label="长期记忆"/><small>{{ memory.content.length }} / 4000</small><p v-if="status && memoryOpen" class="chat-status" role="status">{{ status }}</p><p v-if="error && memoryOpen" role="alert">{{ error }}</p><div class="memory-dialog-actions"><button :disabled="busy" @click="saveMemory(true)">{{ features.memory_items ? '清空原有记忆' : '清空记忆' }}</button><button class="save-memory" :disabled="busy" @click="saveMemory()">{{ features.memory_items ? '保存原有记忆' : '保存记忆' }}</button></div></dialog>
  </main>
</template>

<style scoped>
.chat-workspace{display:grid;grid-template-columns:270px minmax(0,1fr);height:calc(100dvh - 78px);min-height:440px;background:#fff;color:var(--ink);overflow:hidden}.chat-workspace button,.chat-workspace select,.chat-workspace input,.chat-workspace textarea{font:inherit}.chat-workspace button:disabled{opacity:.45;cursor:not-allowed}.chat-sidebar{display:flex;flex-direction:column;gap:16px;padding:22px 14px 14px;background:#f4f6f1;border-right:1px solid var(--line);min-height:0;min-width:0}.sidebar-heading{display:flex;justify-content:space-between;align-items:center;padding:0 10px;font-size:17px}.new-chat{width:100%;text-align:left}.history-search{width:100%;min-width:0;padding:10px 12px!important;font-size:12px!important}.sidebar-scroll{min-height:0;flex:1;overflow-y:auto;scrollbar-width:thin}.project-list{margin-bottom:18px}.project-list summary{padding:8px 10px;font-size:12px;color:var(--muted);cursor:pointer}.project-list button{border:0;background:none;color:var(--ink);border-radius:7px;text-align:left;padding:9px 10px;font-size:12px!important;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.project-list>button{width:100%}.project-row{display:flex;align-items:center}.project-row>button:first-child{flex:1;min-width:0}.project-row>button:last-child{flex-shrink:0}.project-list .selected,.history-row.selected{background:#e4ebe1}.history-tabs{display:flex;gap:4px;padding:3px;background:#e8ece3;border-radius:8px}.history-tabs button{flex:1;border:0;padding:6px;background:none;border-radius:6px;color:var(--ink);font-size:12px!important}.history-tabs .selected{background:#fff}.history-group h3{font-size:11px;color:var(--muted);font-weight:500;margin:18px 10px 8px}.history-row{display:flex;align-items:center;border-radius:8px;margin:3px 0}.history-row:hover{background:#e8eee4}.history-item{min-width:0;flex:1;border:0;background:none;text-align:left;padding:10px;color:var(--ink)}.history-item>span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}.history-item small{display:block;color:var(--muted);font-size:10px;margin-top:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.history-menu{border:0;background:none;color:var(--muted);padding:8px;font-size:22px!important;border-radius:6px}.unread-dot{display:inline-block;background:#2f725e;border-radius:50%;width:7px;height:7px;margin-right:6px}.memory-link{border:0;border-top:1px solid var(--line);padding:16px 8px 4px;background:none;color:var(--ink);text-align:left;font-size:13px!important}.chat-main{display:flex;flex-direction:column;min-width:0;min-height:0}.chat-toolbar{height:68px;flex-shrink:0;padding:14px 28px;display:flex;align-items:center;gap:12px;border-bottom:1px solid #edf0e9}.chat-toolbar h1{font-size:16px;margin:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.chat-toolbar select{max-width:220px;min-width:0;font-size:12px;padding:8px;border:1px solid var(--line);border-radius:7px;background:#fafbf8;color:var(--ink)}.toolbar-menu{border:0;background:none;font-size:22px!important;color:var(--ink)}.chat-messages{flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain;padding:28px 36px;scrollbar-width:thin}.conversation-content{max-width:900px;margin:auto}.chat-turn{margin-bottom:36px}.chat-question{white-space:pre-wrap;overflow-wrap:anywhere;width:fit-content;max-width:85%;margin:0 0 26px auto;background:#edf1e9;border-radius:18px 18px 4px 18px;padding:12px 18px;font-size:14px;line-height:1.8}.answer-label{font-size:13px;font-weight:600;color:#416a53}.chat-turn :deep(.has-sources){grid-template-columns:minmax(0,1fr)}.chat-turn :deep(.result-sources){padding-top:18px;border-top:1px solid var(--line)}.execution-details{font-size:12px;color:var(--muted);margin-top:18px}.execution-details summary{cursor:pointer}.stream-answer{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.9}.chat-empty{max-width:640px;margin:7vh auto 28px;text-align:center}.empty-mark{font-size:46px;color:#62836b}.chat-empty h2{font-size:28px;margin:20px 0 12px}.chat-empty p{font-size:14px;color:var(--muted)}.suggestions{display:flex;justify-content:center;gap:12px;flex-wrap:wrap;margin-top:30px}.suggestions button{padding:16px;border:1px solid var(--line);border-radius:12px;background:#fff;color:var(--ink);font-size:13px!important}.chat-composer{padding:12px 32px 8px;flex-shrink:0}.chat-composer form{max-width:900px;margin:auto;border:1px solid #dce3d6;border-radius:20px;padding:14px 18px;background:#fafbf8;box-shadow:0 4px 20px #173f3305}.chat-composer textarea{width:100%;min-height:56px;max-height:160px;resize:vertical;border:0;background:transparent;box-shadow:none;padding:0;line-height:1.8;outline:none}.compose-actions{display:flex;align-items:center;gap:16px;margin-top:8px}.upload-button{position:relative;overflow:hidden;font-size:12px;cursor:pointer;color:#3c674f}.upload-button input{position:absolute;inset:0;opacity:0;width:100%;cursor:pointer}.upload-button:focus-within{outline:2px solid var(--green)}.memory-toggle{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--muted)}.memory-toggle input{width:auto}.keyboard-hint{margin-left:auto;font-size:10px;color:var(--muted)}.send-button{border-radius:50%;width:34px;height:34px;padding:0;font-size:23px!important}.composer-note{text-align:center;font-size:10px;color:var(--muted);margin:8px 0 0}.chat-files{display:flex;gap:8px;flex-wrap:wrap;max-height:90px;overflow:auto;margin-bottom:8px}.chat-files>span{background:#edf3ee;padding:6px 10px;border-radius:6px;font-size:12px;overflow-wrap:anywhere}.chat-files button{border:0;background:none}.notice{padding:9px 18px;margin:8px 24px 0;background:#e8f1e9;border-radius:8px;font-size:12px}.danger{color:#a7443c;background:#fff1ed}.muted{color:var(--muted);font-size:12px;line-height:1.8}.memory-dialog{border:1px solid var(--line);border-radius:14px;padding:24px;width:min(460px,calc(100% - 32px));max-height:85dvh;overflow:auto;color:var(--ink);box-shadow:0 16px 70px #123a3229}.memory-dialog::backdrop{background:#123a3233}.memory-dialog-heading,.memory-dialog-actions{display:flex;align-items:center;justify-content:space-between;gap:12px}.memory-dialog h2{margin:0;font-size:19px}.memory-dialog p{line-height:1.8;font-size:13px}.memory-dialog textarea,.memory-dialog input{width:100%}.memory-dialog-actions{margin-top:18px}.memory-dialog button{padding:9px 12px;border:1px solid var(--line);border-radius:7px;background:#f7f9f5;color:var(--ink)}.memory-dialog .delete-confirm{background:#a7443c;color:#fff}.conversation-actions{display:grid;gap:4px}.conversation-actions button{text-align:left;border:0;background:none;padding:12px}.conversation-actions button:hover{background:#edf2e9}.conversation-actions .danger-action{border-top:1px solid var(--line);color:#a7443c;border-radius:0}.managed-title{overflow-wrap:anywhere;color:var(--muted)}.memory-dialog label{display:block;margin-bottom:8px}.mobile-toggle,.sidebar-backdrop{display:none}button:focus-visible,summary:focus-visible{outline:2px solid var(--green);outline-offset:2px}
@media(max-width:760px){.chat-workspace{grid-template-columns:minmax(0,1fr);height:calc(100dvh - 130px);min-height:500px}.chat-sidebar{position:fixed;inset:0 auto 0 0;z-index:41;width:min(300px,85vw);transform:translateX(-100%);visibility:hidden}.chat-sidebar.is-open{transform:translateX(0);visibility:visible}.sidebar-backdrop{display:block;position:fixed;inset:0;z-index:40;background:#123a3255;border:0}.mobile-toggle{display:block;background:none;border:0;font-size:20px!important;color:var(--ink)}.chat-toolbar{padding:12px 16px;gap:8px}.chat-toolbar select{max-width:120px}.chat-messages{padding:20px 16px}.chat-empty{margin:30px auto}.chat-empty h2{font-size:24px}.chat-composer{padding:10px 12px}.compose-actions{gap:12px}.keyboard-hint{display:none}.send-button{margin-left:auto}.chat-question{max-width:94%}}
</style>
