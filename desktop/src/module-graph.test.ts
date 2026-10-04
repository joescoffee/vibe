import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * What is reachable from the entry point, and what is not.
 *
 * The deep review of 2026-10-04 listed several modules with no importer, and separately corrected
 * a claim in `verify-vibe`'s feature map that rested on a reachability argument made by eye. Both
 * are the same need: a mechanical answer to "does anything reach this file".
 *
 * The baseline below is not an assertion that these twenty files should exist. It is a ratchet --
 * a new unreachable module fails here on the pull request that adds it, and deleting one of these
 * is a one-line edit to the list, which is the point: the decision becomes explicit either way.
 */

const SRC = dirname(fileURLToPath(import.meta.url))
const ENTRY = join(SRC, 'main.tsx')

/** `from '...'` and `await import('...')`. Missing the second one is how this walk first lied. */
const SPEC = /(?:from\s*|\bimport\s*\(\s*)['"]([^'"]+)['"]/g

function resolveSpec(from: string, spec: string): string | null {
	let base: string
	if (spec.startsWith('~/')) base = join(SRC, spec.slice(2))
	else if (spec.startsWith('.')) base = resolve(dirname(from), spec)
	else return null
	for (const suffix of ['', '.tsx', '.ts', '/index.tsx', '/index.ts']) {
		const candidate = base + suffix
		try {
			if (statSync(candidate).isFile()) return candidate
		} catch {
			/* next suffix */
		}
	}
	return null
}

function reachable(): Set<string> {
	const seen = new Set([ENTRY])
	const queue = [ENTRY]
	while (queue.length) {
		const current = queue.pop()!
		const source = readFileSync(current, 'utf8')
		for (const match of source.matchAll(SPEC)) {
			const next = resolveSpec(current, match[1])
			if (next && !seen.has(next)) {
				seen.add(next)
				queue.push(next)
			}
		}
	}
	return seen
}

function allSources(dir: string): string[] {
	const out: string[] = []
	for (const entry of readdirSync(dir)) {
		const path = join(dir, entry)
		if (statSync(path).isDirectory()) {
			if (entry === 'paraglide' || entry === 'mock-tauri') continue
			out.push(...allSources(path))
		} else if (/\.tsx?$/.test(entry) && !/\.(test|d)\.tsx?$/.test(entry)) {
			out.push(path)
		}
	}
	return out
}

/** Unreachable today. Shrinking this is a deletion; growing it needs a reason. */
const KNOWN_UNREACHABLE = [
	'components/advanced-options-button.tsx',
	'components/advanced-transcribe.tsx',
	'components/app-menu.tsx',
	'components/dictation-promo.tsx',
	'components/drop-modal.tsx',
	'components/format-multi-select.tsx',
	'components/info-tooltip.tsx',
	'components/text-area.tsx',
	'components/ui/badge.tsx',
	'components/ui/card.tsx',
	'components/ui/collapsible.tsx',
	'components/ui/native-select.tsx',
	'components/ui/scroll-area.tsx',
	'components/ui/separator.tsx',
	'components/ui/tabs.tsx',
	'pages/home/audio-input.tsx',
	'pages/home/audio-player.tsx',
	'pages/home/audio-visualizer.tsx',
	'pages/home/hooks/use-media-selection.ts',
	'pages/home/progress-panel.tsx',
]

describe('module graph', () => {
	const reached = reachable()

	it('actually traverses something', () => {
		// A walk that resolves nothing reports every file as dead and reads like a clean result.
		// That is exactly what happened the first time: `main.tsx` imports only through
		// `await import()`, the pattern matched `from` alone, and the answer was "0 reachable".
		expect(reached.size).toBeGreaterThan(100)
	})

	it('reaches the two pages/home hooks the live session imports', () => {
		// The claim in `.claude/skills/verify-vibe/features/README.md` used to be that nothing under
		// pages/home is reachable. `pages/main/session.tsx` imports two of its hooks.
		expect(reached.has(join(SRC, 'pages/home/hooks/use-recording.ts'))).toBe(true)
		expect(reached.has(join(SRC, 'pages/home/hooks/use-audio-download.ts'))).toBe(true)
	})

	it('has no unreachable module beyond the known list', () => {
		const unreachable = allSources(SRC)
			.filter((path) => !reached.has(path))
			.map((path) => relative(SRC, path))
			.sort()
		expect(unreachable).toEqual(KNOWN_UNREACHABLE)
	})
})
