import { invoke } from '@tauri-apps/api/core'
import { platform } from '@tauri-apps/plugin-os'
import { toast } from 'sonner'
import { m } from '~/paraglide/messages.js'

export type PermissionStatus = 'granted' | 'denied' | 'not_determined' | 'restricted' | 'not_applicable'

/**
 * Ensures system audio recording permission is granted on macOS.
 * - Already granted: returns true immediately
 * - First time: shows native macOS prompt, waits for user response
 * - Previously denied: opens System Settings, shows toast, returns false
 *
 * Returns true if permission is granted and recording can proceed.
 * No-op on non-macOS platforms (always returns true).
 */
export async function ensureSystemAudioPermission(): Promise<boolean> {
	if (platform() !== 'macos') {
		return true
	}

	const status = await invoke<PermissionStatus>('request_system_audio_permission')
	if (status === 'granted' || status === 'not_applicable') {
		return true
	}

	if (status === 'denied' || status === 'restricted') await invoke('open_system_audio_settings')
	toast.error(m.permissionAudioRecording())
	return false
}

/**
 * The same preflight for the microphone, which had none on any path.
 *
 * Every reference to `microphone_permission` outside the mock harness was the definition, the
 * registration, or the two lines in Settings that let someone check it by hand -- so the first
 * time a denied microphone was noticed was a silent or failed recording. System audio has had
 * this since it was added; the microphone is the one almost every recording uses.
 */
export async function ensureMicrophonePermission(): Promise<boolean> {
	if (platform() !== 'macos') {
		return true
	}

	const status = await invoke<PermissionStatus>('request_microphone_permission')
	if (status === 'granted' || status === 'not_applicable') {
		return true
	}

	if (status === 'denied' || status === 'restricted') await invoke('open_microphone_settings')
	toast.error(m.permissionMicrophone())
	return false
}
