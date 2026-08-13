# Enterprise adapter contract — how a question gets routed

**For: Dhruv** · Updated 2026-08-13 with Dhruv's answers · Status: contract only, nothing wired in ·
Code: [`agents/enterprise_agent_interface.py`](agents/enterprise_agent_interface.py)

---

## The problem you raised

A lot of what sits in ERP is also described in the documents — the same customers, the same
invoicing, the same processes. So how does the assistant know which to answer from?

The honest answer is that **it cannot be decided from what the question is about.** Both sources are
about the same things. That is a property of the data, not a gap in the keyword list, and it gets
worse with every system connected. Any rule of the form "if the question mentions *invoice*, use
ERP" is wrong about half the time, because "what is our invoice approval process" is a document
question and "what was invoiced on SL3012627000486" is not.

## What does separate them

What the question asks **for**:

| | answers | example |
|---|---|---|
| **Documents** | what is *written down* — policy, process, definitions | "What is our invoice approval process?" |
| **ERP** | what is *recorded* — transactions, documents, master data | "How many purchase orders were raised in June?" |

Note this is deliberately **"what is recorded"**, not "what is currently true". Your views record
*movements, not balances* — so the ERP answers "what was invoiced" and cannot answer "what is owed".
That distinction runs through everything below.

---

## The contract

### 1. The adapter decides, not the orchestrator

The orchestrator cannot see inside ERP. It does not know which invoices or vendors exist, so any
judgement it makes about ownership is a guess about data it has never seen. You know.

`can_handle(query) -> bool` has been replaced, because a boolean cannot be compared against the
document retrieval score. Instead:

```python
async def assess(self, query: str, user_context: UserContext) -> Coverage: ...
```

```python
@dataclass(frozen=True)
class Coverage:
    score: float                    # 0.0 = not mine, 1.0 = certainly mine
    reason: str                     # shown in logs when a route is questioned
    question_kind: QuestionKind
    entities: tuple[str, ...] = ()  # records recognised, e.g. ("SL3012627000486",)
```

Rules: **cheap and side-effect free** (it runs before anything is decided — checking an identifier is
fine, running the report is not); **never raises** (an erroring adapter is treated as declining, so a
broken integration degrades to documents-only rather than breaking the chat); **`score=0.0` when it
is not yours**.

### 2. A named record wins

An identifier you can resolve means the question is yours, score `1.0`. Documents describe
*categories*; only a system holds the *instances*.

### 3. Declared gaps are refused, not guessed

The 13 subjects the ERP cannot answer are refused explicitly and **not** silently handed to the
documents. A document describing how stock is managed is not an answer to "how much stock do we
have" — answering it approximately is worse than saying we do not cover it.

### 4. Every answer names its source

Routing is invisible to the user, so the answer must say whether a figure came from ERP or a named
document. A wrong route then shows up as a visible wrong source instead of an untraceable number.
Your `/ask` already returns the view and the SQL used — we will log both.

---

## What your answers settled

**Integration point.** You are not an MCP server and do not need to be — `POST /ask` returning
`{answer, view, sql}` is a perfectly good adapter boundary. We will write a thin `ERPAdapter` on our
side that implements `assess()` **locally** (from the entity list, identifier prefixes and declared
gaps below — no network call, microseconds) and only calls your `/ask` in `handle()`, once routing
has already decided. That keeps your 12–35s off every unrelated question.

**Identifier formats — no separators.** Recorded and implemented:

| kind | examples | detection |
|---|---|---|
| transactions | `SL3012627000486`, `HSS202627000001`, `PO2032526000003`, `SRAMD2627000017`, `SODCR2425000131` | prefix + FY + serial, longest prefix matched first |
| vendor / customer | `F0045`, `S0226`, `A0001` | letter + 4 digits |
| GL account | `11016101F0045` | 13 chars, party code embedded |
| **item** | `BD0001543`, `PLXX8H0101`, `13W310002301LIM00Z` | **no usable pattern — not detectable** |

Item codes are a declared limitation: they cannot be recognised from the text, so item questions have
to be routed on phrasing alone. Party codes are detected but **are not unique** (3,183 of 5,584 rows
share a code) — the real key is `(party status, party code)`, so a detected code is a routing hint,
never an identification.

**Entities owned:** purchase invoices & GRNs, sales invoices & credit notes, sales orders & delivery
schedules, purchase orders, vendor/customer master, item master, GL postings, payments & receipts,
GST tax lines.

**Declared gaps (refused):** stock on hand, outstanding/ageing/receivables, BOM, fixed assets, QC
results, TDS, proforma invoices, price list.

**Read-only:** confirmed, and the `nl2sql_app` login has no insert/update/delete anywhere. Good.

---

## What is still open

### 1. Per-user permissions — the blocker

ERP is reached over **one shared connection**, so today the adapter answers every question with the
same rights regardless of who asked. Our side enforces per-user access on documents; wiring ERP in
as-is would mean any pilot user can ask about any sales invoice, any vendor, any GL posting. The
document permissions would still work and would no longer mean anything.

This does not block building or shadow-testing. It blocks **exposing ERP answers to anyone whose
ERP rights are narrower than the shared connection's.** Two ways forward, either is fine:

- your planned per-user RBAC app, with a user identity passed on every `/ask`; or
- an interim allow-list: ERP answers enabled only for users we have confirmed may see everything in
  those 15 views.

We need to agree which before it goes in front of anyone.

### 2. A total deadline

12s single-view and 23–35s chained is already long for a chat box, and a pathological case running
into minutes is worse than an error. Agreed on a hard client-side timeout treated as a decline —
**we will set 45s, not 60s**, because our own retrieval and answer generation sit on top of yours.
Your ~10-line total-budget fix on the server side is still worth doing.

### 3. Test data — this is the thing to hand over first

Cloning the read-only `nl2sql_app` login against `SEPLTESTDB` is the five-minute job that unblocks
everything else. Please do that one first.

**Caveat to carry into the pilot:** `SEPLTESTDB` data ends **2026-07-02**. Any "last month" or
"latest" question will answer as of early July and look simply wrong to a user in August. Until it
points at live data, ERP answers must be labelled with the data-as-of date.

---

## Suggested order of work

1. **Read-only sandbox login** against `SEPLTESTDB` → hand to us.
2. **We build the adapter** — `assess()` locally, `handle()` calling `/ask`. No dependency on you.
3. **Shadow mode.** We run `assess()` on every real question and **log what it would have done**,
   while still answering from documents. Zero user risk, and after a week we have real evidence on
   how often ERP would have claimed a question and whether it was right. This is how the thresholds
   get set from measurement instead of guesswork.
4. **Resolve per-user permissions** (§1 above).
5. **Enable for a small group**, watching the source labels.

## Acceptance bar for the first milestone

| question | must go to | why it is the test |
|---|---|---|
| "what was invoiced on SL3012627000486" | ERP | named record |
| "what is our invoice approval process" | documents | same topic word, opposite answer |
| "how many purchase orders were raised in June" | ERP | transaction aggregate |
| "how much stock do we have" | **refused** | declared gap, must not fall through to documents |
| anything, with ERP switched off | documents, no error | degrades safely |

Rows 1 and 2 both contain "invoice" and must go different ways. Row 4 is the one most likely to be
got wrong, because refusing takes deliberate effort and guessing does not.
