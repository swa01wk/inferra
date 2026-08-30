import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // VITE_API_TARGET: where the Vite dev-server proxy forwards API calls.
  // Defaults to VITE_GATEWAY_URL for backwards-compat, then to localhost:9100.
  const apiTarget = env.VITE_API_TARGET || env.VITE_GATEWAY_URL || 'http://localhost:9100'

  return {
    plugins: [react()],
    server: {
      proxy: {
        '/v1': { target: apiTarget, changeOrigin: true },
        '/health': { target: apiTarget, changeOrigin: true },
        '/metrics': { target: apiTarget, changeOrigin: true },
      },
    },
  }
})
