"use client";

import { SearchableSelect } from "@/components/searchable-select";
import { COUNTRIES } from "@/lib/countries";

const OPTIONS = COUNTRIES.map((name) => ({ value: name, label: name }));

export function CountrySelect({
  value,
  onValueChange,
  id,
  disabled,
}: {
  value: string;
  onValueChange: (value: string) => void;
  id?: string;
  disabled?: boolean;
}) {
  return (
    <SearchableSelect
      id={id}
      value={value}
      onValueChange={onValueChange}
      options={OPTIONS}
      placeholder="Select country"
      searchPlaceholder="Search country..."
      emptyText="No country found."
      ariaLabel="Country"
      disabled={disabled}
    />
  );
}
