<template>
  <div class="embed-layout w-screen h-screen overflow-hidden bg-transparent">
    <router-view />
  </div>
</template>

<script setup lang="ts">
// Embed Layout - Minimalist full screen container
// Used for /embed/chat route
</script>

<style scoped>
.embed-layout {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 0; /* Let router-view content handle z-index if needed */
}
</style>
