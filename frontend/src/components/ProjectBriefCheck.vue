<script setup lang="ts">
import { ref, watch } from 'vue'
import { api, writeHeaders } from '../api'
const props = defineProps<{draft: Record<string, unknown>}>()
const report = ref<any>(null), busy = ref(false), error = ref('')
const labels: Record<string,string> = {name:'项目名称',customer_name:'客户名称',customer_contact:'客户联系人',contact_email:'联系邮箱',employee_count:'成员人数',target_go_live_date:'上线日期',departments:'部门',requirements_text:'实施需求',company_id:'公司',assisting_company_id:'协助公司'}
watch(() => props.draft, () => { report.value = null }, {deep:true})
async function check() {
  busy.value = true; error.value = ''
  const captured = JSON.stringify(props.draft)
  try {
    const result = await api('/api/project-briefs/check', {method:'POST',headers:writeHeaders(),body:JSON.stringify({draft:JSON.parse(captured)})})
    if (captured === JSON.stringify(props.draft)) report.value = result
  }
  catch(e:any) { error.value=e.message }
  finally { busy.value=false }
}
</script>
<template>
  <section aria-label="项目材料检查">
    <button type="button" class="secondary" :disabled="busy" @click="check">{{busy ? '检查中…' : '检查材料完整性'}}</button>
    <p v-if="error" role="alert">{{error}}</p>
    <template v-if="report">
      <p role="status">{{report.complete ? '必填材料完整，正式提交时仍会重新校验。' : '材料仍需完善，请补充或修正以下内容。'}}</p>
      <ul v-if="!report.complete"><li v-for="field in report.missing_fields" :key="field">请补充{{labels[field] || '对应项目资料'}}</li><li v-for="(issue,index) in report.issues" :key="index">{{labels[issue.field.split('.')[0]] || '项目资料'}}的格式或范围需要调整</li></ul>
    </template>
  </section>
</template>
