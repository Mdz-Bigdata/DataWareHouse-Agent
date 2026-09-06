import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const gatewayTarget = env.PLATFORM_GATEWAY_URL || 'http://127.0.0.1:8080'
  const backendTarget = env.CORE_BACKEND_URL || 'http://127.0.0.1:8000'
  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        '/api/platform': { target: gatewayTarget, changeOrigin: true },
        '/platform': { target: gatewayTarget, changeOrigin: false },
        '/api': { target: backendTarget, changeOrigin: true },
      },
    },
    build: {
      cssMinify: false,
    },
  }
})
