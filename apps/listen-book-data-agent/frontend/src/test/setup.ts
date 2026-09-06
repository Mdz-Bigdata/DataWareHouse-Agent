import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// vitest runs with globals disabled, so register RTL cleanup explicitly.
afterEach(() => {
  cleanup();
});

// jsdom lacks these browser APIs used by Carbon components.
if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  }
  if (!window.ResizeObserver) {
    class ResizeObserverStub {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    window.ResizeObserver = ResizeObserverStub as unknown as typeof window.ResizeObserver;
  }
}
