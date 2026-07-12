<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		getPipelineStatus,
		getAutoTrainConfig,
		updateAutoTrainConfig,
		getLatestWeight,
		cleanupOldWeights,
		type PipelineStatus,
		type AutoTrainConfig,
		type LatestWeightInfo
	} from '$lib/apis/videoBridge';

	let pipeline: PipelineStatus | null = null;
	let loading = true;
	let error = '';
	let polling: ReturnType<typeof setInterval> | null = null;

	// Auto-train config editing
	let editingConfig = false;
	let configDraft: AutoTrainConfig | null = null;
	let savingConfig = false;

	// Weight cleanup
	let cleaningUp = false;

	const refresh = async () => {
		try {
			pipeline = await getPipelineStatus();
			error = '';
		} catch (e: any) {
			error = e.message || 'Failed to load pipeline status';
		} finally {
			loading = false;
		}
	};

	const startEditConfig = () => {
		if (pipeline?.auto_train) {
			configDraft = { ...pipeline.auto_train };
			editingConfig = true;
		}
	};

	const saveConfig = async () => {
		if (!configDraft) return;
		savingConfig = true;
		try {
			await updateAutoTrainConfig({
				enabled: configDraft.enabled,
				threshold: configDraft.threshold,
				max_train_steps: configDraft.max_train_steps
			});
			toast.success('自动训练配置已更新');
			editingConfig = false;
			await refresh();
		} catch (e: any) {
			toast.error(e.message || '配置更新失败');
		} finally {
			savingConfig = false;
		}
	};

	const toggleAutoTrain = async () => {
		if (!pipeline?.auto_train) return;
		const newEnabled = !pipeline.auto_train.enabled;
		try {
			await updateAutoTrainConfig({
				enabled: newEnabled,
				threshold: pipeline.auto_train.threshold,
				max_train_steps: pipeline.auto_train.max_train_steps
			});
			toast.success(newEnabled ? '自动训练已开启' : '自动训练已关闭');
			await refresh();
		} catch (e: any) {
			toast.error(e.message);
		}
	};

	const handleCleanup = async () => {
		cleaningUp = true;
		try {
			const result = await cleanupOldWeights(3);
			toast.success(result.message || '清理完成');
			await refresh();
		} catch (e: any) {
			toast.error(e.message);
		} finally {
			cleaningUp = false;
		}
	};

	// Pipeline stage status helper
	const stageStatus = (active: boolean, hasData: boolean): string => {
		if (active) return 'active';
		if (hasData) return 'ready';
		return 'idle';
	};

	onMount(() => {
		refresh();
		polling = setInterval(refresh, 15000);
	});

	onDestroy(() => {
		if (polling) clearInterval(polling);
	});
</script>

<div class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4">
	<div class="flex items-center justify-between mb-4">
		<div class="flex items-center gap-2">
			<svg class="w-5 h-5 text-indigo-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
			</svg>
			<h3 class="font-medium text-sm">闭环流水线</h3>
		</div>
		<div class="flex items-center gap-2">
			{#if pipeline?.pipeline_active}
				<span class="flex items-center gap-1 text-xs text-green-600">
					<span class="w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>
					自动闭环
				</span>
			{:else}
				<span class="flex items-center gap-1 text-xs text-gray-400">
					<span class="w-2 h-2 rounded-full bg-gray-300"></span>
					手动模式
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
	{:else if pipeline}
		<!-- Pipeline Flow Diagram -->
		<div class="flex items-center justify-between mb-4 px-2">
			<!-- Stage 1: Analysis -->
			<div class="flex flex-col items-center gap-1 flex-1">
				<div
					class="w-10 h-10 rounded-full flex items-center justify-center text-xs font-bold
					{stageStatus(false, pipeline.analysis.completed > 0) === 'ready'
						? 'bg-blue-100 text-blue-600 dark:bg-blue-900/30'
						: 'bg-gray-100 text-gray-400 dark:bg-gray-800'}"
				>
					📊
				</div>
				<div class="text-xs text-center">
					<div class="font-medium">分析</div>
					<div class="text-gray-400">{pipeline.analysis.completed}/{pipeline.analysis.total}</div>
				</div>
			</div>

			<!-- Arrow -->
			<div class="text-gray-300 text-lg">→</div>

			<!-- Stage 2: Dataset -->
			<div class="flex flex-col items-center gap-1 flex-1">
				<div
					class="w-10 h-10 rounded-full flex items-center justify-center text-xs font-bold
					{stageStatus(false, pipeline.dataset.total > 0) === 'ready'
						? 'bg-purple-100 text-purple-600 dark:bg-purple-900/30'
						: 'bg-gray-100 text-gray-400 dark:bg-gray-800'}"
				>
					📦
				</div>
				<div class="text-xs text-center">
					<div class="font-medium">数据集</div>
					<div class="text-gray-400">{pipeline.dataset.total} 条</div>
				</div>
			</div>

			<!-- Arrow -->
			<div class="text-gray-300 text-lg">→</div>

			<!-- Stage 3: Training -->
			<div class="flex flex-col items-center gap-1 flex-1">
				<div
					class="w-10 h-10 rounded-full flex items-center justify-center text-xs font-bold
					{stageStatus(pipeline.training.is_training, pipeline.training.lora_count > 0) === 'active'
						? 'bg-green-100 text-green-600 dark:bg-green-900/30 animate-pulse'
						: stageStatus(pipeline.training.is_training, pipeline.training.lora_count > 0) === 'ready'
							? 'bg-green-100 text-green-600 dark:bg-green-900/30'
							: 'bg-gray-100 text-gray-400 dark:bg-gray-800'}"
				>
					🔥
				</div>
				<div class="text-xs text-center">
					<div class="font-medium">训练</div>
					<div class="text-gray-400">
						{pipeline.training.is_training ? '训练中' : `${pipeline.training.lora_count} 权重`}
					</div>
				</div>
			</div>

			<!-- Arrow -->
			<div class="text-gray-300 text-lg">→</div>

			<!-- Stage 4: Generation -->
			<div class="flex flex-col items-center gap-1 flex-1">
				<div
					class="w-10 h-10 rounded-full flex items-center justify-center text-xs font-bold
					{stageStatus(false, pipeline.generation.completed > 0) === 'ready'
						? 'bg-orange-100 text-orange-600 dark:bg-orange-900/30'
						: 'bg-gray-100 text-gray-400 dark:bg-gray-800'}"
				>
					🎬
				</div>
				<div class="text-xs text-center">
					<div class="font-medium">生成</div>
					<div class="text-gray-400">{pipeline.generation.completed}/{pipeline.generation.total}</div>
				</div>
			</div>

			<!-- Arrow back (feedback loop) -->
			<div class="text-gray-300 text-lg">↺</div>

			<!-- Stage 5: Quality Feedback -->
			<div class="flex flex-col items-center gap-1 flex-1">
				<div
					class="w-10 h-10 rounded-full flex items-center justify-center text-xs font-bold
					{pipeline.generation.avg_quality > 0
						? 'bg-yellow-100 text-yellow-600 dark:bg-yellow-900/30'
						: 'bg-gray-100 text-gray-400 dark:bg-gray-800'}"
				>
					⭐
				</div>
				<div class="text-xs text-center">
					<div class="font-medium">质量</div>
					<div class="text-gray-400">{pipeline.generation.avg_quality || '-'}</div>
				</div>
			</div>
		</div>

		<!-- Detailed Stats Grid -->
		<div class="grid grid-cols-2 gap-2 mb-3">
			<!-- Dataset Stats -->
			<div class="p-2 rounded-lg bg-gray-50 dark:bg-gray-800">
				<div class="text-xs text-gray-400 mb-1">数据集统计</div>
				<div class="text-xs space-y-0.5">
					<div class="flex justify-between">
						<span>总条目</span><span class="font-mono">{pipeline.dataset.total}</span>
					</div>
					<div class="flex justify-between">
						<span>已处理</span><span class="font-mono">{pipeline.dataset.processed}</span>
					</div>
					<div class="flex justify-between">
						<span>平均美学分</span><span class="font-mono">{pipeline.dataset.avg_aesthetic}</span>
					</div>
					<div class="flex justify-between">
						<span>平均运动分</span><span class="font-mono">{pipeline.dataset.avg_motion}</span>
					</div>
				</div>
			</div>

			<!-- Training Stats -->
			<div class="p-2 rounded-lg bg-gray-50 dark:bg-gray-800">
				<div class="text-xs text-gray-400 mb-1">训练状态</div>
				<div class="text-xs space-y-0.5">
					<div class="flex justify-between">
						<span>训练数据</span><span class="font-mono">{pipeline.training.training_data_count}</span>
					</div>
					<div class="flex justify-between">
						<span>LoRA权重</span><span class="font-mono">{pipeline.training.lora_count}</span>
					</div>
					<div class="flex justify-between">
						<span>当前状态</span>
						<span class="font-mono {pipeline.training.is_training ? 'text-green-500' : ''}">
							{pipeline.training.is_training ? '训练中' : '空闲'}
						</span>
					</div>
					{#if pipeline.latest_weight?.active}
						<div class="flex justify-between">
							<span>最新权重</span>
							<span class="font-mono text-xs truncate max-w-[80px]" title={pipeline.latest_weight.name}>
								{pipeline.latest_weight.name}
							</span>
						</div>
					{/if}
				</div>
			</div>
		</div>

		<!-- Auto-Train Config -->
		{#if pipeline.auto_train}
			<div class="p-3 rounded-lg bg-indigo-50 dark:bg-indigo-900/20 mb-3">
				<div class="flex items-center justify-between mb-2">
					<div class="text-xs font-medium text-indigo-700 dark:text-indigo-300">自动训练配置</div>
					<div class="flex items-center gap-2">
						<button
							class="text-xs px-2 py-0.5 rounded-full transition
							{pipeline.auto_train.enabled
								? 'bg-green-500 text-white'
								: 'bg-gray-300 text-gray-600 dark:bg-gray-700 dark:text-gray-400'}"
							on:click={toggleAutoTrain}
						>
							{pipeline.auto_train.enabled ? 'ON' : 'OFF'}
						</button>
						<button
							class="text-xs px-2 py-0.5 rounded hover:bg-indigo-100 dark:hover:bg-indigo-900/40 text-indigo-600"
							on:click={startEditConfig}
						>
							编辑
						</button>
					</div>
				</div>
				{#if !editingConfig}
					<div class="text-xs space-y-0.5 text-indigo-600 dark:text-indigo-400">
						<div class="flex justify-between">
							<span>触发阈值</span>
							<span class="font-mono">{pipeline.auto_train.threshold} 样本</span>
						</div>
						<div class="flex justify-between">
							<span>当前样本</span>
							<span class="font-mono">
								{pipeline.auto_train.current_samples ?? 0} / {pipeline.auto_train.threshold}
							</span>
						</div>
						{#if pipeline.auto_train.progress_percent != null}
							<div class="w-full h-1 bg-indigo-200 dark:bg-indigo-800 rounded-full overflow-hidden mt-1">
								<div
									class="h-full bg-indigo-500 transition-all"
									style="width: {pipeline.auto_train.progress_percent}%"
								></div>
							</div>
						{/if}
						<div class="flex justify-between">
							<span>训练步数</span>
							<span class="font-mono">{pipeline.auto_train.max_train_steps}</span>
						</div>
					</div>
				{:else if configDraft}
					<div class="space-y-2">
						<div>
							<label class="text-xs text-gray-400">触发阈值（样本数）</label>
							<input
								type="number"
								bind:value={configDraft.threshold}
								min="5"
								max="100"
								class="w-full mt-0.5 px-2 py-1 rounded border border-gray-200 dark:border-gray-700 bg-transparent text-xs"
							/>
						</div>
						<div>
							<label class="text-xs text-gray-400">训练步数</label>
							<input
								type="number"
								bind:value={configDraft.max_train_steps}
								min="10"
								max="500"
								class="w-full mt-0.5 px-2 py-1 rounded border border-gray-200 dark:border-gray-700 bg-transparent text-xs"
							/>
						</div>
						<div class="flex gap-2">
							<button
								class="flex-1 py-1 rounded border border-gray-200 dark:border-gray-700 text-xs"
								on:click={() => editingConfig = false}
							>
								取消
							</button>
							<button
								class="flex-1 py-1 rounded bg-indigo-500 text-white text-xs hover:bg-indigo-600"
								on:click={saveConfig}
								disabled={savingConfig}
							>
								{savingConfig ? '保存中...' : '保存'}
							</button>
						</div>
					</div>
				{/if}
			</div>
		{/if}

		<!-- Latest Weight Info -->
		{#if pipeline.latest_weight?.active}
			<div class="p-2 rounded-lg bg-gray-50 dark:bg-gray-800 mb-3">
				<div class="text-xs text-gray-400 mb-1">当前LoRA权重</div>
				<div class="flex items-center justify-between text-xs">
					<div class="flex items-center gap-2">
						<span class="w-2 h-2 rounded-full bg-green-500"></span>
						<span class="font-mono truncate max-w-[120px]" title={pipeline.latest_weight.name}>
							{pipeline.latest_weight.name}
						</span>
					</div>
					<div class="flex items-center gap-2 text-gray-400">
						<span>{pipeline.latest_weight.size_mb}MB</span>
						<button
							class="text-xs px-2 py-0.5 rounded border border-red-200 text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition"
							on:click={handleCleanup}
							disabled={cleaningUp}
						>
							{cleaningUp ? '清理中...' : '清理旧权重'}
						</button>
					</div>
				</div>
			</div>
		{/if}

		<!-- Generation Quality Summary -->
		{#if pipeline.generation.total > 0}
			<div class="p-2 rounded-lg bg-gray-50 dark:bg-gray-800">
				<div class="text-xs text-gray-400 mb-1">生成质量反馈</div>
				<div class="flex items-center justify-between text-xs">
					<span>已完成: {pipeline.generation.completed}/{pipeline.generation.total}</span>
					<span class="flex items-center gap-1">
						平均质量:
						<span class="font-mono font-bold {pipeline.generation.avg_quality >= 0.7
							? 'text-green-500'
							: pipeline.generation.avg_quality >= 0.4
								? 'text-yellow-500'
								: 'text-red-500'}">
							{pipeline.generation.avg_quality || 'N/A'}
						</span>
					</span>
				</div>
			</div>
		{/if}
	{/if}
</div>
