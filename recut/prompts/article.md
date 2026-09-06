Turn a talk into a written article, using the claim inventory below.

The source was spoken, not written. Speech repeats itself, circles back and leans on
the speaker's delivery to carry a point. Writing cannot do that. Your job is to give
the same argument the shape it would have had if it had been written first: state
things once, in the order that makes each one land.

{{faithfulness}}

SOURCE TITLE: {{title}}

CLAIM INVENTORY
---
{{claims}}
---

Shape:

- `title`: the argument, not the topic. Under 70 characters.
- `body`: {{low}} to {{high}} words of markdown. That range comes from how much the
  source actually contains. Do not pad to reach it: a shorter honest article beats
  a longer one carrying filler.
  - No title heading, the title is a separate field.
  - Open with the claim that makes a reader want the rest. Never open with what the
    talk was about or that it was a talk.
  - Two to six `##` sections. A section exists because the argument turns there,
    not because the speaker changed subject. Write headings in sentence case, not
    Title Case.
  - Every claim in the inventory that carries weight should appear. This is the one
    output where completeness matters more than brevity, because it is standing in
    for the source rather than pointing at it.
  - Do not write "in this video", "the speaker says", "as mentioned earlier" or any
    other reference to the source as a recording. The article is the argument, not
    a report about a recording of the argument.
  - Close on the thesis, stated plainly. No summary section, no "in conclusion".
  - Write in the register of the voice samples, if you were given any. Do not copy
    their sentences.
  - Plain sentences. No em dashes.

Cite every claim id you used in `claim_ids`. If you write a number, it must come
from a `stat` claim and it must be identical to that claim's `verbatim` text. If you
write anything in quotation marks, it must be a `quote` claim's `verbatim` text,
copied exactly.

Reply with a single JSON object and nothing else:

{"title": "...", "body": "markdown", "claim_ids": ["c0"]}
