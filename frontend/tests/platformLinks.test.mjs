import assert from "node:assert/strict";
import test from "node:test";

import { resolvePlatformUiUrl } from "../src/lib/platformLinks.ts";

test("native applications open on the browser's host with their own ports", () => {
  assert.equal(
    resolvePlatformUiUrl("data-api", "", "http://192.168.3.8:3000/?tab=platform#top"),
    "http://192.168.3.8:8020/",
  );
  assert.equal(
    resolvePlatformUiUrl("agents", " ", "http://[::1]:5173/platform"),
    "http://[::1]:8030/",
  );
});

test("configured public addresses preserve their origin, path and query", () => {
  const configured = "https://agents.example.com/workbench?from=warehouse";
  assert.equal(resolvePlatformUiUrl("agents", configured, "http://localhost:3000"), configured);
  assert.equal(
    resolvePlatformUiUrl("data-api", "http://localhost:9000/custom", "https://warehouse.example.com"),
    "http://localhost:9000/custom",
  );
});

test("the registered UI port is used when no public address is configured", () => {
  assert.equal(
    resolvePlatformUiUrl("agents", "", "http://192.168.3.8:3000", 9030),
    "http://192.168.3.8:9030/",
  );
  assert.equal(resolvePlatformUiUrl("agents", "", "http://localhost:3000", -1), undefined);
});

test("unsafe or invalid configured addresses cannot become navigable links", () => {
  for (const configured of ["javascript:alert(1)", "data:text/html,hello", "//untrusted.example.com", "/relative", "not a URL", "https://user:password@example.com"]) {
    assert.equal(resolvePlatformUiUrl("agents", configured, "http://localhost:3000"), undefined);
  }
  assert.equal(resolvePlatformUiUrl("unknown", "", "http://localhost:3000"), undefined);
});
