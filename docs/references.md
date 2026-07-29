# References

Where each reference value comes from — including, explicitly, the ones whose primary
sources are not yet documented.

## Status summary

| profile | values | provenance |
|---|---|---|
| `fx-impact` | sub ≤ 60 %, body ≥ 5 %, bite ≥ 0.16 %, attack ≤ 0.15 | **Fully documented.** Derived by measurement over a named, public-domain set — every threshold's derivation is in [calibration.md](calibration.md) and in the source comments of `targets.py`. |
| `techno-club` | sub ≈ 22 %, sub+bass 48–52 %, −8 to −6 LUFS-I, LRA 5–8 LU, true peak ≤ −1 dBFS | **Sources not recorded.** See below. |

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

## `techno-club` — values in use, sources undocumented

The values below are the ones the tool currently judges against. They were researched
against published mastering references for the style during development, but **the
specific titles, authors and links were never written down**, and reconstructing them
after the fact from memory would be fabrication.

| value | in use |
|---|---|
| sub (20–60 Hz) | ≈ 22 %, window 18–26 % |
| sub+bass (20–120 Hz) | 48–52 % |
| integrated loudness | −8 to −6 LUFS-I |
| loudness range | 5–8 LU |
| true peak | ≤ −1 dBFS |

**What can be said honestly right now:**

- The loudness figures are consistent with widely used club/streaming mastering practice,
  where true peak ≤ −1 dBFS is the standard headroom allowance for lossy codecs.
- The band shares are expressed in this tool's magnitude convention (see
  [measurement-methodology.md](measurement-methodology.md)) and are **not** transferable to
  any published figure without confirming which convention that figure uses. This alone
  makes citing an external source non-trivial: most published references do not state their
  convention.

**What should not be said:** that these numbers are "researched" without being able to
show what was read. Until the sources are re-established, treat `techno-club` as a
working profile, not a citable standard.

### To close this gap

For each value: title, author, publication or institution, link, the specific figure
taken, the reason it was chosen, the convention it is expressed in, and the date consulted.
Where a published figure uses the power convention, the conversion has to be stated
explicitly rather than assumed.

## Standards referenced by the tool

- **EBU R128** — the loudness standard implemented by `ffmpeg`'s `ebur128` filter, which
  is what produces the LUFS-I, LRA and true-peak figures reported here. The tool consumes
  that implementation; it does not reimplement the standard.
