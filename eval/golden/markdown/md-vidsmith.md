# vidsmith

Script in, narrated and captioned YouTube video out.

![Seven seconds of a finished vidsmith render: stock footage, captions landing on
the word being spoken, cuts on the sentence](docs/demo.gif)

That is untouched output. Nothing transcribed the audio to place those captions:
the speech engine reports when each word starts and stops, and those numbers are
what you see. The same timings decide where the picture cuts.

Whole videos made this way: [Why Rome Never Really
Fell](https://youtu.be/0PkBP0dk4Lw). Or run one in the browser without installing
anything at [vidsmith.duckdns.org](https://vidsmith.duckdns.org).

You write a markdown script. vidsmith speaks it in a neural voice, finds a shot
for every scene, burns word-timed captions, mixes music under the narration, and
encodes a delivery-ready mp4, plus an `.srt`, a `.vtt` and a draft
title/description/chapters.

```bash
git clone https://github.com/veer0608/vidsmith.git
cd vidsmith
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m vidsmith build demo
```

```
script   4 scenes, ~35s estimated
voice    en-US-AndrewNeural at +8%
visuals  provider=cards 1920x1080
captions captions.ass + captions.srt + captions.vtt
render   32.7s of picture, mixing and encoding
done     out/why-your-bank-statement-lies.mp4  (32.7s, 6.5 MB, 62s to build)
```

That run needs no API keys and no account: measured from a clean clone. ffmpeg
is the only thing to install yourself, everything else comes from pip, and the
narration voice is a free Microsoft endpoint. Stock footage and a written
description are upgrades, not requirements.

## Why the captions are exact

Most tools generate speech, then run Whisper over that speech to find out where
the words landed. vidsmith never does that. Edge's TTS returns a `WordBoundary`
event for every word it speaks, so the timings come from the engine that made
the audio. There is nothing to drift, no model to download, and no transcription
step to be wrong.

Punctuation is the one thing those events drop, so it is stitched back on from
the source script before captions are grouped. Otherwise nothing ever breaks on
a full stop.

## Install

ffmpeg is the only non-Python dependency.

| | |
| --- | --- |
| macOS | `brew install ffmpeg` |
| Debian, Ubuntu | `sudo apt install ffmpeg` |
| Windows | `winget install Gyan.FFmpeg` |

Then the package:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

On Windows the interpreter is `.venv\Scripts\python.exe`, and `.\vidsmith.cmd` wraps it, so
commands read as `.\vidsmith.cmd build demo`. PowerShell 5.1 has no `&&`; chain with `;`.

Check what this machine can actually do:

```bash
.venv/bin/python -m vidsmith doctor
```

It reports whether ffmpeg was found, which keys resolved, and what each missing
key would have added. None of them are required to render a video.

## Writing a script

Paragraphs are scenes. Headings start a new scene. `[visual: ...]` sets the
stock-footage search for that scene, `[hold: 4.0]` forces a minimum duration,
and lines starting with `>` are production notes that never get spoken.

```markdown
# Why Your Bank Statement Lies

## The hook
[visual: paper bank statement on a desk]
Your bank statement is not a record of what you spent. It is a record of what
your bank found convenient to store.

## Merchant names
[visual: card terminal in a small shop]
The merchant name on a transaction is typed by the payment processor, not the
shop. That is why a coffee costs money at a company you have never heard of.
```

Or have Gemini draft one:

```powershell
.venv/bin/python -m vidsmith new gil --topic "why Python's GIL still matters" --minutes 3
```

## Commands

| command | what it does |
| --- | --- |
| `vidsmith new NAME [--topic ... --minutes N]` | create a project, optionally drafting the script |
| `vidsmith build NAME` | render to mp4 |
| `vidsmith voices --lang en-IN` | list narration voices |
| `vidsmith meta NAME` | regenerate YouTube title/description/chapters |
| `vidsmith thumbs NAME [--count 6]` | rank thumbnail frames, compose a titled one |
| `vidsmith thumbs NAME --refresh` | redo the delivery thumbnails from stock, no re-render |
| `vidsmith check NAME` | read a finished build for faults before publishing it |
| `vidsmith upload NAME` | put the cut on YouTube, with its description, thumbnail and captions |
| `vidsmith doctor` | check ffmpeg, edge-tts and keys |

Useful `build` flags:

```
--aspect 9:16              vertical cut for Shorts (16:9, 9:16, 1:1, 4:5)
--provider cards           generated cards instead of stock footage (needs no key)
--voice en-IN-PrabhatNeural
--music path/to/bed.mp3    ducked under the narration automatically
--captions block           no karaoke highlight
--theme ink                colour and type preset
--accent "#FF7A59"         accent override
--watermark "@handle"      channel mark, bottom-right
--no-cards                 skip the title and end cards
--force voice,visuals      redo cached stages
--stop-after voice         stop early to check timings before rendering
```

Each aspect gets its own picture, captions and output file, so a vertical cut
never overwrites the landscape one. Narration is shared between them, so changing
aspect does not re-synthesize speech.

## Two voices, one set of timings

`edge-tts` is the default: free, no key, and the reason a clean clone renders a
video without an account. `vidsmith voices --lang en-IN` lists what it offers
and `--voice` picks one.

Amazon Polly is the alternative, and the reason is licensing rather than
quality - see [Licence](#licence) below, which is not a footnote if anything
with revenue is attached. Set `voice.provider: polly` in the project config
with AWS credentials in the environment.

What matters technically is that **the edit does not change between them**.
Polly is one of the few services that reports word timings at all, so the cut
still lands on the sentence boundaries the speaker actually spoke and the
captions still come from the engine rather than from a transcription. The two
report differently and are normalised to the same word list: edge-tts sends a
boundary event per word, Polly sends speech marks in milliseconds carrying
starts and no durations, so the ends are reconstructed from the next word and
the last one from the audio itself.

```
{'text': 'said', 'start': 1.507, 'end': 1.716}
{'text': 'each', 'start': 1.716, 'end': 1.889}
{'text': 'word', 'start': 1.889, 'end': 2.4}     <- audio is 2.4s
```

Polly bills the audio and the speech marks as separate requests, so a video
costs its script length twice. Its `generative` engine returns no speech marks
at all, which is why it is not a selectable value: it cannot time captions or
the cut, and that is the whole design.

## Cut rhythm

A scene is not a shot. Narration runs six to eight seconds, and one unbroken
take that long is what makes generated video look generated - so each scene is
split into shots at the sentence boundaries the TTS already reported, and the
picture changes exactly where the speaker lands a full stop.

```
visual  scene   0  2 shots  2.7+3.6   paper bank statement on a desk
visual  scene   1  2 shots  4.3+4.1   card terminal in a small shop
```

One search serves every shot in a scene, so the shots stay on the same subject
and it still costs one API call. A sentence longer than `max_shot_seconds` is
broken at its last comma; if the provider cannot supply enough distinct clips,
the plan collapses back to fewer, longer shots rather than cutting to the same
footage twice.

```yaml
visuals:
  cut_on_sentences: true
  min_shot_seconds: 2.4
  max_shot_seconds: 5.5
```

## Footage that matches the line

Stock search ranks by popularity, not by whether a clip depicts what is being
said - "calendar pages turning" returns a book. With `GEMINI_API_KEY` set, the
preview stills of the top candidates are shown to Gemini alongside the narration
line, and the results are reordered by what is actually in frame.

```
visual  scene   2  2 shots  3.6+3.2   calendar pages turning
  rerank: picked #2 over the top result
```

The model also marks candidates that show the wrong subject outright, and those
are never used. Only judged candidates are eligible - letting the unjudged tail
of the result list backfill would quietly reinstate the clips the reject pass
just removed. When that leaves fewer usable clips than the scene has shots, the
shot plan collapses:

```
visual  scene   2  1 shot   6.8   calendar pages turning
  rerank: rejected 7 of 8 as the wrong subject
```

Holding one correct shot for 6.8s beats cutting to a book halfway through a line
about calendars.

It judges stills, not video, so it costs one call per scene and no extra
downloads. The ordering is cached in `build/visuals*/rerank.json`, so rebuilds
do not re-ask. Without a Gemini key, or if the call fails, the provider's own
order is used and the build carries on.

```yaml
visuals:
  rerank: true
  rerank_pool: 8      # candidates shown to the model
```

## Music

There is no free API for licensed music, and an unlicensed track is a copyright
strike waiting to happen - so the bed is synthesised. It is a slow chord pad:
detuned sines per chord with soft attack and release, low-passed and smeared
with an echo until it reads as atmosphere rather than as notes.

```yaml
audio:
  music: auto        # "auto", "" for none, or a path to your own file
  mood: calm         # calm | warm | tense
  music_gain_db: -18
  duck: true
```

The bed is loudness-normalised when generated, so `music_gain_db` means "this
far under the voice" rather than "this far under whatever amplitude the
synthesis happened to land on". Measured on the demo: the bed sits around
-34 dB, and ducks 8 dB whenever anyone is speaking.

```
--music auto --mood tense     # generated bed
--music path/to/track.mp3     # your own
--music none                  # silence
```

## Diagrams for what cannot be filmed

Some ideas have no footage anywhere. A script about B-trees asks for "branching
tree diagram" and every stock library returns a photograph of a tree - the
reranker cannot fix that, because the footage does not exist.

Those scenes get drawn instead. Gemini writes a small JSON spec (text, not image
generation - the free tier has no image quota) and vidsmith draws it in the
project's theme, so a diagram frame sits beside the cards and the footage
without looking pasted in. Four layouts: `flow`, `tree`, `stack`, `compare`.

Two things trigger one. An explicit directive in the script:

```markdown
## How B-trees work
[diagram: a root node branching down to leaves]
Most databases build these shortcuts using tree structures.
```

Or the reranker deciding for itself. It already looks at every candidate still,
so it also answers whether a camera can point at the idea at all - and that
verdict matters more than the rejection count, because candidates can all look
related to a bad query while none of them illustrate anything:

```
visual  scene   2  3 shots  3.5+3.4+3.8   complex branching tree diagram graphic
  rerank: no camera can point at this idea
  not filmable; drawing a tree diagram
```

The space a diagram may use is derived from the caption settings, not assumed:
`captions.caption_top()` computes where the caption box will reach from the same
numbers that build the ASS styles, and the layout stops above it. Raise
`captions.size` or `margin_v` and the diagram moves up; switch captions off and
it takes the whole frame.

On a multi-shot scene the diagram builds as it is explained - one element
revealed per shot, the rest ghosted in place so the layout never jumps. Diagrams
never get Ken Burns: a frame someone is reading must not drift under them.

```yaml
visuals:
  diagrams: true
  diagram_on_reject: 0.7    # rejected fraction that also triggers one
```

Which scenes get drawn is decided **once**, in `build/diagram_scenes.json`, and
every aspect obeys it. The model's filmability verdict is not stable between
runs - on the same script the landscape pass called three scenes unfilmable and
the portrait pass called none of them unfilmable - so asking again per aspect
gave a 16:9 cut and a Shorts cut that showed different things. The spec lives in
`build/diagrams.json` for the same reason: a diagram describes the idea, not the
frame, so the second cut costs no extra calls.

## Thumbnails

```powershell
.venv/bin/python -m vidsmith thumbs demo
```

A build prefers a **stock photograph** over anything cut from the video. A frame
is graded to sit behind captions at speed, and a diagram is the clearest frame in
the video and the worst thing to put on a thumbnail; a photograph is composed to
be looked at on its own. The search is written from the scenes' visual
directives, never the hook, because every explainer hook is a frustration and a
hook-fed query returns a stressed person every time. Gemini then ranks the
candidates from their previews and alt text, and is told these are photographs
rather than frames, so it is not hunting for a mechanism that none of them show.

Frames are the fallback, and what `vidsmith thumbs` gives you. They are sampled
from the picture track, not the delivery file, so no captions, watermark or
progress bar end up in a thumbnail. Candidates are ranked on edge detail and
colour spread, penalised for crushed or blown exposure, and spaced at least 2.5s
apart so six candidates are not six frames of one shot. Either way the winner is
composed into `titled.jpg` at 1280x720 with the video title in the project's
theme.

`--refresh` redoes the delivery thumbnails from stock without re-rendering,
which matters when the first build ran with the model out of quota and nothing
picked between the candidates. It refuses rather than degrading: writing the same
keyword fallback again is worse than leaving what is already there. It rewrites
`description.txt` too, because the credit has to follow the photo.

## Before you publish

```bash
.venv/bin/python -m vidsmith check demo
```

`check` reads the delivered files against each other and exits non-zero if they
disagree. A full build runs it automatically; run it by hand after anything that
touches the outputs.

It compares the thumbnail credit in `credits.txt` against the one in
`description.txt`, which is the file that actually gets published; each
thumbnail's orientation against the cut it names; caption and chapter timings
against the runtime; and it reports any image matching no delivered cut.
Chapters have to start at `0:00`, or YouTube drops the whole list rather than the
offending line.

Every check is a fault that reached a finished build here. Each of those files
looked correct on its own and wrong beside the next one, which is the case a test
over the code that wrote them does not catch. It calls no model and no network,
so it works on a day the quota is gone, which is when a hurried refresh is most
likely to be published anyway.

## Uploading it

```bash
.venv/bin/python -m vidsmith upload demo
```

`check` runs first and refuses the upload if it finds anything, because every
fault it looks for is worse once the video is public and taking it down does not
unpublish it. `--force` uploads anyway, after printing what it found.

It sends three things, because YouTube takes them at three endpoints: the mp4
with the title, description and tags from `youtube.json`; the thumbnail; and
`captions.srt` as a real caption track. That last one is the point. Leave it out
and YouTube transcribes the audio itself, which is how a published video here
ended up carrying a machine transcript of narration whose exact word timings were
sitting in the repository.

All three files are resolved by the same aspect tag, so `--aspect 9:16` uploads
the vertical cut with the vertical cut's description. Publishing the widescreen
description under a Shorts cut names photographers whose clips are not in it,
which is a licence problem rather than a cosmetic one.

Uploads are `private` by default. Read the listing, then flip it to public
yourself and verify what is actually live:

```bash
.venv/bin/python -m vidsmith check demo --published VIDEO_ID
```

Setting it up is a one-off. In Google Cloud, enable the **YouTube Data API v3**,
create an OAuth client of type **Desktop app**, and put its two values in `.env`:

```
YOUTUBE_CLIENT_ID=...
YOUTUBE_CLIENT_SECRET=...
```

The first upload opens Google's consent screen in your browser and writes the
refresh token to `.youtube-token.json`, which is gitignored. Nothing else is
stored, and the token can be revoked at
[myaccount.google.com/permissions](https://myaccount.google.com/permissions).

Note the daily quota: the API costs about 1600 units for one `videos.insert`
against a default allowance of 10,000, so roughly six uploads a day.

## The look

Every on-screen element reads from one `theme`, so a video looks designed rather
than assembled. The title card rule, the caption highlight, the kicker and the
progress bar are the same accent by construction.

```yaml
theme:
  preset: midnight      # midnight | ink | sunset | forest | paper | mono
  accent: ""            # "#RRGGBB" to override just the accent
  watermark: "@veer0608"
  title_card: false     # opening frame with the video title; off, so it opens cold
  end_card: true        # closing frame with the last takeaway
  lower_thirds: false   # scene-heading chip, top-left
  progress_bar: true
  scrim: true           # bottom gradient so captions read over any footage
  scene_counter: true   # "02 / 04", bottom-left
```

What that buys you per frame:

- **Title and end cards** are real clips in the timeline, not overlays, so
  narration and captions stay in sync behind them.
- **Scene cards** are laid out editorially: accent rule, letterspaced kicker,
  two-line headline clamped so it can never reach into the caption zone.
- **Captions** fade in and out, scale up slightly on entry, and keep identical
  glyph widths as the highlight moves, so nothing reflows mid-line.
- **The scrim** is the reason captions stay legible once real footage replaces
  the cards: a bottom gradient burned under everything.

Override any of it per build:

```
--theme ink --accent "#FF7A59" --watermark "@yourhandle" --no-cards
```

## Visual providers

| provider | key needed | what you get |
| --- | --- | --- |
| `pexels` (default) | free `PEXELS_API_KEY` | real stock video, one clip per scene, no repeats |
| `pixabay` | free `PIXABAY_API_KEY` | same, different library |
| `cards` | none | generated gradient cards with Ken Burns motion |
| `local` | none | your own clips in `assets/clips`, matched on filename |

Without a stock key the build does not fail. It logs the fallback and renders
cards. With `GEMINI_API_KEY` set, the search query for each scene is written by
Gemini from the narration ("hands counting cash", not "personal finance");
without it, queries fall back to keyword extraction from the sentence.

**AI image generation is not wired in on purpose.** Gemini's image models are
listed on a free key but return `RESOURCE_EXHAUSTED` on the first call, because the
free tier has no image quota at all. Adding billing to the Google key is the
only way to turn that on, so cards and stock footage are the honest options.

## Keys

Put them in `.env` next to this README:

```
PEXELS_API_KEY=...
GEMINI_API_KEY=...
```

Keys are read from the environment first, then from `.env` beside the project,
its parent, and the repository root. Everything key-dependent is optional:
narration, captions, cards, music and the encode need no key at all.

## How a build is staged

```
parse   -> scenes.json          script split into scenes
queries -> scenes.json          Gemini writes a b-roll search per scene
voice   -> build/audio/*.mp3    edge-tts or Polly, plus word timings
visuals -> build/visuals/*.mp4  one normalised clip per scene
captions-> build/captions.ass   karaoke ASS, plus .srt and .vtt in out/
render  -> out/*.mp4            three ffmpeg passes: narration, picture, master
meta    -> out/youtube.txt      title, description, chapters, tags
```

Every stage skips work it already has on disk, so a failed encode never costs
you the narration again. `--force` names the stages to redo.

## Output

```
projects/demo/out/
  why-your-bank-statement-lies.mp4    delivery file, faststart, AAC 192k, -14 LUFS
  why-your-bank-statement-lies.jpg    thumbnail frame
  captions.srt                        upload as a caption track
  captions.vtt                        the same cues as WebVTT, for any other player
  youtube.txt / youtube.json          title, description, chapters, tags
  credits.txt                         creator attribution when stock footage was used
```

## Tests

```powershell
.venv/bin/python -m pytest
```

200 fast tests run in seconds; 12 more marked `slow` encode real video with
ffmpeg. `-m "not slow"` skips those. GitHub Actions runs the whole suite,
encodes included, on every push and pull request.

One of them runs pyflakes over the package and fails on a name that cannot
resolve. That gate exists because the stock thumbnail ranking called through an
undefined variable for months: the bare `except` around it caught the
`NameError`, logged a fallback, and shipped the first search result every time.
Nothing went red, because the only tests touching it were testing the fallback.

They exist because the same class of bug kept shipping: cache and timing
invariants that look fine until you watch the whole video. The suite pins the
ones that actually broke - a shot plan must sum to its narration slot, caption
lines must never overlap, an ASS Format row must match its Dialogue fields, a
cached clip is only reused when its real duration fits the slot, and every clip
used has to end up in the credits.

Writing them immediately found another: a sentence longer than `max_shot_seconds`
with no comma in the usable window fell through and held one 12-second shot -
exactly what the cutting is there to prevent. It now falls back to a word gap,
and to an arithmetic split if there is not even one of those.

## Running it as a web service

`web/` is a small FastAPI front: paste a script, watch the pipeline log stream,
download the mp4. Locally:

```powershell
.venv/bin/python -m uvicorn web.app:app --port 8077
```

Renders happen on a worker thread, not in the request, because a video takes
minutes. The browser polls `/api/jobs/{id}` and the progress bar tracks real
pipeline stages rather than a timer. **One render at a time, with a line behind
it** - two concurrent x264 encodes starve each other on a small box and neither
finishes sooner, so exactly one runs and a second submission waits its turn
rather than being refused. The line is bounded at three: saturated, it answers
429 as it did before, because telling the tenth caller "queued" and leaving them
for half an hour is a worse answer than a refusal that says why.

Because a render is minutes long, the page is built around not wasting them:

- **It counts the script as you type** - scenes, estimated runtime, and words
  against this instance's limit. Going over disables Render, so the 400 arrives
  while you can still edit rather than after you submit. It also names any scene
  with no `[visual:]` line, which will be searched on its own words.
- **It shows the shape of the wait.** The nine pipeline stages are drawn as a
  stepper: done, current, still to come. The list comes from the server, so it
  cannot drift out of step with what the worker actually does.
- **It says when the box is taken.** A second visitor sees the running stage and
  how long it has been going before writing anything, and a submission made
  anyway joins the line and reports its position rather than losing the work.
- **It can stop.** Cancelling is cooperative: the run ends at the next stage
  boundary, not mid-encode, which frees the queue rather than the CPU.
- **It survives a reload.** Refreshing mid-render re-attaches to the job, log and
  Stop button included, and after one finishes a reload still shows the video.

| route | what it does |
| --- | --- |
| `POST /api/jobs` | start a render, or join the line; returns a job id and its position |
| `GET /api/jobs/{id}` | status, progress, log tail, output list |
| `POST /api/jobs/{id}/cancel` | stop it at the next stage boundary |
| `GET /api/jobs/{id}/files/{name}` | download one output |
| `GET /api/jobs/{id}/description` | the paste-ready YouTube description |
| `POST /api/draft` | write a script from a topic |
| `GET /api/busy` | whether the one render slot is taken, by what, and how many are waiting |
| `GET /api/options` | aspects, themes, moods, limits, whether auth is on |
| `GET /healthz` | ffmpeg found, and whether a render is running; `keys` needs the token |
| `GET /api/docs` | generated OpenAPI docs |

### A public URL without hosting it

A Cloudflare quick tunnel puts the local server on the internet. It is free, it
needs no Cloudflare account and no domain, and the render happens on your own
machine - so it runs at full local speed instead of a hosted instance's fraction
of a CPU. The URL lasts as long as the window stays open.

```powershell
.\scripts\serve-public.ps1
```

It starts the server, mints an access token into `.env` on first run, opens the
tunnel and prints the `https://....trycloudflare.com` URL.

Pass `-NoToken` to open the tunnel with no gate at all, for when you are showing
one person for ten minutes and a token is friction rather than protection. It
comments out any token in `.env` so the server does not pick one up, and says in
red what it has done. Without the flag the script refuses to open an ungated
tunnel, because that is almost always a mistake rather than a decision.

`cloudflared` comes from `winget install Cloudflare.cloudflared`.

**The token is the point.** Any exposed instance - tunnel or host - is a renderer
that spends your Pexels and Gemini quota. Set `VIDSMITH_TOKEN` (the script does
it for you) and the API refuses anything without it; leave it unset and there is
no gate at all, which is the right default only on localhost.

`/healthz` stays open either way, so an uptime check needs no secret and a
deploy that cannot answer is distinguishable from one that is merely unhealthy.
Its `keys` field is the exception: that is an inventory of which credentials the
box holds, so it is returned only to a caller with the token. A wrong token is
told nothing rather than refused, because refusing would break the uptime check
for anyone who fat-fingers it.

### Deploying on Hugging Face

Hugging Face Spaces gives 2 vCPU and 16 GB, comfortably enough to encode 1080p,
against 0.1 vCPU and 512 MB on a free Render instance. `Dockerfile` targets it
and the Space builds the image itself, so nothing runs Docker on your machine.
Steps and caveats are in [deploy/huggingface.md](deploy/huggingface.md).

**It stopped being free.** Since 2026-08-25 a Docker Space on free cpu-basic is
refused with `402 Payment Required` and needs a PRO subscription; only Static
Spaces remain free, and a static page cannot run ffmpeg. The measured cost of a
render is small either way: a 2 vCPU box builds at roughly 2.2x realtime, so a
five-minute video is about eleven minutes of one instance.

**A public Space is a public renderer**: anyone with the URL spends your Pexels
and Gemini quota. Keep it private unless you put auth in front of it.

### Deploying on AWS

The only option here that gives a stable URL without your own machine being on.
A quick tunnel dies with its window and a Hugging Face Space now needs PRO, so
this is the route when the link has to keep working. Ubuntu with `apt` means
ffmpeg and the fonts install as packages, and nothing needs Docker.

Two vCPUs and 2 GB is the floor: a 1080p encode runs out of memory below that.
`scripts/cloud-init.sh` goes in the instance's user data and the box builds
itself while it boots, so the only manual steps are the keys and the hostname.

You do not need to buy a domain. A free DuckDNS subdomain is enough for Caddy to
get a real certificate, which matters because the page passes its token as a
query parameter and plain HTTP would send it in the clear.

Steps, the systemd unit and the billing traps are in
[deploy/aws.md](deploy/aws.md). Set `VIDSMITH_TOKEN` before the keys, not after:
unlike a tunnel this URL does not go away on its own, and the gap between
booting and gating is a window someone can walk through.

### Deploying on Render

`render.yaml` is a Render blueprint. It uses the native Python runtime, not a
container: `scripts/fetch-runtime-deps.sh` pulls a static ffmpeg into `bin/` and
the DejaVu fonts into `assets/fonts/` at build time, both of which the code
already looks in. Nothing needs Docker.

Two things that bite on a host:

- **ffmpeg is not there.** `FFMPEG_BINARY`/`FFPROBE_BINARY`, then `bin/`, then
  `PATH` - the fetch script covers the second.
- **Neither are the fonts.** The themes name Windows families, and libass will
  silently substitute something else, so `assets/fonts` is handed to the
  `subtitles` filter as `fontsdir`.

Set `PEXELS_API_KEY` and `GEMINI_API_KEY` in the dashboard, and keep
`VIDSMITH_MAX_MINUTES` honest for the instance size - encoding is CPU-bound and
a free instance is roughly ten times slower than a laptop.

## Known limits

- Edge voices are a free, undocumented Microsoft endpoint. Occasional connection
  failures are normal; each scene retries three times.
- Crossfades (`transition: fade`) re-encode the whole picture track and are much
  slower than the default hard cuts.
- `local` provider matching is filename keyword overlap, not content matching.

## Licence

Source-available under [PolyForm Noncommercial 1.0.0](LICENSE.md): personal use,
study, hobby projects, non-profits and public institutions are all covered, and
you may read, change and redistribute the source for those purposes.

Making money with it is not covered: a channel carrying ads or sponsorship,
client work, resale, or running it as a service. A commercial licence is
[**$49 once** for one person](https://buy.polar.sh/polar_cl_V1vRQDzKFWNV8dzI6VrcXma2YAmGETvuBiWP62OCuZ9),
[**$299 once** for one company](https://buy.polar.sh/polar_cl_EPwQhQnDXgtajhz7HoNumI83cBf16NeFaQFru0Q0Qw0), and a quote for
agency or client work. All perpetual, no per-video fee, no renewal.
[COMMERCIAL.md](COMMERCIAL.md) has the detail, and lists the third-party terms a
licence to this code does not grant you.

One of those is worth repeating here rather than leaving in a file nobody opens:
**the narration path is not cleared for commercial use.** `edge-tts` is an
unofficial client for the endpoint behind Edge's Read Aloud, Microsoft publishes
no terms permitting commercial use of it, and their support answers point
commercial users at Azure Speech. Personal use is uncontroversial. Anything with
revenue attached is not.

Amazon Polly is the licensed path and `voice.py` speaks it: install
`requirements-polly.txt`, set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and
`AWS_REGION`, and put `voice.provider: polly` in the project config.
`voice.name` becomes a Polly VoiceId rather than an edge-tts name, and
`voice.engine` picks `standard`, `neural` or `long-form`.

Polly reports word timings too, so the cut and the captions are unchanged. Two
things to know: it bills audio and speech marks as separate requests, so a video
spends its script length twice, and its `generative` engine returns no speech
marks at all, which is why it is not a selectable value. `vidsmith doctor` says
which keys resolve.
