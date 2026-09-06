Write one LinkedIn post from the claim inventory below.

{{faithfulness}}

SOURCE TITLE: {{title}}

CLAIM INVENTORY
---
{{claims}}
---

Shape:

- 120 to 220 words. Longer gets truncated by the feed.
- The first line is the whole post's job. It must be the most surprising concrete
  claim in the inventory, stated flat, as a fact about the world. Surprising means
  the most *specific* thing the source says, never the strongest sentence you could
  build out of it. If the sharpest honest line is mild, use the mild line.

  Never open by describing what the subject is or does. "X is a tool that turns A
  into B", "X automates C", "X is a framework for D" are all the same dead opener
  and all of them lose the reader. Compare:
    dead:  vidsmith turns markdown scripts into narrated videos.
    alive: Nothing transcribed the audio to place those captions.
  The second one is a claim. Start there and let the reader work out the context.

  No question openers, no "Here's the thing", no "Let that sink in", no "I've been
  thinking about".
- Short paragraphs, one to three lines each, blank line between them.
- Write in the register of the voice samples, if you were given any. Match their
  bluntness and their appetite for specifics. Do not copy their sentences.
- Prefer the specific detail over the general capability. A number, a name or a
  mechanism from the inventory beats an adjective every time.
- End with one line that is either a genuine question or a plain statement. Not
  both. No "Thoughts?" and no engagement bait.
- At most three hashtags, lowercase, at the very end. None is also fine.
- No emoji unless the inventory itself is about something playful.
- Plain sentences. No em dashes.

Cite every claim id you used in `claim_ids`. If you write a number, it must come
from a `stat` claim and it must be identical to that claim's `verbatim` text. If you
write anything in quotation marks, it must be a `quote` claim's `verbatim` text,
copied exactly.

Reply with a single JSON object and nothing else:

{"body": "the post text", "claim_ids": ["c0", "c3"]}
