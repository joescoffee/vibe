import { useEffect, useState } from 'react'
import { FallbackProps } from 'react-error-boundary'
import { ErrorModalState } from '~/providers/error-modal'
import ErrorModal from './error-modal'

/**
 * Shown when a render throws. It owns its state rather than reading the error-modal context.
 *
 * It used to read the context, and the context was always empty here: `ErrorBoundary` wraps
 * `ErrorModalProvider` in `app.tsx`, so by construction this component renders *outside* the
 * provider, took `createContext`'s `{}` default, and `setState` was `undefined`. Both buttons threw
 * `TypeError: setState is not a function` on click. Moving the provider outward would not fix it
 * either: the provider lives in the subtree the boundary has just unmounted. A fallback cannot
 * depend on the tree it is replacing.
 */
export function BoundaryFallback({ error }: FallbackProps) {
	const [state, setState] = useState<ErrorModalState>({ open: true, log: `Boundary error:\n${String(error)}` })

	useEffect(() => {
		// In case of error in first renders show the window
		// Tauri API from import won't be available...
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const currentWindow = (window as any).__TAURI__.webviewWindow.getCurrentWebviewWindow()
		currentWindow.show()
		currentWindow.setFocus()
	}, [])

	return (
		<div>
			<ErrorModal setState={setState} state={state} />
		</div>
	)
}
