import { WEBUI_API_BASE_URL } from '$lib/constants';

const BRIDGE_BASE = `${WEBUI_API_BASE_URL}/video-bridge`;

// ==========================================
// Types
// ==========================================
export type TrainingStatus = {
	is_training: boolean;
	lora_weights: Array<{
		name: string;
		path: string;
		has_weights: boolean;
		created_at: string;
		size_mb: number;
	}>;
	training_data_count: number;
	latest_log?: string;
};

export type TrainingProgress = {
	status: string;
	current_step?: number;
	total_steps?: number;
	current_loss?: number;
	avg_loss?: number;
	elapsed_seconds?: number;
	eta_seconds?: number;
	final_loss?: number;
	message?: string;
};

export type TrainingDataPreview = {
	training_pairs_count: number;
	image_count: number;
	image_dir: string;
	sample_pairs: Array<{
		prompt: string;
		generation_id: number;
		style: string;
		created_at: string;
	}>;
};

export type TrainingHistoryItem = {
	id: number;
	output_name: string;
	status: string;
	total_steps: number;
	current_step: number;
	final_loss: number | null;
	total_time_seconds: number | null;
	created_at: string;
	completed_at: string | null;
};

export type SystemHealth = {
	overall: string;
	timestamp: string;
	checks: Record<string, any>;
};

export type CeleryStatus = {
	status: string;
	workers: Array<{
		worker_name: string;
		active_tasks: number;
		active_task_details: Array<any>;
	}>;
	registered_tasks: string[];
	worker_count: number;
};

export type VideoQARequest = {
	video_id: number;
	question: string;
};

export type VideoQAResponse = {
	message: string;
	analysis_id: number;
	question: string;
	status: string;
	celery_task_id: string;
};

export type SimilarSearchRequest = {
	video_id: number;
	top_k?: number;
	threshold?: number;
};

export type SimilarSearchResponse = {
	source_video_id: number;
	results: Array<{
		video_id: number;
		title: string;
		similarity: number;
		distance: number;
		thumbnail_url?: string;
	}>;
	engine: string;
};

// ==========================================
// API Functions
// ==========================================
const _fetch = async (url: string, options: RequestInit = {}) => {
	const res = await fetch(url, {
		...options,
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${localStorage.token}`,
			...options.headers
		}
	});
	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: res.statusText }));
		throw new Error(err.detail || err.message || 'Request failed');
	}
	return res.json();
};

// Training
export const getTrainingStatus = async (): Promise<TrainingStatus> => {
	return _fetch(`${BRIDGE_BASE}/training/status`);
};

export const getTrainingProgress = async (outputName?: string): Promise<TrainingProgress> => {
	const params = outputName ? `?output_name=${encodeURIComponent(outputName)}` : '';
	return _fetch(`${BRIDGE_BASE}/training/progress${params}`);
};

export const getTrainingDataPreview = async (): Promise<TrainingDataPreview> => {
	return _fetch(`${BRIDGE_BASE}/training/data/preview`);
};

export const getTrainingHistory = async (skip = 0, limit = 10): Promise<{ items: TrainingHistoryItem[]; total: number }> => {
	return _fetch(`${BRIDGE_BASE}/training/history?skip=${skip}&limit=${limit}`);
};

export const startTraining = async (params: { steps?: number; learning_rate?: number; rank?: number }): Promise<any> => {
	return _fetch(`${BRIDGE_BASE}/training/start`, {
		method: 'POST',
		body: JSON.stringify(params)
	});
};

export const listLoraWeights = async (): Promise<any[]> => {
	return _fetch(`${BRIDGE_BASE}/training/weights`);
};

// Monitor
export const getSystemHealth = async (): Promise<SystemHealth> => {
	return _fetch(`${BRIDGE_BASE}/monitor/health`);
};

export const getCeleryStatus = async (): Promise<CeleryStatus> => {
	return _fetch(`${BRIDGE_BASE}/monitor/celery/status`);
};

// QA
export const submitVideoQA = async (videoId: number, question: string): Promise<VideoQAResponse> => {
	return _fetch(`${BRIDGE_BASE}/qa`, {
		method: 'POST',
		body: JSON.stringify({ video_id: videoId, question })
	});
};

// Similar Search
export const searchSimilarVideos = async (
	videoId: number,
	topK = 5,
	threshold = 0.5
): Promise<SimilarSearchResponse> => {
	return _fetch(`${BRIDGE_BASE}/search/similar`, {
		method: 'POST',
		body: JSON.stringify({ video_id: videoId, top_k: topK, threshold })
	});
};

// Videos list
export const listLargeModelVideos = async (skip = 0, limit = 20): Promise<any> => {
	return _fetch(`${BRIDGE_BASE}/videos/list?skip=${skip}&limit=${limit}`);
};

// ==========================================
// L4: Dataset Management
// ==========================================
export type DatasetStats = {
	total: number;
	processed: number;
	avg_aesthetic: number;
	avg_motion: number;
};

export type DatasetItem = {
	id: number;
	video_id: number;
	video_title: string;
	clip_path: string;
	clip_start: number;
	clip_end: number;
	caption: string;
	aesthetic_score: number | null;
	motion_score: number | null;
	scene_type: string;
	status: string;
	created_at: string;
};

export const getDatasetStats = async (): Promise<DatasetStats> => {
	return _fetch(`${BRIDGE_BASE}/dataset/stats`);
};

export const listDatasetItems = async (
	skip = 0,
	limit = 50,
	status?: string,
	videoId?: number
): Promise<{ total: number; items: DatasetItem[] }> => {
	let url = `${BRIDGE_BASE}/dataset/items?skip=${skip}&limit=${limit}`;
	if (status) url += `&status=${encodeURIComponent(status)}`;
	if (videoId) url += `&video_id=${videoId}`;
	return _fetch(url);
};

export const triggerPreprocess = async (videoId: number): Promise<any> => {
	return _fetch(`${BRIDGE_BASE}/dataset/preprocess/${videoId}`, { method: 'POST' });
};

export const updateDatasetItem = async (itemId: number, data: Partial<DatasetItem>): Promise<any> => {
	return _fetch(`${BRIDGE_BASE}/dataset/items/${itemId}`, {
		method: 'PUT',
		body: JSON.stringify(data)
	});
};

export const deleteDatasetItem = async (itemId: number): Promise<any> => {
	return _fetch(`${BRIDGE_BASE}/dataset/items/${itemId}`, { method: 'DELETE' });
};

// ==========================================
// L4: Generation Management
// ==========================================
export type GenerationItem = {
	id: number;
	prompt: string;
	enhanced_prompt: string | null;
	generation_type: string;
	provider: string;
	status: string;
	progress: number;
	result_video_id: number | null;
	thumbnail_path: string;
	duration: number;
	resolution: string;
	generation_mode: string;
	quality_score: number | null;
	error_message: string | null;
	created_at: string;
	completed_at: string | null;
};

export const listGenerations = async (
	skip = 0,
	limit = 20,
	status?: string
): Promise<GenerationItem[]> => {
	let url = `${BRIDGE_BASE}/generation/list?skip=${skip}&limit=${limit}`;
	if (status) url += `&status=${encodeURIComponent(status)}`;
	return _fetch(url);
};

export const getGenerationStatus = async (generationId: number): Promise<any> => {
	return _fetch(`${BRIDGE_BASE}/generation/status/${generationId}`);
};

export const getGenerationDetail = async (generationId: number): Promise<any> => {
	return _fetch(`${BRIDGE_BASE}/generation/${generationId}`);
};

export const createGeneration = async (params: {
	prompt: string;
	style?: string;
	generation_type?: string;
	provider?: string;
	duration?: number;
	resolution?: string;
	auto_enhance?: boolean;
	auto_import?: boolean;
}): Promise<any> => {
	return _fetch(`${BRIDGE_BASE}/generation/create`, {
		method: 'POST',
		body: JSON.stringify(params)
	});
};

export const enhancePrompt = async (prompt: string, style = 'cinematic'): Promise<any> => {
	return _fetch(`${BRIDGE_BASE}/generation/enhance?prompt=${encodeURIComponent(prompt)}&style=${style}`, {
		method: 'POST'
	});
};

export const listGenerationTemplates = async (): Promise<any[]> => {
	return _fetch(`${BRIDGE_BASE}/generation-templates`);
};

export const deleteGeneration = async (generationId: number): Promise<any> => {
	return _fetch(`${BRIDGE_BASE}/generation/${generationId}`, { method: 'DELETE' });
};

// ==========================================
// L4: LoRA Weight Advanced Management
// ==========================================
export type LatestWeightInfo = {
	active: boolean;
	path: string | null;
	name?: string;
	filename?: string;
	size_mb?: number;
	created_at?: string;
	message?: string;
};

export const getLatestWeight = async (): Promise<LatestWeightInfo> => {
	return _fetch(`${BRIDGE_BASE}/training/weights/latest`);
};

export const deleteLoraWeight = async (weightName: string): Promise<any> => {
	return _fetch(`${BRIDGE_BASE}/training/weights/${encodeURIComponent(weightName)}`, { method: 'DELETE' });
};

export const cleanupOldWeights = async (keep = 3): Promise<any> => {
	return _fetch(`${BRIDGE_BASE}/training/weights/cleanup?keep=${keep}`, { method: 'POST' });
};

export const getTrainingLog = async (outputName: string): Promise<any> => {
	return _fetch(`${BRIDGE_BASE}/training/logs/${encodeURIComponent(outputName)}`);
};

// ==========================================
// L4: Auto-Train Configuration
// ==========================================
export type AutoTrainConfig = {
	enabled: boolean;
	threshold: number;
	max_train_steps: number;
	current_samples?: number;
	progress_percent?: number;
};

export const getAutoTrainConfig = async (): Promise<AutoTrainConfig> => {
	return _fetch(`${BRIDGE_BASE}/training/auto-train/config`);
};

export const updateAutoTrainConfig = async (config: {
	enabled: boolean;
	threshold: number;
	max_train_steps: number;
}): Promise<any> => {
	return _fetch(`${BRIDGE_BASE}/training/auto-train/config`, {
		method: 'POST',
		body: JSON.stringify(config)
	});
};

export const getScheduleConfig = async (): Promise<any> => {
	return _fetch(`${BRIDGE_BASE}/training/schedule`);
};

export const setScheduleConfig = async (config: {
	enabled: boolean;
	interval_hours: number;
	max_train_steps: number;
	learning_rate: number;
}): Promise<any> => {
	return _fetch(`${BRIDGE_BASE}/training/schedule`, {
		method: 'POST',
		body: JSON.stringify(config)
	});
};

// ==========================================
// L4: Pipeline Status (Closed-Loop Overview)
// ==========================================
export type PipelineStatus = {
	dataset: DatasetStats;
	training: {
		is_training: boolean;
		lora_count: number;
		training_data_count: number;
	};
	auto_train: AutoTrainConfig;
	latest_weight: LatestWeightInfo;
	generation: {
		total: number;
		completed: number;
		avg_quality: number;
	};
	analysis: {
		total: number;
		completed: number;
	};
	pipeline_active: boolean;
};

export const getPipelineStatus = async (): Promise<PipelineStatus> => {
	return _fetch(`${BRIDGE_BASE}/pipeline/status`);
};