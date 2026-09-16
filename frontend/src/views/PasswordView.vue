<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
const route=useRoute(),router=useRouter(),email=ref(''),password=ref(''),error=ref(''),message=ref(''),preview=ref('')
const resetting=Boolean(route.query.token)
async function submit(){error.value='';message.value='';try{if(resetting){await api('/api/auth/password/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:String(route.query.token),password:password.value})});message.value='密码已重置，请使用新密码登录。';setTimeout(()=>router.push('/login'),900)}else{const data=await api<any>('/api/auth/password/forgot',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:email.value})});message.value=data.message;preview.value=data.preview_url||''}}catch(e:any){error.value=e.message}}
</script>
<template><main class="auth-page"><section class="auth-card"><h1>{{resetting?'设置新密码':'找回密码'}}</h1><p>{{resetting?'新密码保存后，所有旧会话都会失效。':'无论邮箱是否存在，系统都会返回相同提示。'}}</p><form @submit.prevent="submit"><label v-if="!resetting"><span>邮箱</span><input v-model.trim="email" type="email" required></label><label v-else><span>新密码</span><input v-model="password" type="password" minlength="10" required></label><p v-if="error" class="alert alert-danger">{{error}}</p><p v-if="message" class="alert alert-success">{{message}}</p><a v-if="preview" :href="preview">本地开发：打开重置链接</a><button class="primary">提交</button></form></section></main></template>
