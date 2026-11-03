import { test } from '@playwright/test'

// Not an assertion test: saves screenshots for the README / visual review.
test('screenshot at the drain', async ({ page }, info) => {
  await page.goto('/?frame=20')
  await page.waitForTimeout(800)
  await page.screenshot({ path: `test-results/shots/drain-${info.project.name}.png`, fullPage: true })
})

test('screenshot what-if and optical cliff', async ({ page }, info) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'What-if', exact: true }).click()
  await page.getByTestId('fibre-F12').click()
  await page.getByTestId('fibre-F34').click()
  await page.waitForTimeout(800)
  await page.screenshot({ path: `test-results/shots/whatif-${info.project.name}.png`, fullPage: true })
  await page.getByRole('button', { name: 'Optical cliff', exact: true }).click()
  await page.getByLabel('Extra span loss').fill('27')
  await page.waitForTimeout(800)
  await page.screenshot({ path: `test-results/shots/optical-${info.project.name}.png`, fullPage: true })
})
