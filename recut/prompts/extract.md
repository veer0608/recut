You are building a claim inventory for a piece of source content. Downstream writers
will only ever see this inventory, never the source itself. Anything you leave out
cannot be written about, and anything you record inaccurately becomes a false
statement published under someone's name.

SOURCE TITLE: {{title}}
SOURCE TYPE: {{source_type}}

The source below is split into segments. Each paragraph is prefixed with its segment
id in square brackets, for example `[s4]`. Timed sources also carry a timecode.

---
{{source}}
---

Extract every claim a writer would plausibly want to reuse. For each one:

- `text`: the claim restated in one clear sentence. Do not embellish.
  **Carry the hedge into the text.** If the source says "may", "can", "often",
  "sometimes", "in our tests", "good practice", those words go into the claim. A
  hedge dropped here cannot be recovered downstream, because the writers only ever
  see this inventory. Recording "reconciliation is necessary" when the source said
  "reconciling frequently is good practice" is the single most common way this
  pipeline ends up publishing something false.
- `kind`: one of `fact`, `stat`, `quote`, `opinion`, `narrative`.
  - `stat` for anything containing a number.
  - `quote` for words attributed to a named person.
  - `opinion` for the author's judgement rather than a checkable fact.
  - `narrative` for an anecdote or example.
- `segment_ids`: every segment that supports the claim. This is mandatory. A claim
  you cannot anchor to at least one segment will be discarded, so do not guess ids
  and do not invent them.
- `verbatim`: for `stat` and `quote` only, the exact characters from the source that
  carry the number or the quoted words. Copy them character for character. Leave
  null for every other kind.

Also return:

- `thesis`: the single argument the source is making, in one sentence. An argument,
  not a description. "X is a tool that does Y" is a description and it is wrong here
  even when it is accurate. Ask what the source is trying to convince you of.
    description: vidsmith turns markdown scripts into narrated videos.
    argument:    caption timing should come from the speech engine, not from
                 transcribing the audio back.
- `entities`: every person, company, product and place named in the source, spelled
  exactly as the source spells them.
- `hook_candidates`: up to five short openers the source itself supports. Each must
  be the most surprising or most specific thing the source says, not a summary of
  what the source is. "Your bank statement is not a record of what you spent" is a
  hook. "This article explains bank statements" is not. No hook may assert anything
  the source does not.
- `voice_samples`: up to five sentences copied character for character from the
  source, chosen because they show how it sounds: its rhythm, its bluntness, the
  kind of detail it reaches for. Copy exactly. A paraphrase here is useless and
  will be discarded.

Rules that override anything else:

1. Never merge two figures into a new one. If the source says 40 and 60, do not
   write 100 unless the source does.
2. Never round, convert currencies, or restate a number in a different unit.
3. Never attribute an unattributed statement to a person.
4. If the source is thin, return few claims. A short honest inventory is correct;
   padding it is the failure mode.

Reply with a single JSON object and nothing else:

{
  "thesis": "...",
  "claims": [
    {"id": "c0", "text": "...", "kind": "stat", "segment_ids": ["s3"], "verbatim": "38 percent"}
  ],
  "entities": ["..."],
  "hook_candidates": ["..."],
  "voice_samples": ["..."]
}
