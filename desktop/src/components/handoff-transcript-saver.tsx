import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { useEffect, useRef } from 'react'
import { toast } from 'sonner'
import { m } from '~/paraglide/messages.js'
import { autoProjectName } from '~/lib/project-name'
import type { Segment } from '~/lib/transcript'
import { notifyTranscriptsChanged, saveTranscript } from '~/lib/transcripts-store'
import { usePreferenceProvider } from '~/providers/preference'

/**
 * Phone handoff transcripts land in Recents.
 *
 * A phone recording is transcribed entirely in Rust, so it never passes through the transcribe
 * queue that normally persists a finished job. Without this listener the result exists only as a
 * `handoff_activity` event and disappears the moment the app is closed.
 *
 * The Settings → Phone section listens to the same event, but it is mounted only while that modal
 * is open — and a phone transcription is by definition something that arrives while the user is
 * doing something else. This component is mounted once for the app's lifetime instead.
 */

interface HandoffActivity {
	state: 'receiving' | 'loading_model' | 'transcribing' | 'done' | 'error'
	message?: string | null
	/** Absolute path of the saved phone audio. Only on `done`; either spelling is accepted. */
	savedPath?: string | null
	saved_path?: string | null
	/** Everything below is only present on `done`, and only once the backend supplies it. */
	segments?: Segment[] | null
	language?: string | null
	modelPath?: string | null
	model_path?: string | null
	name?: string | null
}

function isSegment(value: unknown): value is Segment {
	if (typeof value !== 'object' || value === null) return false
	const candidate = value as Partial<Segment>
	return typeof candidate.text === 'string' && typeof candidate.start === 'number' && typeof candidate.stop === 'number'
}

/** Keep only well-formed segments; a payload without any is treated as "nothing to save". */
function usableSegments(payload: HandoffActivity): Segment[] {
	return Array.isArray(payload.segments) ? payload.segments.filter(isSegment) : []
}

export default function HandoffTranscriptSaver() {
	const preference = usePreferenceProvider()
	// The listener is registered once; reading the preference through a ref keeps it current.
	const preferenceRef = useRef(preference)
	// Guards against saving the same recording twice (a re-emitted event, a remount in dev).
	const savedRef = useRef(new Set<string>())

	useEffect(() => {
		preferenceRef.current = preference
	}, [preference])

	useEffect(() => {
		let unlisten: UnlistenFn | undefined
		let cancelled = false

		const pending = listen<HandoffActivity>('handoff_activity', ({ payload }) => {
			if (payload?.state !== 'done') return

			const segments = usableSegments(payload)
			const sourcePath = payload.savedPath ?? payload.saved_path ?? ''
			// The backend may not carry the transcript yet. With no audio either there is nothing
			// worth a project folder; with audio, the folder is what rescues it -- see below.
			if (segments.length === 0 && !sourcePath) return

			const name = autoProjectName(payload.name?.trim() || m.phoneRecording(), 'record')
			// Not the rule a local *transcription* follows, and the comment at session.tsx:158 names
			// this file as one that does. It was wrong about this one. `transfer.rs` stages the phone
			// recording in `get_vibe_temp_folder()`, which `cleaner.rs` globs and `setup.rs` deletes
			// on the next launch on a later day, so this save is the only thing that moves the audio
			// somewhere durable. Gating it means switching the preference off silently destroys every
			// phone recording -- exactly the hazard the local recording is already exempted from, and
			// for exactly the same reason. So the switch decides whether the *transcript* is kept;
			// the audio is kept either way.
			const keepTranscript = preferenceRef.current.saveTranscripts
			if (!keepTranscript && !sourcePath) return

			const key = sourcePath || `${name}:${segments.length}:${segments[0].start}`
			if (savedRef.current.has(key)) return
			savedRef.current.add(key)

			// Fire-and-forget, like the queue's own persist: saving must never block the UI.
			void saveTranscript({
				name,
				sourcePath,
				projectsPath: preferenceRef.current.projectsPath,
				moveSourceMedia: true,
				// Same shape as the local recording's rescue save: the audio is saved, the transcript
				// is not. Nothing here is written that the user asked not to keep.
				segments: keepTranscript ? segments : [],
				language: keepTranscript ? (payload.language ?? undefined) : undefined,
				modelPath: keepTranscript ? (payload.modelPath ?? payload.model_path ?? null) : null,
			}).then((savedTranscript) => {
				if (!savedTranscript) {
					// Let it be retried if the same recording is announced again.
					savedRef.current.delete(key)
					return
				}
				notifyTranscriptsChanged()
				// Quiet, non-modal: the transcription happened while the user was looking elsewhere,
				// so a single line telling them where it went is worth more than silence.
				toast.success(keepTranscript ? m.phoneTranscriptionSaved() : m.phoneRecordingSaved(), { description: name, position: 'bottom-right' })
			})
		})

		void pending.then((fn) => {
			if (cancelled) fn()
			else unlisten = fn
		})

		return () => {
			cancelled = true
			unlisten?.()
		}
	}, [])

	return null
}
