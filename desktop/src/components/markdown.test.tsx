// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Markdown from './markdown'

const mocks = vi.hoisted(() => ({ openUrl: vi.fn() }))
vi.mock('@tauri-apps/plugin-opener', () => ({ openUrl: mocks.openUrl }))

afterEach(() => {
	cleanup()
	mocks.openUrl.mockReset()
})

/** Whether the click would have navigated the webview away from the app. */
function clickLink(name: string) {
	return fireEvent.click(screen.getByRole('link', { name }), { bubbles: true, cancelable: true })
}

describe('links inside untrusted markdown', () => {
	it('opens an external link in the system browser instead of navigating the webview', () => {
		render(<Markdown>{'see [the docs](https://example.com/docs)'}</Markdown>)
		// fireEvent returns false when a handler called preventDefault, i.e. no navigation.
		expect(clickLink('the docs')).toBe(false)
		expect(mocks.openUrl).toHaveBeenCalledWith('https://example.com/docs')
	})

	it('never renders a dangerous scheme as a link in the first place', () => {
		// react-markdown's own defaultUrlTransform strips these to an empty href, so
		// they lose the link role entirely. Asserted so a future rehype-raw or a custom
		// urlTransform that removes that behaviour fails here.
		render(<Markdown>{'[click me](javascript:alert(1)) and [report](file:///etc/passwd)'}</Markdown>)
		expect(screen.queryByRole('link')).toBeNull()
		expect(mocks.openUrl).not.toHaveBeenCalled()
	})

	it('cancels navigation and declines to open a scheme react-markdown does allow', () => {
		// `irc:` survives defaultUrlTransform, so this exercises isOpenableUrl rather
		// than the library underneath it.
		render(<Markdown>{'[chat](irc://example.com/room)'}</Markdown>)
		expect(clickLink('chat')).toBe(false)
		expect(mocks.openUrl).not.toHaveBeenCalled()
	})

	it('still escapes raw HTML, so the override cannot be bypassed with a literal anchor', () => {
		render(<Markdown>{'<a href="https://evil.test">x</a>'}</Markdown>)
		expect(screen.queryByRole('link')).toBeNull()
	})

	it('leaves a caller-supplied components map intact', () => {
		render(<Markdown components={{ em: ({ children }) => <b>{children}</b> }}>{'*hi* and [a](https://example.com)'}</Markdown>)
		expect(screen.getByText('hi').tagName).toBe('B')
		expect(clickLink('a')).toBe(false)
		expect(mocks.openUrl).toHaveBeenCalledWith('https://example.com')
	})
})
