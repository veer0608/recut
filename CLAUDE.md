# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
.venv/Scripts/python -m pytest -q                          # whole suite, ~6s, no network
.venv/Scripts/python -m pytest tests/test_verify.py -q      # one file
.venv/Scripts/python -m pytest -k "intensity" -q            # one topic
.venv/Scripts/python -m pytest --durations=6 -q             # find a test that started hitting the network
```

```bash
.venv/Scripts/python -m recut run README.md --targets linkedin,thread
.venv/Scripts/python -m recut run "https://www.youtube.com/watch?v=ID" --targets all
.venv/Scripts/python -m recut run post.md --targets vidsmith --build   # straight to mp4
.venv/Scripts/python -m recut build                        # render approved drafts only
.venv/Scripts/python -m uvicorn recut.api:app --port 8078   # API + page at /
```

**`--fresh` clears only the sources the run will rewrite.** It used to clear the whole
directory before `--only` had narrowed anything, so `--fresh --only art-willison`
destroyed fourteen sources' stored bodies to regenerate one. Those bodies are what
`rejudge.py` and `measure_copying` read and they cost a full run to produce.

```bash
.venv/Scripts/python -m recut post                    # dry run: prints what would go to X
.venv/Scripts/python -m recut post --confirm          # actually publishes
.venv/Scripts/python -m recut post --draft ID --confirm
```

**`recut post` publishes approved drafts to X, and is a dry run unless `--confirm`.**
Posting is the only action in this product that cannot be taken back, so the default
prints the posts in order and stops. `recut/post.py` refuses on four separate grounds
before any request: the draft is not `approved`, the target is not `thread` (a linkedin
body is prose and a vidsmith draft is a directory), the body is empty, or a post is over
280 characters. That last one is re-checked here rather than trusted from generation,
because a body can be edited in the queue afterwards and a thread that fails halfway has
already published everything before the failure. `PartialThread` carries the ids that did
go out for exactly that case: losing them leaves posts on the account nothing can point
at. Credentials are the four OAuth 1.0a values (`X_API_KEY`, `X_API_SECRET`,
`X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`), read from `.env`, and `Credentials.__repr__`
is redacted so a traceback cannot write them to a log.

The queue already enforced the important half before any of this existed: `posted` is
reachable only from `approved`, and it is terminal. There is no undo for a published
post, so the state machine does not pretend there is one.

`recut build` renders drafts a human approved in the review queue and nothing else.
The draft carries the emitted files through the queue, which is what makes this possible
at all: before that column existed an approved vidsmith draft had nothing to render.
Rendering is the expensive half, a minute or two of compute plus vidsmith's own model
calls, so spending it at generation time spends it on drafts that get rejected. The
queue stores each artifact's emitted files for exactly this, and `ReviewQueue._migrate`
adds that column to databases that predate it, because `CREATE TABLE IF NOT EXISTS`
would otherwise leave an old queue silently dropping every project.

`--build` shells out to vidsmith's interpreter, found from `VIDSMITH_PYTHON`, then
`VIDSMITH_HOME`, then a sibling checkout. It is never imported: rendering drags in
ffmpeg and a speech engine, and the two repos are licensed separately. Exit 4 means
the text was written and verified and only the render failed.

```bash
.venv/Scripts/python eval/run_eval.py --run v3 --fresh --targets linkedin,thread   # 20-30 min, ~120 calls
.venv/Scripts/python eval/run_eval.py --run v3 --fresh --fresh-inventory           # re-extract too, when testing extraction
.venv/Scripts/python eval/tune_intensity.py                # re-derives rule precision from checkpoints, no calls
.venv/Scripts/python eval/measure_copying.py --runs v1,v2   # copying rate over stored bodies, no calls
.venv/Scripts/python eval/run_eval.py --run v6 --no-judge   # generate and score deterministically, ~a third the cost
.venv/Scripts/python eval/rejudge.py v1 --only art-ocr      # re-score stored bodies, judging only, no generation
```

Install: `python -m venv .venv && .venv/Scripts/python -m pip install -e ".[web,api,dev]"`, then copy
`.env.example` to `.env` and add a Gemini or Groq key. Either alone is enough.

**The claim inventory is cached, and that is half the budget.** Extraction was 49 of
v11's 98 calls, and it depends on the source, `extract.md`, `_faithfulness.md`, the model
ladder and `WINDOW_CHARS` -- not on generator prompts, verifier severities, targets or
`pipeline.py`. Four of the five runs on 2026-09-08 changed none of its inputs and paid
for all of it again. `cached_extract` keys on exactly those inputs under `eval/.cache/`,
so editing the extraction prompt invalidates every entry without anyone remembering to,
and `--fresh-inventory` forces a re-extract when extraction itself is what changed.

Eval-only, deliberately: in production every source is new, so this saves nothing there
and is not wired into `pipeline.repurpose`. The stored entry carries `_dropped` and
`_demoted_quotes` beside the `ClaimSet`, because those are diagnostics that do not
survive `model_dump` and would otherwise come back as zero.

## The one rule the whole design rests on

**Outputs are never generated from the raw source.** `extract.py` turns a source into a
`ClaimSet` where every claim cites the segment ids it came from, and every generator in
`recut/generate/` receives only `render_claims(...)` output. A generator cannot quote what it
was never shown. If you find yourself passing `document.text` into a generator prompt, the
product's only guarantee is gone.

Corollary in `extract.py`: a claim the model returns without a valid `segment_id` is
**dropped**, not repaired. Unanchored claims are the exact failure this exists to prevent.

**Second corollary: a `quote` claim with no `verbatim` is relabelled `opinion`.** A claim's
`text` is a restatement in the extractor's own words by construction, so a generator told
that a claim is a quotation will quote the restatement and attribute it to a named person.
`art-willison` published `IanCal argues that "the correct way to model data depends entirely
on the specific questions one intends to answer."` from a claim whose `verbatim` was null.
Telling the generator not to was tried first and made it **worse**: errors on that source
went 2 and 3 to 6 and 4, because a block about quoting mechanics increased quoting
generally. The label is the invitation, so the label goes, deterministically and in code.
It runs after the check that nulls a `verbatim` absent from the source, so an invented
exact-words span cannot keep the label either. Demoted rather than dropped: the claim is
anchored and its content is real, and dropping loses a true claim to fix a false badge.
`_demoted_quotes` carries the count, and the eval stores it as `demoted_quotes`.

**The corollary that took a day to find.** A generator never sees the source, so any
sentence it publishes word for word arrived through a claim. Copying was therefore
inherited, not authored, which is why an anti-copying paragraph added to the *generator*
prompts moved the rate from 80% to 77% and nothing else. Telling `extract.md` to restate
claim text in its own words took the inventory from 36% carrying an 8+ word verbatim run
to 4%, and the outputs from 77% to 37%.

`hook_candidates` was the same leak one level down: once claim text was clean, every
remaining copied run traced to a hook, which the prompt had asked to be "supported by the
source" without ever saying to write rather than lift. `voice_samples` are verbatim by
design and are tone reference; they were the obvious suspect and the measurement cleared
them.

## Architecture

```
ingest/ (markdown | article | youtube)  ->  Document
extract.py            one LLM pass per ~6k-char window  ->  ClaimSet
generate/<target>.py  one LLM call each, fed only the ClaimSet  ->  Artifact
verify.py             six deterministic checks, no model call
pipeline.py           one repair retry on errors, then surfaces what is left
align.py              maps each output sentence back to a claim, for the UI
review.py             a queue where a human approves before anything is published
jobs.py               a run is a job id and a poll, not a held-open request
api.py + web/         FastAPI over jobs.py and review.py, static page served at /
```

A run takes 20 to 60 seconds, which is longer than a browser will hold a request, so
`POST /api/jobs` returns an id and a background thread does the work. **SQLite is the
entire persistence layer** and one file, `recut.db` (override with `RECUT_DB`), backs both
`JobStore` and `ReviewQueue`. Each write opens its own connection, which is what makes it
safe across threads without a pool. There is no Celery, no Redis, and no Docker: Docker
cannot run on the dev machine, and a queue needing three services to repurpose a blog post
is a worse product than one needing none.

**Every ingest adapter guarantees the same invariant:**
`document.raw[segment.char_start:segment.char_end] == segment.text`. That is what lets the web
UI highlight a span of the real source. Any new adapter must hold it, and
`tests/test_ingest_adapters.py` asserts it across all three source types with one shared
fixture. Timed sources additionally carry `t_start`/`t_end`, which is why a claim from a video
can be shown as "14:32" for free.

**Targets** are modules in `recut/generate/`, registered in `pipeline.GENERATORS`. A module
sets `requires_timed = True` to be offered only for audio and video (`article` does, because
rewriting an article as an article is not repurposing). A target may emit files instead of
prose via `Artifact.files`; `vidsmith` emits a buildable project directory.

**What a target emits carries its own provenance.** `pipeline._carry_provenance` writes
`provenance.json` *inside* each emitted directory after verification, because the sidecar
written beside it does not travel and that directory is what becomes a published video.
The one output that reaches an audience was the one that could not be traced back.

**A local source is credited by its title, not its path.** `generate/vidsmith.credit_for`
puts the URL in `config.yaml`'s `source` for an article or video, and the document title
for a local file. vidsmith writes that string verbatim into a YouTube description, where
an absolute path off this laptop credits nothing and publishes a directory layout. The
comment on line one still names the real input, for whoever opens the file; the two are
separate arguments to `render_config` because they are separate jobs.

**Prompts live in `recut/prompts/*.md`**, loaded at runtime, not as Python strings. They change
more often than the code. The file name is the `load_prompt` argument, not the module name, and
the two are not always the same word: the `vidsmith` target loads `prompts/video.md`.
`{{faithfulness}}` includes the shared `_faithfulness.md` block, which is shared rather than
copied so one target cannot drift to a weaker standard.

**A tie goes to the repair.** `pipeline.py` keeps the retry unless it is worse on
error count. Counting alone treated every rule as interchangeable and kept a draft that
tied; the repair was written knowing what was wrong with the draft before it, so it wins
a tie. `repair_spans_changed` is **not** proof a fault was fixed: it differences span
strings, and a model rewords the same fault every regeneration, so the one invented
quotation below came back as three different spans across v6, v7 and v8. That guard is
therefore nearly always true and the rule reduces in practice to "no worse on count".
The field is named for what it observes. This paragraph used to claim the retry was
verified against the spans it was asked to clear, which the code never did.
Counting errors treated every rule as interchangeable and cost a real output:
`art-willison/linkedin` in v6 tied on count, so the first pass won, and the first pass
was the draft reading `IanCal argues that "the effectiveness of data modeling is
entirely dependent on..."` -- an invented direct quotation in a named person's mouth.
A rejected repair also **records that it ran**, on whichever draft survives. The flag
and the first-pass errors used to be written to the retry alone, so discarding the
retry discarded the evidence, and a refused repair was indistinguishable from one that
never happened.

## The verifier's severity contract

The six checks are `numbers`, `quotes`, `entities`, `intensity`, `copying` and `citations`.
`error` triggers exactly one regeneration with the offending span named; a second failure is
surfaced, never hidden. `notice` informs and costs nothing. Getting this wrong is expensive in
both directions, so severities here were set by measurement, not taste:

- **intensity** demotes `ensures?` and `critical` to notice. With them as errors the rule fired
  on 55% of artifacts the judge passed clean and only 47% of the ones it failed, which is no
  discrimination at all. Without them: 0% and 37%. See `eval/tune_intensity.py`.
- **entity** is an error only when a *word* of the name is absent from the source. A phrase
  that is absent but built from present words ("Western Roman") is a notice: a missing word is
  strong evidence of invention, a missing phrase is not.
- **copying** is an error since v6, and was a notice before it. 80% of artifacts across v1
  and v2 carried an 8+ word verbatim run; a gate there sends four drafts in five back for
  repair, which is an outage rather than a gate. After the `extract.md` fix: v4 37%, v6 23%,
  **v11 20%**, each over all fifteen sources. The three do not differ (v6 against v11 is
  `z=0.31, p=0.75`) and pre-fix against post-fix pooled is 80% to 27% (`z=6.41`). Articles
  stay the hard case at 38% against markdown's 12%.
- **Promoting it put copying in front of `inject.score`,** which counts a clean body raising
  any error as a false positive. Copying is never planted and a body that reproduced the
  source really did reproduce it, so the false-positive scorer excludes the rule by name.
  Without that, the metric would have risen every time the copying rule worked.
- **copying also needs `MIN_COPIED_PROSE` words that carry meaning** before it counts. The
  generator prompts require a figure to match its `stat` claim exactly, so a run of them drags
  its connecting words along: "from 9.0 to 5.6 and tokens from 13,024 to 8,576" is fourteen
  words and one of them is prose. Measured over 84 copied runs, a floor of four drops 5% of
  them and every one it drops is a figure recital; five starts taking real sentences. A
  disqualified run is skipped and the scan continues, so numbers cannot mask a copied
  sentence further along the body.

**What the repair retry buys is now measured, not assumed.** `_repair_stats` aggregates
`repair_detail` into attempted / kept / reduced / fully_cleared, plus `cleared_by_rule`
counted per error rather than per body. `kept` is the pipeline's own decision and a tie
satisfies it; `reduced` is the stricter question.

**The repair is not one mechanism working unevenly. It is two outcomes.** Over the first
four runs it clears `entity` 8 times in 11, and `quote` 1 in 7, `copying` 1 in 6,
`intensity` 2 in 7. Deleting an invented name is something a regeneration can do;
rewriting a sentence that copies, over-reaches or misquotes is the same model producing
the same phrasing from the same claims. An overall repair rate averages those into a
number describing neither, which is why the figure is per rule.

**v11 settled the copying severity, against a threshold written down before the run
reported.** The rule was: below about a third, keep copying an error but stop retrying on
it; above a third, the retry earns its call and nothing changes. At fifteen sources
copying clears **5 of 10, 50%**, so nothing changes.

The small-sample reading said 1 in 6 and pointed the other way. Nine repairs on two
article sources was not a basis for moving a severity, and pre-registering the threshold
is the only reason it did not quietly happen anyway.

    citation   2/2  100%      copying     5/10   50%
    entity     3/3  100%      intensity   5/8    62%

**6 of 19 repairs end clean**, against 1 of 9 on the small sample. The shape holds:
removing an invented name or a bad id is something a regeneration does reliably, and
rewriting a sentence is where it struggles. It struggles less than two sources suggested.

**The two inventory counters, first full reading.** `dropped_unanchored` is **1** over 405
claims, in `art-ocr`. It had been zero across v1, v2, v4 and v6, 1,586 claims, so the
corollary the design rests on is rare rather than dormant. `demoted_quotes` is **0**, and
the reason matters more than the number: v11 produced **2 quote claims in 405** and both
carried `verbatim`. The demotion had nothing to catch.

**Quote claims are rare, and that is the shape of the risk.** Across every run that stored
an inventory: v4 2 of 414, v6 3 of 404, v11 2 of 405, so about 0.5%. Only two sources in
the golden set ever produce one, `art-retrieval` every time and `art-willison` once. So
the fabricated-quotation failure is **low frequency and worst-case severity**: a direct
quotation invented and attributed to a named person, on roughly one claim in two hundred.
That proportion is the argument for the guard being a deterministic relabel in
`extract.py` costing nothing, rather than prompt work costing calls and attention on
every generation. It also means the guard will stay nearly untested by the eval: at two
or three quote claims a run, absence of demotions is not evidence it works.

(v1 and v2 cannot be counted here at all: they predate storing the inventory, which was
added when the copying investigation found the runs that would have answered it had
thrown it away. The same gap, a second time.)

**A stored claim with no `verbatim` before v11 means nothing.** The inventory kept only
`id`, `kind` and `text` until the field was added chasing the art-willison quotation, so
`verbatim: null` in v4 or v6 records that nobody wrote it down, not that extraction left
it empty. Only v11 onward can be read that way. This is the third question this repo has
asked of a run that had thrown the answer away.

**The quote path itself is healthy where the words exist.** `art-retrieval` carries a
quote claim in every run and has never raised a quote error, in v4, v6 or v11. In v11
both its claims carry `verbatim` and the thread output quotes that string exactly rather
than the restated `text` beside it, which is the behaviour the target prompts specify.
That is the mechanism working, not evidence that today's changes improved anything:
`art-retrieval` was already clean before them, and `art-willison` was a claim with
nothing to quote.

## The eval, and the abandonment rule

`eval/run_eval.py` publishes one number or none. If any golden source fails,
`unsupported_claim_rate` comes back `None` and only a labelled provisional figure is kept. This
is enforced in `aggregate()`, not left to discipline, because a partial run tends to complete
only the markdown sources, which score roughly three times better than articles. A partial rate
is a rate over the easy half.

Two metrics, deliberately separate. The judge (`eval/judge.py`) runs on Groq
`openai/gpt-oss-120b` and the generators walk Gemini before falling back to Groq
`qwen/qwen3.6-27b`, so nothing grades its own work. Both sides are pinned by name and
`run_eval` refuses to start if the two strings ever match: this used to be two Gemini
ladders that the generators could fall through into, which held by luck.

Full provider separation was tried and reverted the same day. Pinning generators to
Gemini alone was the stronger claim and cost them their fallback, and a fresh run then
failed 15 of 15 sources at extraction when Gemini's daily budget went. With two
providers, both halves cannot be single-provider and independently fault-tolerant at
once, and an eval that cannot run measures nothing.

The judge is **windowed** at `JUDGE_WINDOW_CHARS`, because Groq returns 413 above
roughly 20k where Gemini took 35k. Window size is not a performance knob: support is
existential per window, so a sentence needing two distant paragraphs is supported when
one window holds both and not when none does. Smaller windows move the rate upward,
which is why `aggregate()` withholds the headline when a run mixes sizes.

`eval/inject.py` plants known fabrications so the deterministic layer gets a score whose
truth is known by construction: an unvalidated judge is a number with nothing behind it.

**`unplanted_error_rate` is not a false-positive rate**, and was called one until v6. It
counts errors raised on bodies nothing was planted in, and three of v6's five were invented
quotations the quote rule caught correctly, one attributed to a named person. Injections are
planted *on top of* generated bodies, so an unplanted body is only assumed clean. It is an
upper bound on false alarms; separating them needs the bodies labelled by hand. Renamed for
the same reason `claim_utilisation` is not called recall. `copying` is excluded outright,
since it is never planted and a body that reproduced the source really did reproduce it.
`unplanted_errors_by_rule` says which rule fired, because the bare count hid that quote had
started firing where it never had before. `aggregate` still reads the old key from a
checkpoint written before the rename, because resuming across a code change is the normal
case here.

`eval/rejudge.py` re-scores a finished run's stored bodies when the judge changes, at
the cost of judging and no generation. A rate is only comparable to another rate the
same judge produced. `--only <id>` aims a small budget at one source; it limits what is
paid for and never what is reported, so a source nobody has judged still withholds the
headline. Progress is checkpointed **per window**, in `eval/results/*/partial/`, because
a wall part-way through a large source used to discard every window before it: `art-ocr`
wants about 10k tokens and attempts were each burning one to two thousand and recording
nothing. Those crumbs are deleted when the source completes and are gitignored meanwhile.

`claim_utilisation` is **not** recall. It is the share of extracted claims some output used.
Recall would need a hand-labelled inventory the golden set does not have.

`--seed` only places planted fabrications. It does **not** make a run reproducible, which is why
repeats are the only way to settle a rate.

**The current judge is not uniformly more lenient, which is what this used to say.**
`v1-rejudged` is now complete at **13.4%** (35/262) against v1's published 15.5%, and
`v2-rejudged` stands at 13 of 15.

**Pair only the sources both judges checked the same number of sentences in.** Three of
v1's fifteen did not: `md-citerag` 16 to 18, `md-vidsmith` 19 to 20, `yt-rome` 22 to 23.
The bodies are identical, the judged sentence sets are not, so those three are not a
before-and-after of anything. On the twelve that are comparable, v1 goes 17.9% to 14.4%
(`z=0.95, p=0.34`); v2's fourteen shared sources are all comparable and go 10.1% to 7.2%
(`z=1.14, p=0.25`). Direction down, neither significant.

And not uniform, which is the part that matters for swapping a number: the current judge
is **stricter on 2 of v1's sources** (`md-moneytrail`, `md-vidsmith`, the latter 0/19 to
3/20) and **stricter on 5 of v2's** (`art-ocr`, `art-reconciliation`, `art-vector-db`,
`art-willison`, `yt-rome`). A per-source figure from one judge cannot be swapped for the
other's, and the aggregate shift is not established either.

An earlier version of this paragraph pooled all shared sources without checking sentence
counts and reported `z=1.04, p=0.30` over "23 source comparisons". The conclusion did not
change; the arithmetic was over pairs that were not pairs.

Published: **15.5% unsupported** (v1, 258 claims, all 15 sources). A v2 measured 10.0% but is not
published: z=+1.86, p=0.063 on one run. Articles improved decisively (25.9% to 5.6%, p<0.001)
while markdown regressed (8.9% to 15.0%).

**Both runs are now scored on the current judge, and the gap crosses p<0.05 there.**
`v1-rejudged` 13.4% (35/262) against `v2-rejudged` 6.9% (18/259): `z=2.42, p=0.016`,
where the same two runs on the old judge were `z=1.86, p=0.063`.

**That is not a second experiment and must not be read as one.** Rejudging re-scores the
same single v1 run and single v2 run with a different instrument; it adds no runs, and
generation variance is exactly what `--seed` cannot remove. The p-value moved because the
measured gap widened, 5.5 points to 6.5, not because anything was repeated. The current
judge also took v2 down further than v1 (3.1 points against 2.1) while being stricter on
5 of v2's own sources, so the widening is not obviously the judge being cleaner rather
than differently shaped. Repeats remain the only thing that settles a rate, and the
README's rule that the headline does not move without them still holds.

**A rejudged source is scored against the source as it is today.** `rejudge.py` re-ingests
rather than storing the text, so a source edited since the bodies were written is a
different instrument, and its own docstring says to check before trusting the number.
It warned on the terminal and nothing reached `report.json`, so a rate whose sources had
moved read as clean forever after. `aggregate` now records `sources_drifted` and
`drifted_claims`, and the run prints both. **Recorded, not enforced**: an unjudged source
withholds the headline because the missing ones are the hard ones, while a drifted source
was measured, just against something that moved. Whether that should withhold too is
undecided.

`v6-rejudged` is the case that found it: `md-reruns` +2464 chars and `md-vidsmith` +1952,
36 of 244 judged claims between them, because both are this machine's own READMEs and
they get edited. It did not move the rate much, 8.7% over the thirteen undrifted sources
against 9.0% over all fifteen, but nothing in the file could have said so.

**And the run's printed rate disagreed with its own stored sources by one claim.** The
terminal said 8.6% (21/244) and `md-vidsmith` 5.0%; the per-source files say 9.0%
(22/244) and `md-vidsmith` 2/20. `rejudge.py` writes the result and prints from the same
object, so the two should not be able to differ, and the cause is not established. The
files are what every later tool reads, so **9.0% is the operative number** and the printed
one is not to be quoted. Worth knowing that a printed rate here has been wrong once.

## LLM access, where the traps are

- **Never use Gemini's `-latest` aliases.** They repoint to the newest model, which carries the
  smallest free-tier allowance: `gemini-flash-latest` resolved to `gemini-3.8-flash` at 20
  requests per day. `llm.py` pins ids and walks a ladder because quota is **per model**, so a
  429 on one says nothing about the next.
- **The models are pinned so this project competes with nothing else on this machine.**
  `reruns` measures daily against `gemini-3.7-flash`, `-3.6`, `-3.5` and Groq
  `openai/gpt-oss-20b`; `vidsmith` sits on `gemini-3.5-flash-lite`. recut walks
  `gemini-3.1-flash-lite` then `gemini-3-flash-preview`, falls back to `qwen/qwen3.6-27b`,
  and judges on `openai/gpt-oss-120b`. Sharing one cost two days: a judge probed alive and
  was spent minutes later because something else had been through it. Scheduled tasks
  compete too, so stagger them.
- **Branch a 429 on the quota it names, not the status code.** Gemini's body carries a `quotaId`
  like `...PerDayPerProjectPerModel...`; Groq's says tokens per minute or per day.
  Per-minute is retried, per-day moves on, and a 429 that names **nothing** is retried
  rather than believed: that default once cost a judged run ten of fifteen sources while
  every model still answered. `_raise_for` puts the kind at the front of the message,
  `[per-day quota]`, because Groq names it around character 200 and every layer truncates.
- **Groq's tokens-per-day is a trailing window, not a calendar tally.** `Used` moves in
  both directions on its own as spend ages out, so a rising number is not evidence of
  another process. A small probe also succeeds while the day's budget is gone: at
  `Used 199999/200000` a five-token probe still passed. Only the real request answers it.
  The judge is paced under a ceiling (`tokens_per_minute`) so windowing does not burst.
- **Groq caps a request at 8000 tokens per minute**, which is one reason extraction is windowed
  at ~6000 chars. The other is that long inputs make models quietly drop claims from the middle.
- Groq rejects urllib's default User-Agent with a 403 that reads exactly like a bad key.
- `LLM(allow_env=False)` is what makes bring-your-own-key true. Without it a caller supplying
  only a Gemini key still falls through to the server's Groq key.

## Tests

No test touches the network, and it should stay that way: `ScriptedLLM` in
`tests/test_pipeline.py` drives the whole pipeline including the repair path, the ingest tests
feed fixture HTML and fixture caption cues, and `tests/conftest.py` gives every module a client
against a throwaway database. **342 tests, ~7s.** If the suite jumps to ~17s, a test is
reaching the network; `--durations` finds it. A test that *sleeps* trips the same wire: the
token pacer's first tests waited on a real clock and took the suite to 75 seconds, so they
use a fake clock that sleeping advances and assert the waiting rather than performing it.

`tests/test_cli.py` exists because `python -m recut run` once shipped with a `NameError` on the
first line of `main()` while 155 tests were green. A module that imports cleanly is not a module
that runs.

## Deployment

Runs on the EC2 box that also hosts vidsmith, at `vidsmith.duckdns.org/recut/` behind HTTP basic
auth (port 8079; vidsmith owns 8077). Caddy strips the `/recut` prefix, which is why every URL in
`web/` is relative. The password is protecting an API budget, not a secret: the server's keys are
still the default when a visitor does not supply their own.

**Restart only when `recut/` changed.** The service imports `recut/` and `web/` and
nothing else, so a commit touching only `eval/` or `tests/` needs the pull and not the
restart. Bouncing a live service to deploy a test file is risk for no benefit.

```bash
# code the service runs
ssh -i ~/.ssh/vidsmith-key.pem ubuntu@vidsmith.duckdns.org "cd ~/recut && git pull --ff-only && sudo systemctl restart recut"
```

```bash
# eval or tests only
ssh -i ~/.ssh/vidsmith-key.pem ubuntu@vidsmith.duckdns.org "cd ~/recut && git pull --ff-only"
```

Either way, check it afterwards rather than assuming: `systemctl is-active recut vidsmith
caddy`, and `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8079/api/queue` from
the box. The eval harness lives in this repo but only ever runs locally; the box has no
API keys budget of its own and runs no evals.

Validate any Caddyfile change with `caddy validate` **before** installing it; a bad one takes
vidsmith down too. Rotating the API keys is `tools/rotate-llm-keys.py` in the private
`veer0608/machine-tools` repo, which updates all six locations and probes before writing.

## Conventions

- Commit messages are written in the project's own voice. **No `Co-Authored-By: Claude` trailers
  and no Claude attribution.**
- Licensed PolyForm Noncommercial; `COMMERCIAL.md` deliberately states what the tool does *not*
  do, including that it catches only about a third of inflation.
- The README's headline number does not move without repeated runs to justify it.
