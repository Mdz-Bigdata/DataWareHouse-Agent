export interface StandardResponse<T> {
  code: number;
  message: string;
  data: T;
  timestamp: string;
  trace_id: string;
}

export interface ListResponse<T> {
  total: number;
  items: T[];
  page: number;
  page_size: number;
}
