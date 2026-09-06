/**
 * Utility function to copy text to clipboard with support for non-secure contexts (HTTP).
 * Uses navigator.clipboard if available, otherwise falls back to document.execCommand('copy').
 */
export function copyToClipboard(text: string): Promise<boolean> {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text)
      .then(() => true)
      .catch((err) => {
        console.warn("navigator.clipboard.writeText failed, falling back to legacy copy", err);
        return fallbackCopyToClipboard(text);
      });
  }
  return Promise.resolve(fallbackCopyToClipboard(text));
}

function fallbackCopyToClipboard(text: string): boolean {
  const textArea = document.createElement("textarea");
  textArea.value = text;
  // Prevent scrolling
  textArea.style.top = "0";
  textArea.style.left = "0";
  textArea.style.position = "fixed";
  textArea.style.opacity = "0";
  document.body.appendChild(textArea);

  textArea.focus();
  textArea.select();

  try {
    const successful = document.execCommand("copy");
    document.body.removeChild(textArea);
    return successful;
  } catch (err) {
    console.error("Fallback copy failed:", err);
    document.body.removeChild(textArea);
    return false;
  }
}
