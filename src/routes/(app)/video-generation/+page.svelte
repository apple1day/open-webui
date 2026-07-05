<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { WEBUI_NAME, showSidebar } from '$lib/stores';
	import {
		getVideoGenHealth,
		getVideoGenModels,
		enhancePrompt,
		suggestPrompts,
		generateVideo,
		listVideoGen,
		type VideoGenResult,
		type VideoGenItem
	} from '$lib/apis/video_generation';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import SidebarIcon from '$lib/components/icons/Sidebar.svelte';

	let loaded = false;
	let ffmpegOk = false;
	let sdAvailable = false;
	let sdStatus = '';
	let models: string[] = [];
	let sdModels: string[] = [];

	// form
	let prompt = '';
	let style = 'cinematic';
	let model = '';
	let duration = 5;
	let resolution = '512x512';
	let fps = 24;
	let mode = 'auto'; // "auto", "ai", "demo"
	let showAdvanced = false;

	// actions
	let enhancing = false;
	let generating = false;
	let enhancedPrompt = '';
	let suggestions: string[] = [];

	// results
	let current: any = null;
	let history: VideoGenItem[] = [];

	const STYLES = [
		{ value: 'cinematic', label: '电影感' },
		{ value: 'anime', label: '动漫' },
		{ value: 'realistic', label: '写实' },
		{ value: 'artistic', label: '艺术' },
		{ value: 'sci-fi', label: '科幻' }
	];

	const token = () => localStorage.token;
	const videoSrc = (url: string) => `${url}?token=${token()}`;

	const enhance = async () => {
		if (!prompt.trim()) return toast.error('请输入视频创意描述');
		enhancing = true;
		try {
			const res = await enhancePrompt(token(), {
				prompt: prompt.trim(),
				style,
				model: model || undefined
			});
			enhancedPrompt = res.enhanced_prompt;
			toast.success('提示词已增强');
		} catch (e: any) {
			toast.error(e?.detail ?? e?.message ?? '增强失败');
		} finally {
			enhancing = false;
		}
	};

	const suggest = async () => {
		if (!prompt.trim()) return toast.error('请输入视频创意描述');
		enhancing = true;
		try {
			const res = await suggestPrompts(token(), {
				prompt: prompt.trim(),
				style,
				count: 3,
				model: model || undefined
			});
			suggestions = res.variations ?? [];
		} catch (e: any) {
			toast.error(e?.detail ?? e?.message ?? '建议失败');
		} finally {
			enhancing = false;
		}
	};

	const applySuggestion = (s: string) => {
		prompt = s;
		suggestions = [];
	};

	const generate = async () => {
		if (!prompt.trim()) return toast.error('请输入视频创意描述');
		generating = true;
		current = null;
		try {
			const res = await generateVideo(token(), {
				prompt: prompt.trim(),
				style,
				model: model || undefined,
				duration: Number(duration),
				resolution,
				fps: Number(fps),
				mode // 添加模式参数
			});
			current = res as VideoGenResult;
			enhancedPrompt = res.enhanced_prompt;
			toast.success('视频生成完成');
			loadHistory();
		} catch (e: any) {
			console.error(e);
			toast.error(e?.detail ?? e?.message ?? '生成失败');
		} finally {
			generating = false;
		}
	};

	const playHistory = (item: VideoGenItem) => {
		current = item;
	};

	const loadHistory = async () => {
		try {
			const res = await listVideoGen(token());
			history = (res?.videos ?? []) as VideoGenItem[];
		} catch (e) {
			console.error(e);
		}
	};

	const checkSDHealth = async () => {
		try {
			const health = await getVideoGenHealth(token());
			ffmpegOk = !!(health?.ffmpeg && health?.ffprobe);
			sdAvailable = health?.sd_available ?? false;
			sdStatus = health?.generation_modes?.ai?.includes('Stable Diffusion') 
				? 'Stable Diffusion 可用 (AI 模式)' 
				: 'Stable Diffusion 不可用';
		} catch (e) {
			console.error(e);
		}
	};

	const getModels = async () => {
		try {
			const res = await getVideoGenModels(token());
			models = (res?.models ?? []).filter((m: string) => !!m);
			if (models.length && !model) model = models[0];
		} catch (e) {
			console.error(e);
		}
	};

	onMount(async () => {
		await Promise.all([checkSDHealth(), getModels()]);
		await loadHistory();
		loaded = true;
	});
</script>

<svelte:head>
	<title>AI 视频生成 | {WEBUI_NAME}</title>
</svelte:head>

{#if loaded}
	<div class="flex flex-col h-full max-h-full">
		<div class="flex items-center gap-2 px-4 py-3 border-b border-gray-50 dark:border-gray-850">
			{#if !$showSidebar}
				<Tooltip content="打开侧边栏">
					<button
						class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850"
						on:click={() => showSidebar.set(true)}
					>
						<SidebarIcon />
					</button>
				</Tooltip>
			{/if}
			<div class="text-lg font-medium">AI 视频生成</div>
			<div class="text-xs text-gray-400">Ollama · 本地 · 离线</div>
			<div class="flex-1"></div>
			<a
				href="/video"
				class="text-sm px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 transition"
			>
				视频分析
			</a>
		</div>

		{#if !ffmpegOk}
			<div class="m-4 p-3 rounded-lg bg-yellow-50 text-yellow-800 text-sm">
				后端未检测到 ffmpeg / ffprobe，视频生成将失败。请先安装 ffmpeg。
			</div>
		{/if}

		<div class="flex-1 overflow-y-auto p-4 flex flex-col lg:flex-row gap-4">
			<!-- Left: form -->
			<div class="lg:w-[380px] shrink-0 flex flex-col gap-3">
				<div>
					<div class="text-xs text-gray-500 mb-1">视频创意描述</div>
					<textarea
						rows="4"
						class="w-full text-sm rounded-lg px-3 py-2 bg-gray-50 dark:bg-gray-850 outline-none resize-none"
						placeholder="例如：一只橘猫在阳光下的窗台上打盹，微风拂过窗帘"
						bind:value={prompt}
					></textarea>
				</div>

				<div class="grid grid-cols-2 gap-2">
					<div>
						<div class="text-xs text-gray-500 mb-1">风格</div>
						<select
							class="w-full text-sm rounded-lg px-2 py-2 bg-gray-50 dark:bg-gray-850 outline-none"
							bind:value={style}
						>
							{#each STYLES as s}
								<option value={s.value}>{s.label}</option>
							{/each}
						</select>
					</div>
					<div>
						<div class="text-xs text-gray-500 mb-1">LLM 模型</div>
						<select
							class="w-full text-sm rounded-lg px-2 py-2 bg-gray-50 dark:bg-gray-850 outline-none"
							bind:value={model}
						>
							<option value="">默认（自动选择）</option>
							{#each models as m}
								<option value={m}>{m}</option>
							{/each}
						</select>
					</div>
				</div>

				<div class="grid grid-cols-3 gap-2">
					<div>
						<div class="text-xs text-gray-500 mb-1">时长(s)</div>
						<input
							type="number"
							min="1"
							max="30"
							class="w-full text-sm rounded-lg px-2 py-2 bg-gray-50 dark:bg-gray-850 outline-none"
							bind:value={duration}
						/>
					</div>
					<div>
						<div class="text-xs text-gray-500 mb-1">分辨率</div>
						<input
							class="w-full text-sm rounded-lg px-2 py-2 bg-gray-50 dark:bg-gray-850 outline-none"
							bind:value={resolution}
						/>
					</div>
					<div>
						<div class="text-xs text-gray-500 mb-1">FPS</div>
						<input
							type="number"
							min="1"
							max="60"
							class="w-full text-sm rounded-lg px-2 py-2 bg-gray-50 dark:bg-gray-850 outline-none"
							bind:value={fps}
						/>
					</div>
				</div>

				<div class="grid grid-cols-2 gap-2">
					<div>
						<div class="text-xs text-gray-500 mb-1">生成模式</div>
						<select
							class="w-full text-sm rounded-lg px-2 py-2 bg-gray-50 dark:bg-gray-850 outline-none"
							bind:value={mode}
						>
							<option value="auto">自动选择（推荐）</option>
							<option value="ai" disabled={!sdAvailable}>AI模式（Stable Diffusion）</option>
							<option value="demo">演示模式（渐变动画）</option>
						</select>
						{#if mode === 'ai' && !sdAvailable}
							<div class="text-xs text-red-500 mt-1">⚠️ Stable Diffusion 服务不可用</div>
						{/if}
					</div>
					<div>
						<div class="text-xs text-gray-500 mb-1">高级选项</div>
						<button
							class="w-full text-sm rounded-lg px-2 py-2 border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-850 hover:bg-gray-100 dark:hover:bg-gray-800 transition"
							on:click={() => showAdvanced = !showAdvanced}
							type="button"
						>
							{#if showAdvanced}隐藏高级选项{:else}显示高级选项{/if}
						</button>
					</div>
				</div>

				{#if showAdvanced}
					<div class="rounded-lg border border-gray-50 dark:border-gray-850 p-3 space-y-2">
						<div class="text-xs font-medium text-gray-500">高级选项</div>
						<div class="text-xs text-gray-400">
							{#if sdAvailable}
								<span class="text-green-500">✓ Stable Diffusion 可用</span>: {sdStatus}
							{:else}
								<span class="text-yellow-500">⚠️ Stable Diffusion 不可用</span>: 将使用演示模式
							{/if}
						</div>
						<div class="text-xs text-gray-400">
							FFmpeg: {#if ffmpegOk}<span class="text-green-500">✓ 就绪</span>{:else}<span class="text-red-500">✗ 未找到</span>{/if}
						</div>
					</div>
				{/if}

				<div class="flex gap-2">
					<button
						class="flex-1 py-2 rounded-lg border border-gray-200 dark:border-gray-700 text-sm disabled:opacity-50 flex items-center justify-center gap-2"
						disabled={enhancing || generating}
						on:click={enhance}
					>
						{#if enhancing}
							<Spinner className="size-4" />
						{:else}
							增强提示词
						{/if}
					</button>
					<button
						class="flex-1 py-2 rounded-lg border border-gray-200 dark:border-gray-700 text-sm disabled:opacity-50"
						disabled={enhancing || generating}
						on:click={suggest}
					>
						换几个灵感
					</button>
				</div>

				{#if enhancedPrompt}
					<div class="rounded-lg border border-gray-50 dark:border-gray-850 p-3 text-sm">
						<div class="text-xs text-gray-500 mb-1">增强后的提示词</div>
						<div class="whitespace-pre-wrap leading-relaxed">{enhancedPrompt}</div>
					</div>
				{/if}

				{#if suggestions.length}
					<div class="rounded-lg border border-gray-50 dark:border-gray-850 p-3">
						<div class="text-xs text-gray-500 mb-2">创意变体（点击填入）</div>
						{#each suggestions as s}
							<button
								class="block w-full text-left text-sm py-1 px-2 rounded hover:bg-gray-100 dark:hover:bg-gray-800 transition"
								on:click={() => applySuggestion(s)}
							>
								{s}
							</button>
						{/each}
					</div>
				{/if}

				<button
					class="mt-1 w-full py-2.5 rounded-lg bg-black text-white dark:bg-white dark:text-black text-sm font-medium disabled:opacity-50 flex items-center justify-center gap-2"
					disabled={generating}
					on:click={generate}
				>
					{#if generating}
						<Spinner className="size-4" />正在生成视频…
					{:else}
						生成视频
					{/if}
				</button>
			</div>

			<!-- Right: preview + history -->
			<div class="flex-1 min-w-0 flex flex-col gap-4">
				{#if current}
					<div class="rounded-xl border border-gray-50 dark:border-gray-850 p-4 flex flex-col gap-3">
						<video class="w-full rounded-lg bg-black" controls src={videoSrc(current.video_url)}></video>
						<div class="flex flex-wrap gap-3 text-xs text-gray-500">
							<span>时长 {Math.round((current.duration ?? 0) * 10) / 10}s</span>
							<span>分辨率 {current.resolution}</span>
							<span>{current.fps} FPS</span>
							{#if current.file_size}
								<span>{(current.file_size / 1024 / 1024).toFixed(2)} MB</span>
							{/if}
							{#if current.generation_mode}
								<span>模式: 
									{#if current.generation_mode === 'ai_image_video' || current.generation_mode === 'sd_lora'}
										<span class="text-green-500 font-medium">AI 模式</span>
									{:else if current.generation_mode === 'demo'}
										<span class="text-blue-500 font-medium">演示模式</span>
									{:else}
										{current.generation_mode}
									{/if}
								</span>
							{/if}
							{#if current.quality_score > 0}
								<span>质量: 
									{#if current.quality_score >= 80}
										<span class="text-green-500 font-medium">优秀 ({current.quality_score})</span>
									{:else if current.quality_score >= 60}
										<span class="text-blue-500 font-medium">良好 ({current.quality_score})</span>
									{:else if current.quality_score >= 40}
										<span class="text-yellow-500 font-medium">一般 ({current.quality_score})</span>
									{:else}
										<span class="text-red-500 font-medium">较低 ({current.quality_score})</span>
									{/if}
								</span>
							{/if}
						</div>
						{#if current.enhanced_prompt}
							<div>
								<div class="text-xs text-gray-500 mb-1">增强提示词</div>
								<div class="text-sm whitespace-pre-wrap leading-relaxed">{current.enhanced_prompt}</div>
							</div>
						{/if}
						{#if current.plan}
							<div>
								<div class="text-xs text-gray-500 mb-1">AI 分镜方案</div>
								<div class="text-sm flex flex-wrap gap-2">
									<span class="px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-850">运镜：{current.plan.motion}</span>
									<span class="px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-850">氛围：{current.plan.mood}</span>
									{#if current.plan.overlay_text}
										<span class="px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-850">字幕：{current.plan.overlay_text}</span>
									{/if}
									{#if current.plan.palette}
										<span class="px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-850"
											>主色：rgb({current.plan.palette.join(',')})</span
										>
									{/if}
								</div>
								{#if current.plan.scenes?.length}
									<ol class="list-decimal list-inside text-sm text-gray-600 dark:text-gray-300 mt-2">
										{#each current.plan.scenes as sc}
											<li>{sc}</li>
										{/each}
									</ol>
								{/if}
							</div>
						{/if}
					</div>
				{:else}
					<div class="flex-1 flex items-center justify-center text-sm text-gray-400">
						输入创意描述并点击「生成视频」。
					</div>
				{/if}

				{#if history.length}
					<div class="rounded-xl border border-gray-50 dark:border-gray-850 p-4">
						<div class="font-medium mb-3">生成历史（{history.length}）</div>
						<div class="grid grid-cols-2 sm:grid-cols-3 gap-3">
							{#each history as item (item.name)}
								<button
									class="rounded-lg overflow-hidden border {current && current.name === item.name
										? 'border-black dark:border-white'
										: 'border-gray-100 dark:border-gray-800'} hover:opacity-90 transition text-left"
									on:click={() => playHistory(item)}
								>
									{#if item.thumbnail_url}
										<img
											src={videoSrc(item.thumbnail_url)}
											alt={item.name}
											class="w-full aspect-video object-cover bg-black"
										/>
									{:else}
										<div class="w-full aspect-video bg-gray-100 dark:bg-gray-850"></div>
									{/if}
									<div class="text-xs text-gray-500 px-2 py-1 truncate">{item.name}</div>
								</button>
							{/each}
						</div>
					</div>
				{/if}
			</div>
		</div>
	</div>
{:else}
	<div class="w-full h-full flex items-center justify-center">
		<Spinner />
	</div>
{/if}
