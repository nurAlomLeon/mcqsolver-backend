# AdmissionLife API Documentation

**Base URL:** `https://qb.mcqsolver.com/api/admissionlife/`

---

## Authentication

This API supports **two authentication methods**. Both can access most endpoints.

### Method 1: Google Login (Registered Users)

**Step 1:** Sign in with Google on the Flutter app to get a Google `access_token`.

**Step 2:** Exchange it for a DRF auth token:

```
POST https://qb.mcqsolver.com/dj-rest-auth/google/
Content-Type: application/json

{"access_token": "<google_oauth2_access_token>"}
```

**Response:**
```json
{"key": "9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b"}
```

**Step 3:** Use the token on all requests:

```
Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b
```

---

### Method 2: Guest Login (Anonymous Users)

**Step 1:** Register/retrieve guest session:

```
POST https://qb.mcqsolver.com/api/auth/guest/
Content-Type: application/json

{"device_id": "your-unique-device-id"}
```

**Response:**
```json
{
  "guest_token": "550e8400-e29b-41d4-a716-446655440000",
  "device_id": "your-unique-device-id",
  "is_new_guest": true,
  "guest_data": {
    "guest_id": "550e8400-e29b-41d4-a716-446655440000",
    "device_id": "your-unique-device-id",
    "total_questions_answered": 0,
    "total_quizzes_completed": 0,
    "created_at": "2025-01-15T10:00:00Z",
    "last_active": "2025-01-15T10:00:00Z"
  }
}
```

**Step 2:** Use these headers on all requests:

```
X-Guest-Token: 550e8400-e29b-41d4-a716-446655440000
X-Device-ID: your-unique-device-id
```

---

### Auth Headers Quick Reference

| User Type | Headers |
|-----------|---------|
| Google User | `Authorization: Token <key>` |
| Guest User | `X-Guest-Token: <uuid>` + `X-Device-ID: <device_id>` |

---

### Access Levels

| Level | Who | Description |
|-------|-----|-------------|
| **Guest + Registered** | Both guest and Google users | Read-only browsing endpoints |
| **Registered Only** | Google users only | Payments, enrollments, exams, leaderboards |
| **Admin Only** | `is_staff=True` users | CRUD management endpoints |

---

## Pagination

All list endpoints use page-based pagination:

```json
{
  "count": 100,
  "next": "https://qb.mcqsolver.com/api/admissionlife/batches/?page=2",
  "previous": null,
  "results": [...]
}
```

| Param | Default | Max | Description |
|-------|---------|-----|-------------|
| page | 1 | — | Page number |
| page_size | 50 | 100 | Items per page |

---

## 1. Batches

### List Batches

```
GET /api/admissionlife/batches/
```

**Access:** Guest + Registered (non-admin see only active batches)

**Guest call:**
```
GET /api/admissionlife/batches/
X-Guest-Token: 550e8400-e29b-41d4-a716-446655440000
X-Device-ID: your-device-id
```

**Registered user call:**
```
GET /api/admissionlife/batches/
Authorization: Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b
```

**Response:**
```json
{
  "count": 5,
  "results": [
    {
      "id": 1,
      "name": "BCS Preparation 2025",
      "description": "Complete BCS preparation course",
      "batch_type": "PRE_RECORDED",
      "price": "1500.00",
      "is_active": true
    }
  ]
}
```

### Retrieve Batch

```
GET /api/admissionlife/batches/{id}/
```

**Access:** Guest + Registered

**Response:**
```json
{
  "id": 1,
  "name": "BCS Preparation 2025",
  "description": "Complete BCS preparation course",
  "batch_type": "PRE_RECORDED",
  "price": "1500.00",
  "is_active": true,
  "created_at": "2025-01-15T10:00:00Z",
  "updated_at": "2025-01-15T10:00:00Z",
  "exam_count": 10,
  "enrollment_count": 150
}
```

### Create/Update/Delete Batch (Admin)

```
POST /api/admissionlife/batches/
PUT /api/admissionlife/batches/{id}/
DELETE /api/admissionlife/batches/{id}/
```

**Access:** Admin only (Google user with `is_staff=True`)

```
Authorization: Token <admin_token>
```

**Request Body (Create/Update):**
```json
{
  "name": "BCS Preparation 2025",
  "description": "Complete BCS preparation course",
  "batch_type": "PRE_RECORDED",
  "price": "1500.00",
  "is_active": true
}
```

---

## 2. Payments

### Submit Payment

```
POST /api/admissionlife/payments/
```

**Access:** Registered users only (NOT guests)

```
Authorization: Token <user_token>
```

**Request Body:**
```json
{
  "batch": 1,
  "payment_method": "bKash",
  "transaction_id": "TXN123456789",
  "sender_number": "01712345678",
  "amount": "1500.00"
}
```

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| batch | integer | Yes | Valid batch ID |
| payment_method | string | Yes | `bKash`, `Nagad`, `Rocket`, `Upay` |
| transaction_id | string | Yes | Max 30 chars, unique per method |
| sender_number | string | Yes | 11 digits, starts with "01" |
| amount | decimal | Yes | 1.00 – 99999.00 |

**Response (201):**
```json
{
  "id": 1,
  "batch": 1,
  "batch_name": "BCS Preparation 2025",
  "payment_method": "bKash",
  "transaction_id": "TXN123456789",
  "sender_number": "01712345678",
  "amount": "1500.00",
  "status": "PENDING",
  "admin_notes": "",
  "created_at": "2025-01-15T10:00:00Z",
  "reviewed_at": null
}
```

### List My Payments

```
GET /api/admissionlife/payments/
```

**Access:** Registered users only

```
Authorization: Token <user_token>
```

### Approve Payment (Admin)

```
POST /api/admissionlife/payments/{id}/approve/
```

**Access:** Admin only

```
Authorization: Token <admin_token>
```

### Reject Payment (Admin)

```
POST /api/admissionlife/payments/{id}/reject/
```

**Access:** Admin only

```
Authorization: Token <admin_token>
Content-Type: application/json

{"admin_notes": "Invalid transaction ID"}
```

---

## 3. Enrollments

### Check Enrollment

```
GET /api/admissionlife/enrollments/check/{batch_id}/
```

**Access:** Registered users only

```
Authorization: Token <user_token>
```

**Response:**
```json
{"is_enrolled": true}
```

---

## 4. Exams

### List Exams

```
GET /api/admissionlife/exams/?batch={batch_id}
```

**Access:** Registered users only (enrolled users see `is_unlocked` flag)

```
Authorization: Token <user_token>
```

**Response:**
```json
{
  "results": [
    {
      "id": 1,
      "batch": 1,
      "title": "Chapter 1 Test",
      "duration_minutes": 30,
      "order": 1,
      "passing_score": 60,
      "is_active": true,
      "unlock_datetime": null,
      "question_count": 25,
      "is_unlocked": true
    }
  ]
}
```

### Create/Update/Delete Exam (Admin)

```
POST /api/admissionlife/exams/
PUT /api/admissionlife/exams/{id}/
DELETE /api/admissionlife/exams/{id}/
```

**Access:** Admin only

### Import Exam Questions from CSV (Admin)

```
POST /api/admissionlife/exams/{id}/import-csv/
```

**Access:** Admin only

```
Authorization: Token <admin_token>
Content-Type: multipart/form-data

file: <csv_file>
```

### Set Unlock Datetime (Admin)

```
POST /api/admissionlife/exams/{id}/set-unlock-datetime/
```

**Access:** Admin only

```json
{"unlock_datetime": "2025-03-01T10:00:00Z"}
```

---

## 5. Exam Attempts

### Start Exam Attempt

```
POST /api/admissionlife/exam-attempts/{exam_id}/start/
```

**Access:** Registered users only (must be enrolled + exam unlocked)

```
Authorization: Token <user_token>
```

**Response (201):**
```json
{
  "attempt_id": 1,
  "questions": [
    {
      "id": 1,
      "question_text": "What is the capital of Bangladesh?",
      "answer_1": "Dhaka",
      "answer_2": "Chittagong",
      "answer_3": "Sylhet",
      "answer_4": "Rajshahi"
    }
  ]
}
```

### Submit Exam Attempt

```
POST /api/admissionlife/exam-attempts/{attempt_id}/submit/
```

**Access:** Registered users only (owner of the attempt)

```
Authorization: Token <user_token>
Content-Type: application/json

{
  "submissions": [
    {"question_id": 1, "selected_answer": 1},
    {"question_id": 2, "selected_answer": 3},
    {"question_id": 3, "selected_answer": null}
  ]
}
```

**Scoring:** +1 correct, -0.25 incorrect, 0 unanswered

**Response (200):**
```json
{
  "id": 1,
  "exam": 1,
  "score": "18.50",
  "total_questions": 25,
  "correct_count": 20,
  "incorrect_count": 2,
  "unanswered_count": 3,
  "start_time": "2025-01-15T10:00:00Z",
  "end_time": "2025-01-15T10:28:00Z",
  "is_completed": true
}
```

### Get Exam Attempt Result

```
GET /api/admissionlife/exam-attempts/{attempt_id}/result/
```

**Access:** Registered users only (owner of the attempt)

```
Authorization: Token <user_token>
```

---

## 6. Leaderboards

### Batch Leaderboard

```
GET /api/admissionlife/batches/{batch_id}/leaderboard/?page=1&page_size=20
```

**Access:** Registered users only

```
Authorization: Token <user_token>
```

**Response:**
```json
{
  "entries": [
    {
      "rank": 1,
      "user_display_name": "John Doe",
      "total_score": 95.5,
      "total_exams_completed": 10,
      "average_score": 9.55
    }
  ],
  "total_count": 150,
  "current_user_entry": {
    "rank": 23,
    "user_display_name": "Current User",
    "total_score": 72.0,
    "total_exams_completed": 8,
    "average_score": 9.0
  }
}
```

### Exam Leaderboard

```
GET /api/admissionlife/exams/{exam_id}/leaderboard/?page=1&page_size=20
```

**Access:** Registered users only

```
Authorization: Token <user_token>
```

---

## 7. Categories (User)

### List Categories

```
GET /api/admissionlife/categories/
GET /api/admissionlife/categories/?level=0
GET /api/admissionlife/categories/?parent=5
```

**Access:** Guest + Registered

**Guest call:**
```
GET /api/admissionlife/categories/?level=0
X-Guest-Token: 550e8400-e29b-41d4-a716-446655440000
X-Device-ID: your-device-id
```

**Registered user call:**
```
GET /api/admissionlife/categories/?level=0
Authorization: Token <user_token>
```

**Response:**
```json
{
  "results": [
    {"id": 1, "name": "Bangla", "parent": null, "level": 0, "order": 1},
    {"id": 2, "name": "English", "parent": null, "level": 0, "order": 2}
  ]
}
```

### Get Category Tree

```
GET /api/admissionlife/categories/tree/
```

**Access:** Guest + Registered

**Response:**
```json
[
  {
    "id": 1,
    "name": "Bangla",
    "level": 0,
    "order": 1,
    "children": [
      {
        "id": 3,
        "name": "Grammar",
        "level": 1,
        "order": 1,
        "children": [
          {"id": 5, "name": "Verb", "level": 2, "order": 1, "children": []}
        ]
      }
    ]
  }
]
```

### Get Category Children

```
GET /api/admissionlife/categories/{id}/children/
```

**Access:** Guest + Registered

---

## 8. Questions (User)

### List Questions

```
GET /api/admissionlife/questions/
GET /api/admissionlife/questions/?category=5
GET /api/admissionlife/questions/?category=1&category_level=all
```

**Access:** Guest + Registered

**Guest call:**
```
GET /api/admissionlife/questions/?category=5
X-Guest-Token: 550e8400-e29b-41d4-a716-446655440000
X-Device-ID: your-device-id
```

**Registered user call:**
```
GET /api/admissionlife/questions/?category=5
Authorization: Token <user_token>
```

**Response:**
```json
{
  "results": [
    {
      "id": 1,
      "question_text": "What is the capital of Bangladesh?",
      "question_image": null,
      "explanation": "Dhaka is the capital city.",
      "explanation_image": null,
      "category": 5,
      "category_name": "General Knowledge",
      "answers": [
        {"id": 1, "text": "Dhaka", "image": null, "is_correct": true},
        {"id": 2, "text": "Chittagong", "image": null, "is_correct": false}
      ],
      "labels": [
        {"id": 1, "name": "Previous Year 2023"}
      ]
    }
  ]
}
```

### Retrieve Question

```
GET /api/admissionlife/questions/{id}/
```

**Access:** Guest + Registered

---

## 9. Saved Questions

### List Saved Questions

```
GET /api/admissionlife/saved-questions/
```

**Access:** Guest + Registered

**Guest call:**
```
GET /api/admissionlife/saved-questions/
X-Guest-Token: 550e8400-e29b-41d4-a716-446655440000
X-Device-ID: your-device-id
```

**Registered user call:**
```
GET /api/admissionlife/saved-questions/
Authorization: Token <user_token>
```

**Response:**
```json
{
  "results": [
    {
      "id": 1,
      "question": { "id": 5, "question_text": "...", "answers": [...], "labels": [...] },
      "saved_at": "2025-01-15T10:00:00Z"
    }
  ]
}
```

### Save a Question (Bookmark)

```
POST /api/admissionlife/saved-questions/
```

**Access:** Guest + Registered

```json
{"question": 5}
```

**Note:** Idempotent — saving the same question twice returns the existing record.

### Remove Saved Question

```
DELETE /api/admissionlife/saved-questions/{id}/
```

**Access:** Guest + Registered

---

## 10. Question Reports

### Report a Question

```
POST /api/admissionlife/question-reports/
```

**Access:** Guest + Registered

**Guest call:**
```
POST /api/admissionlife/question-reports/
X-Guest-Token: 550e8400-e29b-41d4-a716-446655440000
X-Device-ID: your-device-id
Content-Type: application/json

{"question": 5, "reason": "The correct answer seems wrong."}
```

**Registered user call:**
```
POST /api/admissionlife/question-reports/
Authorization: Token <user_token>
Content-Type: application/json

{"question": 5, "reason": "The correct answer seems wrong."}
```

---

## 11. Practice Quizzes

### Generate Practice Quiz

```
POST /api/admissionlife/practice-quizzes/
```

**Access:** Guest + Registered

**Guest call:**
```
POST /api/admissionlife/practice-quizzes/
X-Guest-Token: 550e8400-e29b-41d4-a716-446655440000
X-Device-ID: your-device-id
Content-Type: application/json

{
  "categories": [
    {"category_id": 5, "question_count": 10, "include_subcategories": true}
  ]
}
```

**Registered user call:**
```
POST /api/admissionlife/practice-quizzes/
Authorization: Token <user_token>
Content-Type: application/json

{
  "categories": [
    {"category_id": 5, "question_count": 10, "include_subcategories": true}
  ]
}
```

**Response (201):**
```json
{
  "id": 1,
  "name": "Practice Quiz",
  "quiz_type": "PRACTICE",
  "duration_minutes": 10,
  "questions": [
    {
      "id": 1,
      "question_text": "What is 2+2?",
      "question_image": null,
      "answers": [
        {"id": 1, "text": "3", "image": null},
        {"id": 2, "text": "4", "image": null}
      ]
    }
  ]
}
```

### Start Practice Quiz Attempt

```
POST /api/admissionlife/practice-quizzes/{quiz_id}/start/
```

**Access:** Guest + Registered

### Submit Practice Quiz

```
POST /api/admissionlife/practice-quizzes/{attempt_id}/submit/
```

**Access:** Guest + Registered

```json
{
  "submissions": [
    {"question_id": 1, "selected_answer_id": 2},
    {"question_id": 2, "selected_answer_id": null}
  ]
}
```

### Get Practice Quiz Result

```
GET /api/admissionlife/practice-quizzes/{attempt_id}/result/
```

**Access:** Guest + Registered

**Response:**
```json
{
  "id": 1,
  "quiz": 1,
  "score": 8,
  "is_completed": true,
  "start_time": "2025-01-15T10:00:00Z",
  "end_time": "2025-01-15T10:05:00Z",
  "submissions": [
    {
      "question": { "id": 1, "question_text": "...", "answers": [...] },
      "selected_answer": {"id": 2, "text": "4", "image": null, "is_correct": true},
      "correct_answer": {"id": 2, "text": "4", "image": null, "is_correct": true},
      "is_correct": true
    }
  ]
}
```

---

## 12. Admin: Questions

**Access:** Admin only (`is_staff=True`, Google login required)

```
Authorization: Token <admin_token>
```

### List Questions

```
GET /api/admissionlife/admin/questions/
GET /api/admissionlife/admin/questions/?category=5
GET /api/admissionlife/admin/questions/?category=1&include_descendants=true
```

### Create Question

```
POST /api/admissionlife/admin/questions/
```

```json
{
  "question_text": "What is the capital of Bangladesh?",
  "question_image": null,
  "explanation": "Dhaka is the capital.",
  "explanation_image": null,
  "category": 5,
  "labels": [1, 2],
  "answers": [
    {"text": "Dhaka", "image": null, "is_correct": true},
    {"text": "Chittagong", "image": null, "is_correct": false},
    {"text": "Sylhet", "image": null, "is_correct": false},
    {"text": "Rajshahi", "image": null, "is_correct": false}
  ]
}
```

### Update Question

```
PUT /api/admissionlife/admin/questions/{id}/
PATCH /api/admissionlife/admin/questions/{id}/
```

### Delete Question

```
DELETE /api/admissionlife/admin/questions/{id}/
```

### Bulk Import from CSV

```
POST /api/admissionlife/admin/questions/import-csv/
Content-Type: multipart/form-data
Authorization: Token <admin_token>

file: <csv_file>
```

**CSV columns:** `question_text,option_a,option_b,option_c,option_d,correct_option,category_id,explanation`

**Response:**
```json
{
  "total_rows": 50,
  "created": 47,
  "skipped": 3,
  "errors": [
    {"row": 5, "reason": "Missing or empty question_text."},
    {"row": 12, "reason": "Invalid correct_option value: 'e'. Must be one of: a, b, c, d."}
  ]
}
```

---

## 13. Admin: Categories

**Access:** Admin only

```
Authorization: Token <admin_token>
```

### List/Create/Update/Delete

```
GET    /api/admissionlife/admin/categories/
POST   /api/admissionlife/admin/categories/
PUT    /api/admissionlife/admin/categories/{id}/
DELETE /api/admissionlife/admin/categories/{id}/
```

**Create/Update Body:**
```json
{"name": "Grammar", "parent": 1, "order": 1}
```

**Delete rules:**
- Returns 400 if category has children
- Leaf category: deletes and sets `category=null` on associated questions

---

## 14. Admin: Labels

**Access:** Admin only

```
Authorization: Token <admin_token>
```

```
GET    /api/admissionlife/admin/labels/
POST   /api/admissionlife/admin/labels/
PUT    /api/admissionlife/admin/labels/{id}/
DELETE /api/admissionlife/admin/labels/{id}/
```

**Create/Update Body:**
```json
{"name": "Previous Year 2024"}
```

---

## Access Summary Table

| Endpoint | Guest (X-Guest-Token) | Registered (Token) | Admin |
|----------|:-----:|:-----:|:-----:|
| GET /batches/ | ✅ | ✅ | ✅ |
| GET /batches/{id}/ | ✅ | ✅ | ✅ |
| POST/PUT/DELETE /batches/ | ❌ | ❌ | ✅ |
| POST /payments/ | ❌ | ✅ | ✅ |
| GET /payments/ | ❌ | ✅ | ✅ |
| POST /payments/{id}/approve/ | ❌ | ❌ | ✅ |
| POST /payments/{id}/reject/ | ❌ | ❌ | ✅ |
| GET /enrollments/check/{id}/ | ❌ | ✅ | ✅ |
| GET /exams/ | ❌ | ✅ | ✅ |
| POST/PUT/DELETE /exams/ | ❌ | ❌ | ✅ |
| POST /exam-attempts/{id}/start/ | ❌ | ✅ | ✅ |
| POST /exam-attempts/{id}/submit/ | ❌ | ✅ | ✅ |
| GET /exam-attempts/{id}/result/ | ❌ | ✅ | ✅ |
| GET /batches/{id}/leaderboard/ | ❌ | ✅ | ✅ |
| GET /exams/{id}/leaderboard/ | ❌ | ✅ | ✅ |
| GET /categories/ | ✅ | ✅ | ✅ |
| GET /categories/tree/ | ✅ | ✅ | ✅ |
| GET /categories/{id}/children/ | ✅ | ✅ | ✅ |
| GET /questions/ | ✅ | ✅ | ✅ |
| GET /questions/{id}/ | ✅ | ✅ | ✅ |
| GET /saved-questions/ | ✅ | ✅ | ✅ |
| POST /saved-questions/ | ✅ | ✅ | ✅ |
| DELETE /saved-questions/{id}/ | ✅ | ✅ | ✅ |
| POST /question-reports/ | ✅ | ✅ | ✅ |
| POST /practice-quizzes/ | ✅ | ✅ | ✅ |
| POST /practice-quizzes/{id}/start/ | ✅ | ✅ | ✅ |
| POST /practice-quizzes/{id}/submit/ | ✅ | ✅ | ✅ |
| GET /practice-quizzes/{id}/result/ | ✅ | ✅ | ✅ |
| /admin/questions/* | ❌ | ❌ | ✅ |
| /admin/categories/* | ❌ | ❌ | ✅ |
| /admin/labels/* | ❌ | ❌ | ✅ |

---

## Error Responses

| Status | Meaning |
|--------|---------|
| 400 | Validation error |
| 401 | `{"detail": "Authentication credentials were not provided."}` |
| 403 | `{"detail": "You do not have permission to perform this action."}` |
| 404 | `{"detail": "Not found."}` |

---

## Other Auth Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/dj-rest-auth/google/` | POST | Google login → returns `{"key": "..."}` |
| `/dj-rest-auth/user/` | GET | Get current user info (registered only) |
| `/dj-rest-auth/logout/` | POST | Invalidate token (registered only) |
| `/api/auth/guest/` | POST | Guest login → returns `guest_token` |
| `/api/auth/convert-guest/` | POST | Convert guest to registered user |
| `/api/guest/stats/` | GET | Get guest user statistics |
