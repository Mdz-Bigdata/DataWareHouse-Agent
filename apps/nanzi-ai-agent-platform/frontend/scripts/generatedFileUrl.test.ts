import assert from 'node:assert/strict';
import { normalizeGeneratedFileHref } from '../src/utils/generatedFileUrl.ts';

assert.equal(
  normalizeGeneratedFileHref(
    'http://yovole.com/api/v1/chat/generated-files/0123456789abcdef0123456789abcdef?token=abc#download',
  ),
  '/api/v1/chat/generated-files/0123456789abcdef0123456789abcdef?token=abc#download',
);

assert.equal(
  normalizeGeneratedFileHref('https://example.com/report'),
  'https://example.com/report',
);
