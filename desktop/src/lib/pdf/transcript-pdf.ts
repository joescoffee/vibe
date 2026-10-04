import { createElement } from 'react'
import type { Segment } from '~/lib/transcript'
import type { TranscriptExportOptions } from '~/lib/transcript-export'
import { PDF_FONT, PDF_FONT_CJK, type PdfFontFamily } from './transcript-document'
import cjkBoldUrl from '~/assets/fonts/NotoSansTC-Bold.otf?url'
import cjkRegularUrl from '~/assets/fonts/NotoSansTC-Regular.otf?url'
import boldUrl from '~/assets/fonts/Rubik-Bold.ttf?url'
import regularUrl from '~/assets/fonts/Rubik-Regular.ttf?url'

export interface TranscriptPdfLabels {
	transcript: string
	summary: string
}

/**
 * Build the transcript as a real PDF: text stays selectable, right-to-left runs are reordered by
 * the Unicode bidi algorithm, and pages break on their own.
 *
 * The renderer and the fonts weigh about twelve megabytes between them -- Noto Sans TC is eleven
 * of that -- so both the library and the document that uses it are imported on the first export
 * rather than at startup. Nothing is fetched until someone exports a PDF.
 */
/** Han and Kana: Noto Sans TC draws these, Rubik draws none of them. */
const NEEDS_CJK = /[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\u3040-\u30FF]/
/** Hebrew: Rubik draws it, Noto Sans TC does not. */
const NEEDS_RUBIK = /[\u0590-\u05FF]/
/** Hangul: neither bundled font has a single glyph for it. */
const NEEDS_NEITHER = /[\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F]/

/**
 * Which family can draw this text, or `null` when nothing bundled can.
 *
 * react-pdf has no per-character font fallback, so the choice is per document. The percentages
 * behind these three classes are measured from the two cmaps and recorded in
 * `transcript-document.tsx`; the rule follows from them rather than from an impression.
 *
 * The refusal that remains is narrow and specific. The one it replaces rejected every Han, Kana
 * *and* Hangul codepoint, which was right while Rubik was the only font and is now wrong for the
 * first two.
 */
export function pdfFontFor(text: string): { family: PdfFontFamily; reason?: undefined } | { family: null; reason: string } {
	if (NEEDS_NEITHER.test(text)) {
		return {
			family: null,
			reason:
				'PDF export cannot render Korean: neither bundled font has Hangul glyphs, and writing it ' +
				'anyway would produce a file that looks intact and reads as nonsense. Export as DOCX, HTML, ' +
				'TXT, SRT or VTT instead.',
		}
	}
	const cjk = NEEDS_CJK.test(text)
	if (cjk && NEEDS_RUBIK.test(text)) {
		return {
			family: null,
			reason:
				'PDF export cannot put Hebrew and Chinese or Japanese in one file: Rubik has no Han or Kana ' +
				'and Noto Sans TC has no Hebrew, and react-pdf cannot switch font per character. Export as ' +
				'DOCX, HTML, TXT, SRT or VTT instead.',
		}
	}
	return { family: cjk ? PDF_FONT_CJK : PDF_FONT }
}

export async function transcriptToPdf(segments: Segment[], summary: string, options: TranscriptExportOptions, labels: TranscriptPdfLabels) {
	// Refuse rather than write mojibake. Silent corruption is the worse failure: the user keeps a
	// file they believe holds their transcript.
	const sample = segments.map((segment) => segment.text).join('') + summary + options.title
	const chosen = pdfFontFor(sample)
	if (chosen.family === null) {
		throw new Error(chosen.reason)
	}
	const [{ Font, pdf }, { TranscriptDocument }] = await Promise.all([import('@react-pdf/renderer'), import('./transcript-document')])
	// Both families, always: registering is cheap, and only the one the document names is embedded
	// in the output. Registering again is harmless; react-pdf keeps the last source per weight.
	Font.register({
		family: PDF_FONT,
		fonts: [
			{ src: regularUrl, fontWeight: 400 },
			{ src: boldUrl, fontWeight: 700 },
		],
	})
	Font.register({
		family: PDF_FONT_CJK,
		fonts: [
			{ src: cjkRegularUrl, fontWeight: 400 },
			{ src: cjkBoldUrl, fontWeight: 700 },
		],
	})
	// `pdf()` is typed for a <Document> element; ours renders one, which the types cannot see.
	const document = createElement(TranscriptDocument, {
		segments,
		summary,
		options,
		labels,
		family: chosen.family,
	}) as Parameters<typeof pdf>[0]
	const blob = await pdf(document).toBlob()
	return new Uint8Array(await blob.arrayBuffer())
}
