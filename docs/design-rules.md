# Design rules

These aren't style preferences. Each one exists because of a specific failure mode, and
they're listed here so you can hold the code to them.

## No third-party Python packages. No downloads. No network calls. Ever.

The FFT, the WAV parser, the band analysis, the report writer — all Python standard
library. This started as a hard constraint on a machine where an extra dependency was an
unacceptable risk, and it turned out to be the right call for a second reason: **a tool an
AI agent runs should not be able to install anything.** The smaller the surface, the less
there is to audit before you let something automated use it.

**The precise scope, because this is easy to overstate:** the *analysis* has no
third-party dependencies. The *images* (`spectrogram.png`, `waveform.png`) and the *EBU
R128 loudness metrics* are produced by `ffmpeg`, which is an external program invoked as a
subprocess. Without it, the reports and every spectral check still work; the images and
the loudness numbers are reported as unmeasured.

## No orphan processes

Every external call runs with a timeout and a guaranteed kill in `try/finally`. A tool
that leaves processes hanging is unusable in CI and merely annoying on a laptop — and both
of those are the tool's fault, not the user's.

## Never fake a number

If a metric can't be measured, the report says so and the exit code reflects it. Nothing
is rounded to a friendlier value; nothing degrades silently into a default. The rule in
the code reads: *the measured number is always visible.*

A quality gate that hides its own uncertainty is worse than no gate, because you will
trust it.

This rule also governs the documentation. A claim that the tool cannot back up — a source
that was never recorded, a platform that was never tested, a validation that was never
run — is the same failure in a different file.

## No personal paths in the code

Machine-specific configuration goes in an ignored local file, never in a source comment.
If you need to point at an ffmpeg that isn't on your PATH, that is what `ffmpeg.local` and
`AISINESTES_FFMPEG` are for.

Generated reports under `out/` embed the absolute path of the analysed file, which is why
that directory is not published.

## Cross-check against a reference implementation where one exists

The in-house loudness pipeline is compared against `ffmpeg`'s EBU R128 output on every
test run. Measuring something yourself and never comparing it to an established
implementation is how a tool becomes confidently wrong.

Read the check for what it is — see [testing-strategy.md](testing-strategy.md) for its
exact scope and its limits.

## State the convention wherever the numbers appear

Bands are measured by magnitude, not power, and the two conventions differ by more than a
factor of two on real material. Any place that prints a percentage or stores a threshold
says which convention it is in, because a reader who assumes the other one will conclude
the tool is broken.
