# How this was built with AI assistance

This repository was developed with substantial AI assistance, and hiding that would be
both dishonest and less interesting than describing it. What follows is the actual
process: what was delegated, what went wrong, and what had to be corrected.

## The problem that started it

A game audio workflow had a blind spot. The assistant helping with the audio could write
and modify the code around it, but could not inspect the WAV files themselves. The only
check in place was effectively *"does the file contain audio?"*

### The track that could not be evaluated

The project needed music. The assistant composed a techno track for it using a
browser-based sound generation tool, and then had no way to judge the result. Its own
assessment amounted to *"I made something; I cannot tell you whether it is any good, or
even whether it is broken."* The honest position at that point was that a human would have
to be the ears for it.

*(The specific tool is not named here, and its project files are not published: how the
audio was produced is not what this repository is about, and the analysis works the same
on a WAV regardless of what wrote it.)*

The repository owner rejected the framing, and that rejection is the origin of this tool:

> A listener does not receive sound directly either. Air vibrates and the brain interprets
> it. You can see the vibrations — in graphs.

That reframing turns an impossibility into an engineering problem. Not hearing: **seeing**.
A model can be handed a different representation of the same physical event.

The first measurements of that track were unambiguous, and none of them had been visible
from inside the workflow that produced it:

| metric | measured | reference |
|---|---:|---|
| sub (20–60 Hz) | 42.25 % | 18–26 % |
| integrated loudness | −22.70 LUFS | −8 to −6 |
| loudness range | 0.80 LU | 5–8 |

Roughly double the sub-bass it should have had, and about 15 dB quieter than club level.

### The same blind spot, on sound effects

Then it appeared again, worse, because this time it shipped. Two impact effects were sent
for review having passed the only check in place. Both were broken: almost all of their
energy in the sub-bass, essentially nothing in the mid range that gives a hit its weight.
The feedback loop was *"they still sound weak"* → *"try adjusting the synthesis"* → repeat,
with neither side able to name the defect.

Music showed the need. The broken impacts made it urgent — and made it clear that the tool
had to handle short one-shots, not only full tracks, which is why `fx-impact` exists as a
profile at all.

### What happened to the track afterwards

Once the tool existed, the track was corrected in several rounds of export → measure →
adjust, with every change driven by a number rather than a guess:

| metric | before | after | reference |
|---|---:|---:|---|
| sub (20–60 Hz) | 42.25 % 🚩 | **25.64 %** ✅ | 18–26 % |
| sub+bass (20–120 Hz) | 52.21 % 🚩 | **48.98 %** ✅ | 48–52 % |
| integrated loudness | −22.70 LUFS 🚩 | **−12.10 LUFS** 🚩 | −8 to −6 |
| loudness range | 0.80 LU 🚩 | **0.10 LU** 🚩 | 5–8 |

The spectral balance moved into range. The loudness improved by 10.6 LU and still flags.

**And the loudness range got worse — 0.80 LU down to 0.10.** Pushing the level flattened
the dynamics further. This was not noticed at the time; it surfaced on a later measurement,
by which point the "before" and "after" files were both being used as documentation
examples.

That is the argument for the entire project, made against its own authors: **an unmeasured
fix is a guess about which trade you just made.** The tool was built because nobody in the
loop could see the audio, and the first thing it proved after being built was that they
still could not see all of it.

## What was defined by a human, before any code

- The problem, and the reframing above.
- The product requirements: what the tool takes in, what it must output, and that the
  output has to be a **verdict**, not a metric dump.
- The hard rules the code follows — no third-party packages, no downloads, no network
  calls, no orphan processes, and above all **never fake a number**. Those are in
  [design-rules.md](design-rules.md), each with the failure that motivated it.
- Every calibration decision: which reference set, which thresholds, and what counts as an
  acceptable false positive.
- The scope boundary: FX support was prioritised over broader music coverage.

## What the AI did

Implementation, technical research, test construction and documentation — including the
in-house FFT, the WAV parser, the band analysis, the harness and these documents.

Part of the work was run as several agents in parallel, each owning a disjoint set of
files, against an **interface contract written before any of them started**. That contract
turned out to be the single highest-leverage artefact in the project, and its gaps were
where the real bugs came from.

## What went wrong, and how it was caught

This section is the point of the document.

**The convention discrepancy.** The genre references were expressed as shares of spectral
*magnitude*; the analysis computed shares of *power*. The same file measured 42.9 % sub one
way and 95.5 % the other, which made every genre reference unreachable. The disagreement
was found by an agent cross-checking its own numbers against the contract, and it was
settled by **experiment** — running both conventions over the same spectrum and comparing
against a measurement that had been done by hand beforehand — rather than by argument. See
[measurement-methodology.md](measurement-methodology.md).

**A silent contract gap.** During a later pass, one agent flagged a key produced by the CLI
and consumed by the evaluator that was not in the contract at all. Left alone, it would
have degraded the attack check to "no data" **without any error** — a check that silently
stops checking is worse than a check that crashes.

**An incomplete work assignment.** The file distribution across agents had a hole: one
module was assigned to nobody. It was caught by a final sweep over the whole tree, not by
any individual agent, none of whom could have seen it.

**An invisible byte.** A machine-local configuration file was written with a tool that
prepended a byte-order mark, and the parser read the resulting comment line as a path.
The harness dropped from 34 passing cases to 28. The fix went on both sides — the parser
now tolerates the BOM — and was verified by writing the file with a BOM deliberately.

**Measurement errors by the AI, more than once.** An early sidechain measurement compared
the wrong two windows and reported a nonsensical +1900 % change. An onset detector reported
122 BPM for material that was 132. In both cases the tool was measuring something real and
the *method* was wrong — which is why the harness runs negative cases: a detector that
cannot be made to fail proves nothing.

**A documentation claim that the artefact contradicted.** The README stated that the
spectrogram renderer was part of the dependency-free core. It is not — the images are
rendered by `ffmpeg`, and the generated PNG says so in its own footer. This was found by
looking at the output rather than at the code that describes it, and corrected here and in
the README.

## How the result was verified

- **34 harness cases, 17 of them negative**, run before every change is considered done.
- **Cross-check against a reference implementation** where one exists, with its scope
  stated narrowly rather than overclaimed — see [testing-strategy.md](testing-strategy.md).
- **Real project audio**, not only synthetic fixtures: the tool's first real job was
  diagnosing the two broken impacts that motivated it, and the diagnosis matched what was
  independently measured by hand.
- **A deliberately flawed reference file that must keep flagging.** If it ever passes, the
  calibration has drifted.

## What this repository is, and is not

It is an AI-directed, human-validated project. The direction, the requirements, the
calibration criteria and the final decisions are the repository owner's; a large share of
the implementation is not hand-written by him, and presenting it otherwise would be false.

It is not a demonstration that an AI can be pointed at a problem and left alone. Every
failure listed above was caught by a human deciding what to check, by a contract written in
advance, or by a test built specifically to fail — and several of them would have shipped
silently otherwise.

---

<!-- The two sections below are the repository owner's to write in his own words.
     They are deliberately left as prompts rather than filled in by the assistant. -->

## What I still don't fully control

*(to be written by the repository owner)*

## What I learned

*(to be written by the repository owner)*
