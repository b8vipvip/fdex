import { apiClient } from './client';
import type { AnalysisResult, Asset, PrivacyDecision } from '../types';
export const uploadAsset = (projectId: number, file: File) => { const form = new FormData(); form.append('file', file); return apiClient.post<Asset>(`/projects/${projectId}/assets/upload`, form).then(r => r.data); };
export const createLocalOnlyAsset = (projectId: number, file: File) => apiClient.post<Asset>(`/projects/${projectId}/assets/local-only`, { original_filename: file.name, mime_type: file.type || 'application/octet-stream', file_size: file.size }).then(r => r.data);
export const getAssets = (projectId: number) => apiClient.get<Asset[]>(`/projects/${projectId}/assets`).then(r => r.data);
export const analyzeAsset = (assetId: number) => apiClient.post<AnalysisResult>(`/assets/${assetId}/analyze`).then(r => r.data);
export const decideAssetPrivacy = (assetId: number, decision: PrivacyDecision) => apiClient.post<Asset>(`/assets/${assetId}/privacy-decision`, { decision }).then(r => r.data);
