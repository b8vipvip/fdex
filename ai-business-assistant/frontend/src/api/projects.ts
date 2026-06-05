import { apiClient } from './client';
import type { Message, ProfessionalLevel, Project, Report } from '../types';
export const getProjects = () => apiClient.get<Project[]>('/projects').then(r => r.data);
export const createProject = (data: {title: string; description: string; professional_level?: ProfessionalLevel}) => apiClient.post<Project>('/projects', data).then(r => r.data);
export const getProject = (id: number) => apiClient.get<Project>(`/projects/${id}`).then(r => r.data);
export const addMessage = (projectId: number, content: string) => apiClient.post<Message>(`/projects/${projectId}/messages`, { content, role: 'user' }).then(r => r.data);
export const getMessages = (projectId: number) => apiClient.get<Message[]>(`/projects/${projectId}/messages`).then(r => r.data);
export const analyzeProject = (projectId: number) => apiClient.post<Report[]>(`/projects/${projectId}/analyze`).then(r => r.data);
export const getReports = (projectId: number) => apiClient.get<Report[]>(`/projects/${projectId}/reports`).then(r => r.data);
