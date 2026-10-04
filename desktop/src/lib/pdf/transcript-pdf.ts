import { createElement } from 'react'
import type { Segment } from '~/lib/transcript'
import type { TranscriptExportOptions } from '~/lib/transcript-export'
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
 * The renderer and the embedded font weigh about a megabyte between them, so both the library and
 * the document that uses it are imported on the first export rather than at startup.
 */
/**
 * Han, Kana and Hangul. Rubik has none of these and react-pdf has no font fallback, so these
 * codepoints map into Rubik's glyph space and draw the wrong glyphs with a wrong ToUnicode map:
 * the output is neither readable nor searchable, yet `qpdf --check` passes, so it looks like a
 * corrupt file rather than a missing font.
 */
const UNRENDERABLE = /[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\u3040-\u30FF\uAC00-\uD7AF]/

export async function transcriptToPdf(segments: Segment[], summary: string, options: TranscriptExportOptions, labels: TranscriptPdfLabels) {
	// Refuse rather than write mojibake. Silent corruption is the worse failure: the user keeps a
	// file they believe holds their transcript. DOCX, HTML and the text formats carry CJK fine.
	const sample = segments.map((segment) => segment.text).join('') + summary
	if (UNRENDERABLE.test(sample)) {
		throw new Error(
			'PDF export cannot render Chinese, Japanese or Korean text: the bundled Rubik font has no ' +
				'glyphs for them and would write unreadable output. Export as DOCX, HTML, TXT, SRT or VTT instead.',
		)
	}
	const [{ Font, pdf }, { PDF_FONT, TranscriptDocument }] = await Promise.all([import('@react-pdf/renderer'), import('./transcript-document')])
	// Rubik carries Latin, Hebrew and Cyrillic in one family, so a mixed transcript needs no font
	// switching. Registering again is harmless; react-pdf keeps the last source for each weight.
	Font.register({
		family: PDF_FONT,
		fonts: [
			{ src: regularUrl, fontWeight: 400 },
			{ src: boldUrl, fontWeight: 700 },
		],
	})
	// `pdf()` is typed for a <Document> element; ours renders one, which the types cannot see.
	const document = createElement(TranscriptDocument, { segments, summary, options, labels }) as Parameters<typeof pdf>[0]
	const blob = await pdf(document).toBlob()
	return new Uint8Array(await blob.arrayBuffer())
}
