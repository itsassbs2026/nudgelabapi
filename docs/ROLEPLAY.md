# Role Play: a training type for practice conversations (design, 2026-10-07)

Status: **design agreed with the owner, not built.** First topic: *Win Every Customer* (CSAT), from the
ElevenLabs version (`roleplay_training.pdf`, 35 pages). This document is the plan for the agent
(`nudgelab`), the API and the dashboard.

## 1. The goal

**Role Play** is a training type, alongside Quiz, Walkthrough and Acknowledgment. One engine is built once.
Every roleplay topic is **data**: a standard template filled in on the studio and stored in the database, with
the person's details supplied when the session starts. A new topic needs no code change and no deploy.
Only a new kind of mechanic would need code (open-answer grading, two customers in one scene, the trainee
playing the customer); once added, every roleplay can use it.

## 2. Decisions (owner, 2026-10-07)

| # | Question | Decision |
|---|---|---|
| 1 | Does the coach step in during the practice conversation? | Yes in the **Beginner** practice ("Quick pause —", then carry on). Never in the **Stress** practice: it runs start to finish. |
| 2 | Does the customer sound like a different person? | Yes: **two voices**, the coach's and the customer's. |
| 3 | Multiple-choice quiz only? | Yes. Open-answer sets (Win Every Customer's General set) are rewritten as A–D. |
| 4 | Quiz pass mark | A setting on each training, **default: every question right**. |
| 5 | Assigned again for a different reason after passing | **Fresh start** on the new track. |
| 6 | Reason for an upload | **One per file**, picked on screen. Different reasons go in separate files. |
| 7 | Generic engine | Template in the database, variables at session start (see 1). |

Also agreed in discussion: code (not the prompt) enforces the rules; a separate grader scores each practice
conversation; code switches stages; tone tags are never spoken.

## 3. A session, start to finish

```
Opening ─▶ Coach ─▶ Practice (Beginner) ─▶ Debrief + Win Meter ─┬─ under the unlock score ─▶ retry practice / coach refresher
                                                               └─ unlock score or better ─▶ (optional Stress practice ─▶ Debrief)
                                                                                           ─▶ Quiz ─▶ (retry missed topics) ─▶ Rating ─▶ Goodbye
```

- **Opening.** The engine already knows the person's progress: no "have you done this before?" if it does.
  New: one line on how it works. Returning: "Last time you finished Coach Mode; want to go straight to the
  practice?" Already passed this track: a short practice offer, no quiz.
- **Coach (required, ask first).** For each coach item in scope (the shared items plus the track's module) the
  coach asks its probe question, then confirms, fills only the gap, or teaches in at most 3 sentences. Coach
  Mode counts as done once every probe has been answered, so someone who knows the material is through in about
  2 minutes. Cap: about 4 minutes.
- **Practice (required).** The coach gives the setup line, then plays the customer in the customer voice until
  the scene ends. Beginner only: on a real slip against the track's framework, a "Quick pause —" in the coach's
  voice naming the step, then "Try that again from …" and back in character. The trainee always gets the last
  turn (their close). Scene length is a setting (default 6–12 exchanges).
- **Debrief.** The scene's transcript goes to the **grader** (section 6). Code stores the result; the coach
  says "Alright, let's check your Win Meter…", then Strength, Gap, Next-time tip and the score, in the
  training's wording for each score. Score under the unlock score: retry offered, focused on the Gap, or a coach
  refresher on that step; the quiz isn't mentioned. At or above: "Your quiz is now unlocked. Want to try a
  tougher version first?"
- **Stress practice (optional).** Offered once, after an unlocking score. No Quick pauses. Scored and debriefed
  the same way; never unlocks or locks anything; stored for reports.
- **Quiz (required).** Locked by code until a Beginner score at or above the unlock score exists for this
  track (this session or an earlier one). One question at a time, A–D, graded by code; missed topics are
  re-coached and retried with a new question on the same idea, as quizzes work today.
- **Rating and goodbye**, as today. The pass is copied to Prime Portal under the training's completion key.

Silence, pauses, dropped connections and rejoining work as in every training today.

## 4. The template (what a trainer fills in)

Stored in `training_versions.content` as a new `roleplay` section (content format 2). Edited on the studio's
Role Play editor; uploads can be drafted into it by the AI, as walkthroughs are today.

### 4.1 Training level

| Field | Purpose | Win Every Customer |
|---|---|---|
| Coach name and manner | Who the coach is and how they sound | Jordan; calm, respectful, direct, never condescending |
| Purpose | One paragraph the coach uses to explain why this matters (not as an opening) | "Why this matters": survey feedback about how visits felt |
| Opening line, new / returning | What the coach says first | "We'll start with Coach Mode, then a practice roleplay…" |
| Shared coach items | Taught to everyone, whatever their track. Each item: title, probe question, what a right answer contains, what to teach on a gap | First 7 Seconds; the survey (item 7) |
| Closing line for Coach Mode | Said once at the end of coaching | The Prime closing line ("Every customer. Every interaction. Every day.") |
| Rubric | What each score 1–5 means; the grader uses it | The Win Meter scoring (5: no Quick pause, framework end to end…) |
| Score lines | What the coach says when revealing each score | "Ding, ding, ding! Five out of five…" |
| Debrief shape | Fixed by the engine: Strength, Gap, Next-time tip, score, what a 5 needed | — |
| Tracks | The list of tracks and which is the **default** (used when a person's reason is missing or unknown) | Compliance, Conduct, Knowledge, Billing, Wait, **General** |
| Knowledge base | Reference text the coach can answer questions from | Sections 1–9 |
| Settings | Unlock score (default 4); quiz pass mark (default all); Stress practice on/off; Quick pauses in Beginner on/off; scene length; customer voice | 4; 7 of 7; on; on; 6–12; (a second Polly voice) |
| Closing lines | Passed / not passed yet | "Great work — you've completed Win Every Customer!" / "Good work today — your progress is saved…" |

### 4.2 For each track

| Field | Purpose | Example (Billing) |
|---|---|---|
| Name and the reason values that select it | Matched to the assignment's reason, ignoring case and spaces | "Billing" |
| What to say if asked why they were assigned | One plain sentence, never quoting feedback or scores | "This one's focused on billing conversations, based on recent customer feedback." |
| Coach module | The track's framework: steps, each with what a right answer contains | Billing Discrepancy: understand first, review the account, set expectations, never promise what you can't confirm, resolve or escalate |
| Probe question | The question that opens the module | "A customer says their bill is thirty dollars higher than they were told. What's your first move?" |
| **Beginner persona** | Setup line; who the customer is (situation, mood, what wins them over); opening line; branches: "if the rep does X → the customer does Y", each Quick pause naming a framework step; how the scene closes | "Confused About the Bill": first bill after an upgrade, $30 higher, confused, not hostile |
| **Stress persona** (optional) | Same fields, plus the grievance and the content boundary (loud and sarcastic, never slurs, threats or harassment) | "Second Time I'm Here About This Bill" |
| Quiz questions | Question, options A–D, the correct letter, a one-line reason for the correction, the topic (for retries) | Billing set, 7 questions |

A track can leave out the coach module if the shared items cover it (Conduct and Wait use shared items 5 and
6). A training with no tracks simply has one, the default.

### 4.3 Fixed in the engine (never typed into content)

The two voices and how the coach steps in and out ("Quick pause —", "Alright, picking back up —"); never
speaking the trainee's lines; the debrief shape; the order of stages; the gate; resuming; silence; asking the
rating; tool calls (there are none for the trainer to make: memory, scores and the gate are code).

## 5. Variables, filled in when the session starts

| Variable | Where it comes from |
|---|---|
| First name, job title, store | `vw_trainees` (by uid) |
| Track | The assignment's reason (`training_assignments.ai_flag`); a preview or tester call can send a track instead; unknown or empty → the training's default track |
| Progress on this track | Coach done, practice passed (and best score), quiz answers, missed topics |
| Past Win Meter scores for this track | Practice attempts (section 7) |
| Training, version, client | The session request, as today |

They reach the coach as plain facts ("The trainee's first name is Maria. Their track is Billing. They passed
Coach Mode on Oct 7."), never as `{{placeholders}}` that could be read aloud. Only today's track's content is
given to the coach, so tracks can't be mixed.

## 6. Scoring a practice conversation

When the scene ends (the trainee's close, or they say they're done), code takes that scene's transcript, the
track's framework and persona, the rubric, and the number of Quick pauses, and asks a separate grader model for
a JSON result: score 1–5, Strength, Gap, Next-time tip, what a 5 needed, and the framework steps involved. The
grader runs once per scene (Sonnet; about 2–3 seconds, covered by the "let's check your Win Meter" line). Rules
the grader follows: base the Gap only on what was said or skipped; if there was no real gap, say so and give a
polish point; Quick pauses lower the score as the rubric says. Code stores it, and the coach reads it out. The
trainee can't talk the coach into a score, and scores are comparable between people.

If the trainee disputes the Gap and is right, the coach acknowledges it in one sentence; the stored score
stands unless an Admin changes it (later, if wanted).

## 7. Data

- `trainings.completion_type` gains `roleplay` (the agent's enum: a small ALTER, run by the owner).
- `training_progress` gains `track`. When a person starts the training with a reason different from the
  progress row's track (decision 5), the old row is copied to a new `training_progress_history` table and a
  fresh row starts. This is the "retake" mechanism, limited to a new reason for now.
- New `roleplay_attempts`: session, uid, training, version, track, tier (beginner/stress), score, Quick pause
  count, Strength, Gap, tip, framework steps, grader model, when the scene started and ended.
- Quiz answers and passes are recorded as today; a roleplay training passes when the quiz passes (the gate
  guarantees the practice was passed first).
- Assigning: the dashboard's Assign dialog and upload get a **Reason** picker when the training is a roleplay
  (the training's track names; one per file, decision 6). The owner's query sets `ai_flag` the same way.

## 8. Engine changes (agent)

- Stage controller for roleplay sessions (coach → practice → debrief → stress → quiz → rating), with
  stage-specific instructions, like walkthrough → quiz today.
- **Two voices**: the coach marks customer lines; code sends them to the customer voice and strips any tone
  tags (`[annoyed]` is never spoken). Customer emotion comes from wording.
- Quick pause counting (Beginner only) and scene boundaries in the session log.
- The grader call (section 6) and `roleplay_attempts`.
- Quiz grading with **four options** (today's code assumes A–C in a few places).
- The gate, track lock, fresh start on a new reason, resuming at the right stage.

## 9. Dashboard and API

- Studio: *Role Play* as a type; the template editor (training level, tracks, personas, quiz sets); publish
  checks (every track has a Beginner persona and enough quiz questions; a default track; the unlock score and
  pass mark are set); preview calls with a **track picker**.
- Assignments: the Reason picker (dialog and upload).
- Reports: a Role Play page (attempts, average Win Meter by track, attempts needed to unlock the quiz, the
  most common Gap steps); the session viewer marks where each scene starts and ends and shows its debrief.
- Help & FAQ: what Role Play is, how to build one, how reasons and tracks work.

## 10. Converting Win Every Customer

- Drop everything ElevenLabs-specific: the tool calls (`get_previous_training_state`, `get_csat_history`,
  `CSAT_count`, `record_answer`, `get_score`, `save_session_summary`) and the tone tags. The engine covers them.
- Rewrite the General quiz set (open answers) as A–D (drafted by us, checked by Kseniia).
- **Content check for Kseniia:** the knowledge base's Script A ("If you currently do not feel you could give me
  a 5…") contradicts the training's own rule (never ask for a 5, never coach the score); the coach could quote
  it as a good example. Drop it or label it as what not to do. Scripts A and B also name two real trainers,
  who would then be named on recorded calls.

## 11. Plan

| Step | Days |
|---|---|
| Agent engine (stages, two voices, grader, gate, track lock, fresh start, A–D quiz) | 4–5 |
| Content format, studio editor, publish checks, preview with a track | 4–5 |
| Assigning with a reason, API for attempts | 1 |
| Reports and session viewer | 2 |
| Win Every Customer converted and bot-tested on every track; Help & FAQ | 2–3 |
| **Total** | **about 3 weeks** |

Can ship in two steps: (1) engine + Win Every Customer published from a file; (2) the studio editor.

Model: start on Haiku for the conversation and Sonnet for the grader; measure with bot calls on every track
before rollout. Roleplay calls run longer than walkthroughs, so cost per call goes up; the setups system can
move a training to a stronger model if Haiku doesn't stay in character.

## 12. Still open

- A second pass after a fresh start: does Prime Portal keep the first pass date or show the latest (the same
  question as retakes)?
- Which Polly voice is the customer by default (a setting per training; pick after hearing samples).
