/**
 * Domain / entity filtering for Financial Core (erpclaw) sidebar.
 *
 * HotelPilot Central Admin exposes erpclaw as "Financial Core" and only
 * surfaces PMS-relevant accounting actions — Chart of Accounts, Journal Entries,
 * Invoices, Payments, and Tax Templates. Retail / manufacturing domains
 * (Sales Orders, Purchase Orders, Stock, etc.) stay hidden.
 */

/** Domains that may appear under Financial Core (when they contain allowlisted entities). */
export const PMS_FINANCIAL_DOMAINS = [
  "chart_of_accounts",
  "entry",
  "sales_billing",
  "template",
] as const;

/**
 * Curated list actions for Financial Core.
 * Includes Payments even when UI.yaml marks list-payments as FormView,
 * so SuperAdmin still gets a nav entry for PMS payment workflows.
 */
export const PMS_FINANCIAL_LIST_ACTIONS: {
  action: string;
  entity: string;
  label: string;
  domain: string;
}[] = [
  {
    action: "list-accounts",
    entity: "account",
    label: "Chart of Accounts",
    domain: "chart_of_accounts",
  },
  {
    action: "list-journal-entries",
    entity: "journal_entry",
    label: "Journal Entries",
    domain: "journal",
  },
  {
    action: "list-sales-invoices",
    entity: "sales_invoice",
    label: "Invoices",
    domain: "sales_billing",
  },
  {
    action: "list-payments",
    entity: "payment_entry",
    label: "Payments",
    domain: "entry",
  },
  {
    action: "list-tax-templates",
    entity: "tax_template",
    label: "Tax Templates",
    domain: "template",
  },
];

const PMS_ENTITY_SET = new Set(PMS_FINANCIAL_LIST_ACTIONS.map((a) => a.entity));
const PMS_ACTION_SET = new Set(PMS_FINANCIAL_LIST_ACTIONS.map((a) => a.action));
const PMS_DOMAIN_SET = new Set([
  ...PMS_FINANCIAL_DOMAINS,
  "journal", // synthetic domain for ungrouped journal_entry
]);

// ── Legacy profile presets (kept for non-PMS contexts / tests) ───────────────

const SETUP = ["company", "defaults", "users"];
const ACCOUNTING = ["chart_of_accounts", "general_ledger", "fiscal", "budgeting", "terms"];
const ITEMS = ["category", "item_master", "pricing"];
const INVENTORY = ["warehousing", "stock_transactions", "tracking"];
const SALES = ["customers", "sales", "fulfillment", "sales_billing"];
const PURCHASING = ["suppliers", "purchasing", "receiving", "purchase_billing"];
const SUBSCRIPTION = ["meter", "rate_plan", "subscription_billing"];

const ERPCLAW_DOMAIN_PRESETS: Record<string, string[]> = {
  "small-business": [...SETUP, ...ITEMS, ...SALES, ...PURCHASING, "chart_of_accounts"],
  retail: [...SETUP, ...ITEMS, ...INVENTORY, ...SALES, ...PURCHASING, "chart_of_accounts"],
  manufacturing: [...SETUP, ...ITEMS, ...INVENTORY, ...SALES, ...PURCHASING, ...ACCOUNTING],
  "professional-services": [...SETUP, ...ITEMS, ...SALES, ...PURCHASING, ...ACCOUNTING],
  distribution: [...SETUP, ...ITEMS, ...INVENTORY, ...SALES, ...PURCHASING, ...ACCOUNTING],
  saas: [...SETUP, ...ITEMS, ...SALES, ...SUBSCRIPTION, "chart_of_accounts"],
  "property-management": [...PMS_FINANCIAL_DOMAINS],
  healthcare: [...SETUP, ...ITEMS, ...SALES, ...PURCHASING, "chart_of_accounts"],
  dental: [...SETUP, "customers", "sales_billing", "chart_of_accounts"],
  veterinary: [...SETUP, "customers", "sales_billing", "chart_of_accounts"],
  "mental-health": [...SETUP, "customers", "sales_billing", "chart_of_accounts"],
  "home-health": [...SETUP, "customers", "sales_billing", "chart_of_accounts"],
  "k12-school": [...SETUP, ...ITEMS, ...SALES, ...PURCHASING, "chart_of_accounts"],
  "college-university": [...SETUP, ...ITEMS, ...SALES, ...PURCHASING, ...ACCOUNTING],
  nonprofit: [...SETUP, ...ITEMS, ...SALES, ...PURCHASING, ...ACCOUNTING],
  enterprise: [],
  "full-erp": [],
  custom: [],
};

/**
 * Whether this skill uses the PMS Financial Core allowlist
 * (always on for erpclaw in HotelPilot Central Admin).
 */
export function isFinancialCoreSkill(skillName: string): boolean {
  return skillName === "erpclaw";
}

/**
 * Get visible domain keys for a skill.
 * Financial Core always uses the PMS accounting allowlist.
 * Returns null if all domains should be shown (no filtering).
 */
export function getVisibleDomains(
  profileKey: string | undefined | null,
  skillName: string,
): string[] | null {
  if (!isFinancialCoreSkill(skillName)) return null;

  // Always restrict Financial Core to PMS accounting domains
  return [...PMS_DOMAIN_SET];
}

/** True if an entity key is allowed under Financial Core. */
export function isPmsFinancialEntity(entity: string): boolean {
  return PMS_ENTITY_SET.has(entity);
}

/** True if a list action is allowed under Financial Core. */
export function isPmsFinancialAction(action: string): boolean {
  return PMS_ACTION_SET.has(action);
}

/**
 * True if any erpclaw action (list/add/get/…) relates to a PMS financial entity.
 * Used by Cmd+K and Action Runner to hide retail/manufacturing actions.
 */
export function isPmsFinancialRelatedAction(action: string): boolean {
  if (PMS_ACTION_SET.has(action)) return true;
  const stems = [
    "account",
    "journal-entry",
    "journal-entries",
    "sales-invoice",
    "payment",
    "tax-template",
  ];
  // Exclude payment-terms / payment-ledger noise from "payment"
  if (action.includes("payment-term") || action.includes("payment-ledger") || action.includes("payment-allocation") || action.includes("payment-deduction")) {
    return action.includes("payment-entry") || action === "list-payments" || action === "add-payment" || action === "get-payment" || action === "submit-payment" || action === "update-payment" || action === "cancel-payment";
  }
  return stems.some((stem) => action.includes(stem));
}

/**
 * Filter list-action rows to PMS Financial Core only.
 * Falls back to the curated nav list so Payments still appears
 * even when UI.yaml does not expose a DataTable for list-payments.
 */
export function filterFinancialCoreActions(
  actions: { action: string; entity: string; label: string; domain?: string; addAction?: string }[],
): { action: string; entity: string; label: string; domain?: string; addAction?: string }[] {
  const byAction = new Map(actions.map((a) => [a.action, a]));
  return PMS_FINANCIAL_LIST_ACTIONS.map((curated) => {
    const fromYaml = byAction.get(curated.action);
    if (fromYaml) {
      return {
        ...fromYaml,
        label: curated.label,
        domain: curated.domain,
      };
    }
    return { ...curated };
  });
}

/**
 * Domain defs to render in Financial Core sidebar.
 * Uses curated labels even when journal_entry is ungrouped in UI.yaml.
 */
export function getFinancialCoreDomains(): { key: string; label: string; entities: string[] }[] {
  return [
    { key: "chart_of_accounts", label: "Chart of Accounts", entities: ["account"] },
    { key: "journal", label: "Journal Entries", entities: ["journal_entry"] },
    { key: "sales_billing", label: "Invoices", entities: ["sales_invoice"] },
    { key: "entry", label: "Payments", entities: ["payment_entry"] },
    { key: "template", label: "Tax Templates", entities: ["tax_template"] },
  ];
}

// localStorage key for "show all domains" override (disabled for Financial Core UX)
const SHOW_ALL_KEY = "erpclaw_show_all_domains";

export function getShowAllDomains(): boolean {
  if (typeof window === "undefined") return false;
  // Financial Core never expands to retail/manufacturing via "show all"
  return false;
}

export function setShowAllDomains(_show: boolean): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(SHOW_ALL_KEY);
}

/** @deprecated Profile presets retained for adaptive onboarding; Financial Core ignores them. */
export function getProfileDomainPreset(profileKey: string | undefined | null): string[] | null {
  if (!profileKey) return ERPCLAW_DOMAIN_PRESETS["property-management"];
  const preset = ERPCLAW_DOMAIN_PRESETS[profileKey];
  if (preset === undefined) return ERPCLAW_DOMAIN_PRESETS["property-management"];
  if (preset.length === 0) return null;
  return preset;
}
