<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { WEBUI_NAME, showSidebar, mobile } from '$lib/stores';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/icons/Tooltip.svelte';
	import SidebarIcon from '$lib/components/icons/Sidebar.svelte';
	import { getVideoHistory, deleteVideoHistory, type VideoHistoryItem } from '$lib/apis/video';

	const i18n: any = getContext('i18n');

	let loaded = false;
	let history: VideoHistoryItem[] = [];
	let loading = false;
	let selectedItem: VideoHistoryItem | null = null;

	// Load history
	const loadHistory = async () => {
		loading = true;
		try {
			history = await getVideoHistory(localStorage.token);
			console.log('Loaded history:', history.length, 'items');
		} catch (err: any) {
			toast.error(typeof err === 'string' ? err : err?.detail ?? $i18n.t('Failed to load history'));
		} finally {
			loading = false;
		}
	};

	// Delete history item
	const deleteItem = async (path: string) => {
		if (!confirm($i18n.t('Are you sure to delete this record?'))) return;
		try {
			await deleteVideoHistory(localStorage.token, path);
			toast.success($i18n.t('Deleted'));
			await loadHistory();
		} catch (err: any) {
			toast.error(typeof err === 'string' ? err : err?.detail ?? $i18n.t('Failed to delete'));
		}
	};

	// View report
	const viewReport = (item: VideoHistoryItem) => {
		selectedItem = item;
	};

	// Close report modal
	const closeReport = () => {
		selectedItem = null;
	};

	// Format date
	const formatDate = (dateStr: string) => {
		const d = new Date(dateStr);
		return d.toLocaleString();
	};

	// Format file size
	const formatSize = (bytes: number) => {
		if (bytes < 1024) return bytes + ' B';
		if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
		if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB';
		return (bytes / 1024 / 1024 / 1024).toFixed(1) + ' GB';
	};

	onMount(() => {
		loadHistory();
		loaded = true;
	});
</script>

<svelte:head>
	<title>{$i18n.t('Video Analysis History')} | {$WEBUI_NAME}</title>
</svelte:head>

{#if loaded}
	<div class="flex flex-col h-full max-h-full">
		<!-- Header -->
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
			<div class="text-lg font-medium">{$i18n.t('Video Analysis History')}</div>
			<div class="flex-1"></div>
			<button
				class="text-sm px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 transition"
				on:click={loadHistory}
				disabled={loading}
			>
				{#if loading}
					<Spinner className="size-4" />
				{:else}
					{$i18n.t('Refresh')}
				{/if}
			</button>
		</div>

		<!-- History list -->
		<div class="flex-1 overflow-y-auto p-4">
			{#if loading}
				<div class="flex items-center justify-center h-64">
					<Spinner />
				</div>
			{:else if history.length === 0}
				<div class="flex items-center justify-center h-64 text-gray-400">
					{$i18n.t('No analysis history yet')}
				</div>
			{:else}
				<div class="space-y-3">
					{#each history as item (item.video_path)}
						<div class="rounded-xl border border-gray-50 dark:border-gray-850 p-4 hover:shadow-md transition">
							<div class="flex items-start justify-between gap-4">
								<div class="flex-1 min-w-0">
									<div class="font-medium text-sm truncate" title={item.video_path}>
										{item.video_name}
									</div>
									<div class="text-xs text-gray-400 mt-1 truncate" title={item.video_path}>
										{item.video_path}
									</div>
									<div class="flex items-center gap-4 mt-2 text-xs text-gray-500">
										<span>{$i18n.t('Model')}: <code class="text-xs">{item.model}</code></span>
										<span>{$i18n.t('Duration')}: {item.duration}s</span>
										<span>{$i18n.t('Frames')}: {item.sampled_frames}</span>
										{#if item.has_audio}
											<span>🎙️ {$i18n.t('Audio')}</span>
										{/if}
									</div>
									<div class="text-xs text-gray-400 mt-1">
										{formatDate(item.analyzed_at)}
										{#if item.file_size}
											· {formatSize(item.file_size)}
										{/if}
									</div>
								</div>
								<div class="flex items-center gap-2 shrink-0">
									<button
										class="text-xs px-3 py-1.5 rounded-lg bg-black text-white dark:bg-white dark:text-black hover:opacity-80 transition"
										on:click={() => viewReport(item)}
									>
										{$i18n.t('View Report')}
									</button>
									<button
										class="text-xs px-3 py-1.5 rounded-lg border border-red-200 text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 transition"
										on:click={() => deleteItem(item.video_path)}
									>
										{$i18n.t('Delete')}
									</button>
								</div>
							</div>
						</div>
					{/each}
				</div>
			{/if}
		</div>
	</div>

	<!-- Report modal -->
	{#if selectedItem}
		<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" on:click={closeReport}>
			<div
				class="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl max-w-4xl w-full max-h-[90vh] overflow-hidden m-4"
				on:click|stopPropagation
			>
				<!-- Modal header -->
				<div class="flex items-center justify-between px-6 py-4 border-b border-gray-100 dark:border-gray-800">
					<div>
						<div class="font-medium">{$i18n.t('Analysis Report')}</div>
						<div class="text-xs text-gray-400 mt-0.5">{selectedItem.video_name}</div>
					</div>
					<button
						class="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
						on:click={closeReport}
					>
						<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
						</svg>
					</button>
				</div>

				<!-- Modal body -->
				<div class="overflow-y-auto p-6 max-h-[calc(90vh-80px)]">
					{#if selectedItem.summary}
						<div class="mb-6">
							<h3 class="text-sm font-medium text-gray-500 mb-2">{$i18n.t('Analysis')}</h3>
							<div class="text-sm whitespace-pre-wrap leading-relaxed">{selectedItem.summary}</div>
						</div>
					{/if}

					{#if selectedItem.transcript}
						<div class="mb-6">
							<h3 class="text-sm font-medium text-gray-500 mb-2">{$i18n.t('Audio Transcript')}</h3>
							<div class="text-sm whitespace-pre-wrap leading-relaxed text-gray-600 dark:text-gray-300">
								{selectedItem.transcript}
							</div>
						</div>
					{/if}

					{#if selectedItem.frames && selectedItem.frames.length > 0}
						<div>
							<h3 class="text-sm font-medium text-gray-500 mb-2">{$i18n.t('Per-frame Descriptions')}</h3>
							<div class="space-y-2">
								{#each selectedItem.frames as fr}
									<div class="text-sm">
										<span class="text-xs font-mono text-gray-400 mr-2">@{fr.timestamp.toFixed(1)}s</span>
										{fr.description}
									</div>
								{/each}
							</div>
						</div>
					{/if}
				</div>
			</div>
		</div>
	{/if}
{:else}
	<div class="w-full h-full flex items-center justify-center">
		<Spinner />
	</div>
{/if}
