# Flutter app: Nudge trainings on NudgeLab (phase A4)

> **For:** whoever changes the Flutter app (or Claude Code in the Flutter repo).
> **What changes:** the Nudge badge, the training list and voice sessions move from Wanaka + ElevenLabs to
> NudgeLab's API (`https://nudgelabapi.myprimeportal.com`) + LiveKit. Sign-in stays with Wanaka, unchanged.
> **Already live and tested on production (2026-10-04):** Wanaka's `POST /v1/nudge/token` and the three NudgeLab
> endpoints below. Nothing server-side is waiting on the app.

## 1. Overview

```
login (Wanaka, unchanged) ──▶ Wanaka token
        │
        ▼
POST wanakaapi/v1/nudge/token  ──▶ NudgeLab pass (15 min)       ← right after login, and whenever it's near expiry
        │
        ├─▶ GET  nudgelabapi/app/v1/trainings/pending-count      ← the badge on the bottom bar
        ├─▶ GET  nudgelabapi/app/v1/trainings                    ← the Nudge screen
        └─▶ POST nudgelabapi/app/v1/trainings/{training_id}/session
                    │
                    ▼
            LiveKit room (livekit_client): microphone on, Anne joins and talks
```

What the app removes: the calls to Wanaka's `/v1/trainer-assignments/...` and everything ElevenLabs.

## 2. The NudgeLab pass

`POST https://wanakaapi.myprimeportal.com/v1/nudge/token` with the Wanaka token as `Authorization: Bearer ...`,
no body. Returns:

```json
{"token": "<pass>", "token_type": "Bearer", "expires_in": 900}
```

Rules:
- Get one **right after login**: the badge needs it before the user opens Nudge.
- Keep it **in memory only** (not in secure storage or preferences), with its expiry time
  (`now + expires_in`).
- Before every NudgeLab call: if it expires within 60 seconds, get a new one first.
- If NudgeLab answers **401**, get a new pass and retry that call **once**.
- If Wanaka refuses the exchange (401), the Wanaka session itself has ended: do what the app does today when
  Wanaka's token expires.
- Never send a uid to NudgeLab. It takes the employee from the pass.
- Never log the pass or the LiveKit token.

```dart
class NudgePass {
  NudgePass(this._wanaka);
  final WanakaApi _wanaka;            // the app's existing Wanaka client (has the Wanaka token)
  String? _token;
  DateTime _expires = DateTime.fromMillisecondsSinceEpoch(0);

  Future<String> get() async {
    if (_token == null || DateTime.now().isAfter(_expires.subtract(const Duration(seconds: 60)))) {
      final r = await _wanaka.post('/v1/nudge/token');          // throws the app's usual "signed out" on 401
      _token = r['token'] as String;
      _expires = DateTime.now().add(Duration(seconds: r['expires_in'] as int));
    }
    return _token!;
  }

  void clear() => _token = null;      // on logout, and before a 401 retry
}
```

```dart
class NudgeLabApi {
  NudgeLabApi(this._pass, this._http);
  static const base = 'https://nudgelabapi.myprimeportal.com/app/v1';
  final NudgePass _pass;
  final http.Client _http;

  Future<Map<String, dynamic>> _send(String method, String path, [Map<String, dynamic>? body]) async {
    for (var attempt = 0; attempt < 2; attempt++) {
      final req = http.Request(method, Uri.parse('$base$path'))
        ..headers['Authorization'] = 'Bearer ${await _pass.get()}'
        ..headers['Content-Type'] = 'application/json';
      if (body != null) req.body = jsonEncode(body);
      final res = await http.Response.fromStream(await _http.send(req));
      if (res.statusCode == 401 && attempt == 0) { _pass.clear(); continue; }   // new pass, retry once
      final data = jsonDecode(res.body) as Map<String, dynamic>;
      if (res.statusCode >= 400) throw NudgeLabError.fromJson(res.statusCode, data);
      return data;
    }
    throw NudgeLabError(401, 'invalid_pass', 'Please sign in again.');
  }

  Future<Map<String, dynamic>> pendingCount() => _send('GET', '/trainings/pending-count');
  Future<Map<String, dynamic>> trainings() => _send('GET', '/trainings');
  Future<Map<String, dynamic>> startSession(String trainingId, {bool startOver = false}) =>
      _send('POST', '/trainings/$trainingId/session', {'start_over': startOver});
}
```

## 3. Badge and list

### `GET /app/v1/trainings/pending-count`

Same JSON as Wanaka's pending count today:

```json
{"uid": 3784, "name": "Binay Gupta", "incompleted_count": 2}
```

Load it wherever the badge loads today (app start, resume, home), and again after a session ends.

### `GET /app/v1/trainings`

**Same JSON as Wanaka's `/v1/trainer-assignments/{uid}`**, so the existing list screen and models keep working.
Each card has these new keys:

| Key | Values | Use |
|---|---|---|
| `training_id` | e.g. `"big4"` | what to send to start a session (also in `trainer_key`) |
| `completion_type` | `quiz` \| `walkthrough` \| `acknowledgment` | optional label |
| `status` | `not_started` \| `in_progress` \| `completed` | button text: Start / Resume / Completed |
| `progress` | `{"topics_done": 1, "topics_total": 16, "quiz_retry": false}` | progress bar; "Retake quiz" when `quiz_retry` |
| `due_at` | `"2026-10-31 23:59:59.000000"` or `null` | optional due date |

A real card from production:

```json
{
  "tags": null, "trainer_id": 22, "assigned_at": "2026-10-04 01:16:09.570000",
  "trainer_key": "big4", "trainer_name": "Big 4", "trainer_category": "Sales",
  "elevenlabs_agent_id": "agent_2001m3qdkrdje6tac8pdatjpxy51",
  "trainer_description": "This is an AI voice-based Big 4 training ...",
  "trainer_person_name": null, "trainer_picture": null,
  "matched_rule_group_id": null, "matched_rule_name": null, "ai_flag": null,
  "is_required": true, "is_completed": false,
  "training_id": "big4", "completion_type": "quiz", "status": "in_progress",
  "progress": {"topics_done": 1, "topics_total": 16, "quiz_retry": false}, "due_at": null
}
```

Notes:
- The list is already sorted: required and not completed first.
- `elevenlabs_agent_id` is now just the training's completion key. Don't use it to start anything.
- `trainer_person_name` / `trainer_picture` are the employee's district manager, as before (null for home
  office).
- Times are UTC, formatted as before.

## 4. Starting a session

`POST /app/v1/trainings/{training_id}/session`, body `{"start_over": false}` (or no body). Returns:

```json
{"server_url": "wss://...livekit.cloud", "participant_token": "<jwt>", "room_name": "nl-big4-3784-327fe5", "expires_in": 1800}
```

- **Start / Resume** (`status` `not_started` or `in_progress`): `start_over: false`. Anne resumes where they left
  off.
- **Start over** (offer it only when `in_progress`): `start_over: true` wipes their progress for that training.
  Confirm first.
- The token is valid for 30 minutes, to join or to rejoin the same room.

### Joining with LiveKit (`livekit_client`, 2.x)

Platform setup:
- **iOS** `Info.plist`: `NSMicrophoneUsageDescription` ("Anne, your AI trainer, needs the microphone to hear you.")
  and `UIBackgroundModes` → `audio`, so the call survives a screen lock.
- **Android** manifest: `RECORD_AUDIO`, `MODIFY_AUDIO_SETTINGS`, `INTERNET`. Ask for the microphone permission
  before connecting.
- **Keep the screen awake** during the call (e.g. `wakelock_plus`). A locked phone was the main cause of dropped
  calls on the web tester page.

Sketch (check event and class names against the `livekit_client` version you install):

```dart
final room = Room(roomOptions: const RoomOptions(adaptiveStream: true, dynacast: true));
final events = room.createListener();
var endedByUser = false, endedByAgent = false;

bool isAnne(Participant p) => p.kind == ParticipantKind.AGENT || p.identity.startsWith('agent-');

events
  ..on<ParticipantConnectedEvent>((e) { if (isAnne(e.participant)) showState('Anne joined'); })
  ..on<ParticipantDisconnectedEvent>((e) {          // Anne leaves when the training ends or she hangs up
    if (isAnne(e.participant)) { endedByAgent = true; room.disconnect(); }
  })
  ..on<RoomReconnectingEvent>((_) => showState('Reconnecting…'))
  ..on<RoomReconnectedEvent>((_) => showState('Back online'))
  ..on<RoomDisconnectedEvent>((_) => onDisconnected());

await room.connect(session['server_url'], session['participant_token']);
await room.localParticipant?.setMicrophoneEnabled(true);
// Anne's audio plays automatically once subscribed. Show "Anne is joining…"; if she hasn't joined within
// 25 seconds, say the trainer may be offline and offer to end and try again.
```

**Optional:**
- **Status line:** Anne's attribute `lk.agent.state` is `initializing`, `listening`, `thinking` or `speaking`.
- **Live captions:** text streams on topic `lk.transcription`. Each stream carries the whole segment so far, so
  replace the line rather than appending to it. The tester page (nudgelab repo, `web/index.html`) shows both.

### Ending and dropped calls

- **End button:** set `endedByUser = true`, then `room.disconnect()`.
- **Anne leaves** (training finished or she ended it): show "Session ended".
- **Disconnected without either** (signal lost, app backgrounded): rejoin **the same room with the same
  token**. Try every few seconds for up to 50 seconds; Anne waits about 60 seconds for the trainee to come back.
  If that fails, show "Connection lost. Your progress is saved" with a **Resume** button, which calls the
  session endpoint again (new room, same progress).
- **After any session ends:** reload the list and the badge (progress is saved during the call).
- Call `room.dispose()` when the session screen closes.

## 5. Errors

Every error is `{"error": {"code": "...", "message": "...", "details": {}}}`.

| Status | `code` | App does |
|---|---|---|
| 401 | `invalid_pass` | new pass, retry once; then the usual sign-in |
| 403 | `inactive_employee` | "Trainings aren't available for this account." |
| 403 | `not_assigned` | reload the list (it changed); "This training is no longer assigned to you." |
| 404 | `not_found` | reload the list |
| 422 | `validation_error` | a bug in the app: log it, generic message |
| 429 | `rate_limited` | "Please wait a moment and try again." (60 reads or 6 starts a minute per person) |
| 503 | `app_not_configured`, `sessions_unavailable` | "Trainings are temporarily unavailable." |
| network | | the app's usual offline message; the badge can keep its last value |

## 6. Rolling it out

**Only assigned trainings appear.** Until go-live, only the testers have assignments, so everyone else would see
an empty Nudge screen. Release this build to **testers only** (TestFlight / Play internal testing) until the
owner says go-live. At go-live, all ElevenLabs trainings have moved to NudgeLab and everyone is assigned.

Tester checklist:
1. After login, the Nudge badge shows the right number before opening Nudge.
2. The list shows the assigned trainings with the right status and progress.
3. Start a training: microphone permission, "Anne is joining…", then Anne talks and hears you.
4. Lock the screen for 10 seconds mid-call, then unlock: the call continues or rejoins.
5. Turn on airplane mode for 15 seconds mid-call, then off: it rejoins and Anne carries on.
6. End mid-training, then Resume: Anne picks up where you left off; the card shows progress.
7. Start over (from an in-progress card): starts from the beginning after confirmation.
8. Finish a training: the card shows Completed and the badge goes down.
9. Leave the app open for more than 15 minutes, then open Nudge: it still works (pass refresh).
10. Log out and in as another tester: only their trainings appear.

Each session shows up in the NudgeLab dashboard (Sessions, client `flutter`) with its transcript and recording,
so problems can be looked up by time and person.
