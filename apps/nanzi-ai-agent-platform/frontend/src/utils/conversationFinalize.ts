import axios from './axios'

/**
 * Flush session summary before switching or creating a new conversation.
 * Fire-and-forget friendly; failures are logged only.
 */
export async function finalizeConversation(
  conversationId: string,
  headers?: Record<string, string>
): Promise<void> {
  const cid = (conversationId || '').trim()
  if (!cid) return
  try {
    await axios.post(
      `/api/v1/chat/conversation/${encodeURIComponent(cid)}/finalize`,
      {},
      headers ? { headers } : undefined
    )
  } catch (e) {
    console.warn('[Memory] conversation finalize failed', cid, e)
  }
}
