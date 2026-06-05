import { apiClient } from './client';
import type { AuthResponse, ProfessionalLevel, User } from '../types';
export const login = (email: string, password: string) => apiClient.post<AuthResponse>('/auth/login', { email, password }).then(r => r.data);
export const register = (data: {email: string; name: string; password: string; professional_level: ProfessionalLevel}) => apiClient.post<AuthResponse>('/auth/register', data).then(r => r.data);
export const me = () => apiClient.get<User>('/auth/me').then(r => r.data);
