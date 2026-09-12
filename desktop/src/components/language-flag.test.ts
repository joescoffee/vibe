import { describe, expect, it } from 'vitest'
import localeRegistry from '../../../i18n/locales.json'
import { flagFor } from './language-combobox'

/** Exactly what display-language-input.tsx passes to FlagSlot for each entry. */
function flagInPicker(code: string, name: string) {
	const baseName = name.replace(/\s*\([^)]*\)$/, '').trim()
	return flagFor(code, baseName)
}

describe('language picker flags', () => {
	it('shows Taiwan its own flag rather than the PRC one', () => {
		expect(flagInPicker('zh-TW', 'chinese (TW)')).toBe('🇹🇼')
		expect(flagInPicker('zh-CN', 'chinese')).toBe('🇨🇳')
	})

	it('still resolves a flag for every locale we ship', () => {
		const missing = localeRegistry.filter(({ code, name }) => !flagInPicker(code, name)).map(({ code }) => code)
		expect(missing).toEqual([])
	})
})
