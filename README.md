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
claims.json                  the full inventory
```

## One article in, one narrated short out

The `vidsmith` target emits a project directory rather than a paragraph. vidsmith's
own `_project_dir` accepts a path, so it builds where it lands with nothing copied
and nothing edited:

```bash
python -m vidsmith build /path/to/recut/out/<slug>/vidsmith
```

```
voiced   54.8s of narration across 5 scenes
visuals  provider=cards 1080x1920
render   59.4s of picture across 5 shots, mixing and encoding
done     empires-do-not-end-in-a-single-day-9x16.mp4  (59.4s, 9.0 MB, 98s to build)
```

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

## The verifier

After each output is written, five deterministic checks run. No model call, no
network, no cost.

| rule | what it catches |
| --- | --- |
| number | a figure with no matching figure in the source, normalised so 1,000 and 1000 and "one thousand" are the same number |
| quote | quotation marks around words that are not a verbatim span of the source |
| entity | a person, company or product named in the output but not in the source |
| intensity | force the output reached for that the source never did: "necessary", "always", "ensures", "relies on", "the ultimate" |
| citation | a claim id that does not exist |

The intensity rule exists because of a measurement rather than a hunch. The first
four were catching 100% of planted fabrications while the unsupported rate sat at
15.5%, because nearly every real failure was inflation, not invention. It asks a
narrower question than "is this claim supported", which no token check can answer,
and instead asks one that a token check can: **did the output reach for a word of
force that the source never reached for?** A source that says "critical" itself
vouches for an output that says "critical", and inflections count, so "necessity" in
the source excuses "necessary" in the output.

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
  and judged against the source by a model pinned to a *different* ladder than the
  generators, so nothing grades its own work. Sentences that assert nothing checkable
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

### Known limits of this measurement

- One video source. The 0.0% on it means nothing yet.
- The judge and the generators share a provider and could in principle share a model
  when the pinned ladders both fall through. The run records provider, not model, so
  no-overlap is designed for but not proven.
- Sources are capped near 40k characters. Behaviour on book-length input is unmeasured.

## Setup

```bash
git clone https://github.com/veer0608/recut.git
cd recut
python -m venv .venv
.venv/Scripts/activate       # Windows
# source .venv/bin/activate  # macOS and Linux
python -m pip install -e ".[web,dev]"
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

118 tests, none of which touch the network. The pipeline tests drive a scripted model
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
