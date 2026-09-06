const platformUiPorts: Record<string, number> = {
  "data-api": 8020,
  agents: 8030,
};

/** Explicit public addresses win; defaults follow the host opened by the user. */
export function resolvePlatformUiUrl(
  slug: string,
  configuredUrl: string,
  browserUrl: string,
  uiPort?: number,
): string | undefined {
  try {
    const configured = configuredUrl.trim();
    const url = new URL(configured || browserUrl);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
      return undefined;
    }
    if (!configured) {
      const port = uiPort ?? platformUiPorts[slug];
      if (!Number.isInteger(port) || port < 1 || port > 65535) return undefined;
      url.port = String(port);
      url.pathname = "/";
      url.search = "";
      url.hash = "";
    }
    return url.href;
  } catch {
    return undefined;
  }
}
