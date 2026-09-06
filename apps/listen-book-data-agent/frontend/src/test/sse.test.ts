import { describe, expect, it } from 'vitest';
import { parseFrame, readSseFrames } from '../lib/sse';

function streamFrom(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

async function collect(chunks: string[]) {
  const frames = [];
  for await (const frame of readSseFrames(streamFrom(chunks))) frames.push(frame);
  return frames;
}

describe('parseFrame', () => {
  it('parses event and data fields', () => {
    expect(parseFrame('event: progress\ndata: {"step":"生成SQL"}')).toEqual({
      event: 'progress',
      data: '{"step":"生成SQL"}',
    });
  });

  it('joins multi-line data fields with newlines', () => {
    const frame = parseFrame('event: answer\ndata: {"a":1\ndata: ,"b":2}');
    expect(frame?.data).toBe('{"a":1\n,"b":2}');
  });

  it('ignores comments and id/retry lines', () => {
    const frame = parseFrame(': keep-alive\nid: 9\nretry: 3000\ndata: hello');
    expect(frame).toEqual({ event: 'message', data: 'hello' });
  });

  it('returns null when the frame has no data', () => {
    expect(parseFrame('event: done')).toBeNull();
    expect(parseFrame(': only a comment')).toBeNull();
  });
});

describe('readSseFrames', () => {
  it('reads frames split across chunks', async () => {
    const frames = await collect([
      'event: pro',
      'gress\ndata: {"step":"A"}\n\nevent: sql\ndata: {"sql":"select 1"}\n\n',
    ]);
    expect(frames).toEqual([
      { event: 'progress', data: '{"step":"A"}' },
      { event: 'sql', data: '{"sql":"select 1"}' },
    ]);
  });

  it('handles CRLF line endings, even split across chunks', async () => {
    const frames = await collect([
      'event: progress\r\ndata: {"step":"A"}\r',
      '\n\r',
      '\nevent: done\r\ndata: {}\r\n\r\n',
    ]);
    expect(frames.map((frame) => frame.event)).toEqual(['progress', 'done']);
  });

  it('parses a trailing frame without the final blank line', async () => {
    const frames = await collect(['event: done\ndata: {"status":"completed"}']);
    expect(frames).toEqual([{ event: 'done', data: '{"status":"completed"}' }]);
  });

  it('skips keep-alive comments between frames', async () => {
    const frames = await collect([': ping\n\nevent: sql\ndata: {"sql":"x"}\n\n: pong\n\n']);
    expect(frames).toHaveLength(1);
    expect(frames[0].event).toBe('sql');
  });

  it('handles multi-byte characters split across chunks', async () => {
    const full = 'event: progress\ndata: {"step":"生成SQL"}\n\n';
    const bytes = new TextEncoder().encode(full);
    const frames = [];
    for await (const frame of readSseFrames(
      new ReadableStream({
        start(controller) {
          // Split inside a multi-byte character boundary.
          controller.enqueue(bytes.slice(0, 25));
          controller.enqueue(bytes.slice(25));
          controller.close();
        },
      }),
    )) {
      frames.push(frame);
    }
    expect(frames[0].data).toBe('{"step":"生成SQL"}');
  });
});
