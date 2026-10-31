"use client";

import { SearchableSelect } from "@/components/searchable-select";
import { timezoneOptions } from "@/lib/timezones";

const OPTIONS = timezoneOptions();

export function TimezoneSelect({
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
      placeholder="Select timezone"
      searchPlaceholder="Search timezone..."
      emptyText="No timezone found."
      ariaLabel="Timezone"
      disabled={disabled}
    />
  );
}
