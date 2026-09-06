/**
 * Minimal, dependency-free Server-Sent Events frame reader.
 *
 * Handles the realities of POST + fetch streaming: frames split across chunks,
 * CRLF line endings, comment/keep-alive lines, multi-line `data:` fields and
 * streams that end with a partial (unterminated) frame.
 */

export interface SseFrame {
  event: string;
  data: string;
}

/** Parse one raw SSE frame (without the trailing blank line). */
export function parseFrame(raw: string): SseFrame | null {
  let event = 'message';
  const dataLines: string[] = [];
  for (const line of raw.split('\n')) {
    if (line === '' || line.startsWith(':')) continue;
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') {
      event = value;
    } else if (field === 'data') {
      dataLines.push(value);
    }
    // `id:` / `retry:` and unknown fields are ignored on purpose.
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join('\n') };
}

/** Yield SSE frames from a response body until it closes. */
export async function* readSseFrames(stream: ReadableStream<Uint8Array>): AsyncGenerator<SseFrame> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      // Normalize CRLF. A `\r` split from its `\n` across chunks stays in the
      // buffer (it cannot form a `\n\n` boundary) until the next read.
      buffer = buffer.replace(/\r\n/g, '\n');
      let boundary: number;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const raw = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const frame = parseFrame(raw);
        if (frame) yield frame;
      }
      if (done) break;
    }
    // Be lenient with a server that closed without the final blank line.
    if (buffer.trim() !== '') {
      const frame = parseFrame(buffer);
      if (frame) yield frame;
    }
  } finally {
    reader.releaseLock();
  }
}
