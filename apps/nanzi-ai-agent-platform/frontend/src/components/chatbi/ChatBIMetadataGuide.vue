<template>
  <section class="mt-3 rounded-xl border border-indigo-100 bg-indigo-50/60 p-3 dark:border-indigo-900/60 dark:bg-indigo-950/20">
    <div class="flex flex-wrap items-center gap-2 text-xs">
      <span class="font-semibold text-indigo-700 dark:text-indigo-300">可分析内容</span>
      <span v-for="metric in guide.metrics.slice(0, 4)" :key="`${metric.table}.${metric.physical_name}`" class="rounded-full bg-white px-2 py-1 text-gray-600 shadow-sm dark:bg-gray-900 dark:text-gray-300" :title="metric.physical_name">
        {{ metric.label }}
      </span>
    </div>
    <div v-if="guide.suggestions.length" class="mt-2 flex flex-wrap gap-2">
      <button v-for="item in guide.suggestions" :key="item.query" type="button" class="rounded-lg border border-indigo-200 bg-white px-2.5 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-100 dark:border-indigo-800 dark:bg-gray-900 dark:text-indigo-300" @click="emit('select', item.query)">
        {{ item.label }}
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import type { ChatBIMetadataGuide } from "@/types/chatbiMetadataGuide";

defineProps<{ guide: ChatBIMetadataGuide }>();
const emit = defineEmits<{ (event: 'select', query: string): void }>();
</script>
