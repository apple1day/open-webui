<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { getTrainingStatus, getTrainingProgress, getTrainingDataPreview, startTraining, type TrainingStatus, type TrainingProgress, type TrainingDataPreview } from '$lib/apis/videoBridge';

	let status: TrainingStatus | null = null;
	let progress: TrainingProgress | null = null;
	let dataPreview: TrainingDataPreview | null = null;
	let loading = true;
	let error = '';
	let polling: ReturnType<typeof setInterval> | null = null;
	let showStartDialog = false;
	let startSteps = 30;
	let startLR = 0.00005;
	let startRank = 4;
	let starting = false;

	const refresh = async () => {
		try {
			[status, progress, dataPreview] = await Promise.all([
				getTrainingStatus(),
				getTrainingProgress(),
				getTrainingDataPreview()
			]);
			error = '';
		} catch (e: any) {
			error = e.message || 'Failed to load training data';
		} finally {
			loading = false;
		}
	};

	const handleStartTraining = async () => {
		starting = true;
		try {
			await startTraining({ steps: startSteps, learning_rate: startLR, rank: startRank });
			showStartDialog = false;
			await refresh();
		} catch (e: any) {
			error = e.message;
		} finally {
			starting = false;
		}
	};

	const formatTime = (seconds: number | undefined) => {
		if (!seconds) return '-';
		const m = Math.floor(seconds / 60);
		const s = Math.round(seconds % 60);
		return `${m}m ${s}s`;
	};

	onMount(() => {
		refresh();
		polling = setInterval(refresh, 5000);
	});

	onDestroy(() => {
		if (polling) clearInterval(polling);
	});
</script>

<div class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4">
	<div class="flex items-center justify-between mb-3">
		<div class="flex items-center gap-2">
			<svg class="w-5 h-5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
			</svg>
			<h3 class="font-medium text-sm">训练状态</h3>
		</div>
		<div class="flex items-center gap-2">
			{#if status?.is_training || progress?.status === 'running'}
				<span class="flex items-center gap-1 text-xs text-green-600">
					<span class="w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>
					训练中
				</span>
			{:else}
				<span class="flex items-center gap-1 text-xs text-gray-400">
					<span class="w-2 h-2 rounded-full bg-gray-300"></span>
					空闲
				</span>
			{/if}
			<button class="text-xs px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800" on:click={refresh}>
				刷新
			</button>
		</div>
	</div>

	{#if loading}
		<div class="text-center text-sm text-gray-400 py-4">加载中...</div>
	{:else if error}
		<div class="text-center text-sm text-red-500 py-4">
			{error}
			<div class="text-xs text-gray-400 mt-1">请确保大模型项目后端(port 8000)正在运行</div>
		</div>
	{:else}
		<!-- Training Progress -->
		{#if progress?.status === 'running'}
			<div class="mb-3 p-3 rounded-lg bg-green-50 dark:bg-green-900/20">
				<div class="flex justify-between text-xs mb-1">
					<span class="text-green-700 dark:text-green-300">训练进度</span>
					<span class="text-green-600 dark:text-green-400">
						Step {progress.current_step}/{progress.total_steps}
					</span>
				</div>
				<div class="w-full h-2 bg-green-200 dark:bg-green-800 rounded-full overflow-hidden">
					<div class="h-full bg-green-500 transition-all duration-500" style="width: {((progress.current_step || 0) / (progress.total_steps || 1)) * 100}%"></div>
				</div>
				<div class="flex justify-between text-xs mt-2 text-gray-500">
					<span>Loss: {progress.current_loss?.toFixed(4) || '-'}</span>
					<span>ETA: {formatTime(progress.eta_seconds)}</span>
					<span>耗时: {formatTime(progress.elapsed_seconds)}</span>
				</div>
			</div>
		{:else if progress?.status === 'completed'}
			<div class="mb-3 p-3 rounded-lg bg-blue-50 dark:bg-blue-900/20 text-xs text-blue-700 dark:text-blue-300">
				✅ 最近训练完成 · Final Loss: {progress.final_loss?.toFixed(4) || '-'}
			</div>
		{:else if progress?.status === 'failed'}
			<div class="mb-3 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 text-xs text-red-700 dark:text-red-300">
				❌ 训练失败: {progress.message || 'Unknown error'}
			</div>
		{/if}

		<!-- Training Data -->
		<div class="grid grid-cols-3 gap-2 mb-3">
			<div class="text-center p-2 rounded-lg bg-gray-50 dark:bg-gray-800">
				<div class="text-lg font-bold text-blue-600">{dataPreview?.training_pairs_count ?? status?.training_data_count ?? 0}</div>
				<div class="text-xs text-gray-400">训练样本</div>
			</div>
			<div class="text-center p-2 rounded-lg bg-gray-50 dark:bg-gray-800">
				<div class="text-lg font-bold text-green-600">{status?.lora_weights?.length ?? 0}</div>
				<div class="text-xs text-gray-400">LoRA权重</div>
			</div>
			<div class="text-center p-2 rounded-lg bg-gray-50 dark:bg-gray-800">
				<div class="text-lg font-bold text-purple-600">{dataPreview?.image_count ?? 0}</div>
				<div class="text-xs text-gray-400">缓存图像</div>
			</div>
		</div>

		<!-- LoRA Weights -->
		{#if status?.lora_weights && status.lora_weights.length > 0}
			<div class="mb-3">
				<div class="text-xs text-gray-400 mb-1">LoRA权重文件</div>
				{#each status.lora_weights.slice(0, 3) as weight}
					<div class="flex items-center justify-between text-xs py-1 px-2 rounded hover:bg-gray-50 dark:hover:bg-gray-800">
						<span class="truncate">{weight.name}</span>
						<span class="text-gray-400 shrink-0 ml-2">{weight.size_mb}MB</span>
					</div>
				{/each}
			</div>
		{/if}

		<!-- Sample Prompts Preview -->
		{#if dataPreview?.sample_pairs && dataPreview.sample_pairs.length > 0}
			<div class="mb-3">
				<div class="text-xs text-gray-400 mb-1">最新训练样本</div>
				<div class="text-xs text-gray-600 dark:text-gray-300 p-2 rounded bg-gray-50 dark:bg-gray-800 max-h-20 overflow-y-auto">
					{dataPreview.sample_pairs[0].prompt.substring(0, 120)}...
				</div>
			</div>
		{/if}

		<!-- Start Training Button -->
		{#if !status?.is_training && progress?.status !== 'running'}
			<button
				class="w-full py-2 rounded-lg bg-blue-500 text-white text-sm hover:bg-blue-600 transition"
				on:click={() => showStartDialog = true}
			>
				手动触发训练
			</button>
		{/if}
	{/if}
</div>

{#if showStartDialog}
	<div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50" on:click|self={() => showStartDialog = false}>
		<div class="bg-white dark:bg-gray-900 rounded-xl p-6 w-96 max-w-[90vw]">
			<h3 class="text-lg font-medium mb-4">启动训练</h3>
			<div class="space-y-3">
				<div>
					<label class="text-xs text-gray-400">训练步数</label>
					<input type="number" bind:value={startSteps} min="10" max="200" class="w-full mt-1 px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent text-sm" />
				</div>
				<div>
					<label class="text-xs text-gray-400">学习率</label>
					<input type="number" bind:value={startLR} step="0.00001" min="0.00001" max="0.001" class="w-full mt-1 px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent text-sm" />
				</div>
				<div>
					<label class="text-xs text-gray-400">LoRA Rank</label>
					<input type="number" bind:value={startRank} min="2" max="16" class="w-full mt-1 px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent text-sm" />
				</div>
			</div>
			<div class="flex gap-2 mt-4">
				<button class="flex-1 py-2 rounded-lg border border-gray-200 dark:border-gray-700 text-sm" on:click={() => showStartDialog = false}>取消</button>
				<button class="flex-1 py-2 rounded-lg bg-blue-500 text-white text-sm hover:bg-blue-600" on:click={handleStartTraining} disabled={starting}>
					{starting ? '启动中...' : '开始训练'}
				</button>
			</div>
		</div>
	</div>
{/if}
