"""Self-contained HTML rendering of a report and of a comparison.

Two rules shape everything in this module, and both come from a failure:

  - NO EXTERNAL REQUESTS. Not a font, not a stylesheet, not an image link. The CSS is
    inline and the PNGs travel inside the document as base64 data URIs. An HTML that
    phones somewhere else is an HTML that stops working the day it is opened offline —
    and it tells that somewhere else who is reading it.
  - ONLY THE BASENAME OF THE FILE. Never the absolute path. report.txt and report.json
    stay on the machine that produced them; the HTML is the one that gets shared, and
    a local path is a piece of personal information nobody asked to publish. Every
    dynamic string that reaches the page goes through `_safe`, which strips anything
    shaped like a path before escaping it.

The page is also deliberately dateless: no "generated at <local clock>". Two runs over
the same file produce the same bytes, which is what makes a diff between reports mean
something.

Failing to build this HTML must never change a verdict: it is a derived artifact, not a
measurement. The caller records the failure and carries on.
"""

import base64
import os

from html import escape as _escape

from aisinestes import pipeline, targets
from aisinestes.targets import fmt_num

# Visual identity of the project (same tokens as the rest of its pages). Kept in one
# string so the document has a single <style> and zero external requests.
_CSS = """
:root{
  --bg:#0c0a15; --panel:#17131f; --panel-2:#1d1826; --border:#2a2438;
  --text:#dcd7ec; --text-2:#9a92b4; --text-3:#6d6688;
  --slime:#55e0a0; --slime-dark:#2ea877; --flag:#e368b0; --gold:#e6c24d;
  --file:#5cc7e6; --code:#0a0810;
  --mono:ui-monospace,"Cascadia Code",Consolas,monospace;
}
*{box-sizing:border-box}
body{
  margin:0; padding:34px 20px 70px; min-height:100vh;
  background:
    radial-gradient(1200px 600px at 20% -10%, #1a1330, transparent 60%),
    radial-gradient(900px 500px at 100% 0%, #1a0f22, transparent 55%),
    var(--bg);
  color:var(--text); line-height:1.55;
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1000px;margin:0 auto}
.hero{
  background:linear-gradient(160deg,#1a1430,#130f1e);
  border:1px solid var(--border); border-radius:18px; padding:26px 30px 22px;
  box-shadow:0 0 60px rgba(85,224,160,.18); margin-bottom:22px;
}
.eyebrow{
  text-transform:uppercase; font-size:12px; letter-spacing:.14em;
  color:var(--slime); margin-bottom:12px;
}
h1{
  margin:0; font-size:31px; line-height:1.2; font-weight:650;
  color:var(--slime); font-family:var(--mono); word-break:break-all;
}
h1 .vs{color:var(--text-3);font-weight:400;padding:0 6px}
h1 .f{color:var(--file)}
.chips{margin-top:14px}
.chip{
  display:inline-block; background:rgba(42,36,56,.55); border:1px solid var(--border);
  border-radius:999px; padding:5px 13px; font-size:12.5px; color:var(--text-2);
  margin:6px 8px 0 0;
}
.chip b{color:var(--text);font-weight:600;font-family:var(--mono)}
.card{
  background:var(--panel); border:1px solid var(--border); border-radius:16px;
  padding:20px 24px 22px; margin-bottom:18px;
}
.card h2{
  margin:0 0 14px; font-size:12px; font-weight:700; text-transform:uppercase;
  letter-spacing:.14em; color:var(--text-3);
}
.verdict{border-left:4px solid var(--border)}
.verdict.v-clean{border-left-color:var(--slime)}
.verdict.v-flag{border-left-color:var(--flag)}
.verdict.v-gold{border-left-color:var(--gold)}
.big{font-size:34px;font-weight:700;letter-spacing:.02em;line-height:1.15}
.v-clean .big{color:var(--slime)}
.v-flag .big{color:var(--flag)}
.v-gold .big{color:var(--gold)}
.sub{color:var(--text-2);font-size:14px;margin-top:6px}
.miss{margin:14px 0 0;padding:0;list-style:none}
.miss li{
  font-family:var(--mono); font-size:12.5px; color:var(--gold);
  background:var(--code); border-left:3px solid var(--gold); border-radius:0 10px 10px 0;
  padding:7px 12px; margin-top:7px;
}
table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:13px}
th{
  text-align:left; font-size:11px; font-weight:700; text-transform:uppercase;
  letter-spacing:.09em; color:var(--text-3); padding:0 10px 9px;
  border-bottom:1px solid var(--border); white-space:nowrap;
}
td{padding:9px 10px;border-bottom:1px solid rgba(42,36,56,.55);vertical-align:top}
tr:last-child td{border-bottom:none}
.n{text-align:right;white-space:nowrap}
.dim{color:var(--text-3)}
.gold{color:var(--gold)}
.slime{color:var(--slime)}
.mag{color:var(--flag)}
.na{color:var(--gold);font-style:italic}
.st{
  display:inline-block; padding:2px 9px; border-radius:999px; font-size:11px;
  font-weight:700; letter-spacing:.06em; color:#0c0a15;
}
.st-ok{background:var(--slime)}
.st-flag{background:var(--flag)}
.tag{
  display:inline-block; padding:1px 9px; border-radius:999px; font-size:11px;
  font-weight:700; letter-spacing:.05em; border:1px solid var(--border);
  color:var(--text-3);
}
.tag-fixed{background:var(--slime);border-color:var(--slime);color:#0c0a15}
.tag-broke{background:var(--flag);border-color:var(--flag);color:#0c0a15}
.tag-flag{border-color:var(--flag);color:var(--flag)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:13px}
.pair{
  background:var(--panel-2); border:1px solid var(--border); border-radius:14px;
  padding:11px 14px 12px;
}
.pair .k{
  font-size:10.5px; text-transform:uppercase; letter-spacing:.1em; color:var(--text-3);
}
.pair .v{font-family:var(--mono);font-size:17px;margin-top:3px}
img{
  display:block; max-width:100%; height:auto; margin-top:4px;
  border:1px solid var(--border); border-radius:12px;
}
.absent{
  background:var(--code); border:1px dashed var(--border);
  border-left:3px solid var(--gold);
  border-radius:0 12px 12px 0; padding:18px 16px; color:var(--gold);
  font-family:var(--mono); font-size:13px;
}
.note{
  background:var(--code); border-left:3px solid var(--slime); border-radius:0 10px 10px 0;
  padding:10px 14px; margin-top:14px; color:var(--text-2);
  font-family:var(--mono); font-size:12.5px;
}
.foot{
  margin-top:24px; padding-top:16px; border-top:1px solid var(--border);
  color:var(--text-3); font-size:12.5px;
}
.foot code{font-family:var(--mono);color:var(--text-2)}
.scroll{overflow-x:auto}
@media (max-width:620px){
  body{padding:22px 12px 50px}
  .hero,.card{padding-left:16px;padding-right:16px}
  h1{font-size:23px}
}
"""


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _safe(value):
    """Everything that reaches the page goes through here: strip paths, then escape.

    The scrub is a second line of defence and it uses the same function the brief does:
    the values that reach a page are supposed to be basenames and numbers already, but an
    error message coming from a subprocess can carry a full path inside it and nobody
    would notice until the page had already been shared.
    """
    return _escape(pipeline.scrub_paths(value), quote=True)


def _num(value, unit="", dec=2):
    """A measured number, formatted with the same rules as the text report.

    `fmt_num` never turns a value that is not zero into "0.00" — it switches to
    scientific notation instead — and returns "no data" for None. That behaviour is
    the reason it is reused here instead of formatting by hand.
    """
    return _safe(fmt_num(value, unit, dec))


def _signed(value, dec=3):
    """A delta, always with its sign: '+11.573' / '-95.040'. None -> not measured."""
    if value is None:
        return '<span class="na">not comparable</span>'
    text = fmt_num(value, "", dec)
    if value > 0:
        text = "+" + text
    return _safe(text)


def _data_uri(path):
    """Reads a PNG and returns (data_uri, None), or (None, reason) if it cannot.

    The reason never includes the path: only the type of failure. This string is
    printed on a page meant to be shared.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as error:
        return None, "the image file could not be read (%s)" % type(error).__name__
    if not raw:
        return None, "the image file is empty"
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii"), None


def _image_card(title, images, key, alt):
    """Card with an embedded image, or an honest panel saying it is not there.

    What must never happen here is a placeholder that could pass for the real thing:
    if the image was not produced, the page says so in words.
    """
    path = (images or {}).get(key)
    if not path:
        reason = "not rendered (ffmpeg unavailable)"
    else:
        uri, reason = _data_uri(path)
        if uri:
            return ('<section class="card"><h2>%s</h2><img src="%s" alt="%s"></section>'
                    % (_safe(title), uri, _safe(alt)))
    return ('<section class="card"><h2>%s</h2><div class="absent">%s</div></section>'
            % (_safe(title), _safe(reason)))


def _pair(label, value_html):
    return '<div class="pair"><div class="k">%s</div><div class="v">%s</div></div>' % (
        _safe(label), value_html)


def _chip(label, value):
    return '<span class="chip">%s <b>%s</b></span>' % (_safe(label), _safe(value))


def _page(title, body):
    """Wraps the body in the document. Everything the page needs travels inside it."""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n"
        '<div class="wrap">\n%s\n</div>\n</body>\n</html>\n'
        % (_safe(title), _CSS, body)
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _verdict_card(data, verdict):
    """The verdict, in the three states a gate can be in — plus the one where it did
    not judge anything at all.

    A file measured with genre "none" has zero checks: calling that "CLEAN" would be
    claiming a pass nobody ever tested for, so it gets its own wording.
    """
    flags = verdict.get("flags", 0)
    total = verdict.get("checks", 0)
    unmeasured = verdict.get("unmeasured") or []

    if not total:
        css, word = "v-gold", "NOT JUDGED"
        sub = "no profile selected (genre '%s'): metrics only, nothing was judged" % (
            data.get("genre"),)
    elif flags:
        css, word = "v-flag", "FLAG"
        sub = "%d of %d checks flagged" % (flags, total)
    elif unmeasured:
        css, word = "v-gold", "INCOMPLETE"
        sub = ("0 of %d checks flagged, but %d measurement%s never happened — clean as "
               "far as it got" % (total, len(unmeasured),
                                  "" if len(unmeasured) == 1 else "s"))
    else:
        css, word = "v-clean", "CLEAN"
        sub = "0 of %d checks flagged, everything measured" % total

    parts = ['<section class="card verdict %s"><h2>Verdict</h2>' % css,
             '<div class="big">%s</div>' % _safe(word),
             '<div class="sub">%s</div>' % _safe(sub)]
    labels = pipeline.unmeasured_labels(unmeasured)
    if labels:
        parts.append('<ul class="miss">')
        for label in labels:
            parts.append("<li>%s — not measured (the reason is in report.txt)</li>"
                         % _safe(label))
        parts.append("</ul>")
    parts.append("</section>")
    return "".join(parts)


def _checks_card(checks, genre):
    if not checks:
        return ('<section class="card"><h2>Checks</h2><div class="absent">'
                "genre '%s': no checks and no references — the metrics are reported "
                "and nothing is judged</div></section>" % _safe(genre))
    rows = []
    for item in checks:
        flagged = item.get("status") == "FLAG"
        rows.append(
            '<tr><td><span class="st %s">%s</span></td><td>%s</td>'
            '<td class="%s">%s</td><td class="dim">%s</td></tr>'
            % ("st-flag" if flagged else "st-ok", _safe(item.get("status")),
               _safe(item.get("check")), "n mag" if flagged else "n",
               _safe(item.get("measured")), _safe(item.get("target"))))
    return ('<section class="card"><h2>Checks</h2><div class="scroll"><table>'
            "<tr><th>status</th><th>check</th><th>measured</th><th>target</th></tr>"
            "%s</table></div></section>" % "".join(rows))


def _bands_card(data, bands_def, images):
    measured = data.get("bands") or {}
    rows = []
    for name, hz_min, hz_max in bands_def or []:
        value = measured.get(name)
        rows.append('<tr><td>%s</td><td class="dim n">%d-%d</td><td class="n">%s</td></tr>'
                    % (_safe(name), hz_min, hz_max,
                       _num(value, "%") if value is not None
                       else '<span class="na">no data</span>'))
    numbers = [v for v in measured.values() if isinstance(v, (int, float))]
    # No band has a number: the spectrum was empty (digital silence), and 0/0 is not 0 %.
    # A "band sum: 0.00 %" line there would be a made-up total for shares that do not
    # exist, so the page says what actually happened.
    if numbers:
        sum_note = ("band sum: %s &nbsp;(sanity check: it has to come out ~100 %%)"
                    % _num(sum(numbers), "%"))
    else:
        sum_note = ("band sum: no data &nbsp;(the spectrum is empty, so there are no "
                    "shares to add up: 0/0 is not 0 %)")
    parts = ['<section class="card"><h2>Spectral distribution</h2>',
             '<div class="scroll"><table>',
             "<tr><th>band</th><th>range Hz</th><th>% magnitude</th></tr>",
             "".join(rows), "</table></div>",
             '<div class="note">%s<br>split by spectral magnitude |X|, not by power '
             "|X|&sup2;</div>" % sum_note]
    path = (images or {}).get("spectrogram")
    if path:
        uri, reason = _data_uri(path)
        if uri:
            parts.append('<img src="%s" alt="Spectrogram, logarithmic frequency axis">'
                         % uri)
        else:
            parts.append('<div class="absent">spectrogram: %s</div>' % _safe(reason))
    else:
        parts.append('<div class="absent">spectrogram: not rendered '
                     "(ffmpeg unavailable)</div>")
    parts.append("</section>")
    return "".join(parts)


def _signal_card(data):
    signal = data.get("signal") or {}
    loudness = data.get("loudness")
    onsets = data.get("onsets") or {}
    pairs = [
        _pair("duration", _num(signal.get("duration"), "s", 3)),
        _pair("sample rate", _safe("%s Hz" % signal.get("rate"))),
        _pair("channels", _safe(signal.get("channels"))),
        _pair("bits", _safe(signal.get("bits"))),
        _pair("peak", "%s <span class=\"dim\">(%s)</span>" % (
            _num(signal.get("peak"), "", 4), _num(signal.get("peak_db"), "dBFS"))),
        _pair("rms", _num(signal.get("rms_db"), "dBFS")),
        _pair("dc offset", _num(signal.get("dc_offset"), "", 6)),
    ]
    if loudness:
        pairs += [
            _pair("integrated loudness", _num(loudness.get("lufs_i"), "LUFS")),
            _pair("loudness range", _num(loudness.get("lra"), "LU")),
            _pair("true peak", _num(loudness.get("true_peak_db"), "dBFS")),
        ]
    else:
        pairs.append(_pair("loudness (EBU R128)",
                           '<span class="na">not measured</span>'))
    bpm = onsets.get("bpm")
    pairs += [
        _pair("onsets detected", _safe(onsets.get("count", "no data"))),
        _pair("bpm estimate", _num(bpm, "BPM", 1) if bpm
              else '<span class="dim">not determined</span>'),
    ]
    if data.get("attack_pos") is not None:
        pairs.append(_pair("envelope peak", "%s <span class=\"dim\">of duration (%s)</span>"
                           % (_num(data.get("attack_pos"), "", 3),
                              _num(data.get("attack_s"), "s", 3))))
    return ('<section class="card"><h2>Signal, loudness and transients</h2>'
            '<div class="grid">%s</div></section>' % "".join(pairs))


def build_report_html(data, checks, bands_def):
    """Full HTML of a report. Returns the document as a string; writes nothing.

    Self-contained by construction: inline CSS, images as base64 data URIs, no request
    to anywhere. The only name of the analysed file that appears is its basename.
    """
    data = data or {}
    basename = os.path.basename(str(data.get("file") or ""))
    verdict = data.get("verdict") or pipeline.build_verdict(data)
    signal = data.get("signal") or {}
    genre = data.get("genre")
    version = data.get("profile_version")
    images = data.get("images") or {}

    chips = [_chip("genre", "%s v%s" % (genre, version) if version else genre)]
    if signal.get("duration") is not None:
        chips.append(_chip("duration", fmt_num(signal.get("duration"), "s", 3)))
    if signal.get("rate"):
        chips.append(_chip("sample rate", "%s Hz" % signal.get("rate")))
    if signal.get("channels"):
        chips.append(_chip("channels", signal.get("channels")))

    description = (targets.GENRES.get(genre) or {}).get("description")

    body = [
        '<header class="hero"><div class="eyebrow">Aisinestes — audio report</div>',
        "<h1>%s</h1>" % _safe(basename),
        '<div class="chips">%s</div>' % "".join(chips),
        ("<div class=\"sub\">%s</div>" % _safe(description)) if description else "",
        "</header>",
        _verdict_card(data, verdict),
        _checks_card(checks, genre),
        _bands_card(data, bands_def, images),
        _signal_card(data),
        _image_card("Waveform", images, "waveform", "Waveform of the whole file"),
        '<div class="foot">',
        ("profile <code>%s</code>" % _safe("%s v%s" % (genre, version))) if version
        else ("profile <code>%s</code> (nothing judged)" % _safe(genre)),
        " &middot; percentages split spectral magnitude <code>|X|</code>, not power "
        "<code>|X|&sup2;</code> &middot; exit code <code>%d</code>"
        % int(verdict.get("exit_code", pipeline.EXIT_NO_REPORT)),
        "<br>Measured with Aisinestes. This page is self-contained: no external "
        "resources, no tracking, and only the file name — never its path.",
        "</div>",
    ]
    return _page("Aisinestes — %s" % basename, "\n".join(part for part in body if part))


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

_DIRECTION_CLASS = {
    "improved": "slime",
    "worsened": "mag",
    "unchanged": "dim",
    "not_comparable": "na",
}

_TRANSITION_TAG = {
    "fixed": ("tag-fixed", "fixed"),
    "broke": ("tag-broke", "broke"),
    "still_flag": ("tag-flag", "still flagged"),
    "still_ok": ("", "still ok"),
}


def _value_cell(value):
    if value is None:
        return '<td class="n na">not measured</td>'
    return '<td class="n">%s</td>' % _num(value, "", 3)


def _label(side):
    """What a side is called on the page: its label, or the bare basename.

    Two files can share a basename (before/impact.wav and after/impact.wav), and the page
    shows basenames only. The label is what tells them apart, by their role — never by
    their parent folder, which is the part that must not travel.
    """
    side = side or {}
    return str(side.get("label") or side.get("file") or "?")


def _missing_card(cmp):
    """Card naming every metric that lost a side, and every reason the new run has.

    The text report says this and the page used to stay quiet about it, which is the wrong
    way round: the page is the artifact that gets shared. A metric that could not be
    measured looks exactly like a metric that did not move, and that is the one confusion
    this whole tool exists to prevent.
    """
    # Imported here and not at the top on purpose: the single-file report must keep
    # working even if compare.py is not there, and only this page needs it.
    from aisinestes import compare as compare_mod

    missing = compare_mod.missing_sides(cmp)
    reasons = (cmp.get("verdict") or {}).get("unmeasured") or []
    errors = cmp.get("errors") or []
    if not missing and not reasons and not errors:
        return ""
    items = []
    for name, side in missing:
        items.append("<li>%s — NOT MEASURED on the %s file</li>"
                     % (_safe(name), _safe(side)))
    for entry in reasons:
        items.append("<li>NOT MEASURED on the new file: %s</li>" % _safe(entry))
    for entry in errors:
        items.append("<li>%s</li>" % _safe(entry))
    return ('<section class="card verdict v-gold"><h2>What could not be compared</h2>'
            '<div class="sub">These rows are not a result of zero: nobody knows what '
            'they are.</div><ul class="miss">%s</ul></section>' % "".join(items))


def build_compare_html(cmp):
    """Full HTML of a comparison between two files. Same rules as the report.

    The two files appear by basename only, and the table says out loud which way each
    metric moved: the whole point of the feature is the metric that got worse while the
    others got better, so "improved" and "worsened" are colour-coded and the OK/FLAG
    transition travels next to them as a chip.
    """
    cmp = cmp or {}
    old = cmp.get("old") or {}
    new = cmp.get("new") or {}
    genre = cmp.get("genre")
    version = cmp.get("profile_version")
    verdict = cmp.get("verdict") or {}

    rows = []
    for metric in cmp.get("metrics") or []:
        direction = metric.get("direction") or "not_comparable"
        transition = metric.get("transition") or "unknown"
        tag_class, tag_text = _TRANSITION_TAG.get(transition, ("", ""))
        tag = ('<span class="%s">%s</span>'
               % (("tag " + tag_class).strip(), _safe(tag_text)) if tag_text else "")
        rows.append(
            "<tr><td>%s</td>%s%s<td class=\"n %s\">%s</td>"
            '<td class="%s">%s</td><td>%s</td><td class="dim">%s</td></tr>'
            % (_safe(metric.get("name")),
               _value_cell(metric.get("old")), _value_cell(metric.get("new")),
               _DIRECTION_CLASS.get(direction, "dim"), _signed(metric.get("delta")),
               _DIRECTION_CLASS.get(direction, "dim"),
               _safe(direction.replace("_", " ")), tag,
               _safe(metric.get("target") or "-")))

    chips = [_chip("genre", "%s v%s" % (genre, version) if version else genre),
             _chip("old", "%s FLAG of %s" % (old.get("flags"), old.get("checks"))),
             _chip("new", "%s FLAG of %s" % (new.get("flags"), new.get("checks")))]

    gate = ""
    if verdict:
        flags = verdict.get("flags", 0)
        unmeasured = verdict.get("unmeasured") or []
        if flags:
            css, word = "v-flag", "FLAG"
        elif unmeasured:
            css, word = "v-gold", "INCOMPLETE"
        elif verdict.get("checks"):
            css, word = "v-clean", "CLEAN"
        else:
            css, word = "v-gold", "NOT JUDGED"
        gate = ('<section class="card verdict %s"><h2>Gate — it follows the new file'
                '</h2><div class="big">%s</div><div class="sub">%s: %s FLAG of %s '
                "checks &middot; exit code %d</div></section>"
                % (css, _safe(word), _safe(_label(new)),
                   _safe(new.get("flags")), _safe(new.get("checks")),
                   int(verdict.get("exit_code", pipeline.EXIT_NO_REPORT))))

    body = [
        '<header class="hero"><div class="eyebrow">Aisinestes — comparison</div>',
        '<h1><span class="f">%s</span><span class="vs">vs</span>'
        '<span class="f">%s</span></h1>'
        % (_safe(_label(old)), _safe(_label(new))),
        '<div class="chips">%s</div></header>' % "".join(chips),
        gate,
        _missing_card(cmp),
        '<section class="card"><h2>Metrics</h2><div class="scroll"><table>'
        "<tr><th>metric</th><th>old</th><th>new</th><th>delta</th><th>direction</th>"
        "<th>transition</th><th>target</th></tr>%s</table></div>" % "".join(rows),
        '<div class="note">direction reads the semantics of each check, not the sign: '
        "for a ceiling going down is better, for a floor going up is better, for a "
        "range getting closer to it is better. Crossing the threshold outranks the "
        "epsilon: if the check flipped, the direction says so however small the step "
        "was.<br><b>not comparable</b> covers two cases, told apart by the delta: "
        "target <b>-</b> means the profile has no reference for that metric (the delta "
        "is real, the judgement is not), and a delta of <b>not comparable</b> means the "
        "difference is not a number — a side was never measured, or it is not finite "
        "(digital silence has a true peak of -inf). Neither is ever reported as a zero."
        "</div></section>",
        '<div class="foot">',
        ("profile <code>%s</code> &middot; " % _safe("%s v%s" % (genre, version)))
        if version else "",
        "the comparison gates on the new file &middot; exit code <code>%d</code> "
        "&middot; percentages split spectral magnitude <code>|X|</code>, not power "
        "<code>|X|&sup2;</code>"
        % int(verdict.get("exit_code", pipeline.EXIT_NO_REPORT)),
        "<br>Measured with Aisinestes. This page is self-contained: no external "
        "resources, no tracking, and only the file names — never their paths.",
        "</div>",
    ]
    return _page("Aisinestes — %s vs %s" % (_label(old), _label(new)),
                 "\n".join(part for part in body if part))
