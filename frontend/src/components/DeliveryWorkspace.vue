<script setup lang="ts">
import { computed, ref } from 'vue'
const props = defineProps<{state:any; delivery:any; gantt:any}>()
const section = ref('requirements'), memberQuery = ref('')
const tabs = computed(()=>[
  {id:'requirements', label:'需求与差距', count:props.state.requirements?.length || 0},
  {id:'plan', label:'实施计划', count:props.gantt.rows.length},
  {id:'configuration', label:'配置方案', count:props.state.configuration_changes?.length || 0},
  {id:'members', label:'成员与导入', count:props.delivery.members.length},
])
const checks = computed(()=>props.state.acceptance_report?.checks || [])
const passed = computed(()=>checks.value.filter((c:any)=>c.passed).length)
const members = computed(()=>props.delivery.members.filter((m:any)=>[m.name,m.email,m.department,m.role].join(' ').toLowerCase().includes(memberQuery.value.toLowerCase())))
// Consolidate identical text for readability without changing persisted requirements.
const requirements = computed(()=>{
  const groups = new Map<string,any>()
  for (const item of props.state.requirements || []) {
    const existing = groups.get(item.statement)
    if (existing) { existing.count++; existing.categories=[...new Set([...existing.categories,item.category])] }
    else groups.set(item.statement,{...item,count:1,categories:[item.category]})
  }
  return [...groups.values()]
})
const gaps = computed(()=>{
  const groups = new Map<string,any>()
  for (const item of props.state.gap_items || []) {
    const key=JSON.stringify([item.requirement,item.fit,item.recommendation,item.capability])
    const existing=groups.get(key)
    if(existing) existing.evidence_ids=[...new Set([...existing.evidence_ids,...item.evidence_ids])]
    else groups.set(key,{...item,evidence_ids:[...item.evidence_ids]})
  }
  return [...groups.values()]
})
const labels:Record<string,string> = {supported:'已支持',partial:'部分支持',gap:'存在差距',human_review:'待人工确认',high:'高优先级',medium:'中优先级',low:'低优先级',admin:'管理员',manager:'项目经理',member:'成员',viewer:'只读成员',departments:'部门',roles:'业务角色','workflow.statuses':'状态流','notifications.due_date':'到期提醒',templates:'项目模板',custom_fields:'自定义字段',name:'工作空间',completed:'已完成',partial_failed:'部分失败',valid:'校验通过',invalid:'校验未通过'}
const display = (value:any):string => value==null?'未设置':typeof value==='boolean'?(value?'已开启':'已关闭'):Array.isArray(value)?(value.join('、') || '未设置'):typeof value==='object'?JSON.stringify(value):String(value)
</script>

<template>
  <div class="delivery-workspace">
    <div class="delivery-metrics" aria-label="实施交付概览">
      <div><span>需求条目</span><strong>{{state.requirements?.length || 0}}<small>项</small></strong></div>
      <div><span>实际成员</span><strong>{{delivery.members.length}}<small>人</small></strong></div>
      <div><span>交付文件</span><strong>{{delivery.artifacts.length}}<small>份</small></strong></div>
      <div><span>上线检查</span><strong>{{checks.length ? `${passed} / ${checks.length}` : '未执行'}}<small v-if="checks.length">通过</small></strong></div>
    </div>
    <nav class="delivery-nav" aria-label="实施材料分区"><button v-for="tab in tabs" :key="tab.id" :aria-pressed="section===tab.id" :class="{active:section===tab.id}" @click="section=tab.id">{{tab.label}}<span>{{tab.count}}</span></button></nav>
    <section class="material-content" aria-live="polite">
      <template v-if="section==='requirements'">
        <div class="content-heading"><h3>客户需求与能力差距</h3><span>保留客户原始诉求，逐项确认产品能力</span></div>
        <div v-if="!state.requirements?.length" class="delivery-empty"><strong>需求尚未提取</strong><p>Agent 完成需求分析后，具体需求和差距会显示在这里。</p></div>
        <div v-else class="requirement-grid"><article v-for="(r,i) in requirements" :key="i" class="requirement-card"><div><span class="item-index">{{String(Number(i)+1).padStart(2,'0')}}</span><span class="subtle-badge">{{r.count>1?`${r.count} 条相同表达已合并展示`:(labels[r.priority] || r.priority)}}</span></div><p>{{r.statement}}</p></article></div>
        <div v-if="gaps.length" class="gap-list"><article v-for="(g,i) in gaps" :key="i"><span class="subtle-badge" :class="{'warning-badge':g.fit!=='supported'}">{{labels[g.fit] || g.fit}}</span><div><strong>{{g.requirement}}</strong><p>{{g.recommendation}}</p><small v-if="g.evidence_ids?.length">证据：{{g.evidence_ids.join(' · ')}}</small></div></article></div>
      </template>
      <template v-if="section==='plan'">
        <div class="content-heading"><h3>实施计划与甘特图</h3><span>{{gantt.rows.length ? `按依赖安排 · 共 ${gantt.total} 天` : '等待计划生成'}}</span></div>
        <div v-if="!gantt.rows.length" class="delivery-empty"><strong>暂无实施计划</strong><p>完成需求分析后生成计划，批准后推进后续实施。</p></div>
        <article v-for="m in gantt.rows" :key="m.name" class="milestone"><div><strong>{{m.name}}</strong><span>{{m.days}} 天 · {{m.owner_role}}</span></div><div class="gantt-track"><span :style="{marginLeft:`${m.start/gantt.total*100}%`,width:`${m.days/gantt.total*100}%`}"></span></div><small>第 {{m.start+1}}—{{m.end}} 天 · 依赖：{{m.dependencies.join('、') || '无'}}</small></article>
      </template>
      <template v-if="section==='configuration'">
        <div class="content-heading"><h3>配置差异与实际配置</h3><span>待审批方案与目标系统数据分开展示</span></div>
        <div v-if="!state.configuration_changes?.length" class="delivery-empty"><strong>暂无配置差异</strong><p>配置方案生成后，会展示变更前后的具体值。</p></div>
        <div v-else class="table-scroll"><table><thead><tr><th>配置项</th><th>变更前</th><th>拟变更为</th><th>风险</th></tr></thead><tbody><tr v-for="c in state.configuration_changes" :key="c.path"><td>{{labels[c.path] || c.path}}</td><td>{{display(c.old_value)}}</td><td>{{display(c.new_value)}}</td><td><span class="subtle-badge" :class="{'warning-badge':c.risk==='high'}">{{c.risk==='high'?'高风险':c.risk==='medium'?'中风险':'低风险'}}</span></td></tr></tbody></table></div>
        <details v-if="delivery.configuration" class="config-snapshot"><summary>查看当前实际配置</summary><dl><div v-for="(value,key) in delivery.configuration" :key="key"><dt>{{labels[key] || key}}</dt><dd>{{display(value)}}</dd></div></dl></details>
      </template>
      <template v-if="section==='members'">
        <div class="content-heading"><h3>实际成员与导入记录</h3><span>仅展示已持久化的业务成员</span></div>
        <input v-if="delivery.members.length" v-model="memberQuery" aria-label="搜索实际成员" placeholder="搜索姓名、邮箱、部门或角色" class="member-search">
        <div v-if="!delivery.members.length" class="delivery-empty"><strong>暂无已导入成员</strong><p>上传 CSV 并经审批执行后，成员将显示在这里；未导入不代表导入成功。</p></div>
        <div v-else class="table-scroll member-table"><table><thead><tr><th>姓名</th><th>邮箱</th><th>部门</th><th>角色</th></tr></thead><tbody><tr v-for="m in members" :key="m.email"><td>{{m.name}}</td><td>{{m.email}}</td><td>{{m.department}}</td><td>{{labels[m.role] || m.role}}</td></tr></tbody></table><p v-if="!members.length" class="delivery-empty">没有匹配的成员。</p></div>
        <article v-for="j in delivery.imports" :key="j.id" class="import-record"><div><strong>{{labels[j.status] || j.status}}</strong><small>{{j.result.successful ?? 0}} 成功 · {{j.result.failed ?? 0}} 失败</small></div><a :href="`/api/imports/${j.id}/errors.csv`">下载错误报告 ↗</a></article>
      </template>
    </section>
    <div class="delivery-bottom">
      <section class="output-section"><div class="content-heading"><h3>交付物</h3><span>{{delivery.artifacts.length}} 份版本文件</span></div>
        <div v-if="!delivery.artifacts.length" class="delivery-empty"><span class="empty-symbol" aria-hidden="true">▤</span><strong>交付物正在准备中</strong><p>计划、培训材料和验收报告生成后，可在此预览和下载。</p></div>
        <article v-for="a in delivery.artifacts" :key="a.id" class="artifact-card"><span class="file-mark" aria-hidden="true">MD</span><div><strong>{{a.title}} <small>v{{a.version}}</small></strong><span class="file-meta">版本化交付文档 · Markdown / JSON</span><div class="file-actions"><a :href="`/api/artifacts/${a.id}?preview=true`" target="_blank" rel="noopener">预览 ↗</a><a :href="`/api/artifacts/${a.id}`">下载</a><a :href="`/api/artifacts/${a.id}?format=json`">JSON</a></div></div></article>
      </section>
      <section class="check-section"><div class="content-heading"><h3>上线检查</h3><span class="subtle-badge" :class="{'warning-badge':checks.length && passed<checks.length}">{{!checks.length?'待执行':passed===checks.length?'检查项全部通过':`${checks.length-passed} 项待处理`}}</span></div>
        <div v-if="!checks.length" class="delivery-empty"><span class="empty-symbol" aria-hidden="true">◎</span><strong>尚未执行上线检查</strong><p>配置、导入和培训节点完成后，读取实际数据生成检查结果。</p></div>
        <template v-else><div class="check-progress" aria-hidden="true"><span :style="{width:`${passed/checks.length*100}%`}"></span></div><article v-for="c in checks" :key="c.name" class="check-row"><span class="check-icon" :class="{failed:!c.passed}" aria-hidden="true">{{c.passed?'✓':'!'}}</span><div><strong>{{c.name}}</strong><p>{{c.details}}</p></div><small>{{c.passed?'通过':'待处理'}}</small></article><p class="check-note">检查通过不等于验收批准，最终以独立审批人的决定为准。</p></template>
        <div v-if="delivery.remediation?.length" class="remediation-list"><h4>整改任务</h4><p v-for="t in delivery.remediation" :key="t.id"><span class="subtle-badge" :class="{'warning-badge':t.status!=='resolved'}">{{t.status==='resolved'?'已解决':'待整改'}}</span> {{t.title}}<small>{{t.evidence.details}}</small></p></div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.delivery-workspace{font-size:14px;line-height:1.65;min-width:0}.delivery-workspace h3{font-size:16px;margin:0;color:#183d33}.delivery-workspace a{color:#286950;text-decoration:none;font-weight:600;font-size:12px}.delivery-workspace a:hover{text-decoration:underline}.delivery-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:20px 0 24px}.delivery-metrics>div{background:#f5f8f5;border:1px solid #e6ece7;padding:15px 18px;border-radius:10px}.delivery-metrics span{display:block;color:#728279;font-size:12px}.delivery-metrics strong{display:block;font-size:25px;font-weight:600;margin-top:3px}.delivery-metrics small{font-size:11px;font-weight:400;color:#7b8b82;margin-left:7px}.delivery-nav{display:flex;gap:6px;border-bottom:1px solid #e0e7e1;overflow:auto}.delivery-nav button{flex-shrink:0;background:none;border:0;border-bottom:2px solid transparent;padding:12px 16px;color:#738279;font-size:13px;display:flex;align-items:center;gap:8px}.delivery-nav button.active{color:#205b46;border-bottom-color:#2c765a;font-weight:700;background:#f3f8f4}.delivery-nav button span{font-size:10px;background:#eaf0eb;border-radius:5px;padding:0 5px}.delivery-nav button:focus-visible,a:focus-visible{outline:2px solid #dd6d36;outline-offset:2px}.material-content{padding:22px 0 26px;min-height:180px}.content-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:17px}.content-heading>span{font-size:11px;color:#859088}.delivery-empty{text-align:center;background:#fafbf9;border:1px dashed #dce5dd;border-radius:10px;padding:25px 18px;color:#8a968f;font-size:12px}.delivery-empty strong{display:block;font-size:13px;color:#62756a;font-weight:500}.delivery-empty p{margin:6px auto 0;max-width:340px}.empty-symbol{display:block;font-size:28px;margin-bottom:8px;color:#a3b5a8}.requirement-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.requirement-card{padding:16px;border:1px solid #e4eae4;border-radius:9px}.requirement-card>div{display:flex;justify-content:space-between}.requirement-card p{margin:12px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}.item-index{font-size:12px;color:#83998b;font-weight:700}.subtle-badge{display:inline-block;padding:2px 8px;border-radius:5px;font-size:10px;background:#edf4ef;color:#527663;white-space:nowrap}.warning-badge{background:#fff4e3;color:#9b742a}.gap-list{margin-top:18px}.gap-list article{display:flex;align-items:flex-start;gap:12px;padding:13px 0;border-top:1px solid #edf0ec}.gap-list strong{font-size:12px;font-weight:500}.gap-list p,.gap-list small{font-size:12px;color:#7a897f;margin:4px 0}.milestone{padding:14px 0;border-top:1px solid #eef1ec}.milestone>div:first-child{display:flex;justify-content:space-between;gap:12px}.milestone span,.milestone small{font-size:11px;color:#7c8a81}.gantt-track{height:10px;background:#eef3ee;border-radius:5px;margin:12px 0;overflow:hidden}.gantt-track>span{display:block;height:100%;background:#60937a;border-radius:5px}.table-scroll{overflow:auto;max-width:100%}.table-scroll table{min-width:500px}.table-scroll td{font-size:12px;max-width:300px;overflow-wrap:anywhere}.member-table{max-height:330px}.member-search{max-width:340px;margin-bottom:14px;font-size:12px}.config-snapshot{margin-top:20px;font-size:12px}.config-snapshot summary{cursor:pointer;color:#4a715c;padding:10px;background:#f6f8f4;border-radius:6px}.config-snapshot dl>div{display:grid;grid-template-columns:120px minmax(0,1fr);padding:8px;border-bottom:1px solid #edf0ec}.config-snapshot dt{color:#8b968f}.config-snapshot dd{margin:0;overflow-wrap:anywhere}.import-record{display:flex;align-items:center;justify-content:space-between;padding:14px 0;border-bottom:1px solid #e7ede6}.import-record small{display:block;color:#8a968d;font-size:11px}.delivery-bottom{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:28px;border-top:1px solid #e0e7e1;padding-top:24px}.delivery-bottom>section{min-width:0}.artifact-card{display:flex;gap:12px;border:1px solid #e4eae3;border-radius:9px;padding:13px 14px;margin:9px 0;background:#fff}.file-mark{display:grid;place-items:center;background:#f0f5ef;color:#668769;font-size:10px;font-weight:700;width:36px;height:42px;border-radius:6px;flex-shrink:0}.artifact-card strong{font-size:13px;font-weight:600}.artifact-card strong small{font-size:10px;font-weight:400;color:#8d9b91;margin-left:5px}.file-meta{display:block;font-size:10px;color:#91a093;margin-top:2px}.file-actions{display:flex;gap:18px;margin-top:7px}.check-progress{height:5px;background:#f3eee4;margin-bottom:15px;border-radius:4px;overflow:hidden}.check-progress span{display:block;height:100%;background:#77a88d}.check-row{display:flex;gap:11px;align-items:flex-start;padding:13px 0;border-bottom:1px solid #edf1eb}.check-icon{display:grid;place-items:center;flex-shrink:0;width:22px;height:22px;background:#eaf4ed;color:#438365;border-radius:50%;font-size:12px}.check-icon.failed{color:#ad7725;background:#fff0d6}.check-row>div{flex:1;min-width:0}.check-row strong{font-size:12px;font-weight:600}.check-row p{margin:4px 0 0;color:#8b968e;font-size:12px;overflow-wrap:anywhere}.check-row>small{font-size:10px;color:#83938a;white-space:nowrap}.check-note{color:#94a096;font-size:11px;margin-top:14px}.remediation-list{margin-top:20px}.remediation-list h4{font-size:13px}.remediation-list p{font-size:12px}.remediation-list small{display:block;color:#859288;margin-top:4px}@media(max-width:760px){.delivery-bottom,.requirement-grid{grid-template-columns:1fr}.delivery-metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.delivery-metrics>div{padding:12px}.delivery-nav button{padding:10px;font-size:12px}.content-heading{align-items:flex-start;flex-direction:column;gap:5px}.milestone>div:first-child{flex-direction:column;gap:4px}.delivery-bottom{gap:24px}}
</style>
