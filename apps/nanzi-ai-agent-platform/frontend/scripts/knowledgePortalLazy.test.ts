import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, "..");

const readSource = (path: string) => readFileSync(resolve(root, path), "utf8");

const composable = readSource("src/composables/useKnowledgePortal.ts");
const drawer = readSource("src/components/knowledge/KnowledgePortalDrawer.vue");

assert.doesNotMatch(
  composable,
  /datasets\.value\.forEach\([\s\S]*?fetchRecommendations\(/,
  "opening the knowledge portal should not eagerly request recommendations for every dataset",
);

assert.match(
  composable,
  /const INITIAL_RECOMMENDATION_PREFETCH_LIMIT = 3;/,
  "knowledge portal should cap automatic recommendation generation at three datasets",
);

assert.match(
  composable,
  /pinnedDatasetIds\.value\.includes\(ds\.id\)[\s\S]*?slice\(0, INITIAL_RECOMMENDATION_PREFETCH_LIMIT\)/,
  "automatic recommendation generation should prioritize pinned datasets and stay bounded",
);

assert.doesNotMatch(
  drawer,
  /<!-- Recommended Questions Section \(Always visible\) -->\s*<div\s+v-if="recommendations\[ds\.id\]\?\.loading \|\| \(recommendations\[ds\.id\]\?\.questions\?\.length \|\| 0\) > 0"/,
  "recommendation section should remain visible so users can load questions lazily",
);

assert.match(
  drawer,
  /生成推荐/,
  "empty recommendation state should expose an explicit lazy-load action",
);

console.log("knowledgePortalLazy.test.ts passed");
