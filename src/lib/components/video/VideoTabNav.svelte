<script lang="ts">
	import { page } from '$app/stores';

	$: currentPath = $page.url.pathname;

	const tabs = [
		{ href: '/video', label: '视频分析', icon: 'analysis' },
		{ href: '/video-generation', label: '视频生成', icon: 'generation' },
		{ href: '/video/history', label: '分析历史', icon: 'history' }
	];

	const isActive = (href: string) => {
		if (href === '/video/history') return currentPath.startsWith('/video/history');
		if (href === '/video-generation') return currentPath.startsWith('/video-generation');
		if (href === '/video') return currentPath === '/video' || (currentPath.startsWith('/video') && !currentPath.startsWith('/video-generation') && !currentPath.startsWith('/video/history'));
		return false;
	};
</script>

<div class="flex items-center gap-1 rounded-lg bg-gray-100 dark:bg-gray-850 p-0.5 text-sm">
	{#each tabs as tab}
		<a
			href={tab.href}
			class="px-3 py-1.5 rounded-md transition flex items-center gap-1.5 {isActive(tab.href)
				? 'bg-white dark:bg-gray-900 shadow font-medium'
				: 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}"
		>
			{#if tab.icon === 'analysis'}
				<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
				</svg>
			{:else if tab.icon === 'generation'}
				<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
				</svg>
			{:else if tab.icon === 'history'}
				<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
				</svg>
			{/if}
			{tab.label}
		</a>
	{/each}
</div>
