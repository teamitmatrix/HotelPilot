"use client";

import { SearchableSelect } from "@/components/searchable-select";
import { currencyOptions } from "@/lib/currencies";

const OPTIONS = currencyOptions();

export function CurrencySelect({
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
      placeholder="Select currency"
      searchPlaceholder="Search currency..."
      emptyText="No currency found."
      ariaLabel="Currency"
      disabled={disabled}
    />
  );
}
