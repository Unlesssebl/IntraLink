import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { viteSingleFile } from 'vite-plugin-singlefile';
import path from 'path';

export default defineConfig({
  plugins: [vue(), viteSingleFile()],
  base: '/admin/',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    outDir: '../core-api/app/static/admin',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/admin/api': 'http://127.0.0.1:8000',
    },
  },
});
