<template>
  <div class="space-y-6">
    <!-- Header -->
    <div class="flex items-center justify-between border-b border-gray-100 pb-4">
      <div>
        <h3 class="text-lg font-semibold text-gray-800">消息通知配置</h3>
        <p class="text-sm text-gray-500 mt-1">配置您在平台内的个人消息通知通道，支持钉钉、企微机器人以及 SMTP 邮件发送。</p>
      </div>
      <button 
        @click="fetchConfigs"
        :disabled="loading"
        class="inline-flex items-center px-3 py-1.5 text-xs font-medium text-blue-600 bg-blue-50 hover:bg-blue-100 active:bg-blue-200 rounded-lg transition-colors duration-200 disabled:opacity-50"
      >
        <svg v-if="loading" class="animate-spin -ml-1 mr-1.5 h-3.5 w-3.5 text-blue-600" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        刷新配置
      </button>
    </div>

    <!-- Main Container -->
    <div v-if="loading && Object.keys(configs).length === 0" class="flex flex-col items-center justify-center py-12">
      <svg class="animate-spin h-8 w-8 text-blue-500" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
      </svg>
      <span class="text-sm text-gray-500 mt-4">正在加载通知配置...</span>
    </div>

    <div v-else class="space-y-6">
      <!-- 1. DingTalk Config Card -->
      <div class="bg-white border border-gray-100 rounded-xl p-6 shadow-sm hover:shadow-md transition-all duration-300">
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-3">
            <div class="p-2 bg-blue-50 rounded-lg text-blue-500">
              <!-- Dingtalk Icon (using SVG) -->
              <svg class="h-6 w-6" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12.012 2C6.488 2 2 6.488 2 12.012c0 5.524 4.488 10.012 10.012 10.012 5.524 0 10.012-4.488 10.012-10.012C22.024 6.488 17.536 2 12.012 2zm3.328 14.88h-6.68c-.372 0-.672-.3-.672-.672 0-.256.148-.488.38-.6l6.68-3.232c.332-.16.736.008.868.344.032.088.048.18.048.272 0 .372-.3.672-.672.672l-5.632.008 5.632 2.544c.336.152.484.552.332.888-.112.248-.364.4-.64.4zm.008-5.36h-6.68c-.372 0-.672-.3-.672-.672 0-.256.148-.488.38-.6l6.68-3.232c.332-.16.736.008.868.344.032.088.048.18.048.272 0 .372-.3.672-.672.672l-5.632.008 5.632 2.544c.336.152.484.552.332.888-.112.248-.364.4-.64.4z"/>
              </svg>
            </div>
            <div>
              <h4 class="font-medium text-gray-800">钉钉群机器人通知</h4>
              <p class="text-xs text-gray-400 mt-0.5">申请审批、报警等结果推送至钉钉群自定义机器人。</p>
            </div>
          </div>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" v-model="configs.dingtalk.is_enabled" class="sr-only peer">
            <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
          </label>
        </div>

        <!-- DingTalk Form (Transition Expand) -->
        <div v-if="configs.dingtalk.is_enabled" class="mt-6 border-t border-gray-50 pt-4 space-y-4">
          <div>
            <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">Webhook 地址</label>
            <input 
              type="text" 
              v-model="configs.dingtalk.webhook_url"
              placeholder="https://oapi.dingtalk.com/robot/send?access_token=..."
              class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 placeholder-gray-300"
            />
            <p class="text-[11px] text-gray-400 mt-1">钉钉群 &rarr; 智能群助手 &rarr; 添加机器人 &rarr; 自定义 &rarr; 复制 Webhook 地址。</p>
          </div>
          <div>
            <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">加签密钥 (可选)</label>
            <input 
              type="password" 
              v-model="configs.dingtalk.secret"
              placeholder="SEC..."
              class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 placeholder-gray-300"
            />
            <p class="text-[11px] text-gray-400 mt-1">若机器人启用了「加签」安全设置，请在此填写 SEC 开头的密钥。</p>
          </div>
          
          <div class="flex items-center justify-end space-x-3 pt-2">
            <button 
              @click="testConfig('dingtalk')"
              :disabled="testingChannel['dingtalk'] || savingChannel['dingtalk']"
              class="px-4 py-2 text-xs font-medium text-gray-700 bg-gray-50 hover:bg-gray-100 rounded-lg active:scale-95 transition-all disabled:opacity-50"
            >
              <span v-if="testingChannel['dingtalk']" class="inline-flex items-center">
                <circle class="animate-spin -ml-0.5 mr-1.5 h-3 w-3 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor"/></circle>
                正在测试...
              </span>
              <span v-else>测试连通性</span>
            </button>
            <button 
              @click="saveConfig('dingtalk')"
              :disabled="testingChannel['dingtalk'] || savingChannel['dingtalk']"
              class="px-4 py-2 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg active:scale-95 transition-all disabled:opacity-50 shadow-sm"
            >
              <span v-if="savingChannel['dingtalk']" class="inline-flex items-center">
                <circle class="animate-spin -ml-0.5 mr-1.5 h-3 w-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor"/></circle>
                保存中...
              </span>
              <span v-else>保存配置</span>
            </button>
          </div>
        </div>
      </div>

      <!-- 2. WeChat Work Config Card -->
      <div class="bg-white border border-gray-100 rounded-xl p-6 shadow-sm hover:shadow-md transition-all duration-300">
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-3">
            <div class="p-2 bg-green-50 rounded-lg text-green-600">
              <!-- WeChat Icon -->
              <svg class="h-6 w-6" fill="currentColor" viewBox="0 0 24 24">
                <path d="M8.22 2c-4.12 0-7.46 3-7.46 6.7 0 2.22 1.13 4.2 2.91 5.38L2.73 17c-.1.25.04.53.3.6a.6.6 0 0 0 .28-.01l3.05-1.52c.6.14 1.22.21 1.86.21 4.12 0 7.46-3 7.46-6.7S12.34 2 8.22 2zm6.26 8.35c.19 0 .37.01.56.03.35-2.73-2.02-5.18-5.32-5.18-3.7 0-6.7 2.46-6.7 5.5 0 1.84.97 3.47 2.48 4.45L4.54 18c-.08.2.03.43.23.49.08.02.16.01.23-.02l2.67-1.33c.53.13 1.09.2 1.67.2.22 0 .43-.01.65-.02-.13-.37-.2-.76-.2-1.17 0-3.2 2.66-5.8 5.92-5.8z"/>
              </svg>
            </div>
            <div>
              <h4 class="font-medium text-gray-800">企业微信群机器人通知</h4>
              <p class="text-xs text-gray-400 mt-0.5">将平台通知推送至企微群的自定义小助手。</p>
            </div>
          </div>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" v-model="configs.wechat_work.is_enabled" class="sr-only peer">
            <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-green-600"></div>
          </label>
        </div>

        <!-- WeChat Work Form -->
        <div v-if="configs.wechat_work.is_enabled" class="mt-6 border-t border-gray-50 pt-4 space-y-4">
          <div>
            <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">Webhook 地址</label>
            <input 
              type="text" 
              v-model="configs.wechat_work.webhook_url"
              placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."
              class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-green-500/20 focus:border-green-500 placeholder-gray-300"
            />
            <p class="text-[11px] text-gray-400 mt-1">企业微信群聊 &rarr; 添加群机器人 &rarr; 新建机器人 &rarr; 复制 Webhook 地址。</p>
          </div>

          <div class="flex items-center justify-end space-x-3 pt-2">
            <button 
              @click="testConfig('wechat_work')"
              :disabled="testingChannel['wechat_work'] || savingChannel['wechat_work']"
              class="px-4 py-2 text-xs font-medium text-gray-700 bg-gray-50 hover:bg-gray-100 rounded-lg active:scale-95 transition-all disabled:opacity-50"
            >
              <span v-if="testingChannel['wechat_work']" class="inline-flex items-center">
                <circle class="animate-spin -ml-0.5 mr-1.5 h-3 w-3 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor"/></circle>
                正在测试...
              </span>
              <span v-else>测试连通性</span>
            </button>
            <button 
              @click="saveConfig('wechat_work')"
              :disabled="testingChannel['wechat_work'] || savingChannel['wechat_work']"
              class="px-4 py-2 text-xs font-medium text-white bg-green-600 hover:bg-green-700 rounded-lg active:scale-95 transition-all disabled:opacity-50 shadow-sm"
            >
              <span v-if="savingChannel['wechat_work']" class="inline-flex items-center">
                <circle class="animate-spin -ml-0.5 mr-1.5 h-3 w-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor"/></circle>
                保存中...
              </span>
              <span v-else>保存配置</span>
            </button>
          </div>
        </div>
      </div>

      <!-- 3. SMTP Email Config Card -->
      <div class="bg-white border border-gray-100 rounded-xl p-6 shadow-sm hover:shadow-md transition-all duration-300">
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-3">
            <div class="p-2 bg-orange-50 rounded-lg text-orange-500">
              <!-- Email Icon -->
              <svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
              </svg>
            </div>
            <div>
              <h4 class="font-medium text-gray-800">邮件通知 (SMTP)</h4>
              <p class="text-xs text-gray-400 mt-0.5">绑定第三方 SMTP 服务器进行系统报警和申请邮件投递。</p>
            </div>
          </div>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" v-model="configs.email.is_enabled" class="sr-only peer">
            <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-orange-500"></div>
          </label>
        </div>

        <!-- Email SMTP Form -->
        <div v-if="configs.email.is_enabled" class="mt-6 border-t border-gray-50 pt-4 space-y-4">
          <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div class="md:col-span-2">
              <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">SMTP 服务地址</label>
              <input 
                type="text" 
                v-model="configs.email.smtp_host"
                placeholder="smtp.example.com"
                class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 placeholder-gray-300"
              />
            </div>
            <div>
              <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">端口</label>
              <input 
                type="number" 
                v-model="configs.email.smtp_port"
                placeholder="465"
                class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 placeholder-gray-300"
              />
            </div>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">发件人账号/邮箱</label>
              <input 
                type="email" 
                v-model="configs.email.smtp_user"
                placeholder="user@example.com"
                class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 placeholder-gray-300"
              />
            </div>
            <div>
              <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">授权码/密码</label>
              <input 
                type="password" 
                v-model="configs.email.smtp_password"
                placeholder="填写您的邮箱授权码或密码"
                class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 placeholder-gray-300"
              />
            </div>
          </div>

          <div>
            <label class="block text-xs font-semibold text-gray-500 uppercase mb-1.5">发件人显示昵称</label>
            <input 
              type="text" 
              v-model="configs.email.sender_name"
              placeholder="AI Agent"
              class="w-full text-sm px-3.5 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-orange-500/20 focus:border-orange-500 placeholder-gray-300"
            />
          </div>

          <div class="flex items-center justify-end space-x-3 pt-2">
            <button 
              @click="testConfig('email')"
              :disabled="testingChannel['email'] || savingChannel['email']"
              class="px-4 py-2 text-xs font-medium text-gray-700 bg-gray-50 hover:bg-gray-100 rounded-lg active:scale-95 transition-all disabled:opacity-50"
            >
              <span v-if="testingChannel['email']" class="inline-flex items-center">
                <circle class="animate-spin -ml-0.5 mr-1.5 h-3 w-3 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor"/></circle>
                正在测试...
              </span>
              <span v-else>测试连通性</span>
            </button>
            <button 
              @click="saveConfig('email')"
              :disabled="testingChannel['email'] || savingChannel['email']"
              class="px-4 py-2 text-xs font-medium text-white bg-orange-500 hover:bg-orange-600 rounded-lg active:scale-95 transition-all disabled:opacity-50 shadow-sm"
            >
              <span v-if="savingChannel['email']" class="inline-flex items-center">
                <circle class="animate-spin -ml-0.5 mr-1.5 h-3 w-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor"/></circle>
                保存中...
              </span>
              <span v-else>保存配置</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import axios from '../../utils/axios'

const emit = defineEmits(['show-toast'])

const loading = ref(false)
const configs = ref<Record<string, any>>({
  dingtalk: { is_enabled: false, webhook_url: '', secret: '' },
  wechat_work: { is_enabled: false, webhook_url: '' },
  email: { is_enabled: false, smtp_host: '', smtp_port: 465, smtp_user: '', smtp_password: '', sender_name: 'AI Agent' }
})

const testingChannel = ref<Record<string, boolean>>({})
const savingChannel = ref<Record<string, boolean>>({})

const getChannelName = (channel: string) => {
  switch (channel) {
    case 'dingtalk': return '钉钉'
    case 'wechat_work': return '企微'
    case 'email': return '邮件'
    default: return channel
  }
}

const fetchConfigs = async () => {
  loading.value = true
  try {
    const res = await axios.get('/api/portal/notifications/config')
    if (res.data) {
      configs.value = res.data
    }
  } catch (error: any) {
    console.error("Failed to load configs", error)
    emit('show-toast', error.response?.data?.detail || '获取通知配置失败', 'error')
  } finally {
    loading.value = false
  }
}

const saveConfig = async (channel: string) => {
  savingChannel.value[channel] = true
  try {
    const res = await axios.put('/api/portal/notifications/config', {
      channel_type: channel,
      config_data: configs.value[channel]
    })
    if (res.data && res.data.status === 'success') {
      emit('show-toast', `${getChannelName(channel)}配置保存成功`, 'success')
      await fetchConfigs() // 重新拉取以更新打星号
    }
  } catch (error: any) {
    emit('show-toast', error.response?.data?.detail || '保存配置失败', 'error')
  } finally {
    savingChannel.value[channel] = false
  }
}

const testConfig = async (channel: string) => {
  testingChannel.value[channel] = true
  try {
    const res = await axios.post('/api/portal/notifications/test', {
      channel_type: channel,
      config_data: configs.value[channel]
    })
    if (res.data && res.data.status === 'success') {
      emit('show-toast', `${getChannelName(channel)}测试连通成功！`, 'success')
    }
  } catch (error: any) {
    emit('show-toast', error.response?.data?.detail || '测试连通失败', 'error')
  } finally {
    testingChannel.value[channel] = false
  }
}

onMounted(() => {
  fetchConfigs()
})
</script>
