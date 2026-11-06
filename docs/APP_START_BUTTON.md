# Nudge app: one Start, one call

For the Flutter app. It covers what happens when Start is tapped, and the connection steps after that.
The session endpoint is `POST /app/v1/trainings/{training_id}/session`, described in APP_HANDOFF.md §3.3.

## What we saw (Thursday, Nov 5, 2026)

- **One person, seven calls:** one employee got 7 separate calls for the same training in 28 seconds.
  - Each one called the session endpoint and joined the LiveKit room.
  - Each one then left within about a second, before the trainer arrived.
  - In every room, Nudge said its welcome to an empty room, waited 60 seconds and closed.
- **6 AM to about 1 PM Central:** 2,867 calls in all.
  - 258 of them ended with nobody in the room.
  - 66 people had 3 or more calls start within a minute of each other.
- **What it looks like to the person:** "it isn't working", so they tap again.
- **What it costs us:** each empty call uses AI, voice and recording minutes. It also holds a trainer seat at
  busy times, which can make other people see "All trainers are busy".

## What we changed on our side (done; deployed with the next API release)

- **A repeat Start reuses the room:** a Start for the same training within 40 seconds of the last one gets
  the same room name back, if the trainer is still waiting there. It comes with a new token.
- **One identity per room:** the token's identity is now fixed for each room. A second connection with that
  token replaces the first one, and the trainer treats it as the same person coming back.
- **A late join restarts the welcome:** if the person joins after Nudge has already spoken its welcome to an
  empty room, Nudge starts again from the top. Before, they heard silence.

This limits the damage, but the app should still stop the repeats. Our change only works while the trainer
is still in the room.

## What we'd like the app to do

### 1. One tap, one request

- **Disable Start on the first tap.** Then show a clear "Connecting to Nudge…" state with a spinner until the
  call is connected or has failed.
- **Ignore further taps** while a start is in progress. That covers taps on Start and on the training card.
- **Don't start a second call while one is open.** If a call screen is already open, or still being
  disconnected, don't call the session endpoint again.

### 2. Connect once, then let LiveKit reconnect

- **Only one session request per call.** The session endpoint gives a `participant_token` that is valid for
  `expires_in` seconds (30 minutes) for that one `room_name`.
- **Retry with the same token.** If connecting fails, or the connection drops, retry with the same `server_url`
  and token. Don't ask for a new session.
- **Let LiveKit reconnect on drops.** The LiveKit SDK reconnects by itself after a short drop. A new session
  request is only needed once the token has expired, or after the person ended the call themselves.
- **Disconnect on purpose when leaving.** When the person leaves the call screen (End, back button, app
  closed), call `room.disconnect()`. That tells the trainer they left on purpose, and the call ends right
  away.
- **Never dispose an open connection to start a new one.** Please check the start code for this. The call
  logs suggest that each tap closed the previous connection about a second after it joined.

### 3. Get the microphone ready before calling the session endpoint

- **Ask for microphone permission first,** and set up the audio session (iOS `AVAudioSession`, Android audio
  focus). Then call the session endpoint.
- **Why:** a permission dialog or audio setup that happens after joining can drop the connection in the first
  second. That matches what we see.
- **If permission is denied,** show a message and don't start a call at all.

### 4. Time-out and error states

- **Not connected after 15 seconds:** show "Couldn't connect to Nudge" with a **Try again** button. Try again
  reuses the same token while it's still valid.
- **Connected, but the trainer hasn't spoken yet:** show "Nudge is joining…". The trainer usually joins
  within 1 to 3 seconds of the room opening.
- **Busy:** if the session endpoint returns `503 trainers_busy`, show its message, as now. Don't retry by
  yourself.

### 5. Logging, so we can see why a connection drops

These help us find out why the connection drops in the first second:

- **On every disconnect,** log or send us the LiveKit `DisconnectReason`, the connection state changes, the
  app version and the platform/OS.
- **Timestamps:** log when Start was tapped, when the session response came back, when the room connected
  and when the first trainer audio played.
- **Sending it:** if it's easy, send these to Crashlytics or analytics with the `room_name`. We can then match
  them to our call logs.

## How to test

1. **Fast taps:** tap Start 5 times quickly. The API should get one session request and the app should show
   one call.
2. **Airplane mode:** start a call, turn airplane mode on for 5 seconds, then off. The same call should carry
   on, and Nudge says "Welcome back".
3. **Denied permission:** start with microphone permission denied. You should see a clear message, and no
   call should start.
4. **Back button:** start a call, then press back. The call should end at once. Starting again should give a
   working call.
