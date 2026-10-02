import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${process.env.STREAM_ANALYSIS_SMOKE_API_PORT ?? '8000'}`,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
});
