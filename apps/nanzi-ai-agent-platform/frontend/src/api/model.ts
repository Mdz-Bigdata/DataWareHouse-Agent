import axios from '@/utils/axios'

export interface AIModel {
  id: string
  name: string
  model_id: string
  provider: string
  type: string
  api_base_url?: string
  has_api_key?: boolean
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface AIModelCreate {
  name: string
  model_id: string
  provider: string
  type: string
  api_base_url?: string
  api_key?: string
  is_active?: boolean
}

export interface AIModelUpdate {
  name?: string
  model_id?: string
  provider?: string
  type?: string
  api_base_url?: string
  api_key?: string
  is_active?: boolean
}

export const modelApi = {
  list: (type?: string) => {
    return axios.get<AIModel[]>('/api/portal/models', { params: { type } })
  },
  
  create: (data: AIModelCreate) => {
    return axios.post<AIModel>('/api/portal/models', data)
  },
  
  update: (id: string, data: AIModelUpdate) => {
    return axios.put<AIModel>(`/api/portal/models/${id}`, data)
  },
  
  delete: (id: string) => {
    return axios.delete(`/api/portal/models/${id}`)
  },

  testConnection: (id: string) => {
    return axios.post<{ status: string; message: string; response?: string }>(`/api/portal/models/${id}/test`)
  }
}
