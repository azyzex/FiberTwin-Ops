import { expect, test } from '@playwright/test'

test('replay loads with the honesty banner, map and timeline', async ({ page }) => {
  await page.goto('/?replay=synthetic-f1')
  await expect(page.getByRole('heading', { name: /FiberTwin/ })).toBeVisible()
  await expect(page.getByTestId('synthetic-banner')).toBeVisible()
  await expect(page.getByTestId('network-map').locator('canvas').first()).toBeVisible()
  await expect(page.getByTestId('timeline').locator('li')).toHaveCount(8)
})

test('the fibre selected by default shows its shared-risk group', async ({ page }) => {
  await page.goto('/?replay=synthetic-f1')
  const inspector = page.getByTestId('inspector')
  await expect(inspector).toContainText('Fibre F12')
  await expect(inspector).toContainText('L12, L13')
  await expect(inspector).toContainText('Shared-risk group')
})

test('deep link to a frame after the drain shows the deploy step reached', async ({ page }) => {
  await page.goto('/?replay=synthetic-f1&frame=22')
  await expect(page.getByRole('button', { name: /Deployed \(GitOps\)/ })).toHaveClass(/ring-1/)
})

test('clicking a timeline step moves the replay there', async ({ page }) => {
  await page.goto('/?replay=synthetic-f1')
  await page.getByRole('button', { name: /Human approved/ }).click()
  await expect(page.getByLabel('Replay position')).toHaveValue('19')
})

test('play advances the replay', async ({ page }) => {
  await page.goto('/?replay=synthetic-f1')
  const slider = page.getByLabel('Replay position')
  await page.getByTestId('play').click()
  await expect.poll(async () => Number(await slider.inputValue()), { timeout: 5000 }).toBeGreaterThan(1)
})

test('access screen shows the four ONT budgets', async ({ page }) => {
  await page.goto('/?replay=synthetic-f1')
  await page.getByRole('button', { name: 'Access' }).click()
  await expect(page.getByTestId('access-panel').getByText(/ONT \d/)).toHaveCount(4)
})

test('the default replay is the recorded golden-path run', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('recorded-banner')).toContainText('recorded run')
  await expect(page.getByTestId('timeline').getByText('Recovery verified')).toBeVisible()
})
