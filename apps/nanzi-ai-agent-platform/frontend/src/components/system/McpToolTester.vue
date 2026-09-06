<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import axios from '@/utils/axios'
import { PlayIcon, XMarkIcon, BeakerIcon } from '@heroicons/vue/24/outline'

const props = defineProps<{
  tool: any,
  isOpen: boolean
}>()

const emit = defineEmits(['close'])

const loading = ref(false)
const result = ref<string | null>(null)
const error = ref<string | null>(null)
const args = ref<Record<string, any>>({})

const schema = computed(() => {
  try {
    return JSON.parse(props.tool.parameter_schema)
  } catch {
    return {}
  }
})

const properties = computed(() => schema.value.properties || {})
const requiredFields = computed(() => schema.value.required || [])

// Initialize args
watch(() => props.tool, () => {
  args.value = {}
  result.value = null
  error.value = null
}, { immediate: true })

const executeTool = async () => {
  loading.value = true
  result.value = null
  error.value = null
  
  try {
    const res = await axios.post(`/api/portal/mcp/tools/${props.tool.id}/execute`, {
      arguments: args.value
    })
    
    if (res.data.status === 'success') {
      result.value = res.data.result
    } else {
      error.value = res.data.message || '执行失败'
    }
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div v-if="isOpen" class="fixed inset-0 z-[70] flex justify-end">
    <!-- Backdrop -->
    <div class="absolute inset-0 bg-gray-900/20 backdrop-blur-sm transition-opacity" @click="emit('close')"></div>

    <!-- Drawer -->
    <div class="relative w-full max-w-md bg-white h-full shadow-2xl flex flex-col animate-slide-in-right">
      <div class="p-4 border-b border-gray-100 flex justify-between items-center bg-gray-50/50">
        <h3 class="text-sm font-bold text-gray-800 flex items-center">
          <BeakerIcon class="w-4 h-4 mr-2 text-primary" />
          工具测试台
        </h3>
        <button @click="emit('close')" class="p-1 text-gray-400 hover:text-gray-600 rounded-md">
          <XMarkIcon class="w-5 h-5" />
        </button>
      </div>

      <div class="p-6 border-b border-gray-100">
        <h2 class="text-lg font-bold text-gray-900 mb-1">{{ tool.tool_name }}</h2>
        <p class="text-xs text-gray-500 italic">{{ tool.tool_description || '暂无描述' }}</p>
      </div>

      <div class="flex-1 overflow-y-auto p-6 space-y-6 custom-scrollbar">
        <!-- Input Form -->
        <div class="space-y-4">
          <h4 class="text-xs font-bold text-gray-500 uppercase tracking-wider">参数输入</h4>
          <div v-if="Object.keys(properties).length === 0" class="text-xs text-gray-400 italic">
            此工具无需参数
          </div>
          <div v-else class="space-y-3">
            <div v-for="(prop, key) in properties" :key="key">
              <label class="block text-xs font-medium text-gray-700 mb-1">
                {{ key }} <span v-if="requiredFields.includes(key)" class="text-red-500">*</span>
              </label>
              <input 
                v-if="prop.type === 'string' || !prop.type"
                v-model="args[key]" 
                class="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary focus:border-transparent outline-none transition-all"
                :placeholder="prop.description"
              />
              <input 
                v-else-if="prop.type === 'integer' || prop.type === 'number'"
                type="number"
                v-model.number="args[key]" 
                class="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary focus:border-transparent outline-none"
                :placeholder="prop.description"
              />
              <label v-else-if="prop.type === 'boolean'" class="flex items-center space-x-2 cursor-pointer">
                <input type="checkbox" v-model="args[key]" class="w-4 h-4 text-primary border-gray-300 rounded focus:ring-primary" />
                <span class="text-xs text-gray-500">{{ prop.description || '启用' }}</span>
              </label>
              <p v-if="prop.description" class="text-[10px] text-gray-400 mt-1">{{ prop.description }}</p>
            </div>
          </div>
        </div>

        <!-- Result Area -->
        <div v-if="result || error" class="space-y-2 animate-fade-in">
          <h4 class="text-xs font-bold text-gray-500 uppercase tracking-wider flex items-center">
            执行结果
            <span v-if="error" class="ml-2 text-[10px] text-red-500 bg-red-50 px-1.5 py-0.5 rounded">Failed</span>
            <span v-else class="ml-2 text-[10px] text-green-600 bg-green-50 px-1.5 py-0.5 rounded">Success</span>
          </h4>
          <div class="p-3 rounded-lg border text-xs font-mono whitespace-pre-wrap break-all max-h-[300px] overflow-y-auto custom-scrollbar"
            :class="error ? 'bg-red-50 border-red-100 text-red-700' : 'bg-slate-900 text-green-400 border-slate-800'"
          >
            {{ error || result }}
          </div>
        </div>
      </div>

      <div class="p-4 border-t border-gray-100 bg-gray-50 flex justify-end">
        <button 
          @click="executeTool" 
          :disabled="loading"
          class="w-full px-4 py-2 bg-primary text-white rounded-lg shadow-lg shadow-primary/20 hover:bg-primary-dark transition-all flex justify-center items-center font-bold text-sm disabled:opacity-70 disabled:cursor-not-allowed"
        >
          <svg v-if="loading" class="animate-spin h-4 w-4 mr-2 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <PlayIcon v-else class="w-4 h-4 mr-2" />
          运行测试
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.animate-slide-in-right { animation: slideInRight 0.3s ease-out; }
@keyframes slideInRight { from { transform: translateX(100%); } to { transform: translateX(0); } }
</style>
