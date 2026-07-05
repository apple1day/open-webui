import { VIDEO_GEN_API_BASE_URL } from '$lib/constants';

export type VideoGenForm = {
	prompt: string;
	model?: string;
	style?: string;
	duration?: number;
	resolution?: string;
	fps?: number;
	mode?: string; // "auto", "ai", "demo"
};

export type VideoGenResult = {
	name: string;
	video_url: string;
	thumbnail_url: string;
	enhanced_prompt: string;
	plan: any;
	duration: number;
	resolution: string;
	fps: number;
	file_size: number;
	mood: string;
	motion: string;
	generation_mode: string;
	quality_score: number;
	status?: string;
};

export type VideoGenItem = {
	name: string;
	video_url: string;
	thumbnail_url: string;
	duration: number;
	resolution: string;
	fps: number;
	file_size: number;
	created_at: number;
};

export type VideoGenHealth = {
	status: boolean;
	ffmpeg: boolean;
	ffprobe: boolean;
	sd_available: boolean;
	supported_exts: string[];
	defaults: {
		duration: number;
		resolution: string;
		fps: number;
	};
	generation_modes: {
		ai: string;
		demo: string;
		auto: string;
	};
};

const _request = async (token: string, path: string, method: string, body?: any) => {
	let error: any = null;
	const res = await fetch(`${VIDEO_GEN_API_BASE_URL}${path}`, {
		method,
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: body ? JSON.stringify(body) : undefined
	})
		.then(async (r) => {
			if (!r.ok) throw await r.json();
			return r.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail ?? err.message ?? err;
			return null;
		});
	if (error) throw error;
	return res;
};

export const getVideoGenHealth = (token: string) => _request(token, '/health', 'GET') as Promise<VideoGenHealth>;
export const getVideoGenModels = (token: string) => _request(token, '/models', 'GET');
export const enhancePrompt = (token: string, payload: VideoGenForm) =>
	_request(token, '/enhance', 'POST', payload);
export const suggestPrompts = (token: string, payload: VideoGenForm & { count?: number }) =>
	_request(token, '/suggest', 'POST', payload);
export const generateVideo = (token: string, payload: VideoGenForm) =>
	_request(token, '/generate', 'POST', payload);
export const listVideoGen = (token: string) => _request(token, '/list', 'GET');
