export type ProfessionalLevel = 'beginner' | 'business' | 'product' | 'developer' | 'auto';
export interface User { id: number; email: string; name: string; professional_level: ProfessionalLevel; created_at: string }
export interface AuthResponse { access_token: string; token_type: string; user: User }
export interface Project { id: number; user_id: number; title: string; description: string; project_type: string; status: string; requirement_score: number; created_at: string; updated_at: string }
export interface Message { id: number; project_id: number; role: string; content: string; created_at: string }
export interface Asset { id: number; project_id: number; filename: string; original_filename: string; file_type: string; mime_type: string; file_size: number; status: string; created_at: string }
export interface AnalysisResult { id: number; asset_id: number; project_id: number; analyzer_type: string; summary: string; structured_json: string; created_at: string }
export interface Report { id: number; project_id: number; report_type: string; title: string; content_markdown: string; structured_json: string; created_at: string; updated_at: string }
