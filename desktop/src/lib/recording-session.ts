/**
 * Which kind of recording owns the microphone right now.
 *
 * `record_finish` carries no session identity, and three places start a recording -- the Record
 * button, the meeting/recording shortcut, and the dictation hotkey -- while two listeners decide
 * from these flags whose event each one is. That only works while at most one kind can be running,
 * so every start path checks the other flag first and refuses rather than overlapping. With both
 * running, `session.tsx` dropped the meeting recording as if it were dictation's and the hotkey
 * provider transcribed it into whatever had focus.
 *
 * Plain module state rather than context, because `record_finish` listeners and `startRecord` are
 * not all inside the same provider tree, and because this file importing nothing is what keeps
 * `hotkey.tsx` and `recording-shortcut.tsx` from importing each other.
 */
let hotkeyRecording = false
let normalRecording = false

export function setHotkeyRecordingActive(active: boolean) {
	hotkeyRecording = active
}

export function isHotkeyRecordingActive() {
	return hotkeyRecording
}

export function setNormalRecordingActive(active: boolean) {
	normalRecording = active
}

export function isNormalRecordingActive() {
	return normalRecording
}
