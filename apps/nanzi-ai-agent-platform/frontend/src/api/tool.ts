import axios from '@/utils/axios'

export interface SysApiTool {
  id: string
  name: string
  description?: string
  method: string
  url_template: string
  headers?: Record<string, string>
  parameter_schema?: Record<string, any>
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface SysApiToolCreate {
  name: string
  description?: string
  method: string
  url_template: string
  headers?: Record<string, string>
  parameter_schema?: Record<string, any>
  is_active?: boolean
}

export interface SysApiToolUpdate {
  name?: string
  description?: string
  method?: string
  url_template?: string
  headers?: Record<string, string>
  parameter_schema?: Record<string, any>
  is_active?: boolean
}

export const toolApi = {
  list: () => {
    return axios.get<SysApiTool[]>('/api/portal/tools')
  },
  
  create: (data: SysApiToolCreate) => {
    return axios.post<SysApiTool>('/api/portal/tools', data)
  },
  
  update: (id: string, data: SysApiToolUpdate) => {
    return axios.put<SysApiTool>(`/api/portal/tools/${id}`, data)
  },
  
  delete: (id: string) => {
    return axios.delete(`/api/portal/tools/${id}`)
  }
}
