type ConversationCrypto = Partial<Pick<Crypto, "getRandomValues" | "randomUUID">>;

const UUID_TEMPLATE = "10000000-1000-4000-8000-100000000000";

const randomByte = (cryptoSource?: Pick<ConversationCrypto, "getRandomValues">): number => {
  if (cryptoSource?.getRandomValues) {
    return cryptoSource.getRandomValues(new Uint8Array(1))[0] ?? 0;
  }
  return Math.floor(Math.random() * 256);
};

/** UUID for browser use; falls back when `randomUUID` is unavailable (e.g. plain HTTP). */
export const createUuid = (
  cryptoSource: ConversationCrypto | undefined = globalThis.crypto
): string => {
  if (typeof cryptoSource?.randomUUID === "function") {
    return cryptoSource.randomUUID();
  }

  return UUID_TEMPLATE.replace(/[018]/g, (char) => {
    const value = Number(char);
    return (value ^ (randomByte(cryptoSource) & (15 >> (value / 4)))).toString(16);
  });
};

export const createConversationId = createUuid;
