<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { getSystemHealth, getCeleryStatus, type SystemHealth, type CeleryStatus } from '$lib/apis/videoBridge';

	let health: SystemHealth | null = null;
	let celery: CeleryStatus | null = null;
	let loading = true;
	let error = '';
	let polling: ReturnType<typeof setInterval> | null = null;

	const refresh = async () => {
		try {
			[health, celery] = await Promise.all([
				getSystemHealth(),
				getCeleryStatus()
			]);
			error = '';
		} catch (e: any) {
			error = e.message;
		} finally {
			loading = false;
		}
	};

	const statusColor = (status: string) => {
		if (status === 'ok' || status === 'online') return 'text-green-500';
		if (status === 'degraded' || status === 'no_workers') return 'text-yellow-500';
		return 'text-red-500';
	};

	const statusDot = (status: string) => {
		if (status === 'ok' || status === 'online') return 'bg-green-500';
		if (status === 'degraded' || status === 'no_workers') return 'bg-yellow-500';
		return 'bg-red-500';
	};

	onMount(() => {
		refresh();
		polling = setInterval(refresh, 10000);
	});

	onDestroy(() => {
		if (polling) clearInterval(polling);
	});
</script>

<div class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4">
	<div class="flex items-center justify-between mb-3">
		<div class="flex items-center gap-2">
			<svg class="w-5 h-5 text-purple-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
			</svg>
			<h3 class="font-medium text-sm">系统监控</h3>
		</div>
		<button class="text-xs px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800" on:click={refresh}>
			刷新
		</button>
	</div>

	{#if loading}
		<div class="text-center text-sm text-gray-400 py-4">加载中...</div>
	{:else if error}
		<div class="text-center text-sm text-red-500 py-4">
			{error}
			<div class="text-xs text-gray-400 mt-1">请确保大模型项目后端(port 8000)正在运行</div>
		</div>
	{:else}
		<!-- Overall Status -->
		<div class="flex items-center gap-2 mb-3 text-sm">
			<span class="w-2.5 h-2.5 rounded-full {statusDot(health?.overall || '')} {health?.overall === 'ok' ? 'animate-pulse' : ''}"></span>
			<span class={statusColor(health?.overall || '')}>
				{health?.overall === 'ok' ? '系统正常' : health?.overall === 'degraded' ? '系统降级' : '系统异常'}
			</span>
		</div>

		<!-- Service Checks -->
		{#if health?.checks}
			<div class="space-y-1.5 mb-3">
				{#each Object.entries(health.checks) as [name, check]}
					<div class="flex items-center justify-between text-xs">
						<div class="flex items-center gap-1.5">
							<span class="w-1.5 h-1.5 rounded-full {statusDot(check.status)}"></span>
							<span class="text-gray-600 dark:text-gray-300 capitalize">{name}</span>
						</div>
						<span class="text-gray-400">
							{#if name === 'redis' && check.used_memory_mb}
								{check.used_memory_mb}MB
							{:else if name === 'celery'}
								{check.worker_count} workers
							{:else if name === 'ai_model' && check.mode}
								{check.mode}
							{:else if name === 'storage' && check.total_size_mb !== undefined}
								{check.total_size_mb}MB
							{:else}
								{check.status}
							{/if}
						</span>
					</div>
				{/each}
			</div>
		{/if}

		<!-- Celery Workers -->
		{#if celery && celery.worker_count > 0}
			<div class="border-t border-gray-100 dark:border-gray-800 pt-2">
				<div class="text-xs text-gray-400 mb-1">Celery Workers ({celery.worker_count})</div>
				{#each celery.workers as worker}
					<div class="flex items-center justify-between text-xs py-0.5">
						<span class="truncate text-gray-600 dark:text-gray-300">{worker.worker_name}</span>
						<span class="text-gray-400 shrink-0 ml-2">{worker.active_tasks} active</span>
					</div>
				{/each}
			</div>
		{:else if celery}
			<div class="border-t border-gray-100 dark:border-gray-800 pt-2 text-xs text-yellow-500">
				⚠️ No Celery workers running
			</div>
		{/if}
	{/if}
</div>
