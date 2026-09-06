# Generated File Link Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure generated document download links always resolve on the currently deployed portal host.

**Architecture:** A small frontend utility canonicalizes only absolute generated-file capability links to their path, query, and hash. `MessageRenderer` applies that utility before its existing generic link rewrite, repairing both new and historic messages. Tool descriptions tell the model to preserve the canonical relative URL, but correctness remains in deterministic code.

**Tech Stack:** Vue 3, TypeScript, Node assert, Python pytest.

---

### Task 1: Add a tested generated-file URL normalizer

**Files:**
- Create: `frontend/src/utils/generatedFileUrl.ts`
- Test: `frontend/scripts/generatedFileUrl.test.ts`

- [x] **Step 1: Write the failing test**

```ts
import assert from 'node:assert/strict';
import { normalizeGeneratedFileHref } from '../src/utils/generatedFileUrl.ts';

assert.equal(
  normalizeGeneratedFileHref('http://yovole.com/api/v1/chat/generated-files/0123456789abcdef0123456789abcdef?token=abc#download'),
  '/api/v1/chat/generated-files/0123456789abcdef0123456789abcdef?token=abc#download',
);
assert.equal(normalizeGeneratedFileHref('https://example.com/report'), 'https://example.com/report');
```

- [x] **Step 2: Run the test to verify it fails**

Run: `node --experimental-strip-types frontend/scripts/generatedFileUrl.test.ts`

Expected: failure because `generatedFileUrl.ts` does not yet exist.

- [x] **Step 3: Write the minimal implementation**

```ts
const GENERATED_FILE_PATH_PREFIX = '/api/v1/chat/generated-files/';

export const normalizeGeneratedFileHref = (href: string) => {
  try {
    const url = new URL(href, 'http://placeholder.local');
    if ((url.protocol === 'http:' || url.protocol === 'https:') && url.pathname.startsWith(GENERATED_FILE_PATH_PREFIX)) {
      return `${url.pathname}${url.search}${url.hash}`;
    }
  } catch {
    // Preserve malformed or non-URL values for the existing renderer logic.
  }
  return href;
};
```

- [x] **Step 4: Run the test to verify it passes**

Run: `node --experimental-strip-types frontend/scripts/generatedFileUrl.test.ts`

Expected: exit code 0.

### Task 2: Apply the normalizer and clarify tool contracts

**Files:**
- Modify: `frontend/src/components/MessageRenderer.vue:1,129-157`
- Modify: `app/services/ai/tools/word_document_tool.py:54-55`
- Modify: `app/services/ai/tools/excel_document_tool.py:104-112`

- [x] **Step 1: Update the renderer**

```ts
import { normalizeGeneratedFileHref } from '@/utils/generatedFileUrl';

const normalizedVal = normalizeGeneratedFileHref(val);
if (normalizedVal !== val) return `${attr}="${normalizedVal}"`;
```

Place this before the existing external-link early return in `postProcessHtml`.

- [x] **Step 2: Update tool docstrings**

Add: `Copy artifact.download_url verbatim in the final response; never add a protocol or host.`

- [x] **Step 3: Run focused tests**

Run: `node --experimental-strip-types frontend/scripts/generatedFileUrl.test.ts && REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/tools/test_generated_file_service.py tests/ai/tools/test_word_document_tool.py tests/ai/tools/test_excel_document_tool.py -q`

Expected: exit code 0.

### Task 3: Compile the affected application

**Files:**
- Verify: `frontend/src/components/MessageRenderer.vue`
- Verify: `frontend/src/utils/generatedFileUrl.ts`
- Verify: `app/services/ai/tools/word_document_tool.py`
- Verify: `app/services/ai/tools/excel_document_tool.py`

- [-] **Step 1: Run the frontend production build**

Run: `npm run build`

Working directory: `frontend`

Expected: exit code 0.

Observed: `npm run build` is blocked before Vite by pre-existing strict TypeScript
errors in `frontend/src/views/EmbedChat.vue:3576-3577`; the lines are unchanged
from `HEAD`. Direct Vite bundling transformed all 10,189 modules and updated
`frontend/dist/index.html`.

- [x] **Step 2: Compile the Python tool modules**

Run: `venv/bin/python -m py_compile app/services/ai/tools/word_document_tool.py app/services/ai/tools/excel_document_tool.py app/services/ai/tools/generated_file_service.py`

Expected: exit code 0.
