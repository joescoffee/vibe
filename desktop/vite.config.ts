import react from '@vitejs/plugin-react'
import { paraglideVitePlugin } from '@inlang/paraglide-js'
import tailwindcss from '@tailwindcss/vite'
// `vitest/config` re-exports vite's defineConfig and adds the `test` field's types.
import { defineConfig } from 'vitest/config'
import svgr from 'vite-plugin-svgr'

// https://vitejs.dev/config/
export default defineConfig(async () => ({
	plugins: [
		paraglideVitePlugin({
			project: './project.inlang',
			outdir: './src/paraglide',
			emitTsDeclarations: true,
			strategy: ['localStorage', 'globalVariable', 'baseLocale'],
		}),
		react(),
		tailwindcss(),
		svgr({
			// allow import it as regular react component
			svgrOptions: { exportType: 'named', ref: true, svgo: false, titleProp: true },
			include: '**/*.svg',
		}),
	],

	resolve: {
		alias: {
			'~': '/src',
		},
	},
	// Tests still opt into a DOM per file with a `// @vitest-environment jsdom` docblock;
	// this sets no global environment. setupFiles exists for one reason, see vitest.setup.ts.
	test: {
		setupFiles: ['./vitest.setup.ts'],
	},
	clearScreen: false,
	server: {
		port: 1420,
		strictPort: true,
		watch: {
			ignored: ['**/src-tauri/**'],
		},
	},
}))
