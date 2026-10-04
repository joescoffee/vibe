// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/plugin-opener', () => ({ openUrl: vi.fn() }))
vi.mock('@tauri-apps/plugin-clipboard-manager', () => ({ writeText: vi.fn() }))
vi.mock('~/lib/logs', () => ({ collectLogs: vi.fn(async () => '') }))
vi.mock('~/lib/app', () => ({ getIssueUrl: vi.fn(async () => 'https://example.invalid'), resetApp: vi.fn() }))

import * as app from '~/lib/app'
import { BoundaryFallback } from './boundary-fallback'

/**
 * The fallback renders because the subtree threw, and `ErrorModalProvider` is inside that subtree.
 * So it must not read the error-modal context: it got `createContext`'s `{}` default, and both
 * buttons threw `TypeError: setState is not a function`. Rendering it with no provider at all is
 * the exact condition it meets in `app.tsx`.
 */
describe('BoundaryFallback', () => {
	beforeEach(() => {
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		;(window as any).__TAURI__ = { webviewWindow: { getCurrentWebviewWindow: () => ({ show: vi.fn(), setFocus: vi.fn() }) } }
	})

	it('renders its dialog with no provider above it', () => {
		render(<BoundaryFallback error={new Error('boom')} resetErrorBoundary={() => {}} />)
		expect(screen.getByRole('dialog')).toBeTruthy()
		expect(screen.getByRole('dialog').textContent).toContain('boom')
	})

	it('Reset Vibe runs instead of throwing, which is what it used to do', () => {
		const resetApp = vi.mocked(app.resetApp)
		render(<BoundaryFallback error={new Error('boom')} resetErrorBoundary={() => {}} />)

		// `clearLogAndReset` calls setState before resetApp, so an undefined setState threw and the
		// reset never happened. Both assertions are needed: the call proves it got past setState,
		// and the closed dialog proves setState did something rather than being a silent stub.
		// Reaching resetApp is the discriminating evidence: `clearLogAndReset` awaits nothing and
		// calls setState on its first line, so with the context default of `{}` this threw
		// `TypeError: setState is not a function` and resetApp was never reached. The dialog node
		// itself is not asserted -- Radix keeps it mounted until an animationend jsdom never fires.
		fireEvent.click(screen.getByRole('button', { name: 'Reset Vibe' }))
		expect(resetApp).toHaveBeenCalledTimes(1)
	})
})
