<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue';

interface AgentOption {
  id: string;
  name: string;
  display_name: string;
  avatar_url?: string;
  description?: string;
  is_system?: boolean;
}

const props = defineProps<{
  visible: boolean;
  keyword: string;
  agents: AgentOption[];
  position: { top: number; left: number };
}>();

const emit = defineEmits<{
  (e: 'select', agent: AgentOption): void;
  (e: 'close'): void;
}>();

const selectedIndex = ref(0);
const listContainer = ref<HTMLElement | null>(null);
const windowWidth = ref(window.innerWidth);

const updateWidth = () => { windowWidth.value = window.innerWidth; };
onMounted(() => window.addEventListener('resize', updateWidth));
onUnmounted(() => window.removeEventListener('resize', updateWidth));

const filteredAgents = computed(() => {
  const k = (props.keyword || "").toLowerCase();
  return props.agents.filter(a => 
    (a.display_name || "").toLowerCase().includes(k) || 
    (a.name || "").toLowerCase().includes(k)
  );
});

watch(() => filteredAgents.value, () => {
  selectedIndex.value = 0;
});

watch(selectedIndex, () => {
  nextTick(() => {
    if (!listContainer.value) return;
    const activeItem = listContainer.value.children[selectedIndex.value] as HTMLElement;
    if (activeItem) {
      activeItem.scrollIntoView({ block: 'nearest' });
    }
  });
});

const handleSelect = (agent: AgentOption) => {
  emit('select', agent);
};

// Expose key handler for parent to call
const handleKeydown = (e: KeyboardEvent) => {
  if (!props.visible) return false;

  if (e.key === 'ArrowDown') {
    e.preventDefault();
    selectedIndex.value = (selectedIndex.value + 1) % filteredAgents.value.length;
    return true;
  }
  if (e.key === 'ArrowUp') {
    e.preventDefault();
    selectedIndex.value = (selectedIndex.value - 1 + filteredAgents.value.length) % filteredAgents.value.length;
    return true;
  }
  if (e.key === 'Enter' || e.key === 'Tab') {
    e.preventDefault();
    const selected = filteredAgents.value[selectedIndex.value];
    if (selected) {
        handleSelect(selected);
    }
    return true;
  }
  if (e.key === 'Escape') {
    e.preventDefault();
    emit('close');
    return true;
  }
  return false;
};

defineExpose({ handleKeydown });
</script>

<template>
  <div
    v-if="visible && filteredAgents.length > 0"
    class="fixed z-[100] bg-white dark:bg-gray-800 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-700 overflow-hidden flex flex-col animate-fade-in-up"
    :class="[
      // Mobile: wider and centered if necessary, Desktop: fixed width
      'w-[calc(100vw-2rem)] sm:w-72'
    ]"
    :style="{ 
      top: windowWidth < 640 ? 'auto' : `${position.top}px`, 
      bottom: windowWidth < 640 ? '80px' : 'auto',
      left: windowWidth < 640 ? '1rem' : `${position.left}px`,
      maxHeight: windowWidth < 640 ? '40vh' : '18rem' 
    }"
  >
    <div class="px-3 py-2.5 bg-gray-50 dark:bg-gray-700 border-b border-gray-100 dark:border-gray-600 flex justify-between items-center">
      <div class="flex items-center space-x-2">
        <span class="text-[10px] font-black text-gray-400 dark:text-gray-400 uppercase tracking-widest">提及智能体</span>
        <span class="px-1.5 py-0.5 bg-primary/10 text-primary text-[9px] font-bold rounded-md">{{ filteredAgents.length }} 匹配</span>
      </div>
      <span class="text-[9px] text-gray-400 hidden sm:inline">Enter 选择 · Esc 关闭</span>
    </div>
    
    <div class="overflow-y-auto custom-scrollbar p-1.5" ref="listContainer">
      <div
        v-for="(agent, index) in filteredAgents"
        :key="agent.id"
        @click="handleSelect(agent)"
        class="flex items-center space-x-3 px-3 py-2.5 rounded-lg cursor-pointer transition-all active:scale-[0.98]"
        :class="index === selectedIndex ? 'bg-primary/10 dark:bg-primary/20 ring-1 ring-primary/20' : 'hover:bg-gray-50 dark:hover:bg-gray-700'"
      >
        <!-- Avatar -->
        <div class="w-8 h-8 rounded-full bg-gradient-to-br from-gray-100 to-gray-200 dark:from-gray-700 dark:to-gray-600 flex items-center justify-center flex-shrink-0 text-sm font-bold text-gray-600 dark:text-gray-300 border border-white dark:border-gray-800 shadow-sm overflow-hidden">
            <img v-if="agent.avatar_url" :src="agent.avatar_url" class="w-full h-full object-cover" />
            <span v-else>{{ Array.from(agent.display_name || 'E')[0] }}</span>
        </div>
        
        <div class="flex-1 min-w-0">
          <div class="flex items-baseline justify-between">
             <div class="flex items-center space-x-1.5 min-w-0">
                 <span class="text-sm font-bold text-gray-900 dark:text-gray-100 truncate" :class="index === selectedIndex ? 'text-primary' : ''">
                    {{ agent.display_name }}
                 </span>
                 <span v-if="agent.is_system" class="px-1 py-0.5 rounded text-[8px] bg-purple-100 text-purple-700 border border-purple-200 dark:bg-purple-900/30 dark:border-purple-800 dark:text-purple-300 font-black uppercase tracking-tighter shrink-0">SYS</span>
             </div>
          </div>
          <div class="text-[10px] text-gray-400 truncate font-mono opacity-70">
            @{{ agent.name }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar {
  width: 4px;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 2px;
}
.animate-fade-in-up {
  animation: fadeInUp 0.2s ease-out;
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>