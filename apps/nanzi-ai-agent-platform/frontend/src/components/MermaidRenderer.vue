<script setup lang="ts">
import { ref, onMounted, watch, nextTick } from 'vue';
import mermaid from 'mermaid';

const props = defineProps({
  content: String, // The Mermaid code (e.g., "graph TD\n A-->B")
  id: String, // Optional unique ID
});

const containerRef = ref<HTMLElement | null>(null);
const uniqueId = `mermaid-${props.id || Math.random().toString(36).substr(2, 9)}`;

const renderChart = async () => {
  if (!containerRef.value || !props.content) return;

  try {
    // 1. Reset
    containerRef.value.innerHTML = '';
    
    // 2. Initialize (Check theme)
    const isDark = document.documentElement.classList.contains('dark');
    mermaid.initialize({
      startOnLoad: false,
      theme: isDark ? 'dark' : 'default',
      securityLevel: 'loose', // Allow basic HTML in nodes
      fontFamily: 'ui-sans-serif, system-ui, -apple-system, sans-serif'
    });

    // 3. Render
    // mermaid.render returns an object { svg: string } in newer versions
    const { svg } = await mermaid.render(uniqueId, props.content);
    containerRef.value.innerHTML = svg;
    
  } catch (error) {
    console.error('Mermaid render failed:', error);
    if (containerRef.value) {
       containerRef.value.innerHTML = `<div class="text-red-500 text-xs font-mono p-2 border border-red-200 rounded bg-red-50">Mermaid Error: ${error instanceof Error ? error.message : String(error)}</div><pre class="text-xs text-gray-400 mt-2">${props.content}</pre>`;
    }
  }
};

onMounted(() => {
  nextTick(() => renderChart());
});

watch(() => props.content, () => {
  nextTick(() => renderChart());
});

// Watch for theme changes (Optional: use MutationObserver if themes switch dynamically without reload)
</script>

<template>
  <div class="mermaid-container w-full overflow-x-auto p-4 bg-white dark:bg-gray-800 rounded-lg border border-gray-100 dark:border-gray-700 shadow-sm flex justify-center items-center">
    <div ref="containerRef" class="w-full text-center">
        <!-- SVG will be injected here -->
        <div class="animate-pulse flex justify-center py-4">
            <div class="h-4 w-32 bg-gray-200 rounded"></div>
        </div>
    </div>
  </div>
</template>

<style>
/* Scoped styles don't always apply to injected SVG, so we use global with specificity if needed */
.mermaid-container svg {
  max-width: 100%;
  height: auto;
}
</style>
