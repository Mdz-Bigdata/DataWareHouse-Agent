// Frontend TypeScript Type Definitions

export interface AskRequest {
  question: string;
  dialect: string;
  user: string;
}

export interface ChartConfig {
  type: string;
  title: string;
  config: {
    xAxis?: { data: string[] };
    series?: Array<{ name: string; data: any[] }>;
    value?: string;
    label?: string;
  };
}

export interface QueryDetails {
  sql: string;
  dialect: string;
  elapsed_time: string;
  tables: string[];
  source_desc: string;
  filters: Array<{ field: string; op: string; value: any }>;
  estimated_rows?: number;
}

export interface ClarificationOption {
  label: string;
  query: string;
}

export interface ClarificationInfo {
  need_clarification: boolean;
  message: string;
  options: ClarificationOption[];
}

export interface AskResponse {
  success: boolean;
  conclusion?: string;
  chart?: ChartConfig;
  data?: Array<Record<string, any>>;
  column_types?: Record<string, string>;
  error?: string;
  details?: QueryDetails;
  clarification?: ClarificationInfo;
}

export interface HistoryRecord {
  id: number;
  user: string;
  question: string;
  sql: string;
  dialect: string;
  execution_time: string;
  result_summary: string;
  created_at: string;
}

export interface PreferenceProfile {
  user: string;
  common_tables: Array<{ table: string; count: number }>;
  common_metrics: Array<{ metric: string; count: number }>;
  common_dimensions: Array<{ dimension: string; count: number }>;
  common_time_ranges: Array<{ range: string; count: number }>;
}

export interface PhaseLog {
  agent?: string;
  skill?: string;
  action: string;
  reviewer?: string;
  review_status?: string;
  review_comments?: string;
  output: {
    summary?: string;
    route_decision?: string;
    architecture_doc?: string;
    ddl_file?: string;
    ddl_content?: string;
    etl_file?: string;
    etl_content?: string;
    job_file?: string;
    job_content?: string;
    uploaded_files?: string[];
    dataarts_directory?: string;
    connection_mapping?: string;
    doc_file?: string;
    readme_file?: string;
    doc_preview?: string;
    status?: string;
    job_name?: string;
    project_id?: string;
    api_endpoint?: string;
    log?: string;
  };
}

export interface ChecklistItem {
  id: number;
  step: string;
  agent: string;
  done: boolean;
}

export interface DevResponse {
  success: boolean;
  table_name: string;
  db_name: string;
  phases: PhaseLog[];
  checklist: ChecklistItem[];
}

export interface VendorConfig {
  api_key: string;
  base_url: string;
  text_models: string[];
  multimodal_models: string[];
  active_text_model: string;
  active_multimodal_model: string;
}

export interface LLMConfig {
  active_vendor: string;
  vendors: Record<string, VendorConfig>;
}

export interface TestConnectionRequest {
  vendor: string;
  api_key: string;
  base_url: string;
}

export interface TestConnectionResponse {
  success: boolean;
  message: string;
  text_models: string[];
  multimodal_models: string[];
}