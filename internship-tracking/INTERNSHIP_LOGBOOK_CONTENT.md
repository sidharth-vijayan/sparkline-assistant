# Internship Logbook — Content Source

**Student:** Sidharth Vijayan
**Company:** Sparkline (Sparkline Equipments Pvt. Ltd.)
**Project:** On-Premise Internal AI Assistant — Local LLM + RAG System
**Internship start:** Wed 8 July 2026
**Content current as of:** Tue 18 August 2026

> Working days Mon–Fri. The **"Comments by Company Supervisor"** column and all
> signature rows are left blank — those are for the supervisor to complete.
>
> **Scope of this file.** Weeks 1–6 (08/07 – 14/08) and the first Periodic
> Progress Report were submitted to the university on 11 August 2026 and have
> been cleared from this working copy. Cumulative progress stood at **38%** at
> the end of Week 6, and the figures below continue from that baseline.
>
> **On the progress percentages:** these are measured against the *full* project
> scope, not the pilot alone. The current build targets the existing 16 GB GPU
> machine; the organisation intends to procure a dedicated server, after which the
> entire system must be migrated, re-validated and tuned on that hardware. Several
> capabilities (structured-data integration, file analysis, injection defences) also
> remain outstanding.

---

## WEEK 7 — Dates: From 17/08/2026 To 21/08/2026

*(Entries complete up to Tuesday 18/08; the remainder of the week is in progress.)*

| Day | Tasks/Activities planned | Tasks/Activities completed |
|---|---|---|
| **Monday 17/08** | — | — |
| **Tuesday 18/08** | Move development off the shared GPU machine now that a second team's workload is destabilising it, and replace the code-tuned language model with one better suited to the policy and HR prose the assistant is actually asked about. | Replaced the code-tuned model with the general instruction-tuned model of the same size, on the reasoning that a model specialised for source code is the wrong instrument for questions about leave policy and site procedure. Removed the 20B model rejected earlier in the month for returning empty responses, recovering 15 GB. Established that the older code-tuned model cannot simply be deleted alongside it: a pre-existing company-wide tool used by several colleagues is registered against that exact model, so it is shared infrastructure rather than a leftover. Since a second model must therefore stay installed permanently, capped the model server to one *resident* model at a time — which converts a permanent 9 GB claim on a contended 16 GB card into memory that is only held while actually in use. Separately, replaced the remote-desktop workflow with an SSH tunnel: the laptop now acts purely as an editor while all computation stays on the server, which removes an entire desktop session from a machine that was running out of memory. Finally, wrote up the operational knowledge that the code does not reveal — that the model runs on a host service shared with another team's tool rather than in our own container, that the live frontend pipeline is stored in a database rather than in the repository, and that changing configuration requires the container to be recreated rather than restarted, each of which fails silently rather than raising an error. Closed two security gaps found while doing so: the administrator account's password was a literal default in the source code and therefore in the public repository, so it was rotated and the default removed in favour of a prompt; and testing from the laptop showed that the container platform publishes ports past the host firewall, leaving the databases reachable from the office network with the vector store answering unauthenticated callers. |
| **Wednesday 19/08** | Build session-scoped access control for per-chat file uploads, so that a document one user attaches to a conversation cannot be retrieved by anyone else. | *(planned)* |
| **Thursday 20/08** | Ingest the pilot testers' own documents and re-measure the retrieval confidence thresholds against that real corpus. | *(planned)* |
| **Friday 21/08** | Release the assistant to the six pilot testers and begin collecting their questions as the first real calibration signal. | *(planned)* |

**Learnings in this week:** *(to be completed Friday)*

**Cumulative Progress till date (% of total Work):** *(to be completed Friday)*

**Plan for next week:** *(to be completed Friday)*
