import assert from "node:assert/strict";
import { createConversationId } from "../src/utils/conversationId.ts";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
type ConversationIdCrypto = Parameters<typeof createConversationId>[0];

const fallbackCrypto = {
  getRandomValues<T extends ArrayBufferView | null>(array: T): T {
    if (array instanceof Uint8Array) {
      for (let i = 0; i < array.length; i += 1) {
        array[i] = (i * 17 + 31) & 0xff;
      }
    }
    return array;
  },
} as ConversationIdCrypto;

const nativeCrypto = {
  randomUUID: () => "11111111-2222-4333-8444-555555555555",
} as ConversationIdCrypto;

assert.equal(createConversationId(nativeCrypto), "11111111-2222-4333-8444-555555555555");

const generatedId = createConversationId(fallbackCrypto);
assert.match(generatedId, uuidPattern);
assert.equal(generatedId[14], "4");
assert.ok(["8", "9", "a", "b"].includes(generatedId[19]));
