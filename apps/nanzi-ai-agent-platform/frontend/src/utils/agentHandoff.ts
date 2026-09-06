import type { AgentHandoffNoticeData } from "@/types/agentHandoff";

export function applyAgentHandoffEvent(
  message: { agentHandoff?: AgentHandoffNoticeData },
  event: any,
): boolean {
  if (event?.type !== "agent_handoff" || event?.data?.version !== 1) return false;
  message.agentHandoff = {
    version: 1,
    from_agent: String(event.data.from_agent || ""),
    from_display_name: String(event.data.from_display_name || event.data.from_agent || "智能助手"),
    to_agent: String(event.data.to_agent || ""),
    to_display_name: String(event.data.to_display_name || event.data.to_agent || "合适的智能助手"),
    reason_label: event.data.reason_label ? String(event.data.reason_label) : undefined,
  };
  return true;
}
