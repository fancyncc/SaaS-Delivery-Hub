import { defineStore } from 'pinia'
import { api, writeHeaders } from './api'

export interface CurrentUser {
  id: string; username: string; email: string | null; phone: string | null; phone_verified: boolean; display_name: string; tenant_id: string | null; tenant_name: string | null
  account_type: 'platform' | 'customer'; session_context: 'platform' | 'customer'
  role: 'tenant_admin' | 'tenant_member' | 'platform_admin'; is_platform_admin: boolean
  company_role_code: 'company_admin' | 'company_member' | ''; platform_roles: string[]
  workspace_kind: 'personal' | 'company'; email_verified: boolean; company_creation_requires_verified_email: boolean
  memberships: { tenant_id: string; tenant_name: string; kind: 'personal' | 'company'; role: string; company_role_code: string }[]
}

export const useAuthStore = defineStore('auth', {
  state: () => ({ user: null as CurrentUser | null, loaded: false }),
  getters: {
    isPlatform: state => state.user?.session_context === 'platform',
    isCompanyAdmin: state => state.user?.session_context === 'customer' && state.user?.workspace_kind === 'company' && state.user?.company_role_code === 'company_admin',
    canCreateProject: state => state.user?.session_context === 'customer' && state.user?.company_role_code === 'company_admin',
  },
  actions: {
    async switchSpace(id: string) {
      await api(`/api/auth/spaces/${id}/switch`, { method: 'POST', headers: writeHeaders() })
      localStorage.setItem('saas-space-changed', `${id}:${Date.now()}`)
      // A full navigation closes SSE and discards all old-space component state.
      window.location.assign('/app')
    },
    async load() {
      try { this.user = await api<CurrentUser>('/api/auth/me') } catch { this.user = null }
      this.loaded = true
      return this.user
    },
    async login(username: string, password: string) {
      await api('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) })
      await this.load()
    },
    async platformLogin(email: string, password: string) {
      await api('/api/auth/platform/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) })
      await this.load()
    },
    async logout() {
      await api('/api/auth/logout', { method: 'POST', headers: writeHeaders() })
      this.user = null
    },
  },
})
