# Commercial use

`LICENSE.md` (PolyForm Noncommercial 1.0.0) covers personal use, study, hobby
projects and non-profits. It does not cover using recut to make money: content
marketing for a business, client work, agency output, or running it as a service
for other people.

For that, a commercial licence is available. Email veer0608 through
[github.com/veer0608](https://github.com/veer0608) with roughly what you do and you
will get a number.

Tiers and checkout links are not set up yet. Until they are, this file is the whole
process: ask, and a licence gets written naming you or your company.

## What you would be buying

A repurposing tool that refuses to publish what the source does not support.

The claim is not that the output is good prose, though it tries to be. The claim is
that every sentence traces back to a span of the source, that fabricated figures,
quotes and names are caught deterministically before you ever see them, and that the
rate at which the whole thing still fails is measured and published rather than
asserted. As of the v1 golden run that rate is **15.5% of published sentences**,
over 258 judged claims and 15 real sources. See the README for what that number is
made of and where it is worst.

Nobody else selling this shows you a number at all. That is the thing being sold.

## What it does not do

- It does not check whether the source itself is true. Garbage in, faithfully
  attributed garbage out.
- It does not catch inflation reliably. The deterministic layer catches invented
  tokens at 100% on planted fabrications, but restating a hedged claim more strongly
  uses only real tokens, and the `intensity` rule catches roughly a third of those.
- It has been measured on English text under about 40k characters. Longer sources,
  and every other language, are unmeasured.

## Third-party terms you are still responsible for

- **Model providers.** recut calls Gemini and Groq. Their terms, quotas and pricing
  are between you and them. The commercial licence covers recut, not their APIs.
- **Sources you feed it.** Repurposing someone else's article does not grant you
  rights to their words. recut copies verbatim spans into quotes and voice samples
  by design, which is what makes it faithful and also what makes attribution your
  responsibility, not the tool's.
- **vidsmith.** The `vidsmith` output target emits a project for a separate tool
  under its own PolyForm Noncommercial licence. Commercial video output needs a
  vidsmith commercial licence too. See
  [veer0608/vidsmith](https://github.com/veer0608/vidsmith).
- **Stock footage and voices**, if you build the emitted video project. Those carry
  their own terms.

## Not legal advice

This file describes intent in plain language. `LICENSE.md` is the licence that
actually governs, and a commercial licence, once issued, governs over both.
