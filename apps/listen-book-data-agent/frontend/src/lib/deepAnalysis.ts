import { apiFetch } from './auth';

export interface DeepAnalysisFact {
  fact_id: string;
  statement: string;
  evidence_ids: string[];
}

export interface DeepAnalysisInference {
  inference_id: string;
  statement: string;
  fact_ids: string[];
  confidence: 'low' | 'medium' | 'high';
}

export interface DeepAnalysisEvidence {
  evidence_id: string;
  description: string;
  values: Record<string, string | number | boolean | null>;
}

export interface DeepAnalysisResult {
  trace_id: string;
  source_trace_id: string;
  status: 'completed';
  facts: DeepAnalysisFact[];
  inferences: DeepAnalysisInference[];
  evidence: DeepAnalysisEvidence[];
  rerun_row_count: number;
  row_limit: number;
  truncated: boolean;
  policy_version: string;
  policy_hash: string;
  build_id: string;
  disclaimer: string;
}

export class DeepAnalysisRequestError extends Error {}

export async function requestDeepAnalysis(traceId: string): Promise<DeepAnalysisResult> {
  let response: Response;
  try {
    response = await apiFetch(`/api/traces/${encodeURIComponent(traceId)}/analysis`, {
      method: 'POST',
    });
  } catch {
    throw new DeepAnalysisRequestError('无法连接深入分析服务，请稍后重试。');
  }
  if (!response.ok) {
    let detail = `深入分析失败（HTTP ${response.status}）。`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === 'string' && payload.detail) detail = payload.detail;
    } catch {
      // Keep the status-based message for a non-JSON response.
    }
    throw new DeepAnalysisRequestError(detail);
  }
  return (await response.json()) as DeepAnalysisResult;
}
