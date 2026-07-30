# References

Where each reference value comes from — including, explicitly, the ones whose primary
sources are not yet documented.

## Status summary

| profile | values | provenance |
|---|---|---|
| `fx-impact` | sub ≤ 60 %, body ≥ 5 %, bite ≥ 0.16 %, attack ≤ 0.15 | **Fully documented.** Derived by measurement over a named, public-domain set — every threshold's derivation is in [calibration.md](calibration.md) and in the source comments of `targets.py`. |
| `techno-club` | sub ≈ 22 %, sub+bass 48–52 %, −8 to −6 LUFS-I, LRA 5–8 LU, true peak ≤ −1 dBFS | **Provenance recovered on 2026-07-30, and it does not hold up.** Of five values: one has a primary standard behind it, one has a secondary source, one was misread from its source, and two have no source at all. See below. |

## `fx-impact` — measured, reproducible

**Source set:** [Kenney](https://kenney.nl/) impact-sounds library, CC0 (public domain).
105 sounds, 21 families × 5 variants. Footsteps excluded as out of scope.

**What was obtained:** the distribution of sub / body / bite magnitude shares and attack
position across professionally made impacts, from which each threshold was placed inside a
measured gap.

**Why it was chosen:** large, freely licensed, made by practitioners, and redistributable
as a citation without licence complications. No audio from it ships in this repository.

**Reproducibility:** download the library, run the tool over the impact files with
`--genre fx-impact`, and the distributions in [calibration.md](calibration.md) should
reappear.

## `techno-club` — provenance recovered, and what it actually shows

An earlier version of this document said these sources "were never written down". They
were: the development transcripts were searched on 2026-07-30 and the origin was
recovered in full. Publishing what it shows is more useful than the previous admission,
because the finding is not that the sources were lost — it is that they do not support
four of the five values.

**The origin is a single web search**, run on 2026-07-26, with this query verbatim:

> `mixing reference targets LUFS spectral balance techno electronic music frequency
> distribution percentages`

It returned nine results. **No page was ever opened.** The values were taken from the
search engine's synthesized summary of those results, which attributes no figure to any
particular page. The two pages that carry the relevant numbers were finally read on
2026-07-30, and compared against what is in the code:

| value | in use | what the source actually says | status |
|---|---|---|---|
| sub (20–60 Hz) | ≈ 22 %, window 18–26 % | 22 % is the column **"Avg Upload"** — the measured average of tracks people upload — annotated `-5% (light on sub)`. The stated **ideal is 27 %** | ⚠️ **Misread.** The number in use is an observed average of amateur uploads, flagged by its own source as deficient |
| sub+bass (20–120 Hz) | 48–52 % | The figure **does not appear** on the page, verified by targeted re-read. Its sub+bass target for Peak-Time / Minimal Techno is **89 %** | 🚩 **Unsupported** |
| integrated loudness | −8 to −6 LUFS-I | Verbatim: *"Club-ready electronic music should be mastered to -6 to -8 LUFS integrated."* | ✅ Supported by a secondary source |
| loudness range | 5–8 LU | **Not present in any consulted source.** EBU R128 defines LRA but sets no target for it | 🚩 **No source.** A working judgement, presented until now as if it were researched |
| true peak | ≤ −1 dBFS | EBU R128: the programme must not exceed **−1 dBTP** | ✅ Supported by a primary standard |

**Two problems on top of the per-value status:**

1. **Convention and bands do not match.** The source states its metric explicitly —
   *"the % linear-power metric"* — and its Bass band runs to **250 Hz**. This tool measures
   **magnitude** over 20–60 Hz and 60–120 Hz. Even the figures that exist are not
   transferable without a stated conversion, and none was ever made. See
   [measurement-methodology.md](measurement-methodology.md) for why the two conventions
   differ by more than a factor of two on real material.
2. **The source is secondary at best.** Both pages are bylined *"Klaus™ (AI Analyst)"* and
   cite no dataset for these figures.

### What this changes, and what it does not

- **`fx-impact` is unaffected.** Its thresholds were derived by measuring 105 real impacts
  and each one sits in a measured gap; that derivation is reproducible and is documented in
  [calibration.md](calibration.md).
- **`techno-club` should be read as a heuristic profile, not a calibrated one.** It is
  useful for spotting gross problems — the track that opened this project was 15 dB below
  club level with twice the sub it should have had, and the profile caught exactly that —
  but its band thresholds should not be quoted as references.
- **No threshold was moved as a result of this.** Adopting the source's 27 % and 89 %
  would mean importing figures expressed in a different convention over different bands
  from an AI-written article with no dataset. That would be a downgrade dressed as a
  correction.

### How to actually close this

The same way `fx-impact` was closed, and no other way: measure a named, redistributable
reference set of club masters with this tool's own convention and bands, publish the
distribution, and place each threshold in a measured gap. Anything else re-imports someone
else's unstated convention.

### Sources consulted

| source | what was taken from it | consulted |
|---|---|---|
| TrackScore.AI, *Frequency Balance in Electronic Music* (Klaus™ / ed. Michael Christopher, 2026-03-24) | band table, `% linear-power` metric, the 27 % ideal and the 22 % average | 2026-07-26 (summary), 2026-07-30 (page) |
| TrackScore.AI, *What LUFS Should Your Electronic Music Track Be?* (same byline, 2026-03-24) | the −6 to −8 LUFS-I club figure | 2026-07-26 (summary), 2026-07-30 (page) |
| [EBU R 128](https://tech.ebu.ch/publications/r128) | maximum true peak −1 dBTP; and that it sets **no** LRA target | 2026-07-30 |

## Standards referenced by the tool

- **EBU R128** — the loudness standard implemented by `ffmpeg`'s `ebur128` filter, which
  is what produces the LUFS-I, LRA and true-peak figures reported here. The tool consumes
  that implementation; it does not reimplement the standard.
