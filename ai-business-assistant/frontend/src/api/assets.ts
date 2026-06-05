import { apiClient } from './client';
import type { AnalysisResult, Asset } from '../types';
export const uploadAsset = (projectId: number, file: File) => { const form = new FormData(); form.append('file', file); return apiClient.post<Asset>(`/projects/${projectId}/assets/upload`, form).then(r => r.data); };
export const getAssets = (projectId: number) => apiClient.get<Asset[]>(`/projects/${projectId}/assets`).then(r => r.data);
export const analyzeAsset = (assetId: number) => apiClient.post<AnalysisResult>(`/assets/${assetId}/analyze`).then(r => r.data);
