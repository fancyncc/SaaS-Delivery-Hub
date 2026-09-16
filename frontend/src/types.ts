export type Approval = {
  id: string
  run_id: string
  kind: string
  status: 'pending' | 'approved' | 'rejected' | 'expired' | 'cancelled'
  version: number
  payload: Record<string, unknown>
  comment: string
  decided_by?: string
  decided_at?: string
  created_at: string
}

export type ProjectDocument = {
  name: string; customer_name: string; customer_contact: string; contact_email: string
  company_id?: string; assisting_company_id?: string | null
  employee_count: number; target_go_live_date: string; departments: string[]
  requirements_text: string; required_modules?: string[]; industry: string
  contact_phone: string; consultant_name: string; migration_scope: string
  acceptance_criteria: string; notes: string
}

export type Project = {
  id: string; name: string; customer_name: string; status: string; created_at: string
  lifecycle_status: 'draft' | 'ready' | 'in_progress' | 'blocked' | 'completed' | 'cancelled' | 'archived'
  execution_status: null | 'pending' | 'running' | 'preparing_materials' | 'waiting_approval' | 'blocked' | 'succeeded' | 'failed' | 'cancelled'
  version: number
  document: ProjectDocument | null
  latest_run: null | { id: string; run_number: number; retry_of_run_id?: string; status: string; version: number; current_node: string; updated_at: string }
  latest_approval: null | { id: string; kind: string; status: string; version: number; comment: string; decided_by?: string }
  can_start: boolean
  my_project_role: string; access_source: string; permissions: string[]
}

export type AgentRun = {
  id: string; project_id: string; status: string; current_node: string
  run_number: number; retry_of_run_id?: string; version: number
  state: Record<string, any>; trace_id: string
  allowed_actions: string[]; blocking_reason?: string
}

export type AgentStep = {
  id: string; sequence: number; node: string; status: string; detail: Record<string, any>; created_at: string
}
