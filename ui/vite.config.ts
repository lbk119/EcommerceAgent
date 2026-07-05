import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:9090',
      '/ws': {
        target: 'ws://127.0.0.1:9090',
        ws: true,
      },
      '/outputs': 'http://127.0.0.1:9090',
    },
  },
})
