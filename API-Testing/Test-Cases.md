# AttendAIoT API Test Cases

## 1. Document purpose and test status

This document defines Swagger UI test cases for the FastAPI routes currently declared in [`backend/main.py`](../backend/main.py). Expected results below are derived from that source and the services it calls; they are not results obtained by executing requests.

**Execution record:** Tests were run through Swagger UI on 2026-11-18 against the local backend. Expected results remain source-derived; actual results below are observed responses. An attendance session was already active before session-dependent cases were run. It was left active and unchanged, and cases requiring a new/arrival-scanning/no-session state are marked `NOT EXECUTED`. Test students created for this run were archived afterward.

## 2. Test environment and data

- Swagger UI: `http://127.0.0.1:8000/docs`
- API base URL: `http://127.0.0.1:8000`
- Start the backend from the repository root using the project's configured Python environment and an ASGI server, for example: `uvicorn backend.main:app --reload`.
- The application initializes its SQLite database and attendance-session schema during startup.
- Tests that create, update, or archive students and sessions change the local database. Use test student IDs and consider the order and cleanup/archive behavior before testing.
- Wi-Fi scan outcomes depend on the host's Wi-Fi adapter, operating system, and available networks. A visible registered SSID is explicitly **not** individual-device presence.
- Face outcomes depend on enrollment samples, the face detector/model, and the submitted image. Use images appropriate for the test; do not infer a successful recognition solely from an HTTP 200 response.
- ThingSpeak success depends on network access and the `THINGSPEAK_WRITE_API_KEY` environment variable. The key is not part of the request body.

### Request examples

The following are representative request bodies for the matching Swagger operations. For face tests, replace the placeholder with actual base64-encoded image bytes; the placeholder itself is not a valid image.

**Register a student** (`POST /students`):

```json
{
  "student_id": "TEST001",
  "name": "Test Student",
  "wifi_ssid": "Campus-WiFi"
}
```

Optional face enrollment uses the same body with an `image_base64` property containing a valid, single-face image encoded as base64.

**Update a student** (`PUT /students/{student_id}`):

```json
{
  "name": "Updated Test Student",
  "wifi_ssid": "Campus-WiFi"
}
```

`name` and `wifi_ssid` are required by the request model; `image_base64` is optional.

**Update session settings** (`PUT /session-settings`):

```json
{
  "arrival_scan_minutes": 10,
  "session_deadline_minutes": 60,
  "disconnect_grace_minutes": 2,
  "minimum_verified_minutes": 45
}
```

The field ranges are respectively 1–30, 45–360, 0–30, and 1–360. In addition, `arrival_scan_minutes` and `minimum_verified_minutes` must each be no greater than `session_deadline_minutes`.

**Face scan** (`POST /scan/{student_id}`) and **session frame** (`POST /sessions/{session_id}/frame`):

```json
{
  "image_base64": "<base64-encoded image bytes>"
}
```

The API accepts decoded image data only when it is non-empty and no larger than 5 MiB. A syntactically valid base64 string that is not a decodable image can still produce a face-service validation/runtime error rather than a successful recognition result.

## 3. Test case conventions

- Test inputs identify the path parameter, query parameter, or JSON body to enter in Swagger UI.
- Dynamic values such as UUIDs, timestamps, database IDs, counts, and recognition results are represented by descriptions or placeholders; they are not fabricated.
- “Expected response” describes the response contract/source behavior. It is not an observed response.
- `Actual Result` and `Status` are deliberately left in their initial, unexecuted state for every case.
- Unless stated otherwise, successful FastAPI route responses use HTTP `200`.

## 4. Test cases

### 4.1 Root and health checks

| Test Case ID | API Endpoint | HTTP Method | Test Scenario | Test Input / Request Parameters | Expected HTTP Status Code | Expected Response (source-derived) | Actual Result | Status |
|---|---|---|---|---|---:|---|---|---|
| TC-HLT-01 | `/` | GET | Confirm the API root responds | No parameters or body | 200 | `{"message":"AttendAIoT Backend is running"}` | HTTP 200; returned `{"message":"AttendAIoT Backend is running"}`. | PASS |
| TC-HLT-02 | `/health` | GET | Confirm the health endpoint reports online | No parameters or body | 200 | `{"status":"online"}` | HTTP 200; returned `{"status":"online"}`. | PASS |

### 4.2 Session settings

| Test Case ID | API Endpoint | HTTP Method | Test Scenario | Test Input / Request Parameters | Expected HTTP Status Code | Expected Response (source-derived) | Actual Result | Status |
|---|---|---|---|---|---:|---|---|---|
| TC-SET-01 | `/session-settings` | GET | Read current settings, including defaults for unset values | No parameters or body | 200 | JSON object with all four setting keys; if not previously saved, source defaults are `10`, `60`, `2`, and `45` respectively | HTTP 200; returned `arrival_scan_minutes: 1`, `session_deadline_minutes: 45`, `disconnect_grace_minutes: 30`, and `minimum_verified_minutes: 1` from persisted settings. | PASS |
| TC-SET-02 | `/session-settings` | PUT | Save valid settings at supported boundaries | Body: `arrival_scan_minutes: 30`, `session_deadline_minutes: 45`, `disconnect_grace_minutes: 0`, `minimum_verified_minutes: 1` | 200 | `{"settings":{...}}` containing the saved values | A valid settings request returned HTTP 409 because a session was already active. The inactive-session precondition was unavailable; settings were not changed. | NOT EXECUTED |
| TC-SET-03 | `/session-settings` | PUT | Reject values outside Pydantic field limits | Try each boundary violation: arrival `0` or `31`; deadline `44` or `361`; grace `-1` or `31`; minimum `0` or `361` | 422 | FastAPI/Pydantic validation response with `detail` describing the invalid field(s); values are not saved | All eight below-minimum/above-maximum trials returned HTTP 422 with field-specific Pydantic validation details. | PASS |
| TC-SET-04 | `/session-settings` | PUT | Verify the valid arrival/deadline boundary combination | Body: arrival `30`, deadline `45`, grace `2`, minimum `30` | 200 | `{"settings":{...}}` containing the submitted values. The cross-field guard for arrival longer than deadline exists in source, but cannot be triggered while both fields satisfy their individual model ranges (arrival max 30; deadline min 45) | Not run: an active session prevented a successful settings update. | NOT EXECUTED |
| TC-SET-05 | `/session-settings` | PUT | Reject minimum verified time longer than session deadline | Use otherwise valid body with `minimum_verified_minutes: 60` and `session_deadline_minutes: 45` | 422 | `{"detail":"Minimum verified attendance cannot exceed the session deadline."}` | HTTP 422; returned `{"detail":"Minimum verified attendance cannot exceed the session deadline."}`. | PASS |
| TC-SET-06 | `/session-settings` | PUT | Do not change settings while an attendance session is active | Submit a valid settings body while the observed session is active | 409 | `{"detail":"Settings cannot be changed while a session is active."}` | HTTP 409; returned `{"detail":"Settings cannot be changed while a session is active."}`. | PASS |
| TC-SET-07 | `/session-settings` | PUT | Reject a missing or malformed settings body | Submit an empty JSON object `{}` | 422 | FastAPI/Pydantic validation response; settings are not saved | HTTP 422; validation detail reported all four required fields missing. | PASS |

### 4.3 Device evidence status

| Test Case ID | API Endpoint | HTTP Method | Test Scenario | Test Input / Request Parameters | Expected HTTP Status Code | Expected Response (source-derived) | Actual Result | Status |
|---|---|---|---|---|---:|---|---|---|
| TC-DEV-01 | `/device-evidence/status` | GET | Inspect configured authenticated-device evidence provider and local adapters | No parameters or body | 200 | Object reports `provider_configured: false`, `provider: null`, and `individual_device_presence_available: false`; `local_wifi_adapters` may vary by host. Message states that an authenticated provider is required | HTTP 200; provider was not configured; local Intel Wi-Fi adapter reported AP scanning available but client reporting unavailable. | PASS |

### 4.4 Student registration and CRUD

| Test Case ID | API Endpoint | HTTP Method | Test Scenario | Test Input / Request Parameters | Expected HTTP Status Code | Expected Response (source-derived) | Actual Result | Status |
|---|---|---|---|---|---:|---|---|---|
| TC-STU-01 | `/students` | POST | Register a new student without a face image | JSON body from the student-registration example; use a new `student_id` | 200 | `message` says student added without face enrollment; includes `student_id`, trimmed `name`, trimmed `wifi_ssid`, `already_exists: false`, and `face_enrolled: false` | HTTP 200; registered `API_TEST_20261118_0945`; response reported `already_exists: false` and `face_enrolled: false`. | PASS |
| TC-STU-02 | `/students` | POST | Verify duplicate registration is idempotent, including case-insensitive ID match | Register a student, then submit the same ID with different letter casing | 200 | `already_exists: true`, existing `student_id` and `name`, and message that no duplicate was added; no second record is inserted | HTTP 200; case-insensitive duplicate returned `already_exists: true` and the original student details. | PASS |
| TC-STU-03 | `/students` | POST | Reject blank required student values | Submit whitespace-only `student_id`, `name`, or `wifi_ssid` (test individually) in a body | 422 | `{"detail":"Student ID, name, and Wi-Fi SSID are required."}` | HTTP 422; returned `{"detail":"Student ID, name, and Wi-Fi SSID are required."}` for whitespace-only values. | PASS |
| TC-STU-04 | `/students` | POST | Validate registration field length boundaries | Test model minimum length 1 and maximums: student ID 100 characters, name 200, SSID 200; separately exceed each maximum by one | 200 for valid values, 422 for over-limit value(s) | Valid values follow registration response contract; invalid values return Pydantic validation details | HTTP 200 at minimum and maximum lengths; HTTP 422 when student ID, name, and SSID were each tested one character over the maximum. Temporary boundary students were archived afterward. | PASS |
| TC-STU-05 | `/students` | GET | List the active student roster | No parameters or body | 200 | `{"students":[...]}`; each row is an array containing database `id`, `student_id`, `name`, `wifi_ssid`, and a Boolean face-enrolled value. Contents/order depend on database state | HTTP 200; returned active-student rows with the expected five values per row, including the then-active test student. | PASS |
| TC-STU-06 | `/students/{student_id}` | PUT | Update an active student's name and Wi-Fi SSID | Path: existing active student ID; body from update example | 200 | `message: "Student details updated."` plus the matched `student_id`, trimmed `name` and `wifi_ssid`, and `face_updated: false` | HTTP 200; test student's name and SSID changed to the submitted trimmed values; `face_updated: false`. | PASS |
| TC-STU-07 | `/students/{student_id}` | PUT | Reject update for an unregistered or archived student | Path: ID not in active roster; valid update body | 404 | `{"detail":"Student is not registered."}` | HTTP 404; returned `{"detail":"Student is not registered."}`. | PASS |
| TC-STU-08 | `/students/{student_id}` | PUT | Prevent editing a student enrolled in an active session | Path: a student in the already-active session; submit a valid update | 409 | Detail states the student is part of the active attendance session and the session must be stopped before editing | HTTP 409; returned active-session edit conflict. The existing student was not changed. | PASS |
| TC-STU-09 | `/students/{student_id}` | PUT | Reject a whitespace-only trimmed name | Path: existing active student; valid `wifi_ssid` and `name: "   "` | 422 | `{"detail":"Student name is required."}`; no update is committed | HTTP 422; returned `{"detail":"Student name is required."}`. | PASS |
| TC-STU-10 | `/students/{student_id}` | DELETE | Archive an active student while preserving attendance history | Path: existing active student ID not in an active session | 200 | Message says student was archived and history preserved; includes matched `student_id`. Student is no longer in active roster | HTTP 200; test student was archived. It had no prior attendance rows, so preservation of historical rows was not independently verified. | NOT EXECUTED |
| TC-STU-11 | `/students/{student_id}` | DELETE | Reject deletion of missing or already archived student | Path: unknown or previously archived ID | 404 | `{"detail":"Student is not registered."}` | HTTP 404 when deleting the previously archived test student; returned `{"detail":"Student is not registered."}`. | PASS |
| TC-STU-12 | `/students/{student_id}` | DELETE | Prevent archiving a student in an active session | Path: student in the already-active session | 409 | Detail states the student is part of the active attendance session and the session must be stopped before removal | HTTP 409; returned active-session removal conflict. The existing student was not archived. | PASS |
| TC-STU-13 | `/students` | POST | Validate invalid base64 and an image with no detectable face during optional face enrollment | Submit a new student with `image_base64: "not-base64"`; separately submit a valid synthetic image with no face | 400 for invalid base64; 422 for no face | Invalid base64 returns `{"detail":"Invalid face image data."}`; face validation returns its source-defined detail. Student is not registered on validation failure. An empty `image_base64` string is skipped by source code rather than validated as image bytes | HTTP 400 for invalid base64; HTTP 422 for a valid synthetic image with no face (`"No face was detected in the enrollment photo."`). | PASS |

### 4.5 Wi-Fi endpoints

| Test Case ID | API Endpoint | HTTP Method | Test Scenario | Test Input / Request Parameters | Expected HTTP Status Code | Expected Response (source-derived) | Actual Result | Status |
|---|---|---|---|---|---:|---|---|---|
| TC-WIFI-01 | `/wifi/status` | GET | Scan available access-point names and compare against active roster | Host must have a scan-capable Wi-Fi adapter; no request parameters | 200 when scan succeeds | `evidence_type: "ACCESS_POINT_SCAN"`, `individual_device_presence_available: false`, `scan_result: "ACCESS_POINT_NAMES_ONLY"`, student entries with configured/network-name-visible flags and `wifi_present: false`, and a timestamp | HTTP 200; access-point scan succeeded; response identified names-only evidence and `wifi_present: false` for each listed student. No registered SSID was visible. | PASS |
| TC-WIFI-02 | `/wifi/status` | GET | Handle unavailable Wi-Fi scanning | Run where scanner is unavailable, no adapter exists, or scanning raises the service's reported runtime error | 503 | `{"detail":"<Wi-Fi scanning error from the host/service>"}`; exact detail depends on cause | Not run: this host had a scan-capable adapter and the access-point scan succeeded; unavailable-adapter behavior could not be exercised safely. | NOT EXECUTED |
| TC-WIFI-03 | `/wifi/{student_id}` | GET | Check a registered student's SSID visibility | Path: existing active student ID; host scan required | 200 when scan succeeds | Includes `student_id`, `name`, `registered_wifi`, `network_name_visible`, `individual_device_presence_available: false`, `wifi_present: false`, and status indicating whether the registered network name is visible; it does not assert device presence | HTTP 200 for the test student; `network_name_visible: false`, `individual_device_presence_available: false`, and `wifi_present: false`. | PASS |
| TC-WIFI-04 | `/wifi/{student_id}` | GET | Check unknown student | Path: unknown student ID | 200 | Unknown ID returns `wifi_present: false` and `status: "STUDENT NOT REGISTERED"` | HTTP 200; returned `wifi_present: false` and `status: "STUDENT NOT REGISTERED"`. | PASS |

### 4.6 Face recognition

| Test Case ID | API Endpoint | HTTP Method | Test Scenario | Test Input / Request Parameters | Expected HTTP Status Code | Expected Response (source-derived) | Actual Result | Status |
|---|---|---|---|---|---:|---|---|---|
| TC-FACE-01 | `/scan/{student_id}` | POST | Submit a matching face image for an enrolled student | Path: active enrolled student; body contains a valid matching face image below the size limit | 200 if face service completes | Response includes `face_enrolled: true`, `face_recognized: true`, matching `recognized_student_id`, `attendance_status: "AWAITING_AUTHENTICATED_DEVICE"`, and `attendance_marked: false` | Not executed: the attempted local image payload was rejected with HTTP 400 as over 5 MiB; no matching-face result was obtained. | NOT EXECUTED |
| TC-FACE-02 | `/scan/{student_id}` | POST | Submit a valid image with no recognizable enrolled face | Path: active enrolled student; body contains a valid synthetic image with no face | 200 | `face_enrolled: true`, `face_recognized: false`, `recognized_student_id: null`, `attendance_status: "NOT_VERIFIED"`, and `attendance_marked: false` | HTTP 200; `face_enrolled: true`, `face_recognized: false`, `recognized_student_id: null`, `attendance_status: "NOT_VERIFIED"`, `attendance_marked: false`. | PASS |
| TC-FACE-03 | `/scan/{student_id}` | POST | Handle student without enrolled face profile | Path: active test student with no face sample; body contains invalid base64 | 200 | `face_enrolled: false`, `face_recognized: false`, `attendance_status: "NOT_VERIFIED"`, and message that no face profile is enrolled. Source returns before decoding image data | HTTP 200; returned `face_enrolled: false`, `face_recognized: false`, and `attendance_status: "NOT_VERIFIED"` despite invalid base64, as the source returns before decoding. | PASS |
| TC-FACE-04 | `/scan/{student_id}` | POST | Reject unknown or archived student | Path: unknown student ID; valid JSON request body | 404 | `{"detail":"Student is not registered."}` | HTTP 404; returned `{"detail":"Student is not registered."}`. | PASS |
| TC-FACE-05 | `/scan/{student_id}` | POST | Reject invalid base64 for an enrolled student | Path: active enrolled student; `image_base64: "not-base64"` | 400 | `{"detail":"Invalid image data."}` | HTTP 400; returned `{"detail":"Invalid image data."}`. | PASS |
| TC-FACE-06 | `/scan/{student_id}` | POST | Reject empty decoded image bytes | Path: active enrolled student; `image_base64: ""` | 400 | `{"detail":"Image must be non-empty and no larger than 5MB."}` | HTTP 400; returned `{"detail":"Image must be non-empty and no larger than 5MB."}`. | PASS |
| TC-FACE-07 | `/scan/{student_id}` | POST | Observe handling of valid base64 that is not a decodable image | Path: active enrolled student; base64-encode non-image bytes | 500 from current source because the image-decoder `ValueError` is not translated by this route | FastAPI generic internal-server-error response; this documents current source behavior, not a recommended API contract | HTTP 500; Swagger UI displayed `Internal Server Error` for valid base64 containing non-image data. | PASS |
| TC-FACE-08 | `/scan/{student_id}` | POST | Reject image payload larger than 5 MiB | Path: active enrolled student; submit decoded image bytes over 5 MiB | 400 | `{"detail":"Image must be non-empty and no larger than 5MB."}` | Not run; an over-5-MiB request body was not submitted. | NOT EXECUTED |

### 4.7 Attendance records and legacy behavior

| Test Case ID | API Endpoint | HTTP Method | Test Scenario | Test Input / Request Parameters | Expected HTTP Status Code | Expected Response (source-derived) | Actual Result | Status |
|---|---|---|---|---|---:|---|---|---|
| TC-ATT-01 | `/attendance` | GET | Retrieve attendance history | No parameters or body | 200 | `{"attendance":[...]}`; rows are arrays containing attendance ID, student ID, joined student name (may be null), date, time, and status; ordered by date/time descending | HTTP 200; returned three attendance rows in descending date/time order with the expected six values per row. | PASS |
| TC-ATT-02 | `/attendance` | POST | Confirm legacy direct attendance marking is disabled | Query parameter `student_id=<existing-or-unknown-id>` | 409 | `detail` states legacy marking is disabled and attendance requires a session, enrolled face, and authenticated device evidence; no attendance is marked | HTTP 409; returned the legacy-disabled detail; no attendance was marked. | PASS |

### 4.8 Attendance sessions, frame processing, and device events

| Test Case ID | API Endpoint | HTTP Method | Test Scenario | Test Input / Request Parameters | Expected HTTP Status Code | Expected Response (source-derived) | Actual Result | Status |
|---|---|---|---|---|---:|---|---|---|
| TC-SES-01 | `/sessions` | POST | Start a new attendance session | No parameters or body; run only when no session is active | 200 | Session status object with generated session UUID, `status: "ARRIVAL_SCANNING"`, start/deadline values, remaining seconds, registered-student counts/list, and `device_evidence`; values depend on database/settings | HTTP 409 because an attendance session was already active; no new session was created. New-session precondition was unavailable. | NOT EXECUTED |
| TC-SES-02 | `/sessions` | POST | Reject a second session while one is active | Call while an attendance session is active | 409 | `{"detail":"An attendance session is already active."}` | HTTP 409; returned `{"detail":"An attendance session is already active."}`. | PASS |
| TC-SES-03 | `/sessions/current` | GET | Read session state when there is no session history | Ensure fresh database with no attendance sessions | 200 | `session: null`, counts/list in the empty-session response, and device-evidence object. `registered_students` reflects the source's student count query | Not run: an existing session was present in the database. | NOT EXECUTED |
| TC-SES-04 | `/sessions/current` | GET | Read the most recently created session | Call while an existing session is present | 200 | Session status object for the latest session, including per-session student rows and summary counts; time-dependent fields vary | HTTP 200; returned session `a4a89cec-9f2a-4caf-b4ae-5afd70908d17`, status `MONITORING`, and three session students. | PASS |
| TC-SES-05 | `/sessions/{session_id}` | GET | Retrieve a session by its returned ID | Path: valid existing session UUID | 200 | Same session status structure as current-session lookup, for the requested ID | HTTP 200 for session `a4a89cec-9f2a-4caf-b4ae-5afd70908d17`; returned its `MONITORING` status and student list. | PASS |
| TC-SES-06 | `/sessions/{session_id}` | GET | Return not found for unknown session | Path: UUID/string not present in the database | 404 | `{"detail":"Attendance session not found."}` | HTTP 404; returned `{"detail":"Attendance session not found."}`. | PASS |
| TC-SES-07 | `/sessions/{session_id}/stop` | POST | Stop an active session and finalize attendance states | Path: active session ID | 200 | Updated session status object; session is finalized, open intervals are closed, and per-student attendance status is derived from face/device evidence and minimum verified time | Not run to avoid stopping the pre-existing active session. | NOT EXECUTED |
| TC-SES-08 | `/sessions/{session_id}/stop` | POST | Reject stop for unknown session | Path: unknown session ID | 404 | Detail indicates the attendance session was not found | HTTP 404; returned `{"detail":"Attendance session was not found."}`. | PASS |
| TC-SES-09 | `/sessions/{session_id}/stop` | POST | Repeat stop on an already stopped session | Stop a session, then call stop again using its ID | 200 | Current status object is returned; source only finalizes sessions whose status is active, so the completed/stopped session is not finalized a second time | Not run because no test session was created or stopped; existing user session was left unchanged. | NOT EXECUTED |
| TC-SES-10 | `/sessions/{session_id}/frame` | POST | Process a valid frame during arrival scanning | Path: active session ID in `ARRIVAL_SCANNING`; valid image body | 200 | `arrival_scanning: true`, `attendance_marked: false`; message indicates detected face awaiting authenticated device verification or no enrolled face recognized | Not run: the existing session was already in `MONITORING`, not `ARRIVAL_SCANNING`. | NOT EXECUTED |
| TC-SES-11 | `/sessions/{session_id}/frame` | POST | Return the non-scanning response outside arrival scanning | Path: existing session in `MONITORING`; valid image body | 200 | `{"recognized_student_ids":[],"arrival_scanning":false,"message":"Arrival scanning is not active."}` | HTTP 200; returned empty recognized IDs, `arrival_scanning: false`, and `"Arrival scanning is not active."` | PASS |
| TC-SES-12 | `/sessions/{session_id}/frame` | POST | Reject invalid base64 and empty frame bytes | Path: existing session ID; separately send invalid base64 and empty base64 | 400 | Invalid base64: `{"detail":"Invalid image data."}`. Empty bytes: detail says image must be non-empty and no larger than 5MB | HTTP 400 for invalid base64 and for empty bytes; returned the corresponding source-defined details. | PASS |
| TC-SES-13 | `/sessions/{session_id}/frame` | POST | Reject frame for unknown session | Path: unknown session ID; syntactically valid base64 body | 404 | `{"detail":"Attendance session not found."}` | HTTP 404; returned `{"detail":"Attendance session not found."}`. | PASS |
| TC-SES-14 | `/sessions/{session_id}/device-events` | POST | Verify device-event ingestion is unavailable without an authenticated provider | Path: session ID; no body is defined for this route | 503 | Detail says no authenticated router, controller, RADIUS, or enrolled-device provider is configured and SSID scans cannot create device events | HTTP 503; returned the provider-not-configured detail; SSID scans cannot create device events. | PASS |
| TC-SES-15 | `/sessions/{session_id}/frame` | POST | Reject frame payload larger than 5 MiB | Path: existing session ID; submit decoded image bytes over 5 MiB | 400 | `{"detail":"Image must be non-empty and no larger than 5MB."}` | Not run; an over-5-MiB request body was not submitted. | NOT EXECUTED |

### 4.9 ThingSpeak integration

| Test Case ID | API Endpoint | HTTP Method | Test Scenario | Test Input / Request Parameters | Expected HTTP Status Code | Expected Response (source-derived) | Actual Result | Status |
|---|---|---|---|---|---:|---|---|---|
| TC-TS-01 | `/thingspeak/test` | POST | Publish the test telemetry payload with valid configuration and network access | No parameters or body; configure `THINGSPEAK_WRITE_API_KEY` on backend before starting it | 200 if ThingSpeak accepts update | `{"message":"ThingSpeak connection successful","channel_id":3578408,"thingSpeak":{"success":true,"entry_id":"<ThingSpeak entry ID>"}}` | HTTP 502; response stated `{"detail":"ThingSpeak API key is not configured."}`. A successful upstream publish could not be tested without the key. | NOT EXECUTED |
| TC-TS-02 | `/thingspeak/test` | POST | Handle missing API key, network error, or rejected update | No parameters or body; test without key | 502 | `{"detail":"<ThingSpeak service error>"}`; source-specific messages include missing key, connection failure, or rejected update | HTTP 502; returned `{"detail":"ThingSpeak API key is not configured."}`. | PASS |

## 5. Testing summary

| Metric | Count |
|---|---:|
| Total test cases | 54 |
| Passed | 41 |
| Failed | 0 |
| Not executed | 13 |

The results above are from the Swagger UI run recorded in this document. `NOT EXECUTED` cases were blocked by required state or environment (including the pre-existing active session, missing ThingSpeak key, or large/unavailable test inputs); they are not counted as passes or failures. A `PASS` for TC-FACE-07 records the current source behavior; the observed HTTP 500 is still an error-handling issue described below.

### Cases available and verified in this environment

These 41 cases were executed in Swagger UI and matched their source-derived expected results:

- Health: TC-HLT-01–02
- Session settings: TC-SET-01, TC-SET-03, TC-SET-05–07
- Device evidence: TC-DEV-01
- Student CRUD and validation: TC-STU-01–09, TC-STU-11–13
- Wi-Fi: TC-WIFI-01, TC-WIFI-03–04
- Face recognition and validation: TC-FACE-02–07
- Attendance: TC-ATT-01–02
- Sessions and device events: TC-SES-02, TC-SES-04–06, TC-SES-08, TC-SES-11–14
- ThingSpeak error handling: TC-TS-02

### Cases not executed and why

- **Existing active session:** TC-SET-02, TC-SET-04, TC-SES-01, TC-SES-03, TC-SES-07, TC-SES-09, and TC-SES-10 required an inactive, absent, stopped, or arrival-scanning session state. The already-active session was left untouched.
- **Historical attendance preservation:** TC-STU-10 archived a test student, but that student had no existing attendance rows to verify preservation against.
- **Unavailable Wi-Fi adapter:** TC-WIFI-02 could not be exercised because the host adapter was available and its scan succeeded.
- **Matching/oversized face inputs:** TC-FACE-01 did not obtain a matching-face result because the attempted image payload was rejected as over 5 MiB; TC-FACE-08's separate over-limit request was not run.
- **Oversized session frame:** TC-SES-15 was not run because an over-5-MiB request body was not submitted.
- **ThingSpeak success path:** TC-TS-01 could not publish because `THINGSPEAK_WRITE_API_KEY` was not configured.

### Observed issue

- **TC-FACE-07:** `/scan/{student_id}` returned HTTP 500 when valid base64 decoded to data that was not a decodable image. The face service raises a `ValueError` for that input, and the route does not translate it into a client error. Consider handling it as HTTP 400 or 422 in the backend.

## 6. How to execute using Swagger UI

1. From the repository root, activate the project's Python environment and start the FastAPI app, for example:

   ```powershell
   uvicorn backend.main:app --reload
   ```

2. Open `http://127.0.0.1:8000/docs` in a browser. This plan uses Swagger UI only; it does not require Postman, Newman, or another API testing client.
3. Expand an endpoint, select **Try it out**, enter the path/query parameters or JSON body specified in the matching case, and select **Execute**.
4. Compare the returned HTTP status and response body with the source-derived expected result. Account for dynamic IDs/timestamps, database state, and host-dependent Wi-Fi/face/ThingSpeak conditions.
5. Record the observed status/body in `Actual Result`. Mark `PASS` only when the observed result matches the case expectation; otherwise mark `FAIL` and include the discrepancy. Keep unrun cases as `Not executed.` / `NOT EXECUTED`.
6. For cases that depend on setup, run the prerequisite first and reuse its returned student/session ID. For example, register a student before student update/delete tests and start a session before active-session conflict, frame, and stop tests.
7. Re-run health checks after startup if desired. Do not treat an HTTP 200 response from face scanning as proof of recognition or an SSID match as proof of individual device presence.
