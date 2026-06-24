import { VIDEO_API_BASE_URL } from '$lib/constants';

export type VideoAnalyzeForm = {
	video_path: string;
	model: string;
	summary_model?: string;
	prompt?: string;
	frame_interval?: number;
	max_frames?: number;
	concurrency?: number;
	language?: string;
	include_audio?: boolean;
	whisper_model?: string;
	save_report?: boolean;
	ollama_url?: string;
};

export const getVideoHealth = async (token: string) => {
	let error = null;
	const res = await fetch(`${VIDEO_API_BASE_URL}/health`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail ?? err;
			return null;
		});

	if (error) throw error;
	return res;
};

export const getVideoModels = async (token: string) => {
	let error = null;
	const res = await fetch(`${VIDEO_API_BASE_URL}/models`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail ?? err;
			return null;
		});

	if (error) throw error;
	return res;
};

export const analyzeVideo = async (token: string, payload: VideoAnalyzeForm) => {
	let error = null;
	const res = await fetch(`${VIDEO_API_BASE_URL}/analyze`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(payload)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail ?? err;
			return null;
		});

	if (error) throw error;
	return res;
};

/**
 * Stream analysis progress over SSE.
 * `onEvent` is called for every parsed event object pushed by the backend.
 * Resolves with the final result object (stage === 'result').
 */
export const analyzeVideoStream = async (
	token: string,
	payload: VideoAnalyzeForm,
	onEvent: (event: any) => void
) => {
	const res = await fetch(`${VIDEO_API_BASE_URL}/analyze/stream`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(payload)
	});

	if (!res.ok || !res.body) {
		const err = await res.json().catch(() => ({ detail: res.statusText }));
		throw err.detail ?? err;
	}

	const reader = res.body.getReader();
	const decoder = new TextDecoder();
	let buffer = '';
	let result: any = null;

	while (true) {
		const { done, value } = await reader.read();
		if (done) break;
		buffer += decoder.decode(value, { stream: true });

		const parts = buffer.split('\n\n');
		buffer = parts.pop() ?? '';
		for (const part of parts) {
			const line = part.trim();
			if (!line.startsWith('data:')) continue;
			const json = line.slice(5).trim();
			if (!json) continue;
			try {
				const event = JSON.parse(json);
				onEvent(event);
				if (event.stage === 'result') result = event.result;
				if (event.stage === 'error') throw event.detail ?? 'Analysis failed';
			} catch (e) {
				if (typeof e === 'string') throw e;
				console.error('Failed to parse SSE chunk', json, e);
			}
		}
	}

	return result;
};
