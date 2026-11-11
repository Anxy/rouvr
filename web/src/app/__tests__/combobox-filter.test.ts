// Combobox filter: opening with a selected label must still list every
// option; typeahead only applies after the user edits the input.
//
// Run: `node --test src/app/__tests__/combobox-filter.test.ts`

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { filterComboboxOptions } from "../../lib/combobox.ts";

const WEB_ROOT = path.resolve(import.meta.dirname, "..", "..", "..");

test("selected 'All projects' still lists every project until the user types", () => {
  const options = ["All projects", "Edprime", "demo"];
  assert.deepEqual(
    filterComboboxOptions(options, "All projects", false),
    options,
    "opening the list with the selected label must not hide projects",
  );
  assert.deepEqual(
    filterComboboxOptions(options, "All projects", true),
    ["All projects"],
    "after typing, substring filter applies",
  );
  assert.deepEqual(
    filterComboboxOptions(options, "ed", true),
    ["Edprime"],
    "typeahead matches project names",
  );
});

test("Combobox source gates filtering on user edits", () => {
  const src = readFileSync(path.join(WEB_ROOT, "src/components/ui/Select.tsx"), "utf8");
  assert.ok(src.includes("setFiltering(true)"), "typing must enable filtering");
  assert.ok(
    src.includes("setFiltering(false)"),
    "focus/select must show the full option list again",
  );
  assert.ok(
    src.includes("filterComboboxOptions"),
    "Combobox must use the shared filter helper",
  );
});
