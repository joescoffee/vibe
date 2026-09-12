import { openUrl } from '@tauri-apps/plugin-opener'
import type { ComponentProps } from 'react'
import ReactMarkdown from 'react-markdown'

/** Schemes a link may hand to the OS. Anything else is rendered but inert. */
function isOpenableUrl(href: string) {
	try {
		return ['http:', 'https:', 'mailto:'].includes(new URL(href).protocol)
	} catch {
		// Relative or malformed — there is nothing meaningful to open externally.
		return false
	}
}

/**
 * `react-markdown` with links routed to the system browser.
 *
 * The markdown rendered in this app is untrusted: transcripts come from arbitrary
 * media, and a summary is whatever the model made of one. A bare `<a href>` inside a
 * Tauri webview navigates the *webview*, replacing the app with a web page and leaving
 * no way back — and that navigation is the click a remote origin needs in order to
 * reach IPC. Opening links externally is already what the rest of the app does
 * (`pages/settings/page.tsx`, `pages/settings/sections/ai.tsx`, `components/error-modal.tsx`).
 *
 * Import this instead of `react-markdown` everywhere, so a new call site cannot quietly
 * miss the override. `src-tauri/src/navigation.rs` is the independent backstop.
 */
export default function Markdown(props: ComponentProps<typeof ReactMarkdown>) {
	return (
		<ReactMarkdown
			{...props}
			components={{
				...props.components,
				a: ({ href, children, ...rest }) => (
					<a
						{...rest}
						href={href}
						onClick={(event) => {
							event.preventDefault()
							if (href && isOpenableUrl(href)) void openUrl(href)
						}}>
						{children}
					</a>
				),
			}}
		/>
	)
}
