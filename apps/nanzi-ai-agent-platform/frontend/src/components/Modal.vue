<script setup lang="ts">
const props = withDefaults(defineProps<{
  title: string
  show?: boolean
  size?: string
  zIndex?: number | string
}>(), {
  show: true,
  zIndex: 60
})

const emit = defineEmits(['close', 'update:modelValue'])

const close = () => {
  emit('close')
  emit('update:modelValue', false)
}
</script>

<template>
  <teleport to="body">
    <transition name="modal">
      <div
        v-if="show"
        class="fixed inset-0 flex items-center justify-center p-4"
        :style="{ zIndex }"
      >
        <!-- Backdrop -->
        <div class="absolute inset-0 bg-black/50 backdrop-blur-sm" @click="close"></div>
        
        <!-- Modal Container -->
        <div 
          class="relative flex max-h-[calc(100vh-2rem)] w-full flex-col overflow-hidden rounded-2xl bg-white shadow-2xl transition-all duration-300"
          :class="size || 'max-w-md'"
        >
          <!-- Header -->
          <div class="px-6 py-4 border-b border-gray-100 flex items-center justify-between bg-gray-50/50">
            <h3 class="text-lg font-bold text-gray-900">{{ title }}</h3>
            <div class="flex items-center gap-3">
              <slot name="header-extra"></slot>
              <button
                @click="close"
                class="p-2 hover:bg-gray-200 rounded-lg text-gray-500 transition-colors"
              >
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          </div>
          
          <!-- Content -->
          <div class="custom-scrollbar min-h-0 flex-1 overflow-y-auto p-6">
            <slot></slot>
          </div>

          <div v-if="$slots.footer" class="border-t border-gray-100 bg-gray-50/80 px-6 py-3">
            <slot name="footer"></slot>
          </div>
        </div>
      </div>
    </transition>
  </teleport>
</template>

<style scoped>
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.3s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

.modal-enter-active > div:nth-child(2),
.modal-leave-active > div:nth-child(2) {
  transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.3s ease;
}

.modal-enter-from > div:nth-child(2),
.modal-leave-to > div:nth-child(2) {
  transform: scale(0.95) translateY(10px);
  opacity: 0;
}

.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: rgba(0,0,0,0.1);
  border-radius: 10px;
}
</style>
