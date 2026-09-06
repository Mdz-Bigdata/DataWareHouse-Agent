<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { metadataApi } from '../../api/metadata'
import type { Metric } from '../../api/metadata'
import { useUser } from '../../composables/useUser'

const { isAdmin: _isAdmin, hasPermission } = useUser()

const props = defineProps<{
  datasetId: number
}>()

const emit = defineEmits(['show-smart-discovery'])

const metrics = ref<Metric[]>([])
const loading = ref(false)
const showModal = ref(false)
const saving = ref(false)
const editingId = ref<number | null>(null)
const deleteId = ref<number | null>(null)

// UI State
const error = ref('')
const modalError = ref('')

const form = ref<Metric>({
  name: '',
  display_name: '',
  description: '',
  calculation_logic: '',
  unit: ''
})

const fetchMetrics = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await metadataApi.getMetrics(props.datasetId)
    metrics.value = res.data
  } catch (e) {
    console.error(e)
    error.value = '无法加载指标列表'
  } finally {
    loading.value = false
  }
}

const openCreate = () => {
  editingId.value = null
  form.value = { name: '', display_name: '', description: '', calculation_logic: '', unit: '' }
  modalError.value = ''
  showModal.value = true
}

const openEdit = (m: Metric) => {
  editingId.value = m.id || null
  form.value = { ...m }
  modalError.value = ''
  showModal.value = true
}

const handleDelete = (id: number) => {
  deleteId.value = id
}

const confirmDelete = async () => {
  if (!deleteId.value) return
  try {
    await metadataApi.deleteMetric(deleteId.value)
    deleteId.value = null
    fetchMetrics()
  } catch (e) {
    console.error(e)
    error.value = '删除失败'
    setTimeout(() => error.value = '', 3000)
    deleteId.value = null
  }
}

const handleSave = async () => {
  modalError.value = ''
  if (!form.value.name || !form.value.display_name) {
    modalError.value = '请填写必要信息 (ID,从名称)'
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await metadataApi.updateMetric(editingId.value, form.value)
    } else {
      await metadataApi.createMetric(props.datasetId, form.value)
    }
    showModal.value = false
    fetchMetrics()
  } catch (e: any) {
    console.error(e)
    modalError.value = '保存失败: ' + (e.response?.data?.detail || e.message)
  } finally {
    saving.value = false
  }
}

watch(() => props.datasetId, fetchMetrics)
onMounted(fetchMetrics)

defineExpose({ fetchMetrics })
</script>

<template>
  <div class="space-y-4">
    <!-- Toolbar -->
    <div class="flex justify-between items-center">
      <h3 class="text-lg font-bold text-gray-800">业务指标 (Metrics)</h3>
      <div class="flex items-center gap-3" v-if="hasPermission('element:metadata:edit')">
        <button 
          @click="emit('show-smart-discovery')"
          class="bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200 px-4 py-2 rounded-lg transition-all flex items-center gap-2 text-sm font-bold shadow-sm"
        >
          <svg class="w-4 h-4 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
          ✨ 智能发现指标
        </button>
        <button 
          @click="openCreate"
          class="bg-amber-500 hover:bg-amber-600 text-white px-4 py-2 rounded-lg transition-all shadow-md flex items-center gap-2 text-sm font-bold"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
          新建指标
        </button>
      </div>
    </div>

    <!-- Error Banner -->
    <div v-if="error" class="bg-red-50 text-red-600 px-4 py-2 rounded-lg text-sm mb-4 border border-red-100 flex items-center gap-2">
      <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
      {{ error }}
    </div>

    <!-- List -->
    <div v-if="loading" class="flex justify-center py-8">
      <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-amber-500"></div>
    </div>
    
    <div v-else-if="metrics.length === 0" class="text-center py-12 bg-amber-50/50 rounded-xl border border-dashed border-amber-200">
      <p class="text-amber-600 font-medium">暂无定义的指标</p>
      <p class="text-xs text-amber-500 mt-1">添加指标以帮助 AI 理解复杂的业务计算逻辑。</p>
    </div>

    <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
       <div v-for="m in metrics" :key="m.id" class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm hover:shadow-md transition-shadow group relative">
          <div class="absolute top-4 right-4 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity" v-if="hasPermission('element:metadata:edit')">
             <button @click="openEdit(m)" class="text-gray-400 hover:text-blue-500"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg></button>
             <button @click="handleDelete(m.id!)" class="text-gray-400 hover:text-red-500"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg></button>
          </div>
          
          <div class="flex items-center gap-2 mb-2">
             <div class="w-8 h-8 rounded-lg bg-amber-100 flex items-center justify-center text-amber-600 font-bold text-xs">M</div>
             <div>
                <h4 class="font-bold text-gray-900">{{ m.display_name }}</h4>
                <p class="text-xs font-mono text-gray-500">{{ m.name }}</p>
             </div>
          </div>
          
          <div class="space-y-2 mt-3">
             <div class="text-xs bg-gray-50 p-2 rounded border border-gray-100 font-mono text-gray-600 break-all">
                {{ m.calculation_logic || '--' }}
             </div>
             <p class="text-xs text-gray-500 line-clamp-2 h-8">{{ m.description || '无描述' }}</p>
             <div v-if="m.unit" class="text-xs text-gray-400 flex items-center gap-1">
                <span>Unit:</span> <span class="bg-gray-100 px-1 rounded text-gray-600">{{ m.unit }}</span>
             </div>
          </div>
       </div>
    </div>

    <!-- Modal -->
    <div v-if="showModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showModal = false">
       <div class="bg-white rounded-xl shadow-2xl w-full max-w-lg overflow-hidden border border-gray-100 animate-fade-in-up">
          <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-amber-50">
             <h3 class="font-bold text-gray-900">{{ editingId ? '编辑指标' : '新建指标' }}</h3>
             <button @click="showModal = false" class="text-gray-400 hover:text-gray-600">&times;</button>
          </div>
          
          <!-- Modal Error -->
          <div v-if="modalError" class="px-6 pt-4 pb-0">
             <div class="bg-red-50 text-red-600 px-3 py-2 rounded text-xs border border-red-100">
                {{ modalError }}
             </div>
          </div>
          <div class="p-6 space-y-4">
             <div class="grid grid-cols-2 gap-4">
                <div>
                   <label class="block text-sm font-medium text-gray-700 mb-1">物理标识 (ID)</label>
                   <input v-model="form.name" :disabled="!!editingId" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 focus:outline-none" placeholder="e.g. pue">
                </div>
                <div>
                   <label class="block text-sm font-medium text-gray-700 mb-1">显示名称</label>
                   <input v-model="form.display_name" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 focus:outline-none" placeholder="e.g. PUE值">
                </div>
             </div>
             <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">计算逻辑</label>
                 <textarea v-model="form.calculation_logic" rows="3" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-amber-500 focus:outline-none" placeholder="e.g. total_power / it_power"></textarea>
                 <p class="text-xs text-gray-400 mt-1">支持 SQL 表达式或自然语言描述。</p>
             </div>
             <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">业务描述</label>
                <textarea v-model="form.description" rows="2" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 focus:outline-none"></textarea>
             </div>
             <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">单位</label>
                <input v-model="form.unit" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 focus:outline-none" placeholder="e.g. kWh, %">
             </div>
          </div>
          <div class="p-6 bg-gray-50 flex justify-end gap-3">
             <button @click="showModal = false" class="px-4 py-2 border border-gray-300 rounded-lg text-sm bg-white hover:bg-gray-50">取消</button>
             <button @click="handleSave" :disabled="saving" class="px-6 py-2 bg-amber-500 hover:bg-amber-600 text-white rounded-lg text-sm font-bold shadow-md disabled:opacity-50">保存</button>
          </div>
       </div>
    </div>
    <!-- Delete Modal -->
    <div v-if="deleteId" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="deleteId = null">
       <div class="bg-white rounded-xl shadow-2xl w-full max-w-sm overflow-hidden border border-gray-100 transform transition-all animate-fade-in-up">
          <div class="p-6 text-center">
             <div class="w-16 h-16 bg-red-50 rounded-full flex items-center justify-center mx-auto mb-4 border border-red-100">
                <svg class="w-8 h-8 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
             </div>
             <h3 class="text-lg font-bold text-gray-900 mb-2">确认删除?</h3>
             <p class="text-sm text-gray-500 mb-6">
               您确定要删除此指标吗？<br>此操作无法撤销。
             </p>
             <div class="flex gap-3 justify-center">
                <button @click="deleteId = null" class="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 bg-white">取消</button>
                <button @click="confirmDelete" class="px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-sm font-medium shadow-md transition-colors shadow-red-500/30">确认删除</button>
             </div>
          </div>
       </div>
    </div>
  </div>
</template>

<style scoped>
.animate-fade-in-up {
  animation: fadeInUp 0.3s ease-out;
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
