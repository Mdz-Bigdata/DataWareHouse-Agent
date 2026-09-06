export interface AgentHandoffNoticeData {
  version: 1;
  from_agent: string;
  from_display_name: string;
  to_agent: string;
  to_display_name: string;
  reason_label?: string;
}
