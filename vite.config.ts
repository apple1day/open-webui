import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

import { viteStaticCopy } from 'vite-plugin-static-copy';

export default defineConfig({
	plugins: [
		sveltekit(),
		viteStaticCopy({
			targets: [
				{
					src: 'node_modules/onnxruntime-web/dist/*.jsep.*',

					dest: 'wasm'
				}
			]
		})
	],
	define: {
		APP_VERSION: JSON.stringify(process.env.npm_package_version),
		APP_BUILD_HASH: JSON.stringify(process.env.APP_BUILD_HASH || 'dev-build')
	},
	build: {
		sourcemap: true
	},
	server: {
		// 监听所有网卡，供局域网同事访问
		host: true,
		// Vite 5 默认会拦截非 localhost/IP 的 Host 头（否则用域名访问会报
		// "Blocked request. This host is not allowed."）。这里放行内网域名。
		allowedHosts: [
			'jiayinghou-any9.devcloud.woa.com',
			'.devcloud.woa.com',
			'.woa.com',
			'localhost'
		]
	},
	worker: {
		format: 'es'
	},
	esbuild: {
		pure: process.env.ENV === 'dev' ? [] : ['console.log', 'console.debug', 'console.error']
	}
});
