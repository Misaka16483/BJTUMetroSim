import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/amap-api': {
        target: 'https://restapi.amap.com',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/amap-api/, ''),
        headers: {
          // 部分 API 可能需要特定 User-Agent
          'User-Agent': 'Mozilla/5.0',
        },
      },
    },
  },
})
