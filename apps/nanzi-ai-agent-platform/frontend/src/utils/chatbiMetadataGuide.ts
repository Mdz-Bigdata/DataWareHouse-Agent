import type { ChatBIMetadataGuide } from "@/types/chatbiMetadataGuide";

export function applyChatBIMetadataGuideEvent(
  message: { chatbiMetadataGuide?: ChatBIMetadataGuide },
  event: any,
): boolean {
  if (event?.type !== "chatbi_metadata_guide" || event?.data?.version !== 1) return false;
  message.chatbiMetadataGuide = {
    ...event.data,
    suggestions: Array.isArray(event.data.suggestions) ? event.data.suggestions.slice(0, 4) : [],
  } as ChatBIMetadataGuide;
  return true;
}
