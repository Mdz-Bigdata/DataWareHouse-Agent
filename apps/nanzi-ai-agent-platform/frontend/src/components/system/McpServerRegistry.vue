<script setup lang="ts">
import { ref, onMounted, watch, computed } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'
import { useUser } from '@/composables/useUser'
import ConfirmModal from '../../components/ConfirmModal.vue'
import McpToolTester from './McpToolTester.vue'
import { 
  PlusIcon,
  BeakerIcon,
  EyeIcon,
  EyeSlashIcon,
  CodeBracketIcon,
  ListBulletIcon,
  ArrowPathIcon,
  TrashIcon,
  PencilSquareIcon,
  LinkIcon,
  CloudArrowDownIcon,
  MagnifyingGlassIcon,
  SparklesIcon,
  ShoppingBagIcon
} from '@heroicons/vue/24/outline'

const { showToast } = useToast()
const { hasPermission } = useUser()
const canSave = hasPermission('element:system:config_save')
const servers = ref<any[]>([])
const loading = ref(false)
const showAddModal = ref(false)
const isEditing = ref(false)
const editingId = ref('')

// Tool Tester Logic
const showTester = ref(false)
const toolToTest = ref<any>(null)

const openTester = (tool: any) => {
  toolToTest.value = tool
  showTester.value = true
}

const wizardStep = ref<1 | 2>(1) // 1: Input & Verify, 2: Preview & Name
const verifying = ref(false)
const discoveredTools = ref<any[]>([])
const syncLoading = ref<Record<string, boolean>>({})

// Batch Actions Logic
const selectedToolIds = ref<Set<string>>(new Set())
const selectedServer = ref<any>(null)
const tools = ref<any[]>([])
const toolsLoading = ref(false)

const isAllSelected = computed(() => {
  return tools.value.length > 0 && selectedToolIds.value.size === tools.value.length
})

const toggleSelectAll = () => {
  if (isAllSelected.value) {
    selectedToolIds.value.clear()
  } else {
    tools.value.forEach(t => selectedToolIds.value.add(t.id))
  }
}

const toggleSelectTool = (id: string) => {
  if (selectedToolIds.value.has(id)) {
    selectedToolIds.value.delete(id)
  } else {
    selectedToolIds.value.add(id)
  }
}

const batchUpdateStatus = async (published: boolean) => {
  if (selectedToolIds.value.size === 0) return
  
  loading.value = true
  try {
    const ids = Array.from(selectedToolIds.value)
    // Batch update in parallel
    await Promise.all(ids.map(id => 
      axios.put(`/api/portal/mcp/tools/${id}/publish?published=${published}`)
    ))
    
    showToast(`成功${published ? '发布' : '下线'} ${ids.length} 个工具`, 'success')
    if (selectedServer.value) fetchTools(selectedServer.value.id)
    selectedToolIds.value.clear()
  } catch (e) {
    showToast('批量操作失败', 'error')
  } finally {
    loading.value = false
  }
}

// Headers Editing Logic
const headerMode = ref<'simple' | 'advanced'>('simple')
const headerPairs = ref<{ key: string, value: string }[]>([{ key: '', value: '' }])

const addHeaderPair = () => {
  headerPairs.value.push({ key: '', value: '' })
}
const removeHeaderPair = (index: number) => {
  headerPairs.value.splice(index, 1)
  if (headerPairs.value.length === 0) addHeaderPair()
}

const newServer = ref({
  server_name: '',
  sse_url: '',
  auth_headers: '{}',
  enabled_status: 1
})

// Sync Header Pairs to JSON string
watch(headerPairs, (newPairs) => {
  if (headerMode.value === 'simple') {
    const obj: Record<string, string> = {}
    newPairs.forEach(p => {
      if (p.key.trim()) obj[p.key.trim()] = p.value
    })
    newServer.value.auth_headers = JSON.stringify(obj, null, 2)
  }
}, { deep: true })

// Sync JSON string to Header Pairs
const syncJsonToPairs = () => {
  try {
    const obj = JSON.parse(newServer.value.auth_headers)
    const pairs = Object.entries(obj).map(([k, v]) => ({ key: k, value: String(v) }))
    headerPairs.value = pairs.length > 0 ? pairs : [{ key: '', value: '' }]
  } catch (e) {
    console.error("Invalid JSON for headers")
  }
}

const toggleHeaderMode = () => {
  if (headerMode.value === 'advanced') {
    syncJsonToPairs()
    headerMode.value = 'simple'
  } else {
    headerMode.value = 'advanced'
  }
}

const fetchServers = async () => {
  loading.value = true
  try {
    const res = await axios.get('/api/portal/mcp/servers')
    servers.value = res.data
  } catch (e) {
    showToast('获取 MCP 服务列表失败', 'error')
  } finally {
    loading.value = false
  }
}

const resetWizard = () => {
  isEditing.value = false
  editingId.value = ''
  wizardStep.value = 1
  verifying.value = false
  discoveredTools.value = []
  newServer.value = { server_name: '', sse_url: '', auth_headers: '{}', enabled_status: 1 }
  headerPairs.value = [{ key: '', value: '' }]
  headerMode.value = 'simple'
}

const openEditModal = (server: any) => {
  isEditing.value = true
  editingId.value = server.id
  wizardStep.value = 1
  newServer.value = {
    server_name: server.server_name,
    sse_url: server.sse_url,
    auth_headers: server.auth_headers || '{}',
    enabled_status: server.enabled_status
  }
  syncJsonToPairs()
  showAddModal.value = true
}

const handleVerify = async () => {
  if (!newServer.value.sse_url) {
    showToast('请输入 SSE 握手地址', 'warning')
    return
  }
  
  verifying.value = true
  try {
    const res = await axios.post('/api/portal/mcp/verify', newServer.value)
    discoveredTools.value = res.data.tools
    wizardStep.value = 2
    if (!newServer.value.server_name) {
        try {
            const url = new URL(newServer.value.sse_url)
            newServer.value.server_name = url.hostname.replace(/\./g, '-') + '-mcp'
        } catch {
            newServer.value.server_name = 'new-mcp-server'
        }
    }
    showToast('连接成功，已发现工具', 'success')
  } catch (e: any) {
    showToast(e.response?.data?.detail || '连接失败，请检查地址或认证信息', 'error')
  } finally {
    verifying.value = false
  }
}

const addServer = async () => {
  if (!newServer.value.server_name || !newServer.value.sse_url) {
    showToast('请填写完整信息', 'warning')
    return
  }
  if (headerMode.value === 'advanced') {
    try { JSON.parse(newServer.value.auth_headers) }
    catch (e) { showToast('JSON 格式错误', 'error'); return }
  }

  try {
    if (isEditing.value) {
      await axios.put(`/api/portal/mcp/servers/${editingId.value}`, newServer.value)
      showToast('更新成功', 'success')
    } else {
      await axios.post('/api/portal/mcp/servers', newServer.value)
      showToast('添加成功', 'success')
    }
    showAddModal.value = false
    fetchServers()
    resetWizard()
  } catch (e: any) {
    showToast(e.response?.data?.detail || '操作失败', 'error')
  }
}

// Deletion Logic
const showDeleteConfirm = ref(false)
const serverToDelete = ref<string | null>(null)

const confirmDeleteServer = (id: string) => {
  serverToDelete.value = id
  showDeleteConfirm.value = true
}

const executeDeleteServer = async () => {
  if (!serverToDelete.value) return
  try {
    await axios.delete(`/api/portal/mcp/servers/${serverToDelete.value}`)
    showToast('删除成功', 'success')
    showDeleteConfirm.value = false
    serverToDelete.value = null
    fetchServers()
    if (selectedServer.value?.id === serverToDelete.value) selectedServer.value = null
  } catch (e) {
    showToast('删除失败', 'error')
  }
}

const syncTools = async (id: string) => {
  if (syncLoading.value[id]) return
  
  syncLoading.value[id] = true
  try {
    await axios.post(`/api/portal/mcp/servers/${id}/sync`)
    showToast('同步成功', 'success')
    fetchServers()
    if (selectedServer.value?.id === id) {
        fetchTools(id)
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || '同步失败', 'error')
  } finally {
    syncLoading.value[id] = false
  }
}

const fetchTools = async (serverId: string) => {
  toolsLoading.value = true
  try {
    const res = await axios.get(`/api/portal/mcp/servers/${serverId}/tools`)
    tools.value = res.data
  } catch (e) {
    showToast('获取工具列表失败', 'error')
  } finally {
    toolsLoading.value = false
  }
}

const selectServer = (server: any) => {
  selectedServer.value = server
  selectedToolIds.value = new Set()
  fetchTools(server.id)
}

const togglePublish = async (tool: any) => {
  try {
    const newStatus = !tool.is_published
    await axios.put(`/api/portal/mcp/tools/${tool.id}/publish?published=${newStatus}`)
    tool.is_published = newStatus
    showToast(newStatus ? '工具已发布' : '工具已下线', 'success')
  } catch (e) {
    showToast('操作失败', 'error')
  }
}

onMounted(fetchServers)
</script>

<template>
  <div class="flex h-full gap-6">
    <!-- Left: Server List -->
    <div class="w-1/3 flex flex-col bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
      <!-- Market Guide (High Contrast) -->
      <div class="p-4 bg-slate-900 text-white border-b border-white/10">
        <div class="flex items-start justify-between">
          <div class="flex-1">
            <h4 class="text-sm font-black flex items-center text-indigo-400">
              <ShoppingBagIcon class="w-4 h-4 mr-1.5" />
              探索 MCP 市场
            </h4>
            <p class="text-[10px] text-slate-400 mt-1 leading-relaxed">
              去魔搭(ModelScope)寻找更多好用的工具集
            </p>
            <div class="mt-2">
              <a href="https://modelscope.cn/mcp" target="_blank" class="inline-flex items-center text-[10px] font-bold text-white bg-indigo-600 hover:bg-indigo-500 px-2 py-1 rounded transition-all">
                立即前往市场
                <MagnifyingGlassIcon class="w-3 h-3 ml-1" />
              </a>
            </div>
          </div>
        </div>
      </div>

      <div class="p-4 border-b border-gray-100 flex justify-between items-center bg-gray-50/80">
        <h3 class="text-xs font-bold text-gray-500 uppercase tracking-wider">
          已连接服务
        </h3>
        <button v-if="canSave" @click="resetWizard(); showAddModal = true" class="px-3 py-1.5 bg-primary text-white rounded-md transition-all flex items-center text-[11px] font-bold shadow-sm hover:bg-primary-dark">
          <PlusIcon class="w-3.5 h-3.5 mr-1" />
          添加服务
        </button>
      </div>

      <div class="flex-1 overflow-y-auto custom-scrollbar">
        <div v-if="loading" class="p-8 text-center">
          <ArrowPathIcon class="w-6 h-6 animate-spin mx-auto text-gray-300" />
        </div>
        <div v-else-if="servers.length === 0" class="p-8 text-center text-gray-400 text-sm italic">
          暂无配置 MCP 服务
        </div>
        <div v-else class="divide-y divide-gray-50">
          <div 
            v-for="server in servers" 
            :key="server.id"
            @click="selectServer(server)"
            class="p-4 cursor-pointer transition-all hover:bg-blue-50/30"
            :class="selectedServer?.id === server.id ? 'bg-blue-50 border-l-4 border-primary' : 'border-l-4 border-transparent'"
          >
            <div class="flex justify-between items-start mb-1">
              <span class="text-sm font-bold text-gray-900">{{ server.server_name }}</span>
              <div v-if="canSave" class="flex space-x-1">
                <button @click.stop="openEditModal(server)" class="p-1 text-gray-400 hover:text-blue-500 transition-colors" title="编辑配置">
                  <PencilSquareIcon class="w-4 h-4" />
                </button>
                <button @click.stop="syncTools(server.id)" :disabled="syncLoading[server.id]" class="p-1 text-gray-400 hover:text-primary transition-colors">
                  <CloudArrowDownIcon class="w-4 h-4" :class="syncLoading[server.id] ? 'animate-bounce' : ''" />
                </button>
                <button @click.stop="confirmDeleteServer(server.id)" class="p-1 text-gray-400 hover:text-red-500 transition-colors">
                  <TrashIcon class="w-4 h-4" />
                </button>
              </div>
            </div>
            <div class="flex items-center text-[10px] text-gray-400 font-mono truncate mb-2">
              <LinkIcon class="w-3 h-3 mr-1" />
              {{ server.sse_url }}
            </div>
            <div class="flex justify-between items-center">
              <span class="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">
                {{ server.tool_count }} 工具 / <span class="text-green-600 font-bold">{{ server.published_tool_count }} 已发布</span>
              </span>
              <span class="text-[9px] text-gray-400 italic" v-if="server.last_sync_at">同步于 {{ new Date(server.last_sync_at).toLocaleString() }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Right: Tool List -->
    <div class="flex-1 flex flex-col bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
      <div v-if="!selectedServer" class="flex-1 flex flex-col items-center justify-center text-gray-400">
        <SparklesIcon class="w-12 h-12 mb-4 opacity-20" />
        <p class="text-sm">请在左侧选择一个 MCP 服务查看工具</p>
      </div>
      
      <template v-else>
        <div class="p-4 border-b border-gray-200 bg-slate-50 flex justify-between items-center">
          <div class="flex items-center">
            <input 
              v-if="canSave"
              type="checkbox" 
              :checked="isAllSelected" 
              @change="toggleSelectAll"
              class="w-4 h-4 text-primary border-gray-400 rounded focus:ring-primary mr-3" 
            />
            <div>
              <h3 class="text-sm font-bold text-slate-800">{{ selectedServer.server_name }} 工具</h3>
              <p class="text-[10px] text-slate-500 mt-0.5" v-if="selectedToolIds.size === 0">发布后的工具智能体才可见</p>
              <p class="text-[10px] text-primary font-black mt-0.5" v-else>已选中 {{ selectedToolIds.size }} 个项</p>
            </div>
          </div>
          
          <div class="flex items-center space-x-3">
            <div v-if="canSave && selectedToolIds.size > 0" class="flex items-center space-x-2 animate-fade-in bg-white p-1 rounded-lg border border-gray-200 shadow-sm">
              <button @click="batchUpdateStatus(true)" class="text-[10px] font-bold bg-green-600 text-white px-3 py-1 rounded shadow-sm hover:bg-green-700 transition-all">批量发布</button>
              <button @click="batchUpdateStatus(false)" class="text-[10px] font-bold bg-slate-600 text-white px-3 py-1 rounded shadow-sm hover:bg-slate-700 transition-all">批量下线</button>
            </div>
            <button v-if="canSave" @click="syncTools(selectedServer.id)" :disabled="syncLoading[selectedServer.id]" class="text-[11px] font-bold text-primary flex items-center hover:underline bg-white px-2 py-1 rounded border border-gray-200">
              <ArrowPathIcon class="w-3.5 h-3.5 mr-1" :class="syncLoading[selectedServer.id] ? 'animate-spin' : ''" />
              刷新
            </button>
          </div>
        </div>

        <div class="flex-1 overflow-y-auto p-4 custom-scrollbar">
          <div v-if="toolsLoading" class="p-12 text-center">
            <ArrowPathIcon class="w-8 h-8 animate-spin mx-auto text-gray-200" />
          </div>
          <div v-else-if="tools.length === 0" class="p-12 text-center">
            <p class="text-gray-400 text-sm">该服务下暂无同步到的工具，请点击同步按钮。</p>
          </div>
          <div v-else class="grid grid-cols-1 gap-4">
            <div 
              v-for="tool in tools" 
              :key="tool.id" 
              @click="canSave && toggleSelectTool(tool.id)"
              class="p-4 rounded-lg border flex justify-between items-start group transition-all"
              :class="[
                canSave ? 'cursor-pointer' : '',
                selectedToolIds.has(tool.id) ? 'border-primary bg-blue-50/50 shadow-sm' : 'border-gray-100 bg-gray-50/30 hover:border-primary/30'
              ]"
            >
              <div class="flex items-start flex-1 min-w-0 pr-4">
                <input 
                  v-if="canSave"
                  type="checkbox" 
                  :checked="selectedToolIds.has(tool.id)" 
                  @click.stop="toggleSelectTool(tool.id)"
                  class="w-3.5 h-3.5 mt-1 text-primary border-gray-300 rounded focus:ring-primary mr-3" 
                />
                <div class="flex-1 min-w-0">
                  <div class="flex items-center space-x-2 mb-1">
                    <span class="text-sm font-bold text-gray-900">{{ tool.tool_name }}</span>
                    <span v-if="tool.usage_count > 0" class="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-medium bg-blue-100 text-blue-700" title="被智能体引用次数">
                      <LinkIcon class="w-3 h-3 mr-0.5" />{{ tool.usage_count }}
                    </span>
                    <span v-if="tool.is_published" class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-green-50 text-green-600 border border-green-100 uppercase tracking-tighter">已发布</span>
                    <span v-else class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-gray-100 text-gray-400 border border-gray-200 uppercase tracking-tighter">待发布</span>
                  </div>
                  <p class="text-xs text-gray-500 line-clamp-2 leading-relaxed italic">
                    {{ tool.tool_description || '暂无描述' }}
                  </p>
                  <div class="mt-2 flex flex-wrap gap-1" v-if="tool.parameter_schema">
                    <code class="text-[9px] bg-white px-1 border rounded text-gray-400" v-for="(_, p) in JSON.parse(tool.parameter_schema).properties" :key="p">{{ p }}</code>
                  </div>
                </div>
              </div>
              <div v-if="canSave" class="flex flex-col items-end space-y-2">
                <button 
                  @click.stop="openTester(tool)"
                  class="flex items-center text-[11px] font-bold transition-colors px-3 py-1.5 rounded-md border shadow-sm bg-white text-indigo-600 hover:bg-indigo-50 border-indigo-100 opacity-0 group-hover:opacity-100"
                  title="在线测试"
                >
                  <BeakerIcon class="w-3.5 h-3.5 mr-1.5" />
                  测试
                </button>
                <button 
                  @click.stop="togglePublish(tool)"
                  class="flex items-center text-[11px] font-bold transition-colors px-3 py-1.5 rounded-md border shadow-sm opacity-0 group-hover:opacity-100"
                  :class="tool.is_published ? 'bg-white text-gray-600 hover:text-red-600' : 'bg-primary text-white hover:bg-primary-dark'"
                >
                  <component :is="tool.is_published ? EyeSlashIcon : EyeIcon" class="w-3.5 h-3.5 mr-1.5" />
                  {{ tool.is_published ? '下线' : '发布' }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- Tool Tester Drawer -->
    <McpToolTester 
      v-if="toolToTest"
      :tool="toolToTest" 
      :is-open="showTester" 
      @close="showTester = false" 
    />

    <!-- Add Server Modal (Connection Wizard) -->
    <div v-if="showAddModal" class="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-gray-900/50 backdrop-blur-sm">
      <div class="bg-white rounded-xl shadow-2xl w-full max-w-lg overflow-hidden animate-fade-in-up">
        <!-- Wizard Header -->
        <div class="p-6 border-b border-gray-100 bg-gray-50/50">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-lg font-bold text-gray-900">
              {{ wizardStep === 1 ? (isEditing ? '编辑配置' : '第一步：建立连接') : '第二步：确认工具并命名' }}
            </h3>
            <div class="flex space-x-1" v-if="!isEditing">
              <div class="w-2 h-2 rounded-full" :class="wizardStep === 1 ? 'bg-primary' : 'bg-gray-200'"></div>
              <div class="w-2 h-2 rounded-full" :class="wizardStep === 2 ? 'bg-primary' : 'bg-gray-200'"></div>
            </div>
          </div>
          <p class="text-xs text-gray-500">
            {{ wizardStep === 1 ? '输入外部 MCP SSE 服务端地址，系统将尝试探测其支持的工具。' : `探测成功！共发现 ${discoveredTools.length} 个工具。请检查列表并为该服务命名。` }}
          </p>
        </div>

        <!-- Wizard Step 1: Input -->
        <div v-if="wizardStep === 1" class="p-6 space-y-5">
          <div>
            <label class="block text-xs font-bold text-gray-700 uppercase tracking-wider mb-1.5 flex items-center">
              <LinkIcon class="w-3 h-3 mr-1" /> SSE 握手地址
            </label>
            <input v-model="newServer.sse_url" placeholder="https://..." class="w-full px-3 py-2 text-sm border rounded-lg focus:ring-2 focus:ring-primary outline-none font-mono" />
            <p class="text-[10px] text-gray-400 mt-1">支持标准的 MCP SSE URL，例如来自 mcpmarket.cn 的代理地址。</p>
          </div>
          
          <!-- Dynamic Headers Editor -->
          <div>
            <div class="flex justify-between items-center mb-2">
              <label class="block text-xs font-bold text-gray-700 uppercase tracking-wider">身份认证 (可选)</label>
              <button @click="toggleHeaderMode" class="text-[10px] text-primary font-bold flex items-center hover:underline">
                <component :is="headerMode === 'simple' ? CodeBracketIcon : ListBulletIcon" class="w-3 h-3 mr-1" />
                切换到{{ headerMode === 'simple' ? '高级 JSON' : '可视化列表' }}
              </button>
            </div>

            <div v-if="headerMode === 'simple'" class="space-y-3">
              <p class="text-[10px] text-gray-400 leading-relaxed">
                如果服务需要令牌或 API Key，请添加下方项。
                <span class="text-primary cursor-pointer hover:underline" @click="headerPairs[0] = {key: 'Authorization', value: 'Bearer '}">[常用推荐：Authorization]</span>
              </p>
              
              <div class="space-y-2 bg-gray-50 p-3 rounded-lg border border-gray-100 max-h-[150px] overflow-y-auto custom-scrollbar">
                <div v-for="(pair, index) in headerPairs" :key="index" class="flex gap-2">
                  <div class="flex-1">
                    <input 
                      v-model="pair.key" 
                      placeholder="名称 (如 Authorization)" 
                      class="w-full px-3 py-1.5 text-xs border rounded focus:ring-1 focus:ring-primary outline-none" 
                    />
                  </div>
                  <div class="flex-1">
                    <input 
                      v-model="pair.value" 
                      :placeholder="pair.key === 'Authorization' ? 'Bearer sk-...' : '内容 (Value)'" 
                      class="w-full px-3 py-1.5 text-xs border rounded focus:ring-1 focus:ring-primary outline-none" 
                    />
                  </div>
                  <button @click="removeHeaderPair(index)" class="p-1.5 text-gray-400 hover:text-red-500">
                    <TrashIcon class="w-4 h-4" />
                  </button>
                </div>
                <button @click="addHeaderPair" class="mt-2 text-[10px] font-bold text-primary flex items-center hover:underline">
                  <PlusIcon class="w-3 h-3 mr-1" /> 继续添加
                </button>
              </div>
            </div>
            <div v-else>
              <textarea v-model="newServer.auth_headers" rows="4" class="w-full px-3 py-2 text-sm border rounded-lg focus:ring-2 focus:ring-primary outline-none font-mono bg-gray-900 text-green-400" placeholder='{}'></textarea>
            </div>
          </div>
        </div>

        <!-- Wizard Step 2: Preview -->
        <div v-else class="p-6 space-y-5">
          <div>
            <label class="block text-xs font-bold text-gray-700 uppercase tracking-wider mb-1.5">服务显示名称</label>
            <input v-model="newServer.server_name" placeholder="起个好记的名字" class="w-full px-3 py-2 text-sm border rounded-lg focus:ring-2 focus:ring-primary outline-none" />
          </div>
          
          <div>
            <label class="block text-xs font-bold text-gray-700 uppercase tracking-wider mb-2">发现的工具预览</label>
            <div class="bg-gray-50 rounded-lg border border-gray-100 max-h-[250px] overflow-y-auto divide-y divide-gray-200 custom-scrollbar">
              <div v-for="tool in discoveredTools" :key="tool.name" class="p-3">
                <div class="text-xs font-bold text-gray-800">{{ tool.name }}</div>
                <div class="text-[10px] text-gray-500 line-clamp-1 italic">{{ tool.description || '无描述' }}</div>
              </div>
            </div>
          </div>
        </div>

        <!-- Wizard Footer -->
        <div class="p-6 bg-gray-50 flex justify-between items-center">
          <button @click="showAddModal = false" class="px-4 py-2 text-sm text-gray-500 hover:text-gray-700 font-medium">取消</button>
          
          <div class="flex space-x-3">
            <button v-if="wizardStep === 2" @click="wizardStep = 1" class="px-4 py-2 text-sm text-primary font-medium hover:underline">返回修改</button>
            
            <button 
              v-if="wizardStep === 1"
              @click="handleVerify" 
              :disabled="verifying"
              class="px-6 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary-dark font-bold shadow-lg shadow-primary/20 transition-all disabled:opacity-50 flex items-center"
            >
              <ArrowPathIcon v-if="verifying" class="w-4 h-4 mr-2 animate-spin" />
              {{ verifying ? '正在尝试建立连接...' : '连接并发现工具' }}
            </button>
            
            <button 
              v-else
              @click="addServer" 
              class="px-6 py-2 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700 font-bold shadow-lg shadow-green-600/20 transition-all active:scale-95"
            >
              {{ isEditing ? '保存修改' : '确认并完成添加' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Delete Confirmation -->
    <ConfirmModal 
      v-if="showDeleteConfirm"
      title="删除 MCP 服务"
      message="确定要删除该服务及其缓存的所有工具吗？此操作不可恢复，且关联这些工具的智能体将失效。"
      type="danger"
      @confirm="executeDeleteServer"
      @cancel="showDeleteConfirm = false"
    />
  </div>
</template>

<style scoped>
.animate-fade-in-up { animation: fadeInUp 0.3s ease-out; }
@keyframes fadeInUp { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
.custom-scrollbar::-webkit-scrollbar { width: 4px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #e5e7eb; border-radius: 4px; }
</style>
