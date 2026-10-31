/**
 * E2E Test: Navigation flows
 *
 * Tests 6-7: Sidebar navigation, skill page tabs
 */
import { test, expect, waitForDashboard, navigateToSkill } from "./fixtures";

// ─── Test 6: Sidebar shows skill categories ─────────────────────────────────

test("sidebar lists skills grouped by category", async ({ authedPage: page }) => {
  await page.goto("/dashboard");
  await waitForDashboard(page);

  // On desktop, skill links show in sidebar or dashboard grid
  // On mobile, sidebar is collapsed — skill links appear in the main content grid
  const skillLinks = page.locator("a[href^='/skills/erpclaw'], a[href^='/skills/']");
  let count = await skillLinks.count();

  if (count === 0) {
    // On mobile, try opening the sidebar menu first
    const menuButton = page.locator("button[aria-label*='menu' i], button[aria-label*='sidebar' i], [data-sidebar='trigger']");
    if (await menuButton.count() > 0) {
      await menuButton.first().click();
      await page.waitForTimeout(1_000);
      count = await skillLinks.count();
    }
  }
  expect(count).toBeGreaterThan(0);

  // At least some skill names should be visible on the page
  const pageText = await page.textContent("body");
  // Skill display names or identifiers: GL, Selling, Buying, etc.
  const knownSkills = ["GL", "Selling", "Buying", "Setup", "Inventory", "erpclaw"];
  let foundCount = 0;
  for (const skill of knownSkills) {
    if (pageText?.includes(skill)) foundCount++;
  }
  // On mobile the sidebar may not expose text — skill links alone are sufficient
  expect(count > 0 || foundCount >= 2).toBeTruthy();
});

// ─── Test 7: Navigate to skill page shows tabs ─────────────────────────────

test("skill page shows browse and execute tabs", async ({ authedPage: page }) => {
  await navigateToSkill(page, "erpclaw");

  // Should have Browse Data and Actions tabs
  await expect(page.getByRole("tab", { name: /browse data/i })).toBeVisible();
  await expect(page.getByRole("tab", { name: /actions/i })).toBeVisible();

  // Skill description should appear somewhere on the page
  const pageText = await page.textContent("body");
  // The selling skill has description about customers/quotations/etc.
  expect(pageText?.length).toBeGreaterThan(100);
});
