"use client";

import { useEffect, useMemo, useState } from "react";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import {
  DIAL_COUNTRIES,
  defaultDialCountry,
  formatE164,
  normalizeNationalNumber,
  parsePhoneValue,
  type DialCountry,
} from "@/lib/phone";

type PhoneInputProps = {
  value: string;
  onChange: (e164: string) => void;
  id?: string;
  disabled?: boolean;
  className?: string;
  placeholder?: string;
  defaultCountryIso?: string;
};

/**
 * Country dial code + national number. Emits E.164 (e.g. +923421234567).
 */
export function PhoneInput({
  value,
  onChange,
  id,
  disabled,
  className,
  placeholder = "3421234567",
  defaultCountryIso = "PK",
}: PhoneInputProps) {
  const parsed = useMemo(() => parsePhoneValue(value), [value]);
  const [country, setCountry] = useState<DialCountry>(() => {
    if (value) return parsed.country;
    return (
      DIAL_COUNTRIES.find((c) => c.iso === defaultCountryIso) || defaultDialCountry()
    );
  });
  const [national, setNational] = useState(() => (value ? parsed.national : ""));

  // Sync from parent when value changes externally (e.g. form reset).
  useEffect(() => {
    if (!value) {
      setNational("");
      return;
    }
    const next = parsePhoneValue(value);
    setCountry(next.country);
    setNational(next.national);
  }, [value]);

  function emit(nextCountry: DialCountry, nextNational: string) {
    onChange(formatE164(nextCountry.dial, nextNational));
  }

  return (
    <div className={cn("flex gap-2", className)}>
      <Select
        value={country.iso}
        disabled={disabled}
        onValueChange={(iso) => {
          const next =
            DIAL_COUNTRIES.find((c) => c.iso === iso) || defaultDialCountry();
          setCountry(next);
          emit(next, national);
        }}
      >
        <SelectTrigger className="w-[8.5rem] shrink-0" aria-label="Country code">
          <SelectValue>
            <span className="flex items-center gap-1.5 truncate">
              <span aria-hidden>{country.flag}</span>
              <span className="tabular-nums">+{country.dial}</span>
            </span>
          </SelectValue>
        </SelectTrigger>
        <SelectContent className="max-h-64">
          {DIAL_COUNTRIES.map((c) => (
            <SelectItem key={c.iso} value={c.iso}>
              <span className="flex items-center gap-2">
                <span aria-hidden>{c.flag}</span>
                <span className="tabular-nums text-muted-foreground">+{c.dial}</span>
                <span className="truncate">{c.name}</span>
              </span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Input
        id={id}
        type="tel"
        inputMode="numeric"
        autoComplete="tel-national"
        disabled={disabled}
        placeholder={placeholder}
        value={national}
        className="min-w-0 flex-1"
        onChange={(e) => {
          const next = normalizeNationalNumber(e.target.value);
          setNational(next);
          emit(country, next);
        }}
      />
    </div>
  );
}
