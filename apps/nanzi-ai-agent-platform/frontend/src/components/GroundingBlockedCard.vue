<script setup lang="ts">
import type {
  GroundingBlockedAction,
  GroundingBlockedPayload,
} from '@/utils/agentscopeSseHandlers';

defineProps<{
  payload: GroundingBlockedPayload;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  (event: 'action', action: GroundingBlockedAction): void;
}>();
</script>

<template>
  <section
    class="rounded-xl border border-amber-200 bg-amber-50/90 p-4 text-amber-950 shadow-sm dark:border-amber-800/70 dark:bg-amber-950/35 dark:text-amber-100"
    role="alert"
  >
    <div class="flex items-start gap-3">
      <div class="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/70 dark:text-amber-300">
        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v3m0 4h.01M10.29 3.86l-8.18 14A2 2 0 003.82 21h16.36a2 2 0 001.71-3.14l-8.18-14a2 2 0 00-3.42 0z" />
        </svg>
      </div>
      <div class="min-w-0 flex-1">
        <h3 class="text-sm font-bold">{{ payload.title }}</h3>
        <p class="mt-1 text-xs leading-5 text-amber-800 dark:text-amber-200">
          {{ payload.message }}
        </p>
        <div v-if="payload.actions.length" class="mt-3 flex flex-wrap gap-2">
          <button
            v-for="action in payload.actions"
            :key="action.id"
            type="button"
            :disabled="disabled"
            class="rounded-lg px-3 py-1.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-50"
            :class="action.style === 'primary'
              ? 'bg-amber-600 text-white hover:bg-amber-700 dark:bg-amber-500 dark:text-amber-950 dark:hover:bg-amber-400'
              : 'border border-amber-300 bg-white/80 text-amber-800 hover:bg-amber-100 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200 dark:hover:bg-amber-900/60'"
            @click="emit('action', action)"
          >
            {{ action.label }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>
