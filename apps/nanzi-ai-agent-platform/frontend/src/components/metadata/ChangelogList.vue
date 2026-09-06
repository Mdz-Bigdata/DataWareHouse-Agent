<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { diffLines } from 'diff'
import { changelogApi } from '../../api/changelog'
import type { ChangelogResponse, ChangeDiffResponse } from '../../api/changelog'
import { useToast } from '../../composables/useToast'

interface Props {
  datasetId: number
}

const props = defineProps<Props>()
const { showToast } = useToast()

const changelogs = ref<ChangelogResponse[]>([])
const loading = ref(false)
const showDiffModal = ref(false)
const selectedChange = ref<ChangelogResponse | null>(null)
const diffData = ref<ChangeDiffResponse | null>(null)
const loadingDiff = ref(false)

// 分页
const currentPage = ref(1)
const pageSize = 20
const total = ref(0)

const totalPages = computed(() => Math.ceil(total.value / pageSize))

const fetchChangelogs = async () => {
  loading.value = true
  try {
    const offset = (currentPage.value - 1) * pageSize
    const response = await changelogApi.getDatasetChangelog(props.datasetId, {
      limit: pageSize,
      offset
    })
    changelogs.value = response.data
    total.value = response.data.length
  } catch (error) {
    console.error('Failed to fetch changelogs:', error)
    showToast('获取变更日志失败', 'error')
  } finally {
    loading.value = false
  }
}

const viewChangeDiff = async (changelog: ChangelogResponse) => {
  selectedChange.value = changelog
  loadingDiff.value = true
  
  try {
    const response = await changelogApi.getChangeDiff(changelog.id)
    diffData.value = response.data
    showDiffModal.value = true
  } catch (error) {
    console.error('Failed to fetch change diff:', error)
    showToast('获取变更详情失败', 'error')
  } finally {
    loadingDiff.value = false
  }
}

/** 计算两个值之间的 diff 行列表 */
const computeDiff = (oldVal: any, newVal: any) => {
  const toStr = (v: any) => {
    if (v === null || v === undefined) return ''
    if (typeof v === 'object') return JSON.stringify(v, null, 2)
    return String(v)
  }
  return diffLines(toStr(oldVal), toStr(newVal))
}

const formatDate = (dateString: string) => {
  return new Date(dateString).toLocaleString('zh-CN')
}

const getOperationColor = (operation: string) => {
  switch (operation) {
    case 'create':
      return 'bg-green-100 text-green-800 border-green-200'
    case 'update':
      return 'bg-blue-100 text-blue-800 border-blue-200'
    case 'delete':
      return 'bg-red-100 text-red-800 border-red-200'
    default:
      return 'bg-gray-100 text-gray-800 border-gray-200'
  }
}

const getOperationText = (operation: string) => {
  switch (operation) {
    case 'create': return '创建'
    case 'update': return '更新'
    case 'delete': return '删除'
    default: return operation
  }
}

const getResourceTypeText = (resourceType: string) => {
  switch (resourceType) {
    case 'dataset': return '数据集'
    case 'table': return '表'
    case 'column': return '字段'
    case 'metric': return '指标'
    case 'relationship': return '关系'
    default: return resourceType
  }
}

const handlePageChange = (page: number) => {
  currentPage.value = page
  fetchChangelogs()
}

onMounted(() => {
  fetchChangelogs()
})
</script>

<template>
  <div class="space-y-4">
    <!-- 头部信息 -->
    <div class="bg-white rounded-xl border border-gray-200 p-4">
      <h3 class="text-lg font-semibold text-gray-900 mb-2">变更历史</h3>
      <p class="text-sm text-gray-600">
        记录了该数据集及其所有子对象（表、字段、指标、关系）的变更历史
      </p>
    </div>

    <!-- 加载状态 -->
    <div v-if="loading" class="flex justify-center py-12">
      <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
    </div>

    <!-- 变更日志列表 -->
    <div v-else-if="changelogs.length > 0" class="space-y-3">
      <div 
        v-for="log in changelogs" 
        :key="log.id"
        class="bg-white rounded-lg border border-gray-200 p-4 hover:shadow-md transition-all cursor-pointer"
        @click="viewChangeDiff(log)"
      >
        <div class="flex items-start justify-between">
          <div class="flex-1 space-y-2">
            <!-- 操作信息 -->
            <div class="flex items-center gap-3">
              <span 
                :class="[
                  'px-2 py-1 rounded-full text-xs font-medium border',
                  getOperationColor(log.operation)
                ]"
              >
                {{ getOperationText(log.operation) }}
              </span>
              <span class="text-sm text-gray-600">
                {{ getResourceTypeText(log.resource_type) }}
              </span>
              <span class="text-sm font-medium text-gray-900">
                {{ log.resource_id }}
              </span>
            </div>

            <!-- 用户和时间 -->
            <div class="flex items-center gap-4 text-xs text-gray-500">
              <span>操作人: {{ log.user_name || '未知用户' }}</span>
              <span>{{ formatDate(log.created_at) }}</span>
            </div>

            <!-- 变更原因 -->
            <div v-if="log.reason" class="text-xs text-gray-600 bg-gray-50 p-2 rounded border">
              <strong>原因:</strong> {{ log.reason }}
            </div>
          </div>

          <!-- 右侧箭头 -->
          <div class="ml-4">
            <svg class="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
            </svg>
          </div>
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-else class="bg-white rounded-lg border border-gray-200 p-8 text-center">
      <div class="text-4xl mb-4 text-gray-300">📋</div>
      <h3 class="text-lg font-medium text-gray-900 mb-2">暂无变更记录</h3>
      <p class="text-sm text-gray-600">该数据集还没有任何变更历史记录</p>
    </div>

    <!-- 分页 -->
    <div v-if="totalPages > 1" class="flex justify-center items-center gap-2 mt-6">
      <button
        @click="handlePageChange(currentPage - 1)"
        :disabled="currentPage <= 1"
        class="px-3 py-1 border border-gray-300 rounded-md text-sm disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-50"
      >
        上一页
      </button>
      
      <span class="text-sm text-gray-600">
        第 {{ currentPage }} 页，共 {{ totalPages }} 页
      </span>
      
      <button
        @click="handlePageChange(currentPage + 1)"
        :disabled="currentPage >= totalPages"
        class="px-3 py-1 border border-gray-300 rounded-md text-sm disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-50"
      >
        下一页
      </button>
    </div>

    <!-- 变更详情弹窗 -->
    <div v-if="showDiffModal && selectedChange" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div class="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[85vh] overflow-hidden flex flex-col border border-gray-200">
        <!-- 头部 -->
        <div class="p-6 border-b border-gray-200 flex justify-between items-center bg-gray-50">
          <div>
            <h2 class="text-xl font-bold text-gray-900">变更详情</h2>
            <p class="text-sm text-gray-600 mt-1">{{ diffData?.summary }}</p>
          </div>
          <button 
            @click="showDiffModal = false" 
            class="text-gray-400 hover:text-gray-600 transition-colors"
          >
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <!-- 内容 -->
        <div class="flex-1 overflow-auto p-6">
          <div v-if="loadingDiff" class="flex justify-center items-center py-12">
            <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
          </div>
          
          <div v-else-if="diffData" class="space-y-6">
            <!-- 变更信息 -->
            <div class="bg-gray-50 p-4 rounded-lg border">
              <h3 class="text-sm font-semibold text-gray-900 mb-3">基本信息</h3>
              <div class="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <span class="text-gray-600">操作类型:</span>
                  <span :class="[
                    'ml-2 px-2 py-1 rounded text-xs font-medium',
                    getOperationColor(selectedChange.operation)
                  ]">
                    {{ getOperationText(selectedChange.operation) }}
                  </span>
                </div>
                <div>
                  <span class="text-gray-600">操作人:</span>
                  <span class="ml-2 font-medium">{{ selectedChange.user_name || '未知用户' }}</span>
                </div>
                <div>
                  <span class="text-gray-600">操作时间:</span>
                  <span class="ml-2">{{ formatDate(selectedChange.created_at) }}</span>
                </div>
                <div v-if="selectedChange.reason">
                  <span class="text-gray-600">变更原因:</span>
                  <span class="ml-2">{{ selectedChange.reason }}</span>
                </div>
              </div>
            </div>

            <!-- 变更对比 -->
            <div class="bg-gray-50 p-4 rounded-lg border">
              <h3 class="text-sm font-semibold text-gray-900 mb-3">变更对比</h3>
              <div class="space-y-4">
                <div 
                  v-for="change in diffData.changes" 
                  :key="change.field"
                  class="bg-white rounded border border-gray-200 overflow-hidden"
                >
                  <div class="text-xs font-semibold text-gray-700 bg-gray-100 px-3 py-2 border-b border-gray-200">
                    {{ change.field }}
                  </div>
                  <!-- Diff 视图 -->
                  <div class="font-mono text-xs overflow-x-auto">
                    <template v-if="change.old_value !== null || change.new_value !== null">
                      <div
                        v-for="(part, idx) in computeDiff(change.old_value, change.new_value)"
                        :key="idx"
                        :class="[
                          'px-3 py-0.5 whitespace-pre-wrap break-all',
                          part.added   ? 'bg-green-50 text-green-800' :
                          part.removed ? 'bg-red-50   text-red-800'   :
                                         'bg-white    text-gray-700'
                        ]"
                      >
                        <span class="select-none mr-1 opacity-50">
                          {{ part.added ? '+' : part.removed ? '-' : ' ' }}
                        </span>{{ part.value }}</div>
                    </template>
                    <div v-else class="px-3 py-2 text-gray-400">(无数据)</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 底部 -->
        <div class="p-4 border-t border-gray-200 bg-gray-50 flex justify-end">
          <button 
            @click="showDiffModal = false"
            class="px-4 py-2 bg-white border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.line-through {
  text-decoration: line-through;
  opacity: 0.6;
}
</style>
