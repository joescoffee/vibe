import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * The defect class this file guards against has seven known instances in this tree's history:
 * a store function answers whether it wrote, and the caller drops the answer. The sidebar's
 * rename did it, and every later edit went to a folder that no longer existed, on screen only.
 *
 * `void f(...)` is this codebase's deliberate "fire and forget", and that is fine for a function
 * that reports its own failure. It is not fine for one whose only failure channel is the value it
 * returns. So: enumerate the store's answer-returning exports from the source rather than from a
 * hand-written list -- a new one is then covered the day it is added -- and forbid `void` on them.
 */

const SRC = join(import.meta.dirname, '..')
const STORE = join(SRC, 'lib', 'transcripts-store.ts')

/** Exported async functions of the store whose return value is the only way a failure is visible. */
function answerReturningExports(): string[] {
	const source = readFileSync(STORE, 'utf8')
	const pattern = /export async function (\w+)\([^)]*\)\s*:\s*Promise<([^>]*(?:<[^>]*>)?[^>]*)>/g
	const names: string[] = []
	for (const match of source.matchAll(pattern)) {
		const [, name, returns] = match
		if (returns === 'void') continue
		names.push(name)
	}
	return names
}

function sourceFiles(dir: string): string[] {
	const out: string[] = []
	for (const entry of readdirSync(dir)) {
		const path = join(dir, entry)
		if (statSync(path).isDirectory()) {
			if (entry === 'paraglide' || entry === 'mock-tauri') continue
			out.push(...sourceFiles(path))
		} else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
			out.push(path)
		}
	}
	return out
}

describe('transcripts-store results are not discarded', () => {
	it('finds the store functions whose answer matters', () => {
		const names = answerReturningExports()
		// A guard that enumerates nothing passes forever. Anchor it on the five the review named.
		expect(names).toEqual(
			expect.arrayContaining([
				'updateTranscriptSegments',
				'updateTranscriptSpeakerNames',
				'updateTranscriptThread',
				'updateTranscriptSummary',
				'renameTranscript',
			]),
		)
	})

	it('no caller discards a store answer', () => {
		const names = answerReturningExports()
		const pattern = new RegExp(`\\bvoid\\s+(${names.join('|')})\\s*\\(`, 'g')
		const offenders: string[] = []
		for (const file of sourceFiles(SRC)) {
			const source = readFileSync(file, 'utf8')
			for (const match of source.matchAll(pattern)) {
				// `void f(...).then(...)` does consume the answer, and that is the shape the
				// fire-and-forget call sites already use. Walk to the matching paren to tell the two
				// apart: a line-wise test calls every multi-line call an offender.
				let depth = 0
				let index = match.index + match[0].length - 1
				for (; index < source.length; index += 1) {
					if (source[index] === '(') depth += 1
					else if (source[index] === ')') {
						depth -= 1
						if (depth === 0) break
					}
				}
				const after = source.slice(index + 1, index + 8)
				if (after.startsWith('.then') || after.startsWith('.catch')) continue
				const line = source.slice(0, match.index).split('\n').length
				offenders.push(`${file.slice(SRC.length + 1)}:${line}  ${match[1]}`)
			}
		}
		expect(offenders).toEqual([])
	})

	it('detects a discarded answer when one is introduced', () => {
		// The guard above passing means nothing unless it can fail. This is the mutation, inline.
		const sample = ['void updateTranscriptSegments(path, segments)', 'void updateTranscriptSummary(path, text).then(() => {})'].join('\n')
		const pattern = /\bvoid\s+(updateTranscriptSegments|updateTranscriptSummary)\s*\(/g
		const flagged: string[] = []
		for (const match of sample.matchAll(pattern)) {
			let depth = 0
			let index = match.index + match[0].length - 1
			for (; index < sample.length; index += 1) {
				if (sample[index] === '(') depth += 1
				else if (sample[index] === ')') {
					depth -= 1
					if (depth === 0) break
				}
			}
			if (sample.slice(index + 1, index + 8).startsWith('.then')) continue
			flagged.push(match[1])
		}
		expect(flagged).toEqual(['updateTranscriptSegments'])
	})
})
