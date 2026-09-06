const GENERATED_FILE_PATH_PREFIX = '/api/v1/chat/generated-files/';

export const normalizeGeneratedFileHref = (href: string) => {
  try {
    const url = new URL(href, 'http://placeholder.local');
    if (
      (url.protocol === 'http:' || url.protocol === 'https:')
      && url.pathname.startsWith(GENERATED_FILE_PATH_PREFIX)
    ) {
      return `${url.pathname}${url.search}${url.hash}`;
    }
  } catch {
    // Preserve malformed or non-URL values for the existing renderer logic.
  }

  return href;
};
