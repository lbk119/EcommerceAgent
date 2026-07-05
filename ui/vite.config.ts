import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:9090',
        changeOrigin: true,
        // 前端 WebSocket 现在统一走 /api/v1/ws/{thread_id}。
        // Vite 需要在 /api 代理上显式开启 ws，否则浏览器连到开发服务器后，升级请求不会稳定转发到 Go Gateway。
        ws: true,
      },
    },
  },
})
