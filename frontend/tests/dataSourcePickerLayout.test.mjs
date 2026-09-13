import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const component = readFileSync(new URL("../src/components/DataSourcePicker.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../src/index.css", import.meta.url), "utf8");

test("data source picker keeps every engine inside a viewport-bounded scroll area", () => {
  assert.match(component, /data-source-picker__popover/);
  assert.match(component, /data-source-picker__list/);
  assert.match(component, /role="listbox"/);
  assert.match(component, /tabIndex=\{0\}/);
  assert.match(component, /onKeyDown=\{scrollListWithKeyboard\}/);
  assert.match(component, /currentTarget\.scrollBy/);
  assert.match(component, /role="option"/);
  assert.match(component, /getBoundingClientRect\(\)/);
  assert.match(component, /window\.innerHeight/);
  assert.match(component, /data-placement=\{popoverLayout\.placement\}/);
  assert.match(component, /style=\{\{ maxHeight: popoverLayout\.maxHeight \}\}/);

  assert.match(styles, /\.data-source-picker__popover\[data-placement="above"\]/);
  assert.match(styles, /\.data-source-picker__list\s*\{[^}]*min-height:\s*0[^}]*overflow-y:\s*auto/s);
  assert.match(styles, /\.data-source-picker__list\s*\{[^}]*overscroll-behavior:\s*contain/s);
});
