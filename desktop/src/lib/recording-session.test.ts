import { beforeEach, describe, expect, it } from 'vitest'
import {
	isHotkeyRecordingActive,
	isNormalRecordingActive,
	setHotkeyRecordingActive,
	setNormalRecordingActive,
} from './recording-session'

/**
 * `record_finish` carries no session identity. Two listeners -- `session.tsx` and the hotkey
 * provider -- decide whose event it is from these two flags, which is only sound while at most
 * one of them is ever true. The start paths enforce that; this asserts the contract they rely on
 * and that each flag is independently observable, because the bug this replaces was one side of
 * the exclusion simply being absent.
 */
describe('recording session flags', () => {
	beforeEach(() => {
		setHotkeyRecordingActive(false)
		setNormalRecordingActive(false)
	})

	it('both read false when nothing is recording', () => {
		expect(isHotkeyRecordingActive()).toBe(false)
		expect(isNormalRecordingActive()).toBe(false)
	})

	it('each flag is independent, so a reader can tell the two apart', () => {
		setHotkeyRecordingActive(true)
		expect(isHotkeyRecordingActive()).toBe(true)
		expect(isNormalRecordingActive()).toBe(false)

		setHotkeyRecordingActive(false)
		setNormalRecordingActive(true)
		expect(isHotkeyRecordingActive()).toBe(false)
		expect(isNormalRecordingActive()).toBe(true)
	})
})
