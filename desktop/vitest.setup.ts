/**
 * Give jsdom tests a working `localStorage`.
 *
 * Node 25 defines `globalThis.localStorage` itself (Web Storage, on by default). vitest's
 * `populateGlobal` only copies a window property over a global that already exists when the
 * key is on its own KEYS list, and `localStorage` is not on it — only `Storage` and
 * `StorageEvent` are. So the jsdom window's real `Storage` never lands on the global and
 * tests see Node's object instead, which has no `clear`, `getItem` or `setItem`.
 *
 * `localStorage` is the only global where this bites: a probe comparing every jsdom window
 * property against Node's globals found exactly one mismatch. `sessionStorage` is untouched
 * because Node does not define it.
 *
 * The replacement is a real jsdom `Storage` rather than a hand-written stand-in, so quota,
 * key coercion and iteration behave as they do in a browser. It comes from a second JSDOM
 * built here, which also keeps each test file's storage isolated.
 */
import { JSDOM } from 'jsdom'

// Setup files run for every test file, including the ones on the default node environment.
// Only jsdom tests have a document, and only they need this.
if (typeof document !== 'undefined') {
	const { localStorage } = new JSDOM('', { url: 'http://localhost:3000' }).window
	Object.defineProperty(globalThis, 'localStorage', {
		value: localStorage,
		configurable: true,
		writable: true,
	})
}
