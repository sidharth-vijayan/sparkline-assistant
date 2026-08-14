# College Logbook Feed

Working file for the college internship logbook. Everything added to the 1-month and 3-month
roadmaps also lands here, in the format the logbook asks for: what was **planned** for each day,
what was **actually achieved**, and a rolling monthly summary.

**How to use it:** at the end of each week, copy the week's section below and paste it into Claude
with a request to add it to the logbook document. Sections are self-contained — no context from
this repo is needed to understand them.

---

## Week of 2026-08-11 (Mon–Fri)

**Theme:** Making the assistant decide for itself whether a question should be answered from the
company's documents or from general knowledge.

---

### Tuesday, 2026-08-11

**Planned:**
- Investigate the complaint that the assistant answers "I couldn't find this in the available
  documents" to ordinary questions.
- Determine whether automatic switching between document search and general answering is feasible
  without asking the user to pick a mode.

**Achieved:**
- Traced the fault to two independent causes: the intent classifier had no patterns for the
  general-question category and fell back to document Q&A, which made the general-answer path
  unreachable code; and retrieval applied no relevance floor, so the five nearest passages always
  reached the model regardless of how unrelated they were, and the grounded-answer instructions then
  made it refuse.
- Decided against fixing this with a longer keyword list. The wording of a question cannot indicate
  whether an answer exists in the corpus — "what is the standard warranty period" is a document
  question only if the warranty policy has been ingested — so the design instead runs retrieval
  first and decides from the relevance score of the best passage retrieved.
- Built a calibration harness (`eval/calibrate_router.py`) to measure that score on 12 in-corpus and
  15 out-of-corpus questions, because cross-encoder scores are raw logits with no universal cut-off
  and the useful threshold depends on the corpus.
- Found that the existing evaluation question set could not be used for this: it asks about safety
  policy, leave policy, quarterly financials and bills of quantities, none of which have ever been
  ingested. Rewrote the in-corpus set from the documents actually present.
- Brought the full stack back up; it had been down since 2026-08-08.

---

### Wednesday, 2026-08-12

**Planned:**
- Use the calibration data to set the routing thresholds, then implement the routing itself and
  validate it through the browser.

**Achieved:**
- Found the calibration data itself was inconsistent — the same question scored -6.54 in one run and
  +1.78 in another — and stopped to investigate rather than average over it.
- Identified two independent retrieval defects behind the inconsistency:
  1. The rank-fusion step was cutting its candidate list down to the final answer size *before*
     reranking, so the cross-encoder only ever scored 5 of roughly 40 retrieved candidates and could
     never promote a passage that fusion had ranked lower. Separated the two limits with a new
     configuration setting.
  2. The keyword-search tokenizer split on whitespace alone, leaving punctuation attached to words,
     so a query for "MinIO?" produced the token "minio?" and matched nothing in an index built from
     "minio". Keyword search was silently dropping out of the hybrid merge for any question ending in
     a question mark.
- Added a tokenizer version stamp to the saved keyword index so that changing the tokenizer forces a
  rebuild instead of silently degrading recall, plus a startup check that compares the index size
  against the database.
- Re-ran calibration on the repaired pipeline: one representative question moved from -6.54 to
  +1.61, and the two question groups separated cleanly (in-corpus median +5.89, out-of-corpus median
  -9.76). Set the thresholds from the measured gap.
- Implemented the routing itself, with three outcomes rather than a binary switch: a confident match
  answers strictly from the documents with citations; a weak-but-real match receives the documents
  plus permission to add general knowledge, with an explicit marker for which is which; anything
  below the floor is answered from general knowledge with no retrieval at all.
- Added rules that run before retrieval only where retrieval cannot help — small talk such as "hi"
  or "who are you" — or where the user explicitly named a source, in which case "the documents do not
  cover this" is a legitimate answer rather than a dead end. Added a safety net so that a document
  answer which still comes back as a refusal is quietly retried in general mode.
- Restructured the document agent to separate retrieval from answering, so the router can inspect
  retrieval quality before committing to an answer, and handled follow-up questions ("and why
  those?") by expanding them with the previous document exchange — the search query only, so the
  model still answers the user's own words.
- Validated through the actual chat interface rather than the API alone, and found two defects that
  were invisible from the API: the chat frontend's own background requests for conversation titles
  and tags were being treated as user questions, each consuming a full access-control, retrieval,
  reranking and GPU pass and occasionally appearing in the chat as the answer; and my own follow-up
  detection treated any question of six words or fewer as a follow-up, so self-contained questions
  were glued onto unrelated document questions. Detection is now grammatical rather than length-based.
- Added labels showing whether an answer came from company documents, partly from documents, or from
  general knowledge, and expanded the test suite from 10 to 45 unit tests plus a 17-check live
  regression suite. The suite caught a genuine bug in my own fix before release.
- Put the whole change behind a single configuration switch so the previous behaviour can be restored
  without a code change; verified the rollback reproduces the original bug exactly.

**Reflection:** fixing the measurement before trusting it mattered more than the thresholds
themselves — calibrating against the broken pipeline would have written both defects permanently
into the configuration.

---

### Thursday, 2026-08-13

**Planned:**
- Make the assistant understand misspelled questions, and create working accounts for the six
  colleagues who will be testing it.

**Achieved:**
- Measured the problem before building anything: three of four misspelled questions about documents
  we hold were scoring below the routing floor and being answered from general knowledge instead.
  To a user that reads as "the assistant does not know its own documents" — the exact failure the
  routing work had just fixed.
- Built correction in layers, cheapest first: ordinary typos by edit distance, then a second pass
  matching on consonant skeleton for misspellings too far off for the first ("diprisiation" →
  "depreciation"). Both run in memory with no model call.
- Made a deliberate design decision that corrections may only be drawn from the vocabulary of
  whatever documents are currently ingested, rebuilt automatically on every upload. There is no
  hand-written dictionary anywhere in the feature — that is precisely what would have broken the
  moment real stakeholder documents arrived. Proved it by ingesting a document never used in
  development and confirming typos in *its* vocabulary were corrected with no code change.
- Found and fixed my own over-correction: against a small technical corpus, ordinary English words
  absent from the documents were being "corrected" into words that were present, turning "tell me a
  joke" into "well me a joke". Real words are now protected using the reranker's own vocabulary of
  about 21,000 English terms — a word list that already existed in the system rather than a new one
  to maintain.
- Found a second defect while testing: a badly mangled word retrieved the right passages, but the
  model was still shown the user's raw typo, did not recognise it, refused, and the fallback then
  invented a definition for a word that does not exist. The corrected question now reaches the model
  as well as the search.
- Reworked user accounts. The chat integration had been logging in as each user with a single
  password written into the source code, which meant a user changing their own password silently
  broke their own chat. It now holds one service credential and asks the backend for a session by
  name, so it never handles user passwords at all. Added the account operations that had never
  existed: create a user, change your own password, and an administrator reset.
- Provisioned the six testers and removed eleven stale accounts. Discovered that access needs three
  separate things, not one — a backend account, a chat-frontend account, and an explicit model
  permission — and that missing the third leaves a user able to log in and see nothing at all.
- Corrected the routing floor after a real question that the documents *do* answer was sent to
  general knowledge. The misspelling was not the cause: the correctly spelled version scored worse.
  The threshold was simply too strict for broadly worded questions, and the risk is asymmetric —
  a general question wrongly given documents is caught by the existing fallback, while a document
  question wrongly sent to general knowledge has no safety net at all.

**Reflection:** the most valuable half hour of the day was spent proving the typo feature worked on a
document it had never seen. It would have been easy to demonstrate it on our two test files and call
it finished, and it would have failed in front of six people the first time they used it.

---

### Friday, 2026-08-14

**Planned:**
- Accept the file formats the testers actually use, and make ingestion fast enough to do in front of
  them.

**Achieved:**
- Audited what the system genuinely accepted rather than what it claimed to. Macro-enabled Excel
  (.xlsm) — the format most real business spreadsheets are saved in — was not accepted at all, and
  both pre-2007 formats were listed as supported while failing on every file, because the libraries
  behind them only read the modern formats.
- Added readers for the older binary Word and Excel formats, and support for plain text, Markdown and
  CSV, with the delimiter detected rather than assumed since exports here are frequently
  semicolon-separated. File type is now decided by inspecting the file's own signature rather than
  trusting its extension, so a spreadsheet someone renamed still opens.
- Established that processing cost is driven by the number of text sections a file produces, not by
  its size — a 16MB Word report of mostly photographs produced 605 sections, while a 5MB spreadsheet
  of 120,000 transaction rows produced 10,786. On the processor that was roughly an hour, during
  which the upload would have looked frozen.
- Traced that to the embedding model running on the processor rather than the graphics card. The
  container already had the right software; it had simply never been granted access to the card.
  Two configuration lines fixed it, and ingestion of the same spreadsheet fell from about an hour to
  **58 seconds** — while indexing twice as much of the file.
- Added a ceiling on how much of one document may be indexed, spread proportionally across its
  sheets so a twenty-sheet workbook keeps every sheet searchable rather than losing the last dozen,
  with the uploader told plainly what was indexed.
- Kept the reranking model on the processor deliberately, even though the card is faster. It runs on
  every question, and I hit a genuine out-of-memory error on the shared card during testing; a slow
  answer is recoverable, a failed one in front of a tester is not. Embedding also falls back to the
  processor automatically rather than failing an upload.
- Wrote the integration contract for Dhruv's ERP workstream and worked through his answers. The
  question he raised — how the system chooses between ERP and the documents when both describe the
  same subjects — resolves once you stop routing on subject matter and route on what is being asked
  for: the documents hold what is written down, the ERP holds what is currently recorded.
  Deliberately built no ERP routing, since there is no data yet and a half-connected integration
  returning a wrong figure is worse than saying the subject is not covered.

- Added the undo that had never existed: a document could be uploaded but never removed, so a file loaded
  to the wrong audience or in the wrong draft stayed answerable indefinitely, and correcting it meant editing
  three systems by hand. An administrator can now withdraw a document and every version of it. The vectors are
  removed before the database rows, so a failure part-way through leaves invisible orphaned rows rather than
  orphaned vectors that would keep being retrieved with nothing left to identify them. The original file is
  deliberately kept for audit.
- Gave the administrator account a chat login as well, having previously made it usable only through the API.
- Wrote the tester handbook and a separate administrator guide, and turned off per-chat file attachment in the
  chat interface before the testers started. The paperclip was still visible, but attachments never reach our
  assistant, so a tester would have attached a spreadsheet, asked about it, received nothing, and reasonably
  concluded the system was broken.
- Declined to build per-chat upload today despite it being wanted, and recorded why: pilot users currently
  resolve to full access, so the retrieval filter is active-version-only and a session-scoped document would be
  visible to every tester. It needs the access filter changed, which is not a change to make while six people
  are live on it.

**Week outcome:** the assistant now decides for itself, per message, whether to answer from the
company's documents or from general knowledge, understands misspelled questions against whatever
documents are loaded, accepts every common Office format, and ingests a five-megabyte twenty-sheet
spreadsheet in under a minute. Automated coverage grew from 10 tests at the start of the week to 75,
plus a 22-check live regression suite. The release to the six testers was moved to the week of
2026-08-17, so the extra time goes into loading their real documents and re-measuring the routing
thresholds against them.

---

## Rolling monthly summary — August 2026

**Month 2 of the internship (Security, Coordination, Hardening & Performance Tuning).**

Work completed so far this month:

- **Automatic query routing (Aug 11–14).** The assistant previously sent every message through
  document search, so any question the documents did not cover — including greetings — returned "I
  couldn't find this in the available documents". It now runs retrieval first and uses the relevance
  score of the best passage found to decide between a document-grounded answer with citations, a
  blended answer that marks what came from general knowledge, and a plain general answer. Users never
  select a mode. Thresholds were measured against the real corpus rather than guessed, and the change
  ships behind a one-variable rollback.
- **Two retrieval defects repaired (Aug 12).** Rank fusion was starving the reranker of candidates,
  and keyword search was dropping out of the hybrid merge for most real questions because of
  punctuation handling. Both were degrading answer quality independently of the routing work and had
  gone unnoticed because neither produces an error.
- **Frontend integration hardened (Aug 12).** The chat frontend's own background requests were
  consuming the retrieval pipeline; they are now handled locally.
- **Typo tolerance (Aug 13).** Misspelled questions about company documents were being answered from
  general knowledge, which reads to a user as the assistant not knowing its own documents. Correction
  now runs in layers before searching and is drawn only from the vocabulary of whatever documents are
  currently loaded, rebuilt on every upload — so it follows new content with no code change. All
  twelve misspelled test questions now score identically to their correctly spelled versions, and
  correctly spelled general questions are untouched.
- **User accounts and authentication reworked (Aug 13).** The chat integration had been logging in as
  each user with one password written into the source code, so a user changing their own password
  broke their own chat. It now uses a service credential and never handles user passwords. Account
  creation, self-service password change and administrator reset were added — none existed before —
  and six pilot testers were provisioned.
- **Document format support (Aug 14).** Macro-enabled Excel was not accepted at all, and both
  pre-2007 Office formats were listed as supported while failing on every file. Added readers for
  those, plus plain text, Markdown and CSV, with file type decided by inspecting the file itself
  rather than trusting its name.
- **Ingestion moved onto the GPU (Aug 14).** The container had the right software but had never been
  granted access to the graphics card, so embedding ran on the processor. A five-megabyte,
  twenty-sheet spreadsheet went from roughly an hour to 58 seconds, while indexing twice as much of
  it.
- **Test coverage expanded (Aug 12–14).** From 10 unit tests to 75, plus a live regression suite that
  grew from 17 checks to 22. This directly advances the month's "expand test coverage beyond the
  smoke test" objective, and it demonstrated its value immediately by catching a defect in the
  routing fix itself.
- **Enterprise integration contract agreed with Dhruv (Aug 14).** Advances the month's "coordinate
  the integration contract with Dhruv's enterprise-adapter workstream" objective. The contract and
  his answers are written up; no ERP routing was built, deliberately, since there is no data yet.

Planned for the week of 2026-08-17: releasing to the six testers (moved from 2026-08-14), loading their
real documents and re-measuring the routing thresholds against them, per-chat file upload with session-scoped
retrieval, and moving document upload into the chat interface so it no longer runs through the API
documentation page.

Still outstanding for the month: prompt-injection and adversarial security testing against the live
pipeline; the RAGAS evaluation baseline (which must be run *after* the routing change, as figures
measured under the old always-search behaviour would not be valid); enabling real per-user access
restrictions once HR provides department and designation data; repairing tool-calling for chart and
export generation; and per-user permissions on the ERP side before any of that data is exposed.
