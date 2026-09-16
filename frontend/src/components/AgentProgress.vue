<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, writeHeaders } from '../api'

const props = defineProps<{run: any}>()
const emit = defineEmits<{refresh: []}>()
const reason = ref(''), error = ref(''), busy = ref(false)
const agent = computed(() => props.run.state.agent)
const stages: Record<string,string> = {discovery:'调研与方案', configuration:'配置方案', migration:'配置执行与导入准备', acceptance:'导入、培训与验收', closure:'项目交付'}
const outcomes: Record<string,string> = {pass:'通过', retry:'重试', replan:'重新规划', blocked:'等待处理'}
const tools: Record<string,string> = {create_project:'确认项目', collect_requirements:'提取需求', retrieve_product_knowledge:'检索产品知识', gap_analysis:'分析能力差距', generate_implementation_plan:'生成实施计划', inspect_tenant_configuration:'读取当前配置', generate_configuration_changes:'生成配置方案', apply_configuration:'执行已审批配置', validate_import_files:'校验导入材料', execute_import:'执行已审批导入', generate_training_materials:'生成培训材料', run_go_live_checks:'检查上线条件', close_project:'完成交付', knowledge_search:'补充知识检索'}
async function resume() {
  busy.value = true; error.value = ''
  try {
    await api(`/api/runs/${props.run.id}/resume`, {method:'POST', headers:writeHeaders(), body:JSON.stringify({expected_version:props.run.version, reason:reason.value})})
    reason.value = ''; emit('refresh')
  } catch(e:any) {error.value = e.message}
  finally {busy.value = false}
}
</script>

<template>
  <section v-if="run.state.engine_version === 'v2' && agent" class="panel agent-progress">
    <h2>实施进度 · {{stages[agent.stage] || '准备中'}}</h2>
    <p>第 {{agent.rounds}} 轮 · 方案版本 {{agent.plan_version}} · 重规划 {{agent.replans}} 次</p>
    <p v-if="agent.evaluation"><strong>{{outcomes[agent.evaluation.outcome]}}</strong> · {{agent.evaluation.reason}}</p>
    <ol v-if="agent.plan">
      <li v-for="m in agent.plan.milestones" :key="m.id">
        <strong>{{agent.completed.includes(m.id) ? '✓ 已完成' : '待执行'}} · {{tools[m.tool] || m.title}}</strong>
        <small v-if="m.dependencies.length">前置步骤：{{m.dependencies.map((id:string) => tools[agent.plan.milestones.find((x:any) => x.id === id)?.tool] || id).join('、')}}</small>
      </li>
    </ol>
    <details v-if="agent.observations?.length"><summary>最近执行结果</summary>
      <article v-for="o in agent.observations" :key="o.action_id"><strong>{{tools[o.tool] || o.tool}}</strong> · {{o.status === 'succeeded' ? '完成' : o.status === 'paused' ? '等待处理' : '未通过'}}<pre>{{JSON.stringify(o.result,null,2)}}</pre></article>
    </details>
    <details v-if="agent.knowledge?.length"><summary>本轮参考知识（{{agent.knowledge.length}}）</summary>
      <article v-for="k in agent.knowledge" :key="k.id"><strong>{{k.title}}</strong> · 版本 {{k.version || '演示'}}<p>{{k.heading}}</p><blockquote>{{k.text}}</blockquote><small>来源：{{k.source || '内置演示知识'}} · 引用 {{k.id}}</small></article>
    </details>
    <details v-if="agent.memories?.length"><summary>已验证的经验建议</summary><p>经验用于辅助处理问题，产品能力仍需知识证据确认。</p>
      <article v-for="m in agent.memories" :key="m.id"><p>{{m.advice}}</p><router-link :to="`/app/runs/${m.source_run_id}`">查看经验来源</router-link></article>
    </details>
    <small>累计模型 token 预留：{{agent.tokens_reserved}}（保守预算，非实际账单）</small>
    <form v-if="run.allowed_actions?.includes('resume')" @submit.prevent="resume">
      <label>处理说明<input v-model="reason" minlength="3" maxlength="500" required placeholder="说明已补充的材料或修复的问题"></label>
      <button class="primary" :disabled="busy">{{busy ? '恢复中…' : '从当前进度恢复'}}</button>
    </form>
    <p v-if="error" class="alert alert-danger">{{error}}</p>
  </section>
</template>

<style scoped>
.agent-progress li {padding: .45rem 0}
.agent-progress small {display:block; margin:.4rem 0; color:#64748b}
.agent-progress details {margin:1rem 0}
.agent-progress article {padding:.6rem 0; border-bottom:1px solid #e2e8f0}
.agent-progress pre,.agent-progress blockquote {white-space:pre-wrap; overflow-wrap:anywhere; max-height:18rem; overflow:auto}
.agent-progress form {display:flex; gap:1rem; align-items:end; flex-wrap:wrap; margin-top:1rem}
</style>
