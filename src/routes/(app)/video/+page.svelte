<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { WEBUI_NAME, showSidebar, mobile } from '$lib/stores';
	import {
		getVideoHealth,
		getVideoModels,
		analyzeVideoStream,
		type VideoAnalyzeForm
	} from '$lib/apis/video';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import SidebarIcon from '$lib/components/icons/Sidebar.svelte';

	const i18n: any = getContext('i18n');

	let loaded = false;
	let opencvOk = false;
	let models: string[] = [];

	// form state
	let videoPath = '';
	let model = '';
	let summaryModel = '';
	let language: 'zh' | 'en' = 'zh';
	let frameInterval = 5;
	let maxFrames = 16;
	let concurrency = 3;
	let includeAudio = false;
	let whisperModel = '';
	let saveReport = true;
	let prompt = '';

	// run state
	let running = false;
	let progress: { done: number; total: number } = { done: 0, total: 0 };
	let logs: string[] = [];
	let frames: { index: number; timestamp: number; description: string }[] = [];
	let summary = '';
	let transcript = '';
	let reportPath = '';
	let elapsed = 0;

	const log = (msg: string) => {
		logs = [...logs, msg];
	};

	const reset = () => {
		progress = { done: 0, total: 0 };
		logs = [];
		frames = [];
		summary = '';
		transcript = '';
		reportPath = '';
		elapsed = 0;
	};

	const run = async () => {
		if (!videoPath.trim()) return toast.error($i18n.t('Please enter a local video path'));
		if (!model) return toast.error($i18n.t('Please select a vision model'));

		running = true;
		reset();

		const payload: VideoAnalyzeForm = {
			video_path: videoPath.trim(),
			model,
			summary_model: summaryModel || undefined,
			prompt: prompt || undefined,
			frame_interval: Number(frameInterval),
			max_frames: Number(maxFrames),
			concurrency: Number(concurrency),
			language,
			include_audio: includeAudio,
			whisper_model: whisperModel || undefined,
			save_report: saveReport
		};

		try {
			await analyzeVideoStream(localStorage.token, payload, (event) => {
				switch (event.stage) {
					case 'extract':
						log('🎞️  ' + event.message);
						break;
					case 'extracted':
						progress = { done: 0, total: event.sampled_frames };
						log(`✅ Sampled ${event.sampled_frames} frames (duration ${event.duration}s)`);
						break;
					case 'audio':
						log('🎧  ' + event.message);
						break;
					case 'audio_done':
						log(`✅ Transcribed audio (${event.chars} chars)`);
						break;
					case 'frame':
						progress = { done: event.done, total: event.total };
						frames = [
							...frames,
							{ index: event.index, timestamp: event.timestamp, description: event.description }
						].sort((a, b) => a.index - b.index);
						break;
					case 'summarize':
						log('🧩  ' + event.message);
						break;
					case 'result':
						summary = event.result.summary;
						transcript = event.result.transcript ?? '';
						reportPath = event.result.report_path ?? '';
						elapsed = event.result.elapsed;
						log(`🏁 Done in ${event.result.elapsed}s`);
						break;
				}
			});
			toast.success($i18n.t('Video analysis complete'));
		} catch (err: any) {
			console.error(err);
			toast.error(typeof err === 'string' ? err : err?.detail ?? $i18n.t('Analysis failed'));
		} finally {
			running = false;
		}
	};

	onMount(async () => {
		try {
			const health = await getVideoHealth(localStorage.token);
			opencvOk = health?.opencv ?? false;
			if (health?.defaults) {
				frameInterval = health.defaults.frame_interval ?? frameInterval;
				maxFrames = health.defaults.max_frames ?? maxFrames;
				concurrency = health.defaults.concurrency ?? concurrency;
			}
		} catch (e) {
			console.error(e);
		}
		try {
			const res = await getVideoModels(localStorage.token);
			models = (res?.models ?? []).filter((m: string) => !!m);
			if (models.length && !model) model = models[0];
		} catch (e) {
			console.error(e);
		}
		loaded = true;
	});
</script>

<svelte:head>
	<title>{$i18n.t('Video Analysis')} | {$WEBUI_NAME}</title>
</svelte:head>

{#if loaded}
	<div class="flex flex-col h-full max-h-full">
		<div class="flex items-center gap-2 px-4 py-3 border-b border-gray-50 dark:border-gray-850">
			{#if !$showSidebar}
				<Tooltip content={$i18n.t('Open sidebar')}>
					<button
						class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850"
						on:click={() => showSidebar.set(true)}
					>
						<SidebarIcon />
					</button>
				</Tooltip>
			{/if}
			<div class="text-lg font-medium">{$i18n.t('Offline Video Analysis')}</div>
			<div class="text-xs text-gray-400">Ollama · local · offline</div>
		</div>

		{#if !opencvOk}
			<div class="m-4 p-3 rounded-lg bg-yellow-50 text-yellow-800 text-sm">
				{$i18n.t('OpenCV is not available on the backend; frame extraction will fail.')}
			</div>
		{/if}

		<div class="flex-1 overflow-y-auto p-4 flex flex-col lg:flex-row gap-4">
			<!-- Left: form -->
			<div class="lg:w-[380px] shrink-0 flex flex-col gap-3">
				<div>
					<div class="text-xs text-gray-500 mb-1">{$i18n.t('Local video path')}</div>
					<input
						class="w-full text-sm rounded-lg px-3 py-2 bg-gray-50 dark:bg-gray-850 outline-none"
						placeholder="/Users/you/Movies/demo.mp4"
						bind:value={videoPath}
					/>
				</div>

				<div class="grid grid-cols-2 gap-2">
					<div>
						<div class="text-xs text-gray-500 mb-1">{$i18n.t('Vision model')}</div>
						<select
							class="w-full text-sm rounded-lg px-2 py-2 bg-gray-50 dark:bg-gray-850 outline-none"
							bind:value={model}
						>
							{#each models as m}
								<option value={m}>{m}</option>
							{/each}
						</select>
					</div>
					<div>
						<div class="text-xs text-gray-500 mb-1">{$i18n.t('Summary model')}</div>
						<select
							class="w-full text-sm rounded-lg px-2 py-2 bg-gray-50 dark:bg-gray-850 outline-none"
							bind:value={summaryModel}
						>
							<option value="">{$i18n.t('Same as vision')}</option>
							{#each models as m}
								<option value={m}>{m}</option>
							{/each}
						</select>
					</div>
				</div>

				<div class="grid grid-cols-3 gap-2">
					<div>
						<div class="text-xs text-gray-500 mb-1">{$i18n.t('Interval (s)')}</div>
						<input
							type="number"
							min="0.5"
							step="0.5"
							class="w-full text-sm rounded-lg px-2 py-2 bg-gray-50 dark:bg-gray-850 outline-none"
							bind:value={frameInterval}
						/>
					</div>
					<div>
						<div class="text-xs text-gray-500 mb-1">{$i18n.t('Max frames')}</div>
						<input
							type="number"
							min="1"
							max="64"
							class="w-full text-sm rounded-lg px-2 py-2 bg-gray-50 dark:bg-gray-850 outline-none"
							bind:value={maxFrames}
						/>
					</div>
					<div>
						<div class="text-xs text-gray-500 mb-1">{$i18n.t('Concurrency')}</div>
						<input
							type="number"
							min="1"
							max="8"
							class="w-full text-sm rounded-lg px-2 py-2 bg-gray-50 dark:bg-gray-850 outline-none"
							bind:value={concurrency}
						/>
					</div>
				</div>

				<div class="grid grid-cols-2 gap-2">
					<div>
						<div class="text-xs text-gray-500 mb-1">{$i18n.t('Language')}</div>
						<select
							class="w-full text-sm rounded-lg px-2 py-2 bg-gray-50 dark:bg-gray-850 outline-none"
							bind:value={language}
						>
							<option value="zh">中文</option>
							<option value="en">English</option>
						</select>
					</div>
					<div>
						<div class="text-xs text-gray-500 mb-1">{$i18n.t('Whisper model')}</div>
						<input
							class="w-full text-sm rounded-lg px-2 py-2 bg-gray-50 dark:bg-gray-850 outline-none"
							placeholder="base / small / medium"
							bind:value={whisperModel}
						/>
					</div>
				</div>

				<label class="flex items-center gap-2 text-sm">
					<input type="checkbox" bind:checked={includeAudio} />
					{$i18n.t('Transcribe audio (faster-whisper)')}
				</label>
				<label class="flex items-center gap-2 text-sm">
					<input type="checkbox" bind:checked={saveReport} />
					{$i18n.t('Save markdown report next to video')}
				</label>

				<div>
					<div class="text-xs text-gray-500 mb-1">{$i18n.t('Custom per-frame prompt (optional)')}</div>
					<textarea
						rows="2"
						class="w-full text-sm rounded-lg px-3 py-2 bg-gray-50 dark:bg-gray-850 outline-none resize-none"
						bind:value={prompt}
					/>
				</div>

				<button
					class="mt-1 w-full py-2 rounded-lg bg-black text-white dark:bg-white dark:text-black text-sm font-medium disabled:opacity-50 flex items-center justify-center gap-2"
					disabled={running}
					on:click={run}
				>
					{#if running}
						<Spinner className="size-4" />
						{$i18n.t('Analyzing...')}
					{:else}
						{$i18n.t('Analyze video')}
					{/if}
				</button>

				{#if progress.total > 0}
					<div>
						<div class="flex justify-between text-xs text-gray-500 mb-1">
							<span>{$i18n.t('Frames')}</span>
							<span>{progress.done}/{progress.total}</span>
						</div>
						<div class="h-1.5 rounded-full bg-gray-100 dark:bg-gray-850 overflow-hidden">
							<div
								class="h-full bg-green-500 transition-all"
								style="width: {progress.total ? (progress.done / progress.total) * 100 : 0}%"
							></div>
						</div>
					</div>
				{/if}

				{#if logs.length}
					<div class="text-xs font-mono bg-gray-50 dark:bg-gray-850 rounded-lg p-2 max-h-40 overflow-y-auto">
						{#each logs as line}
							<div>{line}</div>
						{/each}
					</div>
				{/if}
			</div>

			<!-- Right: results -->
			<div class="flex-1 min-w-0 flex flex-col gap-4">
				{#if summary}
					<div class="rounded-xl border border-gray-50 dark:border-gray-850 p-4">
						<div class="flex items-center justify-between mb-2">
							<div class="font-medium">{$i18n.t('Analysis Report')}</div>
							{#if elapsed}<div class="text-xs text-gray-400">{elapsed}s</div>{/if}
						</div>
						<div class="text-sm whitespace-pre-wrap leading-relaxed">{summary}</div>
						{#if reportPath}
							<div class="text-xs text-gray-400 mt-3">{$i18n.t('Saved to')}: {reportPath}</div>
						{/if}
					</div>
				{/if}

				{#if transcript}
					<div class="rounded-xl border border-gray-50 dark:border-gray-850 p-4">
						<div class="font-medium mb-2">{$i18n.t('Audio Transcript')}</div>
						<div class="text-sm whitespace-pre-wrap text-gray-600 dark:text-gray-300">{transcript}</div>
					</div>
				{/if}

				{#if frames.length}
					<div class="rounded-xl border border-gray-50 dark:border-gray-850 p-4">
						<div class="font-medium mb-3">{$i18n.t('Per-frame descriptions')} ({frames.length})</div>
						<div class="flex flex-col gap-3">
							{#each frames as fr (fr.index)}
								<div class="text-sm">
									<span class="text-xs font-mono text-gray-400 mr-2">@{fr.timestamp.toFixed(1)}s</span>
									{fr.description}
								</div>
							{/each}
						</div>
					</div>
				{/if}

				{#if !summary && !frames.length}
					<div class="flex-1 flex items-center justify-center text-sm text-gray-400">
						{$i18n.t('Enter a local video path and start the offline analysis.')}
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
