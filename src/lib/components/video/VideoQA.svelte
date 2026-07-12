<script lang="ts">
	import { submitVideoQA } from '$lib/apis/videoBridge';

	export let videoId: number | null = null;

	let question = '';
	let loading = false;
	let result: { answer?: string; status?: string; analysis_id?: number } | null = null;
	let error = '';

	const suggestions = [
		'这个视频的主要内容是什么？',
		'视频中有哪些关键场景？',
		'描述视频的运动特征和节奏',
		'视频的拍摄风格和色调如何？'
	];

	const ask = async () => {
		if (!videoId || !question.trim()) return;
		loading = true;
		error = '';
		result = null;
		try {
			const resp = await submitVideoQA(videoId, question);
			result = {
				status: resp.status,
				analysis_id: resp.analysis_id
			};
			// Poll for result
			setTimeout(checkResult, 3000);
		} catch (e: any) {
			error = e.message;
		} finally {
			loading = false;
		}
	};

	let pollCount = 0;
	const checkResult = async () => {
		if (!result?.analysis_id || pollCount > 10) return;
		pollCount++;
		try {
			const res = await fetch(`/api/v1/video-bridge/analysis/${result.analysis_id}`, {
				headers: { Authorization: `Bearer ${localStorage.token}` }
			});
			const data = await res.json();
			if (data.status === 'completed') {
				result = { ...result, answer: data.analysis_result?.answer || data.analysis_result, status: 'completed' };
				pollCount = 0;
			} else if (data.status === 'failed') {
				result = { ...result, status: 'failed' };
				pollCount = 0;
			} else {
				setTimeout(checkResult, 3000);
			}
		} catch {
			pollCount = 0;
		}
	};
</script>

<div class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4">
	<div class="flex items-center gap-2 mb-3">
		<svg class="w-5 h-5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
		</svg>
		<h3 class="font-medium text-sm">视频问答 (QA)</h3>
	</div>

	{#if !videoId}
		<div class="text-center text-xs text-gray-400 py-3">
			需要先在大模型项目中上传视频才能使用QA功能
		</div>
	{:else}
		<!-- Suggestion chips -->
		<div class="flex flex-wrap gap-1 mb-2">
			{#each suggestions as s}
				<button
					class="text-xs px-2 py-1 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-blue-100 dark:hover:bg-blue-900 hover:text-blue-600 transition"
					on:click={() => question = s}
				>
					{s}
				</button>
			{/each}
		</div>

		<!-- Input -->
		<div class="flex gap-2 mb-2">
			<input
				type="text"
				bind:value={question}
				on:keydown={(e) => e.key === 'Enter' && !loading && ask()}
				placeholder="输入关于视频的问题..."
				class="flex-1 px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent text-sm"
				disabled={loading}
			/>
			<button
				class="px-4 py-2 rounded-lg bg-green-500 text-white text-sm hover:bg-green-600 transition disabled:opacity-50"
				on:click={ask}
				disabled={loading || !question.trim()}
			>
				{loading ? '提问中...' : '提问'}
			</button>
		</div>

		<!-- Error -->
		{#if error}
			<div class="text-xs text-red-500 p-2 rounded bg-red-50 dark:bg-red-900/20">{error}</div>
		{/if}

		<!-- Result -->
		{#if result}
			<div class="mt-2 p-3 rounded-lg bg-gray-50 dark:bg-gray-800 text-sm">
				{#if result.status === 'pending' || result.status === 'running'}
					<div class="flex items-center gap-2 text-gray-500">
						<svg class="w-4 h-4 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<circle cx="12" cy="12" r="10" stroke-width="4" class="opacity-25" />
							<path stroke-linecap="round" stroke-width="4" d="M4 12a8 8 0 018-8" />
						</svg>
						AI正在分析视频...
					</div>
				{:else if result.status === 'completed'}
					<div class="text-gray-700 dark:text-gray-200">
						<div class="text-xs text-green-500 mb-1">✅ 回答完成</div>
						{result.answer || '分析结果已保存'}
					</div>
				{:else if result.status === 'failed'}
					<div class="text-red-500">分析失败</div>
				{/if}
			</div>
		{/if}
	{/if}
</div>
