<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, writeHeaders } from '../api'
import { useAuthStore } from '../auth'
const username=ref(''), name=ref(''), password=ref(''), confirm=ref(''), error=ref(''), loading=ref(false)
const route=useRoute(), router=useRouter(), auth=useAuthStore()
const token=computed(()=>String(route.query.token||''))
async function submit(){ error.value=''; if(!auth.user && password.value!==confirm.value){error.value='两次密码不一致';return} loading.value=true; try{await api(`/api/auth/invitations/${String(route.query.token||'')}/accept`,{method:'POST',headers:writeHeaders(),body:JSON.stringify(auth.user ? {} : {username:username.value,display_name:name.value,password:password.value})});await auth.load();await router.push('/')}catch(e:any){error.value=e.message}finally{loading.value=false} }
</script>
<template><main class="auth-page"><section class="auth-card"><span class="eyebrow">INVITATION</span><h1>接受企业邀请</h1><p>已有账号请先登录后确认加入；新用户设置姓名与密码。个人空间与公司数据分别保存。邀请链接 48 小时有效且只能使用一次。</p><p v-if="!token" class="alert alert-danger">当前地址缺少邀请令牌，请向管理员索取完整的接受邀请链接。</p><router-link v-if="!auth.user" :to="{path:'/login',query:{redirect:route.fullPath}}">已有账号，登录后加入</router-link><p v-else>当前账号：{{auth.user.username}}，确认加入不会修改密码和姓名。</p><form v-if="token" @submit.prevent="submit"><template v-if="!auth.user"><label><span>账号</span><input v-model.trim="username" required minlength="3" maxlength="40" pattern="[A-Za-z][A-Za-z0-9_.-]*" autocomplete="username"></label><label><span>姓名</span><input v-model.trim="name" required minlength="2"></label><label><span>密码</span><input v-model="password" type="password" required minlength="10"><small>至少 10 位，同时包含字母和数字</small></label><label><span>确认密码</span><input v-model="confirm" type="password" required></label></template><p v-if="error" class="alert alert-danger">{{error}}</p><button class="primary" :disabled="loading">{{loading?'处理中…':'确认加入公司'}}</button></form><router-link v-if="!token" to="/login">返回登录</router-link></section></main></template>
