# Testing strategy

```bash
python harness/run_harness.py
```

```
SUMMARY: 78 cases in 10.1 s -> PASS=78
EXIT 0 (all PASS)
```

## Every detector has a proven negative

For each test that must pass, there is a twin that must **fail** — and the harness verifies
that it actually fails, reporting `failed as it had to` when it does.

This is the core of the strategy. A detector that cannot be made to fail is not detecting
anything; it is agreeing with itself. Thirty-nine of the seventy-eight cases exist purely
to prove the other thirty-nine aren't lying.

A concrete example from a real run:

```
x01-fx-broken   fx   pos   PASS   fx_roto.wav (fx-impact): 4 checks, 3 FLAG
x01-fx-broken   fx   neg   PASS   failed as it had to -> fx_bueno.wav: the FLAG mentioning 'sub' is missing
x02-fx-good     fx   pos   PASS   fx_bueno.wav (fx-impact): 4 checks, 0 FLAG
x02-fx-good     fx   neg   PASS   failed as it had to -> fx_roto.wav: 0 FLAG were expected and there are 3
```

The pair is symmetric on purpose: the broken file must flag, the good file must not, and
each expectation is also run against the opposite file to confirm it fails there.

## The signals are pure mathematics

All eleven WAV fixtures are generated from code — sine waves, filtered noise, exponential
decays. Delete them, run `harness/make_signals.py`, and you get back the same files **bit
for bit**. No samples, no licences, nothing borrowed.

Two of them carry the history of the project:

```python
def gen_fx_roto(dur_s=0.5):
    """A broken impact: a 45 Hz thud and nothing else.
       No body, no bite. Reproduces the real failure this tool was built for."""

def gen_fx_bueno(dur_s=0.5):
    """A good impact: the three layers the profile asks for —
       thud at 45 Hz, body at 800 + 1300 Hz, bite from noise around 5 kHz."""
```

`fx_roto.wav` is the original mistake, preserved as a test so it cannot happen twice.

## Cross-check against a reference implementation

On the synthetic 1 kHz sine, our own RMS-in-dBFS is compared against `ffmpeg`'s EBU R128
integrated loudness. They agree within **0.010 dB**.

This check is narrow by design and should be read narrowly: on a 1 kHz tone the R128
K-weighting is essentially flat, so the two figures are expected to coincide. Its value is
as a **pipeline check** — if sample scaling, bit depth or channel handling breaks, this
number moves. It does not establish that the in-house analysis implements R128, and the
documentation should never claim that it does.

The negative twin of this case verifies that the comparison can fail: it asserts an
impossible expected value and confirms the harness reports the mismatch.

## Categories covered

| family | what it checks |
|---|---|
| contract | the interface contract between the harness and the modules |
| bands | band split, magnitude convention, band sum ≈ 100 % |
| levels / noise / silence | peak, RMS, DC offset on known signals; the silence gate |
| tempo | onset detection and the BPM estimate on click tracks |
| format | PCM variants and float32 parsing |
| loudness | LUFS-I, LRA, true peak, cross-check against ffmpeg |
| fx | the `fx-impact` profile against the broken/healthy pair |
| exitcode | the real CLI in a child process: 0/1/3 semantics and their precedence |
| brief | the `--brief` output parsed line by line, path-leak guard included |
| compare | metric directions and transitions, gating on the new file |
| html | self-containment, path privacy and escaping of the real pages on disk |
| errors | missing input file: exit 2, clear message, no half report |

### The path-leak guard, and the platform it forgot

The brief, the compare brief and the HTML pages are all swept for absolute paths, because
those three are the outputs made to leave the machine. The sweep originally looked for a
Windows drive letter and nothing else — which meant that on Linux and macOS **it could not
fail**: a leaked `/home/runner/...` matched no pattern, and the case passed while testing
nothing. It now recognises the POSIX roots as well, including the system directories that
appear inside ffmpeg's own error messages.

It is worth stating plainly because the CI matrix runs on those two platforms: until this
was fixed, a green run there certified less than it looked like it did. The guard is
verified the way everything else here is — by mutation, injecting a leaking line and
confirming the suite goes red.

Some routes are exercised manually and are **not** in the harness: unreadable/corrupt
WAV and unsupported formats (verified by hand to exit 2). A claim of coverage this file
once made for them was wrong and is corrected here.

## Reproducibility

The harness is deterministic: same code, same fixtures, same results. Runtime is around
four seconds on a typical laptop — fast enough that there is no excuse for not running it
before a commit.
