<script setup lang="ts">
import { onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { useRouter } from 'vue-router'
import { useAuthStore } from './auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
function spaceChanged(event: StorageEvent) {
  if (event.key === 'saas-space-changed' && auth.user && !auth.isPlatform) window.location.assign('/app')
}
onMounted(() => window.addEventListener('storage', spaceChanged))
onBeforeUnmount(() => window.removeEventListener('storage', spaceChanged))
async function logout() { await auth.logout(); await router.push('/login') }
</script>

<template>
  <div class="app-shell" :class="{ 'chat-shell': route.name === 'chat' }">
    <header class="app-header">
      <router-link class="brand" :to="auth.isPlatform ? '/platform' : '/app'">
        <span class="brand-mark">D</span>
        <span><small>SAAS DELIVERY HUB</small><strong>SaaS 交付中枢</strong></span>
      </router-link>
      <div class="header-context">
        <span class="system-dot"></span><span>系统运行正常</span>
        <span v-if="route.name === 'run'" class="context-divider">实施执行空间</span>
        <template v-if="auth.user">
          <router-link v-if="!auth.isPlatform" class="header-link" to="/app">项目管理</router-link>
          <router-link v-if="!auth.isPlatform" class="header-link" to="/app/chat">AI 助手</router-link>
          <router-link v-if="!auth.isPlatform" class="header-link" to="/app/workbench">待办与知识库</router-link>
          <router-link v-if="!auth.isPlatform" class="header-link" to="/app/rag-check">RAG 查验</router-link>
          <router-link v-if="!auth.isPlatform" class="header-link" to="/app/spaces">空间与账号</router-link>
          <router-link v-if="!auth.isPlatform" class="header-link" to="/app/profile">个人主页</router-link>
          <router-link v-if="auth.isCompanyAdmin" class="header-link" to="/app/company">公司设置</router-link>
          <router-link v-if="auth.isPlatform" class="header-link" to="/platform">平台后台</router-link>
          <span>{{auth.user.display_name}} · {{auth.isPlatform ? '平台人员' : auth.user.tenant_name}}</span>
          <button class="header-button" @click="logout">退出</button>
        </template>
      </div>
    </header>
    <router-view :key="auth.user?.tenant_id || 'public'" />
  </div>
</template>
