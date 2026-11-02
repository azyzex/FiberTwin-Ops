import { test } from '@playwright/test'

// Not an assertion test: saves screenshots for the README / visual review.
test('screenshot at the drain', async ({ page }, info) => {
  await page.goto('/?frame=22')
  await page.waitForTimeout(800)
  await page.screenshot({ path: `test-results/shots/drain-${info.project.name}.png`, fullPage: true })
})
