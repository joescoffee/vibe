import { emit } from '@tauri-apps/api/event'
import { invoke } from '@tauri-apps/api/core'
import { type SetStateAction, useContext, useEffect, useState } from 'react'
import type { AudioDevice } from '~/lib/audio'
import { CONFIG_KEYS } from '~/lib/config-keys'
import { usePersisted } from '~/lib/config-store'
import { KEEP_AWAKE, startKeepAwake, stopKeepAwake } from '~/lib/keep-awake'
import { toast } from 'sonner'
import { m } from '~/paraglide/messages.js'
import { ensureMicrophonePermission, ensureSystemAudioPermission } from '~/lib/permissions'
import { isHotkeyRecordingActive, setNormalRecordingActive } from '~/lib/recording-session'
import { ErrorModalContext } from '~/providers/error-modal'
import { usePreferenceProvider } from '~/providers/preference'

export function useRecording(onBeforeStart: () => void) {
	const preference = usePreferenceProvider()
	const { setState: setErrorModal } = useContext(ErrorModalContext)
	const [devices, setDevices] = useState<AudioDevice[]>([])
	const [savedInputDeviceId, setSavedInputDeviceId] = usePersisted<string | null>(CONFIG_KEYS.inputDeviceId, null)
	const [savedOutputDeviceId, setSavedOutputDeviceId] = usePersisted<string | null>(CONFIG_KEYS.outputDeviceId, null)
	const [inputDevice, setInputDevice] = useState<AudioDevice | null>(null)
	const [outputDevice, setOutputDevice] = useState<AudioDevice | null>(null)
	const [isRecording, setIsRecording] = useState(false)
	const [recordingName, setRecordingName] = useState('')

	function setInputDeviceAndSave(value: SetStateAction<AudioDevice | null>) {
		const device = typeof value === 'function' ? value(inputDevice) : value
		setSavedInputDeviceId(device?.id ?? '')
		setInputDevice(device)
	}

	function setOutputDeviceAndSave(value: SetStateAction<AudioDevice | null>) {
		const device = typeof value === 'function' ? value(outputDevice) : value
		setSavedOutputDeviceId(device?.id ?? '')
		setOutputDevice(device)
	}

	async function loadAudioDevices() {
		const newDevices = await invoke<AudioDevice[]>('get_audio_devices')
		const inputs = newDevices.filter((device) => device.isInput)
		const outputs = newDevices.filter((device) => !device.isInput)
		setInputDevice(
			savedInputDeviceId === null
				? (inputs.find((device) => device.isDefault) ?? null)
				: (inputs.find((device) => device.id === savedInputDeviceId) ?? null),
		)
		setOutputDevice(
			savedOutputDeviceId === null
				? (outputs.find((device) => device.isDefault) ?? null)
				: (outputs.find((device) => device.id === savedOutputDeviceId) ?? null),
		)
		setDevices(newDevices)
	}

	useEffect(() => {
		if (preference.homeTab === 'record') loadAudioDevices()
	}, [preference.homeTab])

	async function startRecord() {
		// A dictation already owns the microphone. Starting here opened a second cpal session over
		// it: `recording-shortcut.tsx` checks this before letting the hotkey start, and this path
		// did not, so the two could run at once from the UI side.
		if (isHotkeyRecordingActive()) {
			toast.error(m.dictationInProgress(), { position: 'bottom-center' })
			return
		}
		if (!(await ensureMicrophonePermission())) return
		if (outputDevice && !(await ensureSystemAudioPermission())) return
		startKeepAwake(KEEP_AWAKE.record)
		onBeforeStart()
		setIsRecording(true)
		setNormalRecordingActive(true)
		const selectedDevices = [inputDevice, outputDevice].filter((device): device is AudioDevice => device !== null)
		try {
			await invoke('start_record', {
				devices: selectedDevices,
				recordingName: recordingName.trim() || null,
			})
		} catch (error) {
			stopKeepAwake(KEEP_AWAKE.record)
			setIsRecording(false)
			setNormalRecordingActive(false)
			console.error('startRecord error: ', error)
			setErrorModal?.({ log: String(error), open: true })
		}
	}

	async function stopRecord() {
		try {
			await emit('stop_record')
		} catch (error) {
			setIsRecording(false)
			console.error('stopRecord error: ', error)
			setErrorModal?.({ log: String(error), open: true })
		} finally {
			// The hold taken in startRecord used to be released only on the two error paths, so a
			// recording that succeeded left the display awake and the machine unable to sleep for
			// the rest of the app's lifetime. Releasing a name that is not held is a no-op, which
			// is what makes a `finally` the right shape here.
			stopKeepAwake(KEEP_AWAKE.record)
		}
	}

	return {
		devices,
		setDevices,
		inputDevice,
		outputDevice,
		isRecording,
		setIsRecording,
		recordingName,
		setRecordingName,
		setInputDevice: setInputDeviceAndSave,
		setOutputDevice: setOutputDeviceAndSave,
		startRecord,
		stopRecord,
	}
}
