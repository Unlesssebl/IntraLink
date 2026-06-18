import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { viteSingleFile } from 'vite-plugin-singlefile';

export default defineConfig({
  plugins: [vue(), viteSingleFile()],
  base: '/admin/',
  build: {
    outDir: '../app/static/admin',
    emptyOutDir: true
  },
  server: {
    proxy: {
      '/admin/api': 'http://127.0.0.1:8000'
    }
  }
});
