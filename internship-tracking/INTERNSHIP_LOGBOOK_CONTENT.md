# Internship Logbook — Content Source

**Student:** Sidharth Vijayan
**Company:** Sparkline (Sparkline Equipments Pvt. Ltd.)
**Project:** On-Premise Internal AI Assistant — Local LLM + RAG System
**Internship start:** Wed 8 July 2026
**Content current as of:** Tue 11 August 2026

> Working days Mon–Fri. The **"Comments by Company Supervisor"** column and all
> signature rows are left blank — those are for the supervisor to complete.
>
> **On the progress percentages:** these are measured against the *full* project
> scope, not the pilot alone. The current build targets the existing 16 GB GPU
> machine; the organisation intends to procure a dedicated server, after which the
> entire system must be migrated, re-validated and tuned on that hardware. Several
> capabilities (structured-data integration, file analysis, injection defences) also
> remain outstanding. 38% at the end of the first month reflects that honestly.

---

## WEEK 1 — Dates: From 08/07/2026 To 10/07/2026

*(Internship commenced Wednesday; Monday and Tuesday fall before the start date.)*

| Day | Tasks/Activities planned | Tasks/Activities completed |
|---|---|---|
| **Monday** | — | — |
| **Tuesday** | — | — |
| **Wednesday 08/07** | Complete joining formalities and understand what the organisation does and what problem the project is meant to solve. | Attended the induction and organisational orientation. Met the Technical Head, Shruti ma'am, who explained the motivation behind the internal AI assistant — employees currently cannot get answers from company data without going through the people who own it — along with the confidentiality expectations governing that data and the reporting structure for the internship. |
| **Thursday 09/07** | Read the architecture documentation and understand the overall system design. | Worked through the Architecture Guide. Understood the six-layer flow a question travels through, and more importantly why the document fixes certain principles as non-negotiable while deliberately leaving tool choices open — the constraints (data never leaving the network, permissions enforced before the model, every answer traceable) are what actually determine the design. Noted that the structured-data layer belongs to a colleague, Dhruv, and interfaces with my layers through a defined contract. |
| **Friday 10/07** | Establish what falls within my scope and how to approach it. | Mapped the nine target capabilities against the layers I own, and confirmed my scope as the chat frontend and identity, agent orchestration, document Q&A, file analysis, the writing assistant, the self-hosted LLM infrastructure, application-side access control and the accuracy test harness. Rather than starting to code immediately, listed out the seven areas requiring investigation first, since several decisions — particularly the model and serving runtime — are constrained by hardware I had not yet assessed. |

**Learnings in this week:**
Understood that an enterprise AI system is shaped far more by its constraints than by its model. The requirements that data must stay on the network, that access control must be applied before the model sees anything, and that every answer must be defensible were what dictated the architecture; the choice of model turned out to be one of the less consequential decisions. Also learned the usefulness of an architecture document that fixes principles but leaves implementation open — it made clear what I was accountable for without prescribing how.

**Cumulative Progress till date (% of total Work):** 2%

**Plan for next week:**
Investigate how to serve a language model efficiently on the available 16 GB GPU, research the document retrieval architecture, decide the technology stack with reasoning, and get the development environment running.

---

## WEEK 2 — Dates: From 13/07/2026 To 17/07/2026

| Day | Tasks/Activities planned | Tasks/Activities completed |
|---|---|---|
| **Monday 13/07** | Work out what size of model can realistically run on a 16 GB GPU. | Studied quantisation formats and the trade-off they represent between memory footprint and answer quality, comparing Q4_K_M, Q3_K_M and Q5 variants. Worked through how the KV cache grows with context length and how concurrent users multiply that consumption — which was the point at which it became clear that the usable model size is considerably smaller than the raw card capacity suggests, since the cache and overhead must fit alongside the weights. |
| **Tuesday 14/07** | Compare the available serving runtimes and decide how the rest of the system should talk to the model. | Compared Ollama and vLLM — the former simpler for local development with good GGUF support, the latter stronger on throughput under concurrency. Rather than committing to one, noted that both expose an OpenAI-compatible interface, and concluded the more important decision was architectural: every component should reach the model only through that single standard endpoint, so the runtime can be replaced later by changing configuration rather than code. |
| **Wednesday 15/07** | Understand how document question-answering pipelines are actually built. | Studied the full pipeline — parsing, chunking, embedding, vector storage, search, re-ranking and citation. The most significant realisation was that answer quality is governed by retrieval rather than by model size, because no model can use a passage that was never fetched. This reframed my priorities for the build. Selected BAAI/bge-large-en as the embedding model and decided on token-aware chunking with overlap so that meaning is not lost at chunk boundaries. |
| **Thursday 16/07** | Decide how search should work and which vector database to use. | Studied why semantic and keyword search fail in different ways — the former missing exact terms like part numbers, the latter missing paraphrasing — and how Reciprocal Rank Fusion combines their rankings without requiring their scores to be comparable. Studied cross-encoder re-ranking as a precision pass over the fused results. Compared vector databases and chose Qdrant, mainly because its payload filtering lets permission rules be applied inside the search query itself, which I judged would matter a great deal for the access-control requirement. |
| **Friday 17/07** | Get the development environment working and design the database structure. | Installed and configured the toolchain — Python 3.11, Poetry, Docker Desktop and Tesseract for scanned documents. Designed the database schema on paper before writing any code: users, documents, document versions, chunks and an audit log. Deliberately designed versioning to be non-destructive, so that a re-uploaded document supersedes rather than replaces its predecessor, since an audit trail is meaningless if old versions are deleted. |

**Learnings in this week:**
The single most useful insight was that retrieval precision, not model size, limits the accuracy of this kind of system — which redirected my effort towards hybrid search and re-ranking instead of towards fitting a larger model onto constrained hardware. I also learned that putting the model behind one standard interface is an architectural choice rather than a convenience, since it is what makes the serving runtime replaceable later. Finally, choosing a database on the basis of a secondary feature — Qdrant's payload filtering — taught me to select tools against the hardest requirement rather than the most obvious one.

**Cumulative Progress till date (% of total Work):** 6%

**Plan for next week:**
Begin implementation: project scaffold and configuration, database models and infrastructure wrappers, the document ingestion pipeline, retrieval, access control, the agents and the API gateway.

---

## WEEK 3 — Dates: From 20/07/2026 To 24/07/2026

| Day | Tasks/Activities planned | Tasks/Activities completed |
|---|---|---|
| **Monday 20/07** | Set up the project structure, containerisation and a configuration approach. | Initialised the repository and assembled the dependency set in `pyproject.toml` — the web framework, async database layer, vector database client, embedding library, document parsers and authentication libraries. Wrote the `Dockerfile` and composed an eight-service stack covering PostgreSQL, Redis, MinIO, Qdrant, the model server, the API and the chat frontend, with named volumes so data survives container restarts. Built `config/settings.py` as a single settings loader, taking the decision early that no host, port, model name or device would ever be hard-coded — a decision that paid off repeatedly later. |
| **Tuesday 21/07** | Implement the database layer and wrappers around the infrastructure services. | Implemented the five database tables — users, documents, document versions, chunks and audit log — with the versioning relationships designed the previous week, plus an initialisation script that creates the schema and seeds the pilot users with hashed passwords. Wrote six service wrappers so that the rest of the codebase never touches an external service directly: PostgreSQL sessions, Redis for conversation state with expiry, MinIO for permanent file retention, Qdrant for filtered vector search, the embedding service with a configurable device, and the single LLM client. |
| **Wednesday 22/07** | Build the document ingestion pipeline and the permission-checking layer. | Implemented four parsers — PDF preserving page numbers, DOCX, Excel, and OCR for scanned documents — followed by a chunker producing 400-token chunks with 80-token overlap while carrying page provenance through, an embedder writing into the vector store, a keyword index builder, and a pipeline tying the stages together. Then implemented the access-control layer in two parts: a decision point that evaluates who the user is against what they are asking for, and an enforcement point that converts that decision into a search filter. Structuring it this way means restricted content is never retrieved at all, rather than being retrieved and then hidden. |
| **Thursday 23/07** | Complete the retrieval pipeline, the agents and the API layer, and commit the initial codebase. | Implemented hybrid retrieval combining keyword and semantic search through Reciprocal Rank Fusion, cross-encoder re-ranking, and a citation builder recording document name, page number, version date and relevance score for every source used. Implemented the query router, the tool layer including a sandboxed code executor for charts and exports, and the agent layer — a general agent, the document RAG agent implementing the full nine-step flow, and an abstract interface defining how Dhruv's data adapters will plug in. Completed the API gateway with authentication and audit middleware and the chat, ingestion and admin routes, then added the ingestion CLI, the chat-frontend integration and the evaluation harness. Committed the initial codebase — 66 commits. |
| **Friday 24/07** | Tidy up the initialisation script and get the dependency set stable. | Refactored the database initialisation so it can be re-run safely without duplicating seeded users. Spent longer than expected resolving version conflicts across the dependency tree — the machine-learning libraries and the web framework had incompatible transitive requirements — and regenerated the lock file once a working combination was found. Extended the smoke tests to cover the service wrappers and the end-to-end request path. |

**Learnings in this week:**
Implementing the permission layer as a search filter rather than as an output filter taught me that the ordering of security controls matters as much as their existence — content that is never retrieved cannot leak, whereas content retrieved and then suppressed depends on every downstream path behaving correctly. I also learned how much strict configuration discipline is worth: because every device and address is environment-driven, the same code later ran unchanged on a laptop and on a GPU server. Writing the interface for a colleague's component before that component existed was a useful exercise in making an integration boundary explicit rather than assumed.

**Cumulative Progress till date (% of total Work):** 18%

**Plan for next week:**
Bring the container stack up, run the ingestion pipeline against real documents, and fix whatever fails. Then verify retrieval quality and confirm the access-control layer behaves correctly.

---

## WEEK 4 — Dates: From 27/07/2026 To 31/07/2026

| Day | Tasks/Activities planned | Tasks/Activities completed |
|---|---|---|
| **Monday 27/07** | Bring the full container stack up and run the first document ingestion. | The stack did not come up, and the entire day went on getting it to. PostgreSQL and Qdrant sat in restart loops because the healthcheck commands I had written were not valid inside those images, so the containers were being killed before they finished initialising — and because the API depended on them, its failure looked like an application problem rather than a container one. Once that was traced, the API still could not reach the services: I had used `localhost` in the configuration, which inside a container refers to the container itself rather than to the other services, and had to be replaced with the Compose service names. Further time went on a volume permission problem and a failing image build. By the end of the day the stack was running and stable, but no ingestion had been attempted — that work slipped to Tuesday. |
| **Tuesday 28/07** | Run the first real ingestion and resolve whatever it exposes. | The ingestion failed in four distinct ways, each of which I traced to its origin rather than patching where it surfaced. The database schema would not build at all, due to a circular foreign-key dependency between documents and their versions — resolved by deferring one side of the relationship to a second write. Once past that, inserts failed on a foreign-key violation because child rows were being written before the parent's key existed, which required explicit flushes at the right points in the transaction. Separately, the container healthchecks were still writing fatal errors into the logs and were replaced with checks appropriate to each service. Finally, and least visibly, the `--public` flag on the ingestion CLI was being parsed incorrectly, so documents intended to be readable by everyone were being stored as restricted — a silent fault rather than an error. All four fixes were committed separately. |
| **Wednesday 29/07** | Confirm the ingestion pipeline works correctly end to end. | Ran documents through the complete flow and verified each stage: parsing, chunking, embedding, vector upsert and keyword index rebuild, with the original file retained in object storage. Then tested the versioning behaviour specifically by re-uploading a modified document, confirming that the previous file is preserved permanently for audit while its chunks are deactivated, and that only the current version is retrievable. |
| **Thursday 30/07** | Check whether retrieval actually returns the right passages. | Tested the retrieval pipeline against questions with known answers. Confirmed that keyword and semantic search do return meaningfully different candidates, that the fusion step merges them sensibly, and that re-ranking improves the ordering of the final passages rather than merely reshuffling them. Verified that citations carry the document name, page number, version date and relevance score, so an answer can be checked against its source. |
| **Friday 31/07** | Verify that the access-control layer holds under the conditions that matter. | Tested the permission layer's behaviour at its boundaries rather than only in the ordinary case: confirmed it denies by default when a user's permitted scope cannot be determined, that the enforcement filter always restricts results to the active document version, and that every query — allowed or denied — writes an audit entry recording the user, the question, the agent used, the decision and the response time. |

**Learnings in this week:**
This was the most instructive week of the internship, and the least comfortable. Losing an entire day to the container stack taught me that infrastructure problems disguise themselves as application problems — the API appearing broken when the real cause was a healthcheck killing its dependencies, and the networking failure stemming from an assumption about `localhost` that is simply untrue inside a container. The following day reinforced the same lesson at the code level: each of the four defects presented as a vague symptom, and in every case the fix only became clear once the failure was traced to its origin instead of patched where it appeared. The most valuable single lesson was that silent misbehaviour is more dangerous than a crash — the flag parsing bug caused documents to be over-restricted without any error at all, which in production would have been extremely difficult to notice.

**Cumulative Progress till date (% of total Work):** 25%

**Plan for next week:**
Integrate the chat frontend with streaming responses, prepare the system for deployment on the shared GPU server, and demonstrate the working system to management.

---

## WEEK 5 — Dates: From 03/08/2026 To 07/08/2026

| Day | Tasks/Activities planned | Tasks/Activities completed |
|---|---|---|
| **Monday 03/08** | Decide which chat interface to use for the user-facing layer. | Assessed the self-hosted chat interface options against what Layer 1 actually requires — multiple users, file upload, authentication, and crucially the ability to send requests to our own backend rather than straight to a model. Selected Open WebUI and studied its extension mechanism to work out how to intercept requests, so that orchestration, permission checks and retrieval all remain server-side where they can be controlled, rather than being delegated to the frontend. |
| **Tuesday 04/08** | Connect the chat interface to the backend and make responses stream. | Added the frontend to the container stack and replaced its healthcheck with a shell-based TCP check that works consistently across the images in use. While integrating, found that the vector search was calling a deprecated client method, and migrated it to the current API before it could break on the next upgrade. Implemented the integration as a Pipe function with a streaming generator so answers appear progressively instead of after a long silence, and added automatic authentication-token refresh after realising that longer conversations would otherwise fail partway through. Seeded an additional pilot user for testing. |
| **Wednesday 05/08** | Test the whole system through the interface as a user would. | Worked through the complete path from browser to pipeline to gateway to agent and back. Verified streaming output, that multi-turn conversations retain context through server-side session state, that citations display correctly against answers, and that the token refresh happens without the user noticing. Testing as a user rather than through the API surfaced presentation issues that the earlier endpoint testing had not. |
| **Thursday 06/08** | Get the system ready to deploy onto the shared GPU server. | Diagnosed and repaired a broken image build along with the container networking that depended on it. Wrote a deployment override for the shared machine, which was needed because of a subtlety I had not anticipated: the port variables serve double duty as both the externally published ports and the addresses the application dials internally, so changing them to avoid clashes with other tenants also broke the application's internal connections. The override pins the internal ports back to their real values. Also wrote a full deployment runbook documenting the shared-machine constraints and the update workflow — development happens on a separate machine and the server only pulls, with the repository mounted so most changes need no rebuild. |
| **Friday 07/08** | Deploy the system onto the shared GPU server and demonstrate it to management. | Deployed the full stack to the shared GPU server running Ubuntu 24.04 with an RTX 5060 Ti. Mapped every published port into a free range to avoid colliding with the other teams already running on the machine, and configured the system to use the server's existing native model server rather than the containerised one — it already held the model data and had direct GPU access, so this avoided a redundant multi-gigabyte download. Seeded the database and ingested demonstration documents. Presented the system to the Director, Siddharth Doshi, with a live demonstration of document question-answering with citations, an explanation of how permissions are enforced before data reaches the model, and a walkthrough of the layered architecture and what remains to be built. |

**Learnings in this week:**
Deploying onto a machine shared with other teams was the most instructive part of the week: ports, GPU memory and even the home directory had to be treated as contended resources rather than as freely available, which is not something local development prepares you for. The port variable problem in particular taught me that a configuration value serving two purposes is a latent bug waiting for the two purposes to diverge. Writing the runbook taught me that documentation is only valuable if it records the non-obvious failures — the settings that fail silently are exactly the ones worth writing down. Presenting to the Director taught me to explain architecture in terms of the guarantees it provides to the business rather than in technical terms.

**Cumulative Progress till date (% of total Work):** 34%

**Plan for next week:**
Verify how the system behaves on the server under realistic use, measure and address the GPU memory constraint, and investigate the issues that real usage exposes.

---

## WEEK 6 — Dates: From 10/08/2026 To 14/08/2026

*(Entries complete up to Tuesday 11/08; the remainder of the week is planned work.)*

| Day | Tasks/Activities planned | Tasks/Activities completed |
|---|---|---|
| **Monday 10/08** | Check the deployed system is behaving correctly and confirm the model is genuinely using the GPU. | Verified the deployment and then measured what was actually happening on the GPU, which proved worthwhile — the configured model needs roughly 14.0 GiB while only 12.9 GiB was free, because another team's process was holding 3.0 GiB of the 15.9 GiB card. The important finding was that the model server does not report an error in this situation; it silently moves the excess onto the CPU, so the symptom is slow generation rather than a failure. Confirmed the actual placement using the runtime's own status output and the GPU utilisation tool rather than relying on the logs, then documented the options in order of preference — reclaiming the memory held by the other tenant, using a smaller quantisation, or falling back to a smaller model already present on the machine. |
| **Tuesday 11/08** | Find out why general questions on the deployed system are answered with "not found in the documents". | Traced the request path from the frontend through the gateway to the router and found the cause: the general-query intent has no entry in the classifier's pattern table, and the classifier falls back to the document intent, which means the general-purpose agent can never be reached — every question in the system was being sent to document search. Before proposing a fix, checked whether simply correcting the fallback would be sufficient and established that it would not, because the document intent's keyword list contains phrases such as "what is the" that match general and document questions equally well. Designed a solution based on retrieval confidence instead — falling back to the general agent when the re-ranking score shows the documents hold nothing relevant — and wrote it up as an implementation plan. |
| **Wednesday 12/08** | Implement the routing fix and separate model-specific prompt handling into its own layer. | *(planned)* |
| **Thursday 13/08** | Add safeguards so that document and uploaded-file content cannot issue instructions to the model. | *(planned)* |
| **Friday 14/08** | Calibrate the retrieval confidence threshold against the golden question set and check for regressions. | *(planned)* |

**Learnings in this week:**
Both problems found this week failed silently rather than loudly — the model quietly running on the CPU instead of refusing to load, and every question being routed to document search instead of raising an error — and neither was visible from the logs. Each required either direct measurement or reading the code path to find. The second lesson was that the obvious fix is often the wrong one: correcting the routing fallback would have appeared to work while leaving the real problem in place, because keyword matching fundamentally cannot separate a policy question from a general one when both are worded the same way. Taking the time to establish that before writing code prevented a fix that would have failed later in a more confusing way.

**Cumulative Progress till date (% of total Work):** 38%

**Plan for next week:**
Implement the routing fix and calibrate its threshold against the golden question set; separate model-specific prompt handling into a single layer; add safeguards so retrieved and uploaded content is always treated as data rather than as instructions; and begin integrating the structured-data tools once the interface contract is settled.

---

# PERIODIC PROGRESS REPORT — I

**(After first month)** — Day & Date: Tuesday, 11 August 2026

### Tasks performed in first month

Designed and implemented the assigned portion of an on-premise internal AI assistant — the chat frontend integration, agent orchestration, document question-answering, access control, self-hosted model infrastructure and evaluation harness.

Began with a research phase covering how to serve a language model within a constrained GPU budget, how retrieval-augmented question answering is actually built, and which technologies suited the requirements. Implemented the system across roughly forty-five modules: a centralised configuration layer with no hard-coded values; a five-table database schema with non-destructive document versioning; six wrappers isolating the application from the infrastructure services; a document ingestion pipeline handling PDF, Word, Excel and scanned files, with token-aware chunking that preserves page references; a retrieval pipeline combining keyword and semantic search with re-ranking, producing answers accompanied by verifiable citations; an access-control layer that applies permissions as a search filter so restricted content is never retrieved; an agent and tool layer including a sandboxed code executor; and an API gateway with authentication and audit logging.

Subsequently integrated a multi-user chat interface with streaming responses, resolved the container and database defects that end-to-end testing exposed, containerised the system across eight services, and deployed it to the shared GPU server together with a deployment runbook.

### Important Meetings/Discussions attended in first month

- **08/07/2026** — Induction and project briefing with the Technical Head, **Shruti ma'am**: the objectives behind the internal AI assistant, the confidentiality requirements around company data, and the reporting structure for the internship.
- **09/07 – 10/07/2026** — Scope and architecture discussions establishing the division of ownership between the structured-data layer and the agent, retrieval and frontend layers, and confirming the engineering principles treated as non-negotiable.
- **Ongoing** — Technical coordination with **Dhruv** on the interface contract between the orchestrator and the enterprise data adapters — the request and response structures, and how the user's identity propagates into every data tool call so that permissions are enforced consistently on both sides.
- **07/08/2026** — **System demonstration and architecture presentation to the Director, Siddharth Doshi**, following deployment to the shared GPU server: a live demonstration of document question-answering with citations, an explanation of the access-control model, and a walkthrough of the layered architecture and the remaining roadmap.

### Learnings from work completed in first month

The strongest technical lesson was that in a retrieval-based system, answer quality is governed by retrieval precision rather than model size — a larger model cannot recover a passage that was never fetched. This shaped the decision to invest effort in hybrid search and re-ranking rather than in fitting a larger model onto limited hardware.

Architecturally, I learned the value of applying access control before the model rather than after it. Translating a permission decision into a search filter means restricted content is never retrieved at all, which is both more secure and easier to reason about than filtering a model's output afterwards.

I also learned how much disciplined configuration is worth. Because every address, device and model name is environment-driven, the same codebase ran unchanged on a development laptop and on a shared GPU server, and the model runtime can be replaced without touching application code.

On engineering practice, the debugging work taught me to trace a failure to its origin rather than patch it where it appears — a habit that repeatedly turned confusing symptoms into small, obvious fixes. Related to this, I learned that silently failing behaviour is considerably more dangerous than an outright error: a model quietly running on the CPU, or documents silently stored as restricted, present as mediocre performance rather than as faults, and can persist unnoticed.

Finally, presenting to management taught me to justify architectural decisions in terms of the guarantees they provide to the business — that data never leaves the network, that every answer can be traced to a source document, and that users see only what they are permitted to see.

### Cumulative Progress till date (% of total Work)

**38%.** The pilot system is implemented, integrated and deployed on the existing shared GPU machine, which represents the foundation rather than the finished project. The remaining work is substantial: the structured-data capabilities (ERP, CRM and analytical queries) depend on integration with the counterpart workstream; the file-analysis capability and its execution sandbox are incomplete; prompt-injection safeguards are not yet implemented; and the accuracy evaluation is not yet gating changes. Beyond that, the organisation intends to procure a dedicated server, after which the entire system must be migrated, re-validated and re-tuned on new hardware — with model selection and performance characteristics revisited against a GPU that is not shared with other teams.

### Plan till next Periodic Report submission

1. Implement the retrieval-confidence routing fix and calibrate its threshold against the golden question set, verifying no regression in document answer quality.
2. Separate model-specific prompt handling into a single prompt layer, and implement safeguards so that retrieved and uploaded content is always treated as data rather than as instructions.
3. Resolve the GPU memory constraint by benchmarking smaller quantisations and finalising model selection with measured throughput figures.
4. Integrate the structured-data tools once the interface contract with the counterpart workstream is settled, enabling the ERP, CRM and analytical query capabilities.
5. Complete the user-facing file-analysis capability and harden the execution sandbox so it runs without network or filesystem access.
6. Wire the accuracy evaluation into the change process so that every modification is checked against the golden question set before being accepted.
7. Prepare a migration plan for the dedicated server once procured, covering redeployment, data migration, re-validation and performance tuning on the new hardware.
