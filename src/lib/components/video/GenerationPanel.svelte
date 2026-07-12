<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		listGenerations,
		deleteGeneration,
		type GenerationItem
	} from '$lib/apis/videoBridge';

	let generations: GenerationItem[] = [];
	let loading = true;
	let error = '';
	let polling: ReturnType<typeof setInterval> | null = null;
	let filterStatus = '';

	const refresh = async () => {
		try {
			generations = await listGenerations(0, 15, filterStatus || undefined);
			error = '';
		} catch (e: any) {
			error = e.message || 'Failed to load generations';
		} finally {
			loading = false;
		}
	};

	const handleDelete = async (id: number) => {
		if (!confirm('确认删除此生成任务？')) return;
		try {
			await deleteGeneration(id);
			toast.success('已删除');
			await refresh();
		} catch (e: any) {
			toast.error(e.message);
		}
	};

	const statusColor = (status: string): string => {
		switch (status) {
			case 'completed': return 'bg-green-100 text-green-600 dark:bg-green-900/30';
			case 'running': return 'bg-blue-100 text-blue-600 dark:bg-blue-900/30 animate-pulse';
			case 'pending': return 'bg-yellow-100 text-yellow-600 dark:bg-yellow-900/30';
			case 'failed': return 'bg-red-100 text-red-600 dark:bg-red-900/30';
			default: return 'bg-gray-100 text-gray-500 dark:bg-gray-800';
		}
	};

	const qualityColor = (score: number | null): string => {
		if (score === null) return 'text-gray-400';
		if (score >= 0.7) return 'text-green-500';
		if (score >= 0.4) return 'text-yellow-500';
		return 'text-red-500';
	};

	const qualityStars = (score: number | null): string => {
		if (score === null) return '☆☆☆';
		const stars = Math.round(score * 3);
		return '★'.repeat(stars) + '☆'.repeat(3 - stars);
	};

	const formatTime = (iso: string): string => {
		if (!iso) return '';
		const d = new Date(iso);
		return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
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
			<svg class="w-5 h-5 text-orange-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
			</svg>
			<h3 class="font-medium text-sm">生成质量监控</h3>
		</div>
		<div class="flex items-center gap-2">
			<select
				bind:value={filterStatus}
				on:change={refresh}
				class="text-xs px-2 py-1 rounded bg-gray-50 dark:bg-gray-800 outline-none"
			>
				<option value="">全部</option>
				<option value="completed">已完成</option>
				<option value="running">生成中</option>
				<option value="pending">等待中</option>
				<option value="failed">失败</option>
			</select>
			<button class="text-xs px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800" on:click={refresh}>
				刷新
			</button>
		</div>
	</div>

	{#if loading}
		<div class="text-center text-sm text-gray-400 py-4">加载中...</div>
	{:else if error}
		<div class="text-center text-sm text-red-500 py-4">{error}</div>
	{:else if generations.length === 0}
		<div class="text-center text-xs text-gray-400 py-4">
			暂无生成任务
		</div>
	{:else}
		<div class="space-y-2 max-h-60 overflow-y-auto">
			{#each generations as gen (gen.id)}
				<div class="p-2 rounded-lg border border-gray-100 dark:border-gray-800">
					<div class="flex items-center justify-between gap-2 mb-1">
						<div class="text-xs truncate flex-1" title={gen.prompt}>
							{gen.prompt.substring(0, 60)}{gen.prompt.length > 60 ? '...' : ''}
						</div>
						<span class="text-xs px-1.5 py-0.5 rounded-full shrink-0 {statusColor(gen.status)}">
							{gen.status}
						</span>
					</div>

					<div class="flex items-center justify-between text-xs text-gray-400">
						<div class="flex items-center gap-2">
							<span>{gen.provider}</span>
							<span>·</span>
							<span>{gen.resolution}</span>
							<span>·</span>
							<span>{gen.duration}s</span>
						</div>
						<div class="flex items-center gap-2">
							{#if gen.status === 'running'}
								<span class="text-blue-500">{gen.progress}%</span>
							{/if}
							{#if gen.quality_score !== null}
								<span class={qualityColor(gen.quality_score)} title="质量评分">
									{qualityStars(gen.quality_score)} {gen.quality_score.toFixed(2)}
								</span>
							{/if}
							<span>{formatTime(gen.created_at)}</span>
							<button
								class="text-gray-400 hover:text-red-500"
								on:click={() => handleDelete(gen.id)}
								title="删除"
							>
								🗑️
							</button>
						</div>
					</div>

					{#if gen.error_message}
						<div class="text-xs text-red-500 mt-1 truncate" title={gen.error_message}>
							⚠ {gen.error_message}
						</div>
					{/if}

					{#if gen.status === 'running'}
						<div class="w-full h-1 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden mt-1">
							<div class="h-full bg-blue-500 transition-all" style="width: {gen.progress}%"></div>
						</div>
					{/if}
				</div>
			{/each}
		</div>

		<!-- Summary -->
		<div class="mt-2 pt-2 border-t border-gray-100 dark:border-gray-800 text-xs text-gray-400 flex justify-between">
			<span>共 {generations.length} 个任务</span>
			<span>
				已完成: {generations.filter(g => g.status === 'completed').length} ·
				平均质量: {(() => {
					const completed = generations.filter(g => g.status === 'completed' && g.quality_score !== null);
					if (!completed.length) return 'N/A';
					return (completed.reduce((s, g) => s + (g.quality_score || 0), 0) / completed.length).toFixed(2);
				})()}
			</span>
		</div>
	{/if}
</div>
