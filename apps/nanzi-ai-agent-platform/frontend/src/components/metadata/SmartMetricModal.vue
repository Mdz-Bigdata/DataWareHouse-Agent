<script setup lang="ts">
import { ref } from 'vue'
import { metadataApi } from '../../api/metadata'
import type { Metric } from '../../api/metadata'
import { useToast } from '../../composables/useToast'
import TraceLogViewer from '../TraceLogViewer.vue'

const props = defineProps<{
  show: boolean
  datasetId: number
}>()

const emit = defineEmits(['close', 'saved'])

const analyzing = ref(false)
const saving = ref(false)
const recommendations = ref<Metric[]>([])
const selectedIndices = ref<number[]>([])
const currentTraceId = ref('')
const showLogs = ref(false)

const { showToast } = useToast()

const handleRecommend = async () => {
  analyzing.value = true
  recommendations.value = []
  selectedIndices.value = []
  currentTraceId.value = ''
  
  try {
    const res = await metadataApi.recommendMetrics(props.datasetId)
    const data = res.data.data
    
    recommendations.value = (data.metrics || []).map((m: any) => ({
      ...m,
      calculation_logic: m.calculation || m.calculation_logic || ''
    }))
    currentTraceId.value = data._trace_id || ''
    
    if (recommendations.value.length === 0) {
      showToast('未发现推荐指标，请检查数据集是否包含有效表结构', 'info')
    } else {
      showToast(`AI 成功推荐了 ${recommendations.value.length} 个指标`, 'success')
      // Default select all
      selectedIndices.value = recommendations.value.map((_, i) => i)
    }
  } catch (e: any) {
    console.error('Recommendation failed', e)
    const detail = e.response?.data?.detail || ''
    const match = detail.match(/Trace ID: ([a-zA-Z0-9-]+)/)
    if (match) currentTraceId.value = match[1]
    
    showToast(e.response?.data?.detail || '推荐失败，请重试', 'error')
  } finally {
    analyzing.value = false
  }
}

const toggleSelection = (index: number) => {
  const i = selectedIndices.value.indexOf(index)
  if (i > -1) {
    selectedIndices.value.splice(i, 1)
  } else {
    selectedIndices.value.push(index)
  }
}

const handleSave = async () => {
  if (selectedIndices.value.length === 0) return
  
  saving.value = true
  let successCount = 0
  
  try {
    for (const idx of selectedIndices.value) {
      const metric = recommendations.value[idx]
      if (metric) {
        await metadataApi.createMetric(props.datasetId, metric)
        successCount++
      }
    }
    
    showToast(`成功保存 ${successCount} 个指标`, 'success')
    emit('saved')
    // Reset state before closing
    recommendations.value = []
    selectedIndices.value = []
    emit('close')
  } catch (e: any) {
    console.error('Save failed', e)
    showToast(e.response?.data?.detail || '部分指标保存失败', 'error')
  } finally {
    saving.value = false
  }
}

const handleClose = () => {
  if (analyzing.value || saving.value) return
  recommendations.value = []
  selectedIndices.value = []
  emit('close')
}
</script>

<template>
  <div v-if="show" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="handleClose">
    <div class="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col overflow-hidden animate-fade-in-up border border-gray-100">
      
      <!-- Header -->
      <div class="px-8 py-6 border-b border-gray-100 flex justify-between items-center bg-gradient-to-r from-indigo-50/50 to-blue-50/50">
        <div class="flex items-center gap-4">
          <div class="w-10 h-10 rounded-xl bg-indigo-600 flex items-center justify-center text-white shadow-lg shadow-indigo-200">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
          </div>
          <div>
            <h2 class="text-xl font-bold text-gray-900">✨ 智能指标发现</h2>
            <p class="text-xs text-gray-500 mt-0.5">利用 AI 自动分析 Schema 并推荐高价值业务逻辑</p>
          </div>
        </div>
        <button @click="handleClose" class="text-gray-400 hover:text-gray-600 transition-colors p-2 hover:bg-white rounded-full">
          <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
        </button>
      </div>

      <!-- Main Content -->
      <div class="flex-1 overflow-y-auto p-8 bg-gray-50/30">
        <!-- Initial State -->
        <div v-if="recommendations.length === 0 && !analyzing" class="h-64 flex flex-col items-center justify-center text-center">
           <div class="w-20 h-20 bg-indigo-50 rounded-full flex items-center justify-center text-indigo-500 mb-6 group cursor-pointer hover:scale-110 transition-transform" @click="handleRecommend">
              <svg class="w-10 h-10" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9l-.707.707M12 21V3"/></svg>
           </div>
           <h3 class="text-lg font-bold text-gray-800">准备好发现新指标了吗？</h3>
           <p class="text-sm text-gray-500 max-w-sm mt-2">AI 将深度分析您的数据结构，自动推断“机房列表”、“辖区占比”等常用业务逻辑。</p>
           <button @click="handleRecommend" class="mt-8 px-10 py-3 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl font-bold shadow-lg shadow-indigo-200 transition-all">
             立即开始识别
           </button>
        </div>

        <!-- Analyzing State -->
        <div v-else-if="analyzing" class="h-64 flex flex-col items-center justify-center">
           <div class="relative w-16 h-16">
              <div class="absolute inset-0 border-4 border-indigo-100 rounded-full"></div>
              <div class="absolute inset-0 border-4 border-indigo-600 rounded-full border-t-transparent animate-spin"></div>
           </div>
           <p class="mt-6 text-sm font-medium text-gray-600 animate-pulse">AI 正在深度思考业务逻辑...</p>
           <p class="text-[10px] text-gray-400 mt-2">分析表关联、字段语义与聚合潜力</p>
        </div>

        <!-- Result List -->
        <div v-else class="space-y-4">
           <div class="flex justify-between items-center mb-6">
              <h3 class="text-sm font-bold text-gray-400 uppercase tracking-widest">推荐列表 ({{ recommendations.length }})</h3>
              <div class="flex items-center gap-4">
                 <button @click="selectedIndices = recommendations.map((_, i) => i)" class="text-xs text-indigo-600 font-bold hover:underline">全选</button>
                 <button @click="selectedIndices = []" class="text-xs text-gray-400 font-bold hover:underline">取消全选</button>
              </div>
           </div>

           <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div 
                v-for="(item, idx) in recommendations" 
                :key="idx" 
                @click="toggleSelection(idx)"
                class="relative bg-white border-2 rounded-2xl p-6 transition-all cursor-pointer group hover:shadow-xl"
                :class="selectedIndices.includes(idx) ? 'border-indigo-500 bg-indigo-50/30' : 'border-transparent shadow-sm hover:border-gray-200'"
              >
                <!-- Checkbox Overlay -->
                <div class="absolute top-4 right-4">
                   <div class="w-6 h-6 rounded-full border-2 flex items-center justify-center transition-colors"
                        :class="selectedIndices.includes(idx) ? 'bg-indigo-600 border-indigo-600 text-white' : 'border-gray-200 bg-white'">
                      <svg v-if="selectedIndices.includes(idx)" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/></svg>
                   </div>
                </div>

                <div class="pr-8">
                  <div class="flex items-center gap-2 mb-2">
                    <span class="font-bold text-gray-900 group-hover:text-indigo-600 transition-colors">{{ item.display_name }}</span>
                    <span class="text-[10px] font-mono text-gray-400">#{{ item.name }}</span>
                  </div>
                  <p class="text-xs text-gray-500 line-clamp-2 h-8 leading-relaxed mb-4">{{ item.description }}</p>
                  
                  <div class="bg-gray-900/5 rounded-xl p-3 font-mono text-[10px] text-gray-600 overflow-hidden relative">
                    <div class="absolute top-0 right-0 px-2 py-0.5 bg-gray-200 text-gray-500 text-[8px] rounded-bl italic">SQL</div>
                    {{ item.calculation_logic }}
                  </div>
                </div>
              </div>
           </div>
        </div>
      </div>

      <!-- Footer -->
      <div v-if="recommendations.length > 0 && !analyzing" class="p-6 border-t border-gray-100 bg-white/80 backdrop-blur flex justify-between items-center">
         <button 
           v-if="currentTraceId"
           @click="showLogs = true"
           class="text-xs text-gray-400 hover:text-indigo-600 flex items-center gap-1.5 transition-colors"
         >
           <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
           查看 AI 思考过程
         </button>
         <div v-else></div>

         <div class="flex gap-3">
           <button @click="handleRecommend" class="px-6 py-2.5 text-sm font-medium text-gray-600 hover:bg-gray-100 rounded-xl transition-colors" :disabled="saving">
             重新发现
           </button>
           <button 
             @click="handleSave" 
             :disabled="selectedIndices.length === 0 || saving"
             class="px-8 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl font-bold shadow-lg shadow-indigo-200 transition-all flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
           >
             <svg v-if="saving" class="animate-spin h-4 w-4 text-white" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
             {{ saving ? '入库中...' : `保存选中项 (${selectedIndices.length})` }}
           </button>
         </div>
      </div>

    </div>
  </div>

  <TraceLogViewer 
    :visible="showLogs" 
    :trace-id="currentTraceId" 
    @close="showLogs = false" 
  />
</template>

<style scoped>
.animate-fade-in-up {
  animation: fadeInUp 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
