// @vitest-environment node
import { execFileSync } from 'node:child_process'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Font, pdf } from '@react-pdf/renderer'
import { createElement } from 'react'
import { describe, expect, it } from 'vitest'
import type { TranscriptExportOptions } from '~/lib/transcript-export'
import { PDF_FONT, PDF_FONT_CJK, TranscriptDocument } from './transcript-document'

/**
 * The bug this guards was found by rendering, not by reading: the structure passed `qpdf --check`
 * while `File-賽局理論與_AI：機制設計與自動化` came out as `File-ý ÀÐ6-êÕ`. A test that only
 * asserts "a PDF was produced" would have passed then too. So this extracts the text back out and
 * requires the characters that went in.
 */

const options: TranscriptExportOptions = {
	content: 'transcript',
	showTimestamps: false,
	showSpeakers: false,
	speakerLabel: 'Speaker',
	title: '賽局理論與 AI',
	direction: 'ltr',
}

async function render(text: string, family: typeof PDF_FONT | typeof PDF_FONT_CJK) {
	Font.register({
		family: PDF_FONT,
		fonts: [
			{ src: 'src/assets/fonts/Rubik-Regular.ttf', fontWeight: 400 },
			{ src: 'src/assets/fonts/Rubik-Bold.ttf', fontWeight: 700 },
		],
	})
	Font.register({
		family: PDF_FONT_CJK,
		fonts: [
			{ src: 'src/assets/fonts/NotoSansTC-Regular.otf', fontWeight: 400 },
			{ src: 'src/assets/fonts/NotoSansTC-Bold.otf', fontWeight: 700 },
		],
	})
	const document = createElement(TranscriptDocument, {
		segments: [{ start: 0, stop: 100, text }],
		summary: '',
		options,
		labels: { transcript: '逐字稿', summary: '摘要' },
		family,
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
	}) as any
	const blob = await pdf(document).toBlob()
	return new Uint8Array(await blob.arrayBuffer())
}

function extract(bytes: Uint8Array): string {
	const dir = mkdtempSync(join(tmpdir(), 'vibe-pdf-'))
	const file = join(dir, 'out.pdf')
	writeFileSync(file, bytes)
	return execFileSync('pdftotext', [file, '-'], { encoding: 'utf8' })
}

describe('CJK in a real PDF', () => {
	it('round-trips the title the review found mojibake', async () => {
		const subject = '賽局理論與 AI：機制設計與自動化'
		const text = extract(await render(subject, PDF_FONT_CJK))
		// Character by character, because a partial match is how mojibake looks when it is lucky.
		for (const character of subject.replace(/\s/g, '')) {
			expect(text, `missing ${character}`).toContain(character)
		}
	}, 60_000)

	it('keeps Latin readable in the same family', async () => {
		const text = extract(await render('Mixed 繁體中文 and English', PDF_FONT_CJK))
		expect(text).toContain('English')
		expect(text).toContain('繁體中文')
	}, 60_000)

	it('shows what the old behaviour looked like: Rubik cannot draw it', async () => {
		// The negative control. Without it, the two tests above could pass for the wrong reason --
		// they would also pass if pdftotext were reading the input rather than the output.
		const text = extract(await render('賽局理論', PDF_FONT))
		expect(text).not.toContain('賽局理論')
	}, 60_000)
})
