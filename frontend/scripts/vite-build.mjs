import path from 'node:path';
import { build, defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const root = process.cwd();

await build(
  defineConfig({
    configFile: false,
    root,
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(root, '.'),
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: 'hidden',
    },
  }),
);
