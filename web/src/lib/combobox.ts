/** Typeahead options for Combobox. Filtering is opt-in so a selected
 *  label like "All projects" does not hide every other choice on open. */
export function filterComboboxOptions(
  options: readonly string[],
  value: string,
  filtering: boolean,
): string[] {
  if (!filtering) return [...options];
  const query = value.trim().toLowerCase();
  if (!query) return [...options];
  return options.filter((option) => option.toLowerCase().includes(query));
}
