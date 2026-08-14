import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const outDir = process.env.FINANCIAL_TERMINAL_OUT_DIR || 'dist'

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'disable-cloudflare-rocket-loader',
      transformIndexHtml(html) {
        return html.replace(
          /<script(?![^>]*data-cfasync=)([^>]*type="module"[^>]*)>/g,
          '<script data-cfasync="false"$1>',
        )
      },
    },
  ],
  base: process.env.NODE_ENV === 'production' ? '/fin-terminal/' : '/',
  build: {
    outDir,
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (id.includes('node_modules/react') || id.includes('node_modules/react-dom') || id.includes('node_modules/scheduler')) return 'vendor-react'
          if (id.includes('node_modules')) return 'vendor-other'
        },
      },
    },
  },
  server: {
    port: 5175,
    strictPort: true,
  },
})
