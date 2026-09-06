Write one email newsletter issue from the claim inventory below.

{{faithfulness}}

SOURCE TITLE: {{title}}

CLAIM INVENTORY
---
{{claims}}
---

Shape:

- `subject`: under 60 characters. It is a claim, not a label. "The date on your
  statement is not the date you paid" is a subject. "This week's newsletter" and
  "Thoughts on bank statements" are not. No colons splitting a topic from a teaser.
  No emoji.
- `preview`: under 90 characters, the line the inbox shows after the subject. It
  must add something the subject did not, never restate it.
- `body`: 300 to 600 words of markdown.
  - Open on the most surprising concrete claim in the inventory. Never open by
    describing what the subject is: "X is a tool that does Y" is a dead opener.
  - Two to four `##` sections, each earning the next. No section is a list of
    facts in arbitrary order.
  - Close with exactly one call to action, and only one. It must follow from what
    the issue argued. No "reply and let me know", no "forward this to a friend",
    no postscript stacking a second ask.
  - Write in the register of the voice samples, if you were given any. Match their
    bluntness and their appetite for specifics. Do not copy their sentences.
  - Plain sentences. No em dashes.

Cite every claim id you used in `claim_ids`. If you write a number, it must come
from a `stat` claim and it must be identical to that claim's `verbatim` text. If you
write anything in quotation marks, it must be a `quote` claim's `verbatim` text,
copied exactly.

Reply with a single JSON object and nothing else:

{"subject": "...", "preview": "...", "body": "markdown", "claim_ids": ["c0"]}
