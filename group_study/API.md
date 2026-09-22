# Group Study — API Reference

Base URL: `/api/group-study/`

Group Study lets registered users create study groups, invite members by email,
build quizzes (from the shared question bank or hand-written questions), schedule
them, publish them to members, and chat within the group.

---

## Authentication & permissions

- **Authentication:** DRF `TokenAuthentication` (and the project's
  `FlexibleAuthentication`). Send the token as:

  ```
  Authorization: Token <your-token>
  ```

- **Access:** every Group Study endpoint uses `IsAuthenticatedUserOnly` — only
  registered users are allowed. Guest sessions are rejected with `401`/`403`.

- **Roles:**
  - `ADMIN` — the group creator (and any member promoted to admin). Can manage
    members and manage every quiz in the group.
  - `MEMBER` — can view the group, create quizzes, manage quizzes they created,
    read messages, chat, take quizzes, and view leaderboards.

  Any member can create a quiz. A quiz can be edited, published, or deleted by
  its creator or by a group admin.

- **Content type:** all request/response bodies are JSON.

---

## Common conventions

### Pagination

Endpoints that return lists use `StandardResultsSetPagination`
(PageNumberPagination, default `page_size = 50`, max `100`). Paginated responses
look like:

```json
{
  "count": 120,
  "next": "http://…/api/group-study/groups/?page=2",
  "previous": null,
  "results": [ … ]
}
```

Query params: `page` (page number), `page_size` (1–100).

### Error format

Validation errors return `400` with a field-keyed object, e.g.:

```json
{ "email": ["No user found with this email."] }
```

Access errors return `403` with a `detail` message. Missing objects return `404`.

### User summary object

```json
{ "id": 4, "username": "alice", "first_name": "Alice", "last_name": "Rahman", "email": "alice@gmail.com" }
```

### Quiz content sources

A quiz can be populated from one of three mutually-exclusive sources:

| Key | Type | Meaning |
|-----|------|---------|
| `question_ids` | `int[]` | Snapshot existing questions from the `api.Question` pool by id. |
| `source_quiz_id` | `int` | Copy every question from an existing `api.Quiz` by id. |
| `questions` | `object[]` | Hand-written questions (see question payload below). |

A question payload is:

```json
{
  "question_text": "What is the capital of France?",
  "explanation": "Paris has been the capital since the 10th century.",
  "answers": [
    { "text": "Paris", "is_correct": true },
    { "text": "London", "is_correct": false }
  ]
}
```

Rules: each question needs at least 2 answers and exactly 1 correct answer.
Optional `position` may be supplied on questions/answers; otherwise positions are
assigned sequentially.

---

## Groups

### List my groups

`GET /groups/`

Returns the groups the caller belongs to (paginated). Each item:

```json
{
  "id": 1,
  "name": "Physics Study Circle",
  "description": "B.Sc. physics prep",
  "is_active": true,
  "created_by": { "id": 4, "username": "alice", "first_name": "Alice", "last_name": "Rahman", "email": "alice@gmail.com" },
  "member_count": 12,
  "quiz_count": 5,
  "unread_message_count": 3,
  "my_role": "ADMIN",
  "my_notify_messages": true,
  "created_at": "2026-09-21T08:00:00Z",
  "updated_at": "2026-09-21T08:00:00Z"
}
```

`my_role` is `"ADMIN"` or `"MEMBER"`.

`unread_message_count` counts chat messages from other members that arrived
after the caller last read the group chat (their own messages never count).

`my_notify_messages` is the caller's own chat notification preference for this
group. It defaults to `true` and can be changed with the toggle endpoint below.

### Create a group

`POST /groups/`

Request:

```json
{
  "name": "Physics Study Circle",
  "description": "B.Sc. physics prep",
  "is_active": true
}
```

| Field | Type | Required | Default |
|-------|------|----------|---------|
| `name` | string | yes | — |
| `description` | string | no | `""` |
| `is_active` | boolean | no | `true` |

Response: `201` with the full group object (including `members`, where the
creator is the first `ADMIN`).

### Get a group

`GET /groups/<group_id>/`

Same as the list item, plus a `members` array:

```json
{
  "id": 1,
  "…": "…",
  "members": [
    { "id": 10, "user": { "…": "…" }, "role": "ADMIN", "joined_at": "2026-09-21T08:00:00Z" }
  ]
}
```

Access: any member. `404` if not a member.

### Update a group

`PUT /groups/<group_id>/` or `PATCH /groups/<group_id>/`

Accepts the same fields as create (PATCH allows partial updates). `ADMIN` only.

Response: the updated group detail object.

### Delete a group

`DELETE /groups/<group_id>/`

`ADMIN` only. Deletes the group and all its memberships, quizzes, questions,
attempts, and messages (cascade). Response: `204 No Content`.

---

## Members

### List members

`GET /groups/<group_id>/members/`

Access: any member. Returns an array of membership objects:

```json
[
  { "id": 10, "user": { "…": "…" }, "role": "ADMIN", "joined_at": "2026-09-21T08:00:00Z" }
]
```

### Add members

`POST /groups/<group_id>/members/`

`ADMIN` only. Adds existing registered users by email (case-insensitive).

Request:

```json
{ "emails": ["alice@gmail.com", "bob@gmail.com"] }
```

Response: `200`

```json
{
  "added_count": 1,
  "already_member_count": 1,
  "memberships": [
    { "id": 11, "user": { "…": "…" }, "role": "MEMBER", "joined_at": "2026-09-21T09:00:00Z" }
  ]
}
```

If any email has no registered account, the whole request fails with `400`:

```json
{
  "error": "No registered user found for these emails.",
  "not_found_emails": ["nobody@gmail.com"]
}
```

Each newly added member receives an in-app + push notification
(`group_study_added`).

### Remove a member

`DELETE /groups/<group_id>/members/<member_id>/`

`ADMIN` only. The `member_id` is the **membership id** (from the members list),
not the user id. You cannot remove yourself. Response: `204 No Content`.

---

## Messaging

### List messages

`GET /groups/<group_id>/messages/`

Access: any member. Paginated, ordered oldest → newest.

```json
{
  "count": 40,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 100,
      "group": 1,
      "sender": { "id": 4, "username": "alice", "first_name": "Alice", "last_name": "Rahman", "email": "alice@gmail.com" },
      "body": "Hello everyone!",
      "created_at": "2026-09-21T09:30:00Z"
    }
  ]
}
```

### Send a message

`POST /groups/<group_id>/messages/`

Access: any member.

Request:

```json
{ "body": "Hello everyone!" }
```

| Field | Type | Required |
|-------|------|----------|
| `body` | string | yes |

Response: `201` with the created message object (same shape as the list item).

Every other member receives a push + in-app notification (`group_study_message`)
with the sender name as the title and the message body (truncated to 200 chars).
Members who turned chat notifications off for this group are skipped.

Sending also marks the chat as read for the sender, so their own message never
appears in `unread_message_count`.

### Mark messages read

`POST /groups/<group_id>/messages/read/`

Access: any member. No body. Clears the caller's unread chat badge.

Response: `200`

```json
{ "detail": "Messages marked as read.", "unread_message_count": 0 }
```

The app calls this when the chat tab opens and whenever new messages arrive
while the chat is on screen.

### Toggle chat notifications

`POST /groups/<group_id>/messages/notifications/`

Access: any member. Turns push/in-app notifications for this group's chat on
or off for the caller only. The setting is stored per membership.

Request:

```json
{ "notify_messages": false }
```

| Field | Type | Required |
|-------|------|----------|
| `notify_messages` | boolean | yes |

Response: `200`

```json
{ "detail": "Chat notifications disabled.", "notify_messages": false }
```

`my_notify_messages` in the group object reflects the updated value.

---

## Quizzes

### List quizzes

`GET /groups/<group_id>/quizzes/`

Access: any member. `ADMIN` sees drafts and published quizzes; `MEMBER` sees
published quizzes plus their own drafts. Paginated summary objects:

```json
{
  "id": 5,
  "name": "Weekly Physics Test",
  "description": "",
  "start_at": "2026-09-22T09:00:00Z",
  "end_at": "2026-09-22T10:00:00Z",
  "duration_minutes": 15,
  "correct_mark": 1,
  "wrong_mark": 0,
  "unanswered_mark": 0,
  "is_published": true,
  "question_count": 20,
  "maximum_score": 20,
  "attempt_status": "NOT_STARTED",
  "created_by": { "id": 4, "username": "alice", "first_name": "Alice", "last_name": "Rahman", "email": "alice@gmail.com" },
  "created_at": "2026-09-21T08:00:00Z"
}
```

`attempt_status` is one of `NOT_STARTED`, `IN_PROGRESS`, `COMPLETED` (for the
requesting user). Numeric mark fields are returned as integers when whole
(e.g. `1`), otherwise floats (e.g. `0.25`).

### Create a quiz

`POST /groups/<group_id>/quizzes/`

Access: any member. The creator becomes the quiz owner.

Request:

```json
{
  "name": "Weekly Physics Test",
  "description": "Chapters 1–3",
  "start_at": "2026-09-22T09:00:00Z",
  "end_at": "2026-09-22T10:00:00Z",
  "duration_minutes": 15,
  "correct_mark": 1.0,
  "wrong_mark": -0.25,
  "unanswered_mark": 0,
  "is_published": false,
  "questions": [
    {
      "question_text": "What is the SI unit of force?",
      "explanation": "Force is measured in newtons.",
      "answers": [
        { "text": "Newton", "is_correct": true },
        { "text": "Joule", "is_correct": false }
      ]
    }
  ]
}
```

| Field | Type | Required | Default |
|-------|------|----------|---------|
| `name` | string | yes | — |
| `description` | string | no | `""` |
| `start_at` | datetime (ISO 8601) | yes | — |
| `end_at` | datetime (ISO 8601) | yes | — |
| `duration_minutes` | positive int | no | `10` |
| `correct_mark` | decimal ≥ 0 | no | `1.00` |
| `wrong_mark` | decimal ≤ 0 | no | `0` |
| `unanswered_mark` | decimal ≤ 0 | no | `0` |
| `is_published` | boolean | no | `false` |
| `question_ids` | int[] | no* | — |
| `source_quiz_id` | int | no* | — |
| `questions` | object[] | no* | — |

\* Provide **at most one** of `question_ids`, `source_quiz_id`, or `questions`.
`end_at` must be after `start_at`.

Response: `201` with the quiz detail object (including `questions` and correct
answers).

### Get a quiz

`GET /quizzes/<quiz_id>/`

Access: any member. The quiz creator and group admins receive the full detail
(answers include `is_correct` and questions include `explanation`); everyone
else receives the **candidate** version (correct answers and explanations are
hidden).

Full (admin) question shape:

```json
{
  "id": 5,
  "…": "…",
  "questions": [
    {
      "id": 30,
      "position": 1,
      "question_text": "What is the SI unit of force?",
      "explanation": "Force is measured in newtons.",
      "answers": [
        { "id": 120, "position": 1, "text": "Newton", "is_correct": true },
        { "id": 121, "position": 2, "text": "Joule", "is_correct": false }
      ]
    }
  ]
}
```

Candidate question shape omits `explanation` and `is_correct`.

### Update a quiz

`PUT /quizzes/<quiz_id>/` or `PATCH /quizzes/<quiz_id>/`

Access: the quiz creator or a group admin. Updates metadata (`name`,
`description`, `start_at`, `end_at`, `duration_minutes`, marks, `is_published`).
Question content is **not** changed here — use `add-questions` to append
content.

Response: the updated quiz detail object.

### Delete a quiz

`DELETE /quizzes/<quiz_id>/`

Access: the quiz creator or a group admin. Response: `204 No Content`.

### Add questions to a quiz

`POST /quizzes/<quiz_id>/add-questions/`

Access: the quiz creator or a group admin. Appends questions to an existing
quiz. Provide **exactly one** of `question_ids`, `source_quiz_id`, or
`questions`:

```json
{
  "questions": [
    {
      "question_text": "Which planet is closest to the sun?",
      "explanation": "Mercury is the closest.",
      "answers": [
        { "text": "Mercury", "is_correct": true },
        { "text": "Venus", "is_correct": false },
        { "text": "Earth", "is_correct": false }
      ]
    }
  ]
}
```

Response: `200` with the updated quiz detail object.

### Publish a quiz

`POST /quizzes/<quiz_id>/publish/`

Access: the quiz creator or a group admin. No body. The quiz must already have
at least one question.

Response: `200`

```json
{ "detail": "Quiz published.", "is_published": true }
```

Publishing notifies all other group members via push + in-app
(`group_study_publish`).

---

## Attempts

### Start an attempt

`POST /quizzes/<quiz_id>/attempts/start/`

Access: any member. The quiz must be **published** and the current time must be
within `[start_at, end_at]`. Starting creates (or resumes) one active attempt for
the calling user. The countdown is fixed: `expires_at = start + duration_minutes`.

Response: `200`

```json
{
  "id": 88,
  "quiz": { "…": "candidate quiz detail (no correct answers) …" },
  "score": 0,
  "is_completed": false,
  "start_time": "2026-09-22T09:10:00Z",
  "expires_at": "2026-09-22T09:25:00Z",
  "end_time": null,
  "total_questions": 20
}
```

Errors: `403` if not published / not started / ended; `400` if the quiz has no
questions.

### Submit an attempt

`POST /attempts/<attempt_id>/submit/`

Access: the attempt owner. Submits every answer in one request. You must provide
exactly one row per quiz question (no duplicates, no missing, no extra).
`selected_answer_id` is `null` for unanswered questions.

Request:

```json
{
  "submissions": [
    { "question_id": 30, "selected_answer_id": 120 },
    { "question_id": 31, "selected_answer_id": null }
  ]
}
```

Response: `200` with the full result (see below).

Errors:
- `400` — expired attempt, or malformed/duplicate/missing submissions.
- `409` — attempt was already submitted with different answers.
- `403` — quiz window no longer valid.

### Get a result

`GET /attempts/<attempt_id>/result/`

Access: the attempt owner, and only for **completed** attempts.

Response: `200`

```json
{
  "id": 88,
  "quiz": { "…": "full quiz detail (with correct answers) …" },
  "score": 18.5,
  "correct_answers": 19,
  "wrong_answers": 1,
  "unanswered": 0,
  "accuracy": 95.0,
  "attempted_string": "20/20",
  "maximum_score": 20,
  "is_completed": true,
  "start_time": "2026-09-22T09:10:00Z",
  "expires_at": "2026-09-22T09:25:00Z",
  "end_time": "2026-09-22T09:15:00Z",
  "total_questions": 20,
  "correct_mark": 1,
  "wrong_mark": -0.25,
  "unanswered_mark": 0,
  "submissions": [
    {
      "question": {
        "id": 30,
        "position": 1,
        "question_text": "What is the SI unit of force?",
        "explanation": "Force is measured in newtons.",
        "answers": [
          { "id": 120, "position": 1, "text": "Newton", "is_correct": true },
          { "id": 121, "position": 2, "text": "Joule", "is_correct": false }
        ]
      },
      "selected_answer_id": 120,
      "is_correct": true
    }
  ]
}
```

Score is computed as:
`correct_answers * correct_mark + wrong_answers * wrong_mark + unanswered * unanswered_mark`.

---

## Leaderboard

### Group leaderboard

`GET /groups/<group_id>/leaderboard/`

Access: any member. Returns every member with at least one completed attempt,
ranked by the sum of their **best score in each group quiz** (retakes only
improve a score, never add to it). Ties are broken by the number of quizzes
attempted, then username. Not paginated.

```json
[
  {
    "rank": 1,
    "user": { "id": 4, "username": "alice", "first_name": "Alice", "last_name": "Rahman", "email": "alice@gmail.com" },
    "score": 16,
    "quizzes_attempted": 2,
    "completed_at": null,
    "is_current_user": false
  },
  {
    "rank": 2,
    "user": { "id": 7, "username": "bob", "first_name": "Bob", "last_name": "Hasan", "email": "bob@gmail.com" },
    "score": 14,
    "quizzes_attempted": 2,
    "completed_at": null,
    "is_current_user": true
  }
]
```

### Quiz leaderboard

`GET /quizzes/<quiz_id>/leaderboard/`

Access: any member. Best completed attempt per member for a single quiz, ranked
by score (ties broken by who finished first). Same entry shape as above, with
`completed_at` set and `quizzes_attempted` always `1`.

---

## Notification kinds

Group Study emits these in-app/push notification kinds (visible as
`notification.kind` on the `Notification` model):

| Kind value | Trigger |
|------------|---------|
| `group_study_added` | A member is added to a group. |
| `group_study_publish` | A quiz is published to a group. |
| `group_study_message` | A new message is sent in a group. |
| `group_study_reminder` | A scheduled quiz is starting/ending soon (cron). |

All push notifications carry `data.group_id` (and `data.quiz_id` / `data.message_id`
where applicable), plus an in-app `Notification` row with `route = "/group-study"`
and a matching `payload` for deep-linking.

---

## Scheduled reminders

Start/end reminders are sent by the cron management command:

```
manage.py run_group_study_schedules
```

It notifies members ~15 minutes before a quiz starts (those who haven't started)
and before it ends (those with an active attempt). Sends are idempotent via the
notification delivery ledger, so the command is safe to run every few minutes.
