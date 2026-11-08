import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    
    
    host: '127.0.0.1',
    port: 5173,
    
    open: false,
    proxy: {
      
      '/api/auth': {
        target: 'http://127.0.0.1:8004',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/auth/, '/auth')
      },
      
      '/api/providers': {
        target: 'http://127.0.0.1:8004',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, '')
      },
      '/api/routes': {
        target: 'http://127.0.0.1:8004',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, '')
      },
      
      '/api/model-pref': {
        target: 'http://127.0.0.1:8004',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, '')
      },
      
      '/api/datasources': {
        target: 'http://127.0.0.1:8004',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, '')
      },
      
      '/api/admin': {
        target: 'http://127.0.0.1:8004',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, '')
      },
      
      '/api/ua': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/ua/, '')
      },
      
      '/api/kb': {
        target: 'http://127.0.0.1:8003',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/kb/, '')
      }
    }
  }
})
