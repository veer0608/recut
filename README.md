# recut

One source in, many pieces out, and every claim anchored to the source.

```
source   vidsmith
         141 segments, 29673 chars
claims   49 anchored
linkedin verified
thread   verified
done     out/vidsmith  (9 model calls)
```

The usual way to repurpose an article is to paste it into a prompt and ask for a
LinkedIn post. That fabricates statistics, invents quotes and drifts from what the
source said, and the buyer does not find out until a made up number goes out under
their name.

recut never generates from the raw source. One extraction pass turns the source into
a claim inventory where every claim cites the paragraph it came from. Every output is
then written from the inventory alone. A writer cannot quote what it was never shown.

## What it does now

A markdown file, an article URL or a YouTube video in. Five targets out, each with a
provenance sidecar naming the source span behind every claim it used.

| target | what it is |
| --- | --- |
| `linkedin` | one post, 120 to 220 words |
| `thread` | 5 to 9 posts, each under 280 characters |
| `newsletter` | subject, preview line, 300 to 600 words, exactly one call to action |
| `article` | a written piece with sections. Video and audio sources only |
| `vidsmith` | a buildable [vidsmith](https://github.com/veer0608/vidsmith) project directory |

`--targets all` expands to whatever the source supports. `article` is offered only for
timed sources, because rewriting an article as an article is not repurposing, it is a
plagiarism surface with a different font.

```bash
python -m recut run path/to/post.md
```

```bash
python -m recut run "https://www.youtube.com/watch?v=0PkBP0dk4Lw"
```

The source type is picked from what you type. All three adapters return the same
`Document`, holding the same invariant: `raw[segment.char_start:segment.char_end]` is
exactly that segment's text, whether raw is a file, the text pulled out of a web page,
or an assembled transcript. Nothing downstream knows which one ran.

Timed sources carry timecodes through to the end, so every line of the output traces
back to the second it was said:

```
c6 @1:33  In 450, farmers often abandoned their land to seek protection from warlords
c0 @0:18  The common narrative attributes the fall of Rome to barbarian invasions in 476
c2 @0:37  Emperors devalued currency by mixing base metals into silver coins
```

Caption cues arrive a few words at a time, which is far too fine a grain to cite: a
claim would end up anchored to "and that is why". Cues are regrouped into
sentence-sized segments keeping the start of their first cue and the end of their
last. Auto-generated captions carry no punctuation at all, so a character cap sits
behind the sentence rule.

Article extraction runs three trafilatura passes, strictest first, and refuses
anything under 200 characters. Recall mode will otherwise happily hand back a nav
link off a page that has no article on it.

Outputs land in `out/<slug>/`:

```
linkedin.txt                 the post
linkedin.provenance.json     claim by claim, which segment and timecode it came from
thread.txt
newsletter.txt
article.txt
vidsmith/script.md           a vidsmith project, buildable as it stands
vidsmith/config.yaml
vidsmith/provenance.json     the anchors, inside the directory that gets handed over
claims.json                  the full inventory
```

## One article in, one narrated short out

The `vidsmith` target emits a project directory rather than a paragraph, and
`--build` renders it in the same command that wrote it:

```bash
python -m recut run article.md --targets vidsmith --build
```

recut shells out to vidsmith's own interpreter rather than importing it. Rendering
needs ffmpeg, a speech engine and a video library, and a text tool that cannot start
without them is a worse text tool. It also keeps the two licences apart: emitting a
script is not rendering a video, and the video half needs its own permission. The
interpreter is found from `VIDSMITH_PYTHON`, then `VIDSMITH_HOME`, then a checkout
sitting beside recut.

A failed render exits 4 and says why. It does not retract the text that was already
written and verified, so the posts survive a build that did not.

The project is still buildable by hand, because vidsmith's own `_project_dir` accepts
a path and nothing needs copying or editing first:

```bash
python -m vidsmith build /path/to/recut/out/<slug>/vidsmith
```

```
voiced   54.8s of narration across 5 scenes
visuals  provider=cards 1080x1920
render   59.4s of picture across 5 shots, mixing and encoding
done     empires-do-not-end-in-a-single-day-9x16.mp4  (59.4s, 9.0 MB, 98s to build)
```

The emitted `config.yaml` names the source, and vidsmith writes that string verbatim
into the description of anything published from the project. A video built out of
someone else's writing credits it, and the credit is assembled rather than generated,
because an attribution a model has paraphrased is not an attribution.

An article or a video is credited by its URL, which is the only attribution a viewer
can act on. A local file is credited by its own title instead: an absolute path off
the machine that made the video credits nothing and publishes a directory layout. The
real path stays in a comment at the top of the file, where it is useful to whoever
opens it and goes nowhere near a description.

Narration is written for a speech engine, so numbers are spelled out and symbols are
rejected before they reach the voice: "four hundred seventy six", never "476". Visual
queries have to name something a camera could point at, so "the concept of decline"
is flagged and "cracked marble statue face" is not. Only the narration is verified.
A `[visual: ...]` line is a stock search string, never spoken and never published,
and checking it would flag every good query that names something the source did not.

Article length follows the material rather than a fixed target. Nine claims from a two
minute video will not honestly fill 800 words, and asking for them invites padding,
which is the failure mode next door to fabrication. The prompt and the check are
computed from the same number so they cannot disagree.

## In the browser

The CLI is not the whole product. `recut.api` serves a page that runs the same
pipeline and then shows the thing a file cannot: hover a sentence of the output and
the span of the source behind it lights up.

```bash
python -m uvicorn recut.api:app --port 8078
```

A run takes 20 to 60 seconds, which is longer than a browser will hold a request
open, so `POST /api/jobs` hands back an id and the page polls it. The job store is
SQLite and nothing else. No Celery, no Redis, no Docker: a queue that needs three
services to repurpose a blog post is a worse product than one that needs none.

**Bring your own key.** Paste a Gemini or Groq key into the page and the run spends
your quota instead of the host's. When you supply one, the server's own keys are
taken off the table entirely rather than kept as a fallback, because falling back
would spend the host's quota while the page told you it was spending yours.

### The review queue

recut measures its own unsupported claim rate and that number is not zero. That is
survivable only because a person reads the output before it goes out, so the path
from generated to published runs through a queue rather than around it.

A draft moves `pending` to `approved` or `rejected`, and `approved` to `posted`.
Changing your mind and approving something you rejected is allowed. Marking something
`posted` that nobody approved is refused, because that would skip the review step
while leaving a record that says it happened. **Nothing here posts anything to
anywhere.** `posted` is a human saying they did it.

Each draft carries its provenance, so a reviewer sees which span of the source stands
behind each sentence instead of judging prose on its own. Each draft also carries the
source as it was when the draft was written, because a URL can change under you and a
review against today's version of a page is not a review of what was generated.

## The verifier

After each output is written, six deterministic checks run. No model call, no
network, no cost.

| rule | what it catches |
| --- | --- |
| number | a figure with no matching figure in the source, normalised so 1,000 and 1000 and "one thousand" are the same number |
| quote | quotation marks around words that are not a verbatim span of the source |
| entity | a person, company or product named in the output but not in the source |
| intensity | force the output reached for that the source never did: "necessary", "always", "ensures", "relies on", "the ultimate" |
| copying | eight or more consecutive words reproduced from the source without quoting |
| citation | a claim id that does not exist |

The intensity rule exists because of a measurement rather than a hunch. The first
four were catching 100% of planted fabrications while the unsupported rate sat at
15.5%, because nearly every real failure was inflation, not invention. It asks a
narrower question than "is this claim supported", which no token check can answer,
and instead asks one that a token check can: **did the output reach for a word of
force that the source never reached for?** A source that says "critical" itself
vouches for an output that says "critical", and inflections count, so "necessity" in
the source excuses "necessary" in the output.

The copying rule asks a different question from the others. Every word it flags is
supported by the source, and that is the problem: reproducing someone else's sentence
unchanged under your own name is a different kind of wrong, and a tool that rewrites
for a living should notice when it did not rewrite. It is a notice rather than an
error on purpose. **80% of artifacts across both eval runs carried an eight word
verbatim run**, so gating on it would send four drafts in five back for repair before
the prompt work that should fix it has had a chance to. It gets promoted when a run
measures the rate down, not before.

A failure triggers exactly one regeneration with the offending span named. A second
failure is surfaced to the user, not hidden. Silently shipping an unanchored figure
is the thing this exists to stop.

Small bare integers, short scare quotes and common acronyms are reported as notices
rather than errors, because "4 things I learned" is list scaffolding and not a claim
about the source.

The expensive judgement, entailment of non numeric claims, belongs in the eval
harness and never runs on the request path.

## The measured number

**15.5% of published sentences are not supported by the source.** Measured
2026-09-06 over 258 judged claims, 30 artifacts, 15 real sources, 120 model calls.
Every source completed, so the number stands.

```
sources     15/15 completed
verifier    recall 100.0% on 120 planted fabrications, false positives 3.3%
            citation 100%   entity 100%   number 100%   quote 100%
format      100.0% compliant
utilisation 54.5% of extracted claims used
UNSUPPORTED CLAIM RATE  15.5%   over 258 judged claims
```

That is a bad number for a product whose entire pitch is provenance, and it is the
most useful thing in this repository.

### The deterministic verifier is not the problem

It caught 120 of 120 planted fabrications, every rule at 100%, with one false
positive across 30 clean bodies. What it cannot see is the failure that actually
dominates, because every token involved is real.

### What the 15.5% actually is

Almost none of it is invented facts. It is **inflation**: the source's own material,
restated at a strength the source never used.

| the source said | the output said |
| --- | --- |
| reconciling at frequent intervals is "good practice" | regular reconciliation "is a necessary accounting practice" |
| differences "may arise" | records are "often not in sync" |
| accuracy measurements exist | accuracy is "the ultimate metric" |
| misinterpreting context causes errors | it "remains a major hurdle" |
| vector databases are used in ML applications | modern ML applications "rely on this infrastructure" |
| three funding rounds are listed in the references | "companies are heavily investing in this space" |

Every number is right. Every name is right. Nothing is fabricated. The claim is
simply stronger than the evidence, and a deterministic token check cannot reach it.

The generator prompts are partly to blame: asking for "the most surprising concrete
claim" and a line that would "stop a scroll" is direct pressure to inflate.

### Where it is worst

| cut | unsupported |
| --- | --- |
| article sources | 25.9% |
| markdown sources | 8.9% |
| video sources | 0.0% (one source, too few to trust) |
| linkedin | 18.2% |
| thread | 12.4% |

Articles are three times worse than markdown. They are dense with figures and named
entities, and the compression to a claim inventory drops the hedges that came with
them. The thread beats the LinkedIn post because one idea per post leaves less room
to editorialise.

### How the number is produced

- **Primary metric, `unsupported_claim_rate`**: each output is split into sentences
  and judged against the source by a model on a *different provider* from the
  generators, so nothing grades its own work. Both sides are pinned by name and the
  run refuses to start if the two ever match. Sentences that assert nothing checkable
  are excluded from the denominator rather than counted as passes.
- **Judge validation**: an unvalidated judge is a number with nothing behind it, so
  the same run scores the deterministic verifier on fabrications planted by
  `eval/inject.py`, where the truth is known by construction.
- **`claim_utilisation` is not recall.** It is the share of extracted claims that some
  output used. Recall would need a hand-labelled inventory of what each source *should*
  yield, which this golden set does not have, and calling it recall would overstate it.
- **The abandonment rule is enforced in code.** If any source fails to complete,
  `unsupported_claim_rate` is returned as `None` and only a clearly labelled
  provisional figure is kept for diagnosis. A rate over the sources that happened to
  survive is a rate over the easy ones.

```bash
python eval/run_eval.py --run v1
```

Checkpointing is per source, so a quota cliff costs one source and the same command
resumes.

Seven of the fifteen golden sources are READMEs from sibling repositories checked out
next to this one, which a fresh clone will not have. Those sources fail to ingest and,
by the abandonment rule, that withholds the headline number rather than quietly
scoring the eight that remain. Point `eval/golden/sources.yaml` at your own long-form
writing to run it somewhere else.

### A second run measured 10.0%, and the headline stays at 15.5%

An anti-inflation pass landed after v1: a shared faithfulness block across every
generator prompt, softened hook instructions, harder hedge capture at extraction, and
the `intensity` rule. Re-running the same golden set gave **10.0%** over 259 judged
claims, all 15 sources complete.

That number is not published as an improvement, because one run does not support it:

```
overall    15.5% -> 10.0%   z=+1.86  p=0.063   not distinguishable from noise
article    25.9% ->  5.6%   z=+4.10  p<0.001   distinguishable
markdown    8.9% -> 15.0%   z=-1.52  p=0.129   not distinguishable from noise
```

What the run does support is narrower and more interesting: **the pass fixed articles
decisively.** Articles were the worst cut in v1 at 25.9% and are now the best at 5.6%.
Against that, **markdown got worse**, from 8.9% to 15.0%, and a single aggregate figure
averaged a large win and a real regression into one comfortable-looking number.

Quoting 10.0% off one run at temperature 0.4 would be a milder version of exactly the
failure this project exists to stop, so 15.5% stands until repeated runs justify moving
it. Both runs are in `eval/results/`.

Note that `--seed` only controls where fabrications are planted for the verifier score.
It does not make a run reproducible: generation varies between runs regardless, which
is the whole reason repeats are needed.

### Known limits of this measurement

- One video source. The 0.0% on it means nothing yet.
- Numbers from the current judge are not comparable to the 15.5% and 10.0% above,
  which a different judge produced. `eval/rejudge.py` re-scores a finished run's stored
  bodies to put them on one scale, and until that is complete the two cannot be set
  beside each other. Partial evidence suggests the current judge is uniformly more
  lenient, which would make a lower figure a change of scale rather than an improvement.
- Sources are capped near 40k characters. Behaviour on book-length input is unmeasured.

## Setup

```bash
git clone https://github.com/veer0608/recut.git
cd recut
python -m venv .venv
.venv/Scripts/activate       # Windows
# source .venv/bin/activate  # macOS and Linux
python -m pip install -e ".[web,api,dev]"
```

Then copy `.env.example` to `.env` and fill in a Gemini or Groq key. Either alone is
enough.

## Notes on the free tiers

Both providers have traps that cost real runs, so the client works around them:

- **Never use Gemini's `-latest` aliases.** They repoint to whatever is newest, and
  newest carries the smallest allowance. `gemini-flash-latest` currently resolves to
  `gemini-3.8-flash` at 20 requests per day, which a single run of this pipeline
  exhausts. recut pins model ids and walks a ladder, because free tier quota is per
  model and a 429 on one says nothing about the next.
- **Groq caps a request at 8000 tokens per minute** and its binding daily limit is
  tokens per day, which appears in no response header. Extraction is windowed at
  about 6000 characters per call, which fits under the cap and also stops long inputs
  from quietly dropping claims out of the middle.

## Tests

```bash
python -m pytest -q
```

236 tests in about five seconds, none of which touch the network. The pipeline tests
drive a scripted model
so the whole loop, including the repair path, runs offline and for free, and the
ingest tests feed fixture HTML and fixture caption cues rather than fetching.

## Licence

[PolyForm Noncommercial 1.0.0](LICENSE.md). Personal use, study, hobby projects and
non-profits are covered. Using recut to make money is not: content marketing for a
business, client work, agency output, or running it as a service. For that, see
[COMMERCIAL.md](COMMERCIAL.md).

The `vidsmith` output target emits a project for a separate tool under its own
noncommercial licence, so commercial video output needs one from
[veer0608/vidsmith](https://github.com/veer0608/vidsmith) as well.
