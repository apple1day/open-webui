<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		getDatasetStats,
		listDatasetItems,
		triggerPreprocess,
		deleteDatasetItem,
		updateDatasetItem,
		type DatasetStats,
		type DatasetItem
	} from '$lib/apis/videoBridge';

	export let videoId: number | null = null;

	let stats: DatasetStats | null = null;
	let items: DatasetItem[] = [];
	let loading = true;
	let error = '';
	let showPreprocessInput = false;
	let preprocessVideoId = '';
	let processing = false;

	// Edit state
	let editingId: number | null = null;
	let editCaption = '';

	const refresh = async () => {
		try {
			const [s, list] = await Promise.all([
				getDatasetStats(),
				listDatasetItems(0, 20, undefined, videoId ?? undefined)
			]);
			stats = s;
			items = list.items;
			error = '';
		} catch (e: any) {
			error = e.message || 'Failed to load dataset';
		} finally {
			loading = false;
		}
	};

	const handlePreprocess = async () => {
		const id = parseInt(preprocessVideoId);
		if (!id) return toast.error('请输入有效的视频ID');
		processing = true;
		try {
			const result = await triggerPreprocess(id);
			toast.success(`预处理任务已提交: ${result.celery_task_id?.substring(0, 8) || 'OK'}`);
			showPreprocessInput = false;
			preprocessVideoId = '';
			setTimeout(refresh, 3000);
		} catch (e: any) {
			toast.error(e.message);
		} finally {
			processing = false;
		}
	};

	const handleDelete = async (itemId: number) => {
		if (!confirm('确认删除此数据集条目？')) return;
		try {
			await deleteDatasetItem(itemId);
			toast.success('已删除');
			await refresh();
		} catch (e: any) {
			toast.error(e.message);
		}
	};

	const startEdit = (item: DatasetItem) => {
		editingId = item.id;
		editCaption = item.caption || '';
	};

	const saveEdit = async () => {
		if (editingId === null) return;
		try {
			await updateDatasetItem(editingId, { caption: editCaption });
			toast.success('描述已更新');
			editingId = null;
			await refresh();
		} catch (e: any) {
			toast.error(e.message);
		}
	};

	const scoreColor = (score: number | null): string => {
		if (score === null) return 'text-gray-400';
		if (score >= 0.7) return 'text-green-500';
		if (score >= 0.4) return 'text-yellow-500';
		return 'text-red-500';
	};

	onMount(() => {
		refresh();
	});

	onDestroy(() => {});
</script>

<div class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4">
	<div class="flex items-center justify-between mb-3">
		<div class="flex items-center gap-2">
			<svg class="w-5 h-5 text-purple-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
			</svg>
			<h3 class="font-medium text-sm">数据集管理</h3>
		</div>
		<div class="flex items-center gap-2">
			<button
				class="text-xs px-2 py-1 rounded bg-purple-500 text-white hover:bg-purple-600 transition"
				on:click={() => showPreprocessInput = !showPreprocessInput}
			>
				+ 入库
			</button>
			<button class="text-xs px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800" on:click={refresh}>
				刷新
			</button>
		</div>
	</div>

	{#if loading}
		<div class="text-center text-sm text-gray-400 py-4">加载中...</div>
	{:else if error}
		<div class="text-center text-sm text-red-500 py-4">{error}</div>
	{:else}
		<!-- Stats -->
		{#if stats}
			<div class="grid grid-cols-4 gap-2 mb-3">
				<div class="text-center p-2 rounded-lg bg-gray-50 dark:bg-gray-800">
					<div class="text-lg font-bold text-purple-600">{stats.total}</div>
					<div class="text-xs text-gray-400">总条目</div>
				</div>
				<div class="text-center p-2 rounded-lg bg-gray-50 dark:bg-gray-800">
					<div class="text-lg font-bold text-green-600">{stats.processed}</div>
					<div class="text-xs text-gray-400">已处理</div>
				</div>
				<div class="text-center p-2 rounded-lg bg-gray-50 dark:bg-gray-800">
					<div class="text-lg font-bold text-blue-600">{stats.avg_aesthetic}</div>
					<div class="text-xs text-gray-400">美学分</div>
				</div>
				<div class="text-center p-2 rounded-lg bg-gray-50 dark:bg-gray-800">
					<div class="text-lg font-bold text-orange-600">{stats.avg_motion}</div>
					<div class="text-xs text-gray-400">运动分</div>
				</div>
			</div>
		{/if}

		<!-- Preprocess Input -->
		{#if showPreprocessInput}
			<div class="mb-3 p-3 rounded-lg bg-purple-50 dark:bg-purple-900/20">
				<div class="text-xs text-purple-600 dark:text-purple-300 mb-2">将视频预处理后自动入库（场景检测→片段切割→质量评分）</div>
				<div class="flex gap-2">
					<input
						type="number"
						bind:value={preprocessVideoId}
						placeholder="视频ID"
						class="flex-1 px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent text-sm"
					/>
					<button
						class="px-3 py-1.5 rounded-lg bg-purple-500 text-white text-sm hover:bg-purple-600 disabled:opacity-50"
						on:click={handlePreprocess}
						disabled={processing || !preprocessVideoId}
					>
						{processing ? '处理中...' : '提交'}
					</button>
					<button
						class="px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 text-sm"
						on:click={() => showPreprocessInput = false}
					>
						取消
					</button>
				</div>
			</div>
		{/if}

		<!-- Items List -->
		{#if items.length > 0}
			<div class="space-y-2 max-h-60 overflow-y-auto">
				{#each items as item (item.id)}
					<div class="p-2 rounded-lg border border-gray-100 dark:border-gray-800">
						<div class="flex items-center justify-between gap-2 mb-1">
							<div class="text-xs font-medium truncate flex-1" title={item.video_title}>
								{item.video_title}
							</div>
							<div class="flex items-center gap-1 shrink-0">
								<span
									class="text-xs px-1.5 py-0.5 rounded-full
									{item.status === 'processed'
										? 'bg-green-100 text-green-600 dark:bg-green-900/30'
										: 'bg-gray-100 text-gray-500 dark:bg-gray-800'}"
								>
									{item.status}
								</span>
								<button
									class="text-xs text-gray-400 hover:text-blue-500"
									on:click={() => startEdit(item)}
									title="编辑描述"
								>
									✏️
								</button>
								<button
									class="text-xs text-gray-400 hover:text-red-500"
									on:click={() => handleDelete(item.id)}
									title="删除"
								>
									🗑️
								</button>
							</div>
						</div>

						{#if editingId === item.id}
							<div class="flex gap-1 mb-1">
								<input
									bind:value={editCaption}
									class="flex-1 px-2 py-1 rounded border border-gray-200 dark:border-gray-700 bg-transparent text-xs"
									placeholder="描述..."
								/>
								<button class="text-xs px-2 py-1 rounded bg-blue-500 text-white" on:click={saveEdit}>保存</button>
								<button class="text-xs px-2 py-1 rounded border border-gray-200 dark:border-gray-700" on:click={() => editingId = null}>取消</button>
							</div>
						{:else}
							{#if item.caption}
								<div class="text-xs text-gray-500 dark:text-gray-400 truncate" title={item.caption}>
									{item.caption}
								</div>
							{/if}
						{/if}

						<div class="flex items-center gap-3 text-xs text-gray-400 mt-1">
							{#if item.aesthetic_score !== null}
								<span>美学: <span class={scoreColor(item.aesthetic_score)}>{item.aesthetic_score.toFixed(2)}</span></span>
							{/if}
							{#if item.motion_score !== null}
								<span>运动: <span class={scoreColor(item.motion_score)}>{item.motion_score.toFixed(2)}</span></span>
							{/if}
							{#if item.scene_type}
								<span>场景: {item.scene_type}</span>
							{/if}
						</div>
					</div>
				{/each}
			</div>
		{:else}
			<div class="text-center text-xs text-gray-400 py-4">
				暂无数据集条目
				<div class="mt-1">点击"+入库"将视频预处理后自动添加</div>
			</div>
		{/if}
	{/if}
</div>
