# Implementation Checklist: Itinerary Multi-Agent System

This checklist follows the [PRD](PRD.md) and is implementation-focused. Complete items in order where dependencies exist; parallelize where possible.

---

## 1. Environment and dependencies

- [ ] **1.1** Ensure Python 3.10+ is available; document in README.
- [ ] **1.2** Install and pin `google-adk` in `requirements.txt`; verify compatibility with current Gemini API.
- [ ] **1.3** Set up Gemini API key: document `GOOGLE_API_KEY` in `.env.example` and README; do not commit keys.
- [ ] **1.4** Add Excel library (e.g. `openpyxl`) to `requirements.txt` for research output.
- [ ] **1.5** If building a frontend: choose stack (plain HTML/JS or framework) and add dependency file (e.g. `package.json`); document in README.

---

## 2. Trip and CSV parsing

- [ ] **2.1** Define trip info file format (e.g. JSON/YAML/text) and required fields: country, city, flight information (optional), days. Document in PRD or a spec file.
- [ ] **2.2** Implement trip parser in `src/tools/trip_parser.py`: read file, validate required fields, return structured object (or raise clear validation error).
- [ ] **2.3** Define CSV format: columns for activity description (vague) and preference level; support incomplete rows (e.g. "korea, seoul, salt bread" or "salt bread, seoul"). Document with example.
- [ ] **2.4** Implement CSV parser in `src/tools/csv_parser.py`: parse rows, normalize into list of activity + preference; handle encoding and basic malformation.
- [ ] **2.5** Add unit tests for trip parser and CSV parser (valid and invalid inputs).

---

## 3. Research agent

- [ ] **3.1** Implement Research agent in `src/agents/research_agent.py` using ADK `Agent` and Gemini (see [main.py](../main.py)); give it a search tool (e.g. `google_search`).
- [ ] **3.2** Define instruction so the agent interprets vague activity list (from CSV + trip context) and returns 2–5 concrete options per activity with: name, address, location/area, optional link.
- [ ] **3.3** Define output schema (e.g. list of dicts or Pydantic model) for research results to be consumed by Excel export and orchestrator.
- [ ] **3.4** Implement Excel export in `src/tools/excel_export.py`: write research results to a workbook with columns: activity_query, option_name, address, location, link, user_rating (empty). Multiple rows per activity.
- [ ] **3.5** Wire Research agent output to Excel export in orchestrator or agent tool; store Excel file or path in session state and expose via API for download.
- [ ] **3.6** Add unit test(s) for Research agent (mock search tool) and for Excel export schema.

---

## 4. Itinerary agent

- [ ] **4.1** Implement Itinerary agent in `src/agents/itinerary_agent.py` using ADK `Agent` and Gemini.
- [ ] **4.2** Define inputs: trip info, research results (or selected options), user ratings, optional free-text feedback. Load from session state or passed context.
- [ ] **4.3** Integrate or specify travel-time source: implement a tool or stub in `src/tools/travel_time.py` that returns estimated duration between two locations (e.g. by mode: bus, train, walking, driving). Document if using external API and env vars.
- [ ] **4.4** Define instruction so the agent produces an hour-by-hour plan: for each day, slots with activity, recommended start/end time, travel buffer, meal/rest indicators.
- [ ] **4.5** Define rules for "take it easy" variant: fewer activities per day, longer rest/buffers; output same structure as main plan.
- [ ] **4.6** Define output schema (e.g. list of days with list of slots) for itinerary and alternatives; store in session state under `itinerary_plan` and `itinerary_alternatives`.
- [ ] **4.7** Add unit test(s) for Itinerary agent with mock travel-time tool and sample inputs.

---

## 5. Orchestrator and step selection

- [ ] **5.1** Implement root/orchestrator in `src/agents/orchestrator.py` (e.g. ADK `Agent` with sub-agents as tools, or `SequentialAgent`/custom flow).
- [ ] **5.2** Implement step mode handling: read `step_mode` ("full" vs "itinerary_only") from request/session; when "full", run trip parse → CSV parse → Research agent → (wait for ratings or use defaults) → Itinerary agent; when "itinerary_only", skip to Itinerary agent using existing session state and optional `user_feedback`.
- [ ] **5.3** Wire session state: ensure trip parser and CSV parser output are stored (e.g. `trip_info`, `activity_csv`); Research agent output stored as `research_results` and Excel; user ratings as `user_ratings`; Itinerary agent output as `itinerary_plan` and `itinerary_alternatives`. Use ADK `output_key` and state read/write as needed.
- [ ] **5.4** Add integration test: run full flow (mock or real Gemini) and itinerary-only flow with pre-populated state.

---

## 6. API / CLI layer

- [ ] **6.1** Choose backend framework (e.g. FastAPI or Flask); add to `requirements.txt` and create `src/api/routes.py` (or equivalent).
- [ ] **6.2** Implement upload endpoints: POST trip file, POST CSV; parse and store in session (or temporary store keyed by session id); return summary or ids. Define request/response schemas in `src/api/schemas.py`.
- [ ] **6.3** Implement POST run research: create or reuse session, run Research agent, return job id or synchronous result; implement GET research results / Excel download for session.
- [ ] **6.4** Implement POST ratings: accept session id and ratings payload; update session state `user_ratings`.
- [ ] **6.5** Implement POST run itinerary: accept session id, step_mode, optional feedback; run orchestrator with step mode; return job id or synchronous itinerary. Implement GET itinerary for session.
- [ ] **6.6** (Optional) Implement CLI in `src/cli/main.py`: subcommands for upload trip, upload CSV, run research, submit ratings, run itinerary, get Excel, get itinerary; use same business logic as API.
- [ ] **6.7** Document API endpoints and payloads in README or OpenAPI spec.

---

## 7. Frontend (if applicable)

- [ ] **7.1** Create frontend layout per PRD: step-based wizard with step mode selector, upload areas for trip file and CSV, Run research / Run itinerary buttons, research table/Excel viewer, itinerary day/hour view, take-it-easy toggle.
- [ ] **7.2** Implement file upload components with validation and loading state; display trip summary and activity count after parse.
- [ ] **7.3** Implement research results table or Excel viewer and rating inputs; download button for Excel.
- [ ] **7.4** Implement itinerary view: day-by-day, hour-by-hour; toggle or tab for "Take it easy" alternative.
- [ ] **7.5** Connect all actions to API: uploads, run research, submit ratings, run itinerary, get results. Handle async (polling) if backend returns job ids.
- [ ] **7.6** Implement error display (banner/toast) and loading indicators per PRD.

---

## 8. Error and loading handling

- [ ] **8.1** Implement validation for trip file and CSV; return 400 with clear messages; surface in UI near relevant inputs.
- [ ] **8.2** Add retry for Gemini/API calls (e.g. 429, 5xx) per existing pattern in [main.py](../main.py); return 429/502/503/504 to client with user-friendly message.
- [ ] **8.3** Handle timeout for long-running research/itinerary runs; return 504 and show "Request took too long" in UI.
- [ ] **8.4** Validate step selection (e.g. itinerary_only requires prior research or re-upload); return 400 and message.
- [ ] **8.5** Ensure UI shows loading state for uploads and agent runs; show partial results (e.g. research done, itinerary failed) and allow retry.

---

## 9. Accessibility

- [ ] **9.1** Ensure all form inputs and buttons have visible or aria labels; no keyboard traps; Tab order is logical.
- [ ] **9.2** Move focus to result area or next action after upload/run completion where appropriate.
- [ ] **9.3** Use proper headings and table structure (headers, scope) for research and itinerary tables.
- [ ] **9.4** Associate error messages with inputs (aria-describedby/aria-errormessage); use aria-live for status updates.
- [ ] **9.5** Verify color contrast and that information is not conveyed by color alone.

---

## 10. Tests

- [ ] **10.1** Unit tests: trip parser, CSV parser, Excel export, Research agent (mocked search), Itinerary agent (mocked travel time).
- [ ] **10.2** Integration test: full flow (trip + CSV → research → ratings → itinerary) and itinerary-only flow with pre-populated state; use test session or in-memory runner.
- [ ] **10.3** (Optional) E2E test for UI: upload files, run research, run itinerary, view results; use headless browser or Playwright/Cypress.

---

## 11. Deployment

- [ ] **11.1** GitHub Pages: add workflow `.github/workflows/deploy-pages.yml` to build frontend (if applicable) and deploy to GitHub Pages (upload-pages-artifact + deploy-pages or push to gh-pages).
- [ ] **11.2** Document frontend base URL (e.g. `/<repo>/`) and backend API URL configuration for production vs local.
- [ ] **11.3** Document backend deployment: where to run (e.g. Cloud Run), env vars (`GOOGLE_API_KEY`, optional travel-time API key); do not expose keys in frontend.
- [ ] **11.4** Update README with: how to run backend locally, how to run frontend locally, how to deploy and configure API URL for GitHub Pages.

---

*End of implementation checklist.*
