import { RotateCcw } from 'lucide-react'
import { m } from '~/paraglide/messages.js'
import { Switch } from '~/components/ui/switch'
import { defaultOptions } from '~/providers/preference'
import { ActionRow, SettingsGroup, SettingsRow, type SettingsViewModel } from './shared'

/**
 * "Custom command" used to live here. It had no reader: `custom_command` appears in Rust as a
 * struct field and a `None` in its `Default`, and nowhere else, so whatever was typed went into
 * `transcription.ffmpegOptions` and was never used. It is not wired up here either, because
 * `ffmpeg::normalize`'s doc comment says why: the value lands in an option position, ffmpeg has no
 * `--` separator, and an injected `-y` or a second output path takes effect. Wiring it needs an
 * explicit allowlist, not a split on whitespace. A control that writes attacker-shaped text into
 * the config file and does nothing is worse than no control, so it is gone until that exists. The
 * field stays in the type and in `defaultOptions` so an existing config still parses.
 */
export function AudioProcessingSection({ vm }: { vm: SettingsViewModel }) {
	return (
		<div className="space-y-6">
			<SettingsGroup>
				<SettingsRow label={m.normalizeLoudness()} description={m.infoNormalizeLoudness()}>
					<Switch
						checked={vm.preference.ffmpegOptions.normalize_loudness}
						onCheckedChange={(checked) => vm.preference.setFfmpegOptions({ ...vm.preference.ffmpegOptions, normalize_loudness: checked })}
					/>
				</SettingsRow>
			</SettingsGroup>
			<SettingsGroup>
				<ActionRow
					label={m.resetAudioProcessing()}
					icon={<RotateCcw className="h-4 w-4" />}
					activateOnClick
					onClick={() => vm.preference.setFfmpegOptions(defaultOptions.ffmpegOptions)}
				/>
			</SettingsGroup>
		</div>
	)
}
