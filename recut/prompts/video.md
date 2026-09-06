Write a short narrated video from the claim inventory below.

{{faithfulness}}

A fabricated line is worse here than in text, because it will be spoken aloud in a
confident voice over stock footage.

SOURCE TITLE: {{title}}

CLAIM INVENTORY
---
{{claims}}
---

This becomes a 60 second vertical short. Every constraint below comes from what the
renderer can actually do, so none of them are negotiable.

- `title`: under 60 characters. A claim, not a topic.
- `scenes`: 5 to 7 of them. Each scene is:
  - `heading`: two or three words, for the editor's benefit. Never spoken.
  - `visual`: a stock footage search query, 3 to 8 words, describing a **concrete
    filmable thing**. "farmer walking away from a field at dusk" works. "economic
    decline", "the concept of trust" and "history" do not, because no camera has
    ever pointed at them. Do not name a person, a brand or a logo: those return
    nothing usable.
  - `narration`: one to three sentences, 20 to 35 words. It is read aloud, so write
    for the ear. Short sentences. No parentheses, no bullet characters, no markdown,
    no numbered lists, no "firstly". Spell out symbols: write "40 percent", not
    "40%", and "dollars" not "$".
- Total narration across all scenes: 130 to 180 words. That is the whole budget for
  60 seconds and going over it truncates the video.
- Scene 1 is the hook and gets the most surprising concrete claim in the inventory,
  stated flat. Never open by saying what the subject is.
- The final scene lands the thesis as an argument. No "subscribe", no "follow for
  more", no "let me know in the comments".
- No scene may say "as we saw" or otherwise refer to another scene, because a viewer
  arriving mid-scroll has not seen it.

Cite every claim id you used in `claim_ids`. If you write a number in narration it
must come from a `stat` claim and carry that claim's exact value, written as words.

Reply with a single JSON object and nothing else:

{
  "title": "...",
  "scenes": [
    {"heading": "The hook", "visual": "farmer walking away from a field at dusk",
     "narration": "In 450, farmers walked off their own land. Not because the soil failed, but because the state could no longer protect it."}
  ],
  "claim_ids": ["c0"]
}
