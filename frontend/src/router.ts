import { createRouter, createWebHistory } from 'vue-router'
import ProjectHome from './views/ProjectHome.vue'
import Workbench from './views/Workbench.vue'
import RunDetail from './views/RunDetail.vue'
import TraceView from './views/TraceView.vue'
import EvaluationsView from './views/EvaluationsView.vue'
import LoginView from './views/LoginView.vue'
import InvitationView from './views/InvitationView.vue'
import PasswordView from './views/PasswordView.vue'
import AdminView from './views/AdminView.vue'
import PlatformView from './views/PlatformView.vue'
import PlatformLoginView from './views/PlatformLoginView.vue'
import PlatformInvitationView from './views/PlatformInvitationView.vue'
import RegistrationView from './views/RegistrationView.vue'
import EmailVerificationView from './views/EmailVerificationView.vue'
import ProfileView from './views/ProfileView.vue'
import SpacesView from './views/SpacesView.vue'
import MemberImportView from './views/MemberImportView.vue'
import ChatView from './views/ChatView.vue'
import RagInspectView from './views/RagInspectView.vue'
import ProjectDocumentsView from './views/ProjectDocumentsView.vue'
import ProjectDetailView from './views/ProjectDetailView.vue'
import KnowledgeDocumentView from './views/KnowledgeDocumentView.vue'
import { useAuthStore } from './auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
    { path: '/register', name: 'register', component: RegistrationView, meta: { public: true } },
    { path: '/verify-email', name: 'verify-email', component: EmailVerificationView, meta: { public: true } },
    { path: '/platform/login', name: 'platform-login', component: PlatformLoginView, meta: { public: true } },
    { path: '/accept-invitation', name: 'invitation', component: InvitationView, meta: { public: true } },
    { path: '/accept-platform-invitation', name: 'platform-invitation', component: PlatformInvitationView, meta: { public: true } },
    { path: '/forgot-password', name: 'forgot', component: PasswordView, meta: { public: true } },
    { path: '/reset-password', name: 'reset', component: PasswordView, meta: { public: true } },
    { path: '/', redirect: '/app' },
    { path: '/app', name: 'home', component: ProjectHome, meta: { customer: true } },
    { path: '/app/chat', name: 'chat', component: ChatView, meta: { customer: true } },
    { path: '/app/rag-check', name: 'rag-check', component: RagInspectView, meta: { customer: true } },
    { path: '/app/projects/:id/documents', component: ProjectDocumentsView, meta: { customer: true } },
    { path: '/app/projects/:id', component: ProjectDetailView, meta: { customer: true } },
    { path: '/app/knowledge/:id', component: KnowledgeDocumentView, meta: { customer: true } },
    { path: '/app/spaces', component: SpacesView, meta: { customer: true } },
    { path: '/app/profile', component: ProfileView, meta: { customer: true } },
    { path: '/app/company/member-imports', component: MemberImportView, meta: { customer: true, companyAdmin: true } },
    { path: '/app/workbench', name: 'workbench', component: Workbench, meta: { customer: true } },
    { path: '/app/runs/:id', name: 'run', component: RunDetail, meta: { customer: true } },
    { path: '/app/runs/:id/trace', name: 'trace', component: TraceView, meta: { customer: true } },
    { path: '/app/company', name: 'company-settings', component: AdminView, meta: { customer: true, companyAdmin: true } },
    { path: '/platform', name: 'platform', component: PlatformView, meta: { platform: true } },
    { path: '/platform/evaluations', name: 'evaluations', component: EvaluationsView, meta: { platform: true } },
  ],
})

router.beforeEach(async to => {
  const auth = useAuthStore()
  if (!auth.loaded) await auth.load()
  if (to.meta.public) {
    if (auth.user && (to.name === 'login' || to.name === 'platform-login')) return auth.isPlatform ? '/platform' : '/app'
    return true
  }
  if (!auth.user) return { name: to.meta.platform ? 'platform-login' : 'login', query: { redirect: to.fullPath } }
  if (to.meta.platform && !auth.isPlatform) return '/app'
  if (to.meta.customer && auth.isPlatform) return '/platform'
  if (to.meta.companyAdmin && !auth.isCompanyAdmin) return '/app'
  return true
})

export default router
