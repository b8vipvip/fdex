import { API_BASE_URL, apiClient } from './client';
import type { Report } from '../types';
export const getReport = (id: number) => apiClient.get<Report>(`/reports/${id}`).then(r => r.data);
export const exportReportUrl = (id: number) => `${API_BASE_URL}/reports/${id}/export-md`;
