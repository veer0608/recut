Write one X thread from the claim inventory below.

{{faithfulness}}

SOURCE TITLE: {{title}}

CLAIM INVENTORY
---
{{claims}}
---

Shape:

- 5 to 9 posts. Fewer is better than padded.
- Every post is 280 characters or fewer, counted strictly, including spaces.
- Post 1 is the hook and must stand alone as a complete statement. It must be the
  most surprising concrete claim in the inventory, not a tease, not a promise of
  what follows, and never a description of what the subject is. Surprising means the
  most specific thing the source says, never the strongest sentence you could build
  out of it.
    dead:  vidsmith turns markdown scripts into narrated videos.
    alive: Nothing transcribed the audio to place those captions.
- One idea per post. Do not split a sentence across two posts.
- Order the middle posts so each one earns the next. A list of facts in arbitrary
  order is not a thread.
- The last post lands the thesis as an argument. It is a statement, not a sign-off
  and not a summary of what the subject is. No "follow me for more", no link-drop,
  no "RT if you agree".
- The "X is a tool that does Y" pattern is banned in every post, not only the first.
  Moving it to the end does not rescue it. If the thesis you were given reads like a
  description, state the argument underneath it instead.
- Write in the register of the voice samples, if you were given any. Match their
  bluntness. Do not copy their sentences.
- No numbering like "1/", the client adds position itself.
- At most one emoji in the whole thread, and only if it carries meaning.
- Plain sentences. No em dashes.

Cite every claim id you used in `claim_ids`. If you write a number, it must come
from a `stat` claim and it must be identical to that claim's `verbatim` text. If you
write anything in quotation marks, it must be a `quote` claim's `verbatim` text,
copied exactly.

Reply with a single JSON object and nothing else:

{"posts": ["post one", "post two"], "claim_ids": ["c0", "c3"]}
