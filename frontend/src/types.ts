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
  data_source?: "demo" | "configured";
}

export interface DataSourceInfo {
  mode: "demo" | "configured";
  label: string;
  engine?: string;
  description?: string;
  data_origin?: "project_fixture" | "business";
  database_identity?: string;
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

export interface AttributionData {
  analysis_type?: string;
  metric_unit?: string;
  metric_name: string;
  metric_display: string;
  dimension: string;
  dimension_display: string;
  total_value: number;
  current_period?: { start: string; end: string };
  baseline_period?: { start: string; end: string };
  current_value?: number;
  baseline_value?: number;
  total_change?: number;
  change_rate?: number | null;
  top_driver: string;
  top_driver_ratio: number;
  waterfall_items: Array<{
    name: string;
    value: number;
    ratio: number;
    baseline_value?: number;
    current_value?: number;
  }>;
}

export interface LineageNode {
  id: string;
  name: string;
  layer: string;
  type: string;
  domain: string;
}

export interface LineageEdge {
  source: string;
  target: string;
  relation: string;
}

export interface LineageData {
  nodes: LineageNode[];
  edges: LineageEdge[];
}

export interface AskResponse {
  success: boolean;
  data_source_info?: DataSourceInfo;
  skill_type?: string;
  conclusion?: string;
  chart?: ChartConfig;
  data?: Array<Record<string, any>>;
  column_types?: Record<string, string>;
  error?: string;
  details?: QueryDetails;
  clarification?: ClarificationInfo;
  attribution_data?: AttributionData;
  lineage_data?: LineageData;
  cache_hit?: boolean;
  cache_type?: string;
  matched_question?: string;
  similarity_score?: number;
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

export interface ErrorCorrectionRecord {
  question: string;
  error_message: string;
  wrong_sql: string;
  corrected_sql: string;
  created_at?: string;
}

export interface CacheEntry {
  key: string;
  question: string;
  role: string;
  dialect: string;
  hit_count: number;
  ttl_remaining_sec: number;
  has_embedding: boolean;
}

export interface CacheStats {
  total_requests: number;
  total_hits: number;
  hit_ratio_percent: number;
  exact_hits: number;
  semantic_hits: number;
  cached_exact_count: number;
  cached_semantic_count: number;
  cached_entries?: CacheEntry[];
}

export interface InferredMetric {
  name: string;
  field: string;
  table: string;
  display_name: string;
  calculation: string;
  default_agg: string;
  aliases: string[];
}

export interface InferredDimension {
  name: string;
  table: string;
  display_name: string;
  aliases: string[];
  value_range: string[];
}

export interface MetadataEnrichResult {
  table_name: string;
  domain: string;
  description: string;
  metrics: InferredMetric[];
  dimensions: InferredDimension[];
}
