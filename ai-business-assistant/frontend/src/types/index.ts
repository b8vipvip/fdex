export type ProfessionalLevel = 'beginner' | 'business' | 'product' | 'developer' | 'auto';
export type StorageMode = 'local_only' | 'cloud' | 'hybrid' | 'temporary';
export type RetentionPolicy = 'keep_forever' | 'delete_after_analysis' | 'delete_after_1_day' | 'delete_after_7_days' | 'delete_after_30_days';
export type PrivacyLevel = 'public' | 'normal' | 'sensitive' | 'highly_sensitive';
export type PrivacyDecision = 'desensitize' | 'temporary' | 'local_only' | 'confirm_upload';
export interface User { id: number; email: string; name: string; avatar?:string; company_name?:string; is_verified_company?:boolean; realname_verified?:boolean; deleted_retention_days?:number; professional_level: ProfessionalLevel; created_at: string }
export interface AuthResponse { access_token: string; token_type: string; user: User }
export interface Project { id: number; user_id: number; title: string; description: string; storage_mode: StorageMode; data_retention_policy: RetentionPolicy; allow_third_party_ai: boolean; auto_desensitize: boolean; project_type: string; status: string; requirement_score: number; created_at: string; updated_at: string }
export interface Message { id: number; project_id: number; role: string; content: string; created_at: string }
export interface PrivacyDetectionItem { type: string; count: number; examples: string[] }
export interface PrivacyDetection { is_sensitive: boolean; privacy_level: PrivacyLevel | string; detected_items: PrivacyDetectionItem[]; suggested_action: string }
export interface Asset { id: number; project_id: number; filename: string; original_filename: string; file_type: string; mime_type: string; file_size: number; status: string; privacy_level: PrivacyLevel | string; is_sensitive: boolean; desensitized_path: string; original_deleted_at?: string | null; retention_deadline?: string | null; privacy_detection?: PrivacyDetection | null; created_at: string }
export interface PrivacySummary { project_id: number; storage_mode: StorageMode; data_retention_policy: RetentionPolicy; allow_third_party_ai: boolean; auto_desensitize: boolean; total_assets: number; sensitive_assets: number; highly_sensitive_assets: number; pending_decision_assets: number; retention_deadline?: string | null }
export interface AnalysisResult { id: number; asset_id: number; project_id: number; analyzer_type: string; summary: string; structured_json: string; created_at: string }
export interface Report { id: number; project_id: number; report_type: string; title: string; content_markdown: string; structured_json: string; created_at: string; updated_at: string }
export interface Employee { id:number; user_id:number; name:string; avatar:string; avatar_url?:string; job_role_id?:number; is_system?:boolean; is_material_manager?:boolean; allow_upload_assets?:boolean; allow_receive_project_context?:boolean; department:string; position:string; role_prompt:string; industry:string; reply_mode:'text'|'voice'; can_create_project:boolean; can_delete_project:boolean; can_view_project_data:boolean; can_view_project_reports:boolean; can_view_other_employee_messages:boolean; can_view_project_progress:boolean; created_at:string; updated_at:string }
export interface EmployeeMessage { id:number; user_id:number; employee_id:number; project_id:number|null; role:'user'|'employee'|'system'; content:string; message_type:string; created_at:string; metadata_json:string }
export interface ConversationResponse { user_message:EmployeeMessage; employee_message:EmployeeMessage }

export interface JobRole{id:number;industry:string;category:string;title:string;aliases:string;description:string;role_prompt_template:string;is_common:boolean;sort_order:number}
