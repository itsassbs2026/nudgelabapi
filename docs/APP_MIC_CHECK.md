# Flutter app: making sure Anne can hear the trainee

For the Nudge app developers. It adds to `FLUTTER_APP_GUIDE.md` §4 (Joining with LiveKit). Check class and method
names against the `livekit_client` version you use (2.x); the behaviour is what matters.

## Why

In about **1 call in 6, the trainee never says a word** that Anne can hear. Anne talks, asks "Are you still
there?", and the trainee hangs up, often to try another training and hit the same problem (one person tried 4
trainings in 5 minutes on Nov 12). From the agent logs of 395 such calls on Nov 11–12:

| What the agent saw | Calls | Likely cause |
|---|---|---|
| The app never joined the room | 58 | Start tapped, then the app never connected |
| Joined, but **no microphone audio arrived** at all | ~89 | Permission denied, or the microphone track was never published |
| Microphone audio arrived, but **no words** in it | ~248 | Muted, or a silent microphone: another app or a phone call holding it, a Bluetooth headset that's connected but not picking up; or the trainee only listened |

The agent can't tell these apart; the phone can. So most of the fix is in the app.

## 1. Before connecting

Do these before `POST /app/v1/trainings/{id}/session`, so a call that can't work never starts (an empty room
still counts as a session in the reports).

1. **Microphone permission.** Check it (e.g. `permission_handler`, `Permission.microphone`).
   - Not asked yet: ask.
   - Denied: explain why ("Anne needs your microphone to hear your answers") and ask again.
   - Permanently denied, or restricted: show a screen with an **Open Settings** button (`openAppSettings()`), and
     don't start the session.
2. **A phone call in progress.** If the phone is on a call (iOS: `CXCallObserver`; Android: the call state, or
   the audio mode is already in-call), show "Finish your phone call, then start the training." The call holds the
   microphone, and Anne would hear silence.
3. **First time only (optional): a 3-second mic test.** "Say hello" with a level bar that moves. If the bar
   doesn't move, show the tips in §3. Skip it for people who have finished a training before.

## 2. During the call

1. **Publish the microphone, and check it worked.** After `room.connect(...)`, call
   `localParticipant.setMicrophoneEnabled(true)` and catch any error. If it throws, or
   `isMicrophoneEnabled()` is still false, retry once; then show "We couldn't turn on your microphone" with the
   tips in §3 and a **Try again** button. Log it (see §4).
2. **Show that Anne hears them.** A small level ring or bar on the call screen that moves when they talk (the
   local participant's `audioLevel` / `isSpeaking`, or the level of the local audio track). People who can see
   their voice register don't wonder whether the app is listening.
3. **Muted state.** Make Mute obvious (red, "Muted" label). If the trainee is muted and Anne has finished
   speaking (her attribute `lk.agent.state` is `listening`) for more than 5 seconds, show "You're muted. Tap to
   unmute."
4. **Silent microphone.** If the microphone is on and unmuted, Anne is `listening`, and the local level stays
   near zero for **10 seconds** twice in a row, show the banner in §3. This catches a microphone held by another
   app (Android 10+ gives silence to the second app that records), a sleeping Bluetooth headset, or a blocked
   mic. Don't end the call: let them fix it and carry on.
5. **Audio route.** Show where audio is going: Phone speaker, Earphones, Bluetooth (name). Let them switch input
   to the phone's own microphone (e.g. `Hardware.instance.enumerateDevices()` / `selectAudioInput`, or the
   platform's route picker). A Bluetooth headset that's connected but in someone's pocket is a common cause of
   silence.
6. **Interruptions.** An incoming phone call, Siri, or another app taking the microphone stops capture. When the
   interruption ends (iOS `AVAudioSession` interruption ended; Android audio focus regained), turn the microphone
   back on (`setMicrophoneEnabled(true)`) and show "Welcome back. Say 'continue' when you're ready." If it
   can't, show the §3 banner.
7. **Keep what's there:** voice-call audio mode for echo cancellation and the screen-awake lock (guide §4).

## 3. The "Anne can't hear you" banner

Short, with actions, not a paragraph:

> **Anne can't hear you**
> - Check you're not muted
> - Close other apps using the microphone (calls, Teams, voice notes)
> - Using Bluetooth? Switch to the phone's microphone
>
> [ Switch to phone mic ] [ Microphone settings ]

Hide it as soon as the level moves.

## 4. Tell us what the phone sees

So we can tell the three cases apart in the dashboard, set these **participant attributes** right after
connecting, and update them when they change (`localParticipant.setAttributes({...})`). The agent will record
them with the session (we'll add that on our side):

| Attribute | Values |
|---|---|
| `nudge.platform` | `ios 18.1`, `android 14` |
| `nudge.device` | model, e.g. `iPhone15,2`, `SM-S918U` |
| `nudge.app_version` | e.g. `4.12.0+311` |
| `nudge.mic_permission` | `granted`, `denied`, `permanently_denied`, `restricted` |
| `nudge.mic` | `on`, `muted`, `failed` |
| `nudge.audio_route` | `speaker`, `earpiece`, `wired`, `bluetooth` |
| `nudge.mic_silent` | `1` while the silent-mic banner is showing, otherwise `0` |

Existing: `nudge.media` = `1` (the app can show images) stays as it is.

## 5. What we'll do on our side

- Log the attributes above with each session, and show "Mic: silent / muted / no permission" on the session page
  and in the reports, so we can see how often each case happens before and after the app release.
- Optionally, when audio arrives with no words for 20 seconds after Anne asks something, the agent sends
  `{"type": "cant_hear_you"}` on text-stream topic `nudge.status`; the app shows the §3 banner. This is a backup
  for phones where the level check isn't reliable. Tell us if you want it.

## 6. Test checklist

1. Deny the microphone permission: the app explains, offers Settings, and doesn't start a session.
2. Turn the permission off in Settings, come back, start: the same.
3. Start a training during a phone call: "Finish your phone call first."
4. Mid-call, receive a phone call, decline or finish it: the microphone comes back and Anne hears you.
5. Start a voice note or Teams call in another app, then start a training (Android): the silent-mic banner shows
   within about 20 seconds.
6. Bluetooth earbuds connected but in the case: the banner shows; **Switch to phone mic** fixes it.
7. Tap Mute and stay silent after Anne's question: "You're muted" shows within about 5 seconds.
8. Normal call on speakerphone: the level ring moves when you talk; no banner ever shows.
9. Every call above shows its attributes on the NudgeLab session page.
