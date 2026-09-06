export interface ChatBIMetadataField {
  physical_name: string;
  label: string;
  type: string;
  table: string;
  dataset: string;
}

export interface ChatBIMetadataGuide {
  version: 1;
  datasets: string[];
  tables: Array<{ physical_name: string; label: string; dataset: string }>;
  metrics: ChatBIMetadataField[];
  dimensions: ChatBIMetadataField[];
  freshness?: ChatBIMetadataField | null;
  suggestions: Array<{ label: string; query: string; dataset: string }>;
}
