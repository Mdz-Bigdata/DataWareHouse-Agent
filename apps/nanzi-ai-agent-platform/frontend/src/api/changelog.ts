import axios from '../utils/axios'

export interface ChangelogResponse {
  id: number
  resource_type: string
  resource_id: string
  operation: string
  old_data?: any
  new_data?: any
  changed_fields?: string[]
  user_id?: number
  user_name?: string
  reason?: string
  created_at: string
}

export interface ChangeDiffResponse {
  operation: string
  resource_name: string
  changes: Array<{
    field: string
    old_value: any
    new_value: any
  }>
  summary: string
}

export interface ChangelogQueryParams {
  resource_type?: string
  operation?: string
  user_id?: number
  user_name?: string
  start_date?: string
  end_date?: string
  limit?: number
  offset?: number
}

export const changelogApi = {
  // 获取数据集的变更历史
  getDatasetChangelog: (datasetId: number, params?: ChangelogQueryParams) => {
    return axios.get(`/api/portal/changelog/datasets/${datasetId}`, { params })
  },

  // 根据条件查询变更日志
  getChangelog: (params?: ChangelogQueryParams) => {
    return axios.get('/api/portal/changelog', { params })
  },

  // 获取指定资源的变更历史
  getResourceChangelog: (resourceType: string, resourceId: string, params?: ChangelogQueryParams) => {
    return axios.get(`/api/portal/changelog/${resourceType}/${resourceId}`, { params })
  },

  // 获取变更详情对比
  getChangeDiff: (changelogId: number) => {
    return axios.get(`/api/portal/changelog/${changelogId}/diff`)
  },

  // 获取变更统计
  getChangelogStats: (days: number = 30) => {
    return axios.get('/api/portal/changelog/stats', { params: { days } })
  }
}
