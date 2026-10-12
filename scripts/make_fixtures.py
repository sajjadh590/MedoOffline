"""
scripts/make_fixtures.py
Generate tests/fixtures/before.mhtml and tests/fixtures/after.mhtml
using the EXACT DOM structure observed on medofast.ir/qbank.

Sample question (from spec):
  Stem  : 55-year-old woman, T2DM on metformin, HbA1c 8.7%, EF 35%, CVD hx.
           Which drug class to add?
  Options: 1 Thiazolidinedione  2 DPP4I  3 SGLT2 inhibitor  4 GLP1 Agonist
  Correct: 3
  Votes  : 2 / 3 / 86 / 8
  Marker : (تایید گزینه 3)

Run once:  python scripts/make_fixtures.py
"""
import base64
from pathlib import Path

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


# Invisible / zero-width characters the real site injects between letters
ZW = "\u200b\u200c\u200d\u200e\u200f\u2060\ufeff"


def inject_invisible(text: str) -> str:
    """Sprinkle zero-width chars into a string to simulate site obfuscation."""
    out = []
    zw_chars = list(ZW)
    for i, ch in enumerate(text):
        out.append(ch)
        if i % 3 == 1:
            out.append(zw_chars[i % len(zw_chars)])
    return "".join(out)


# ---------------------------------------------------------------------------
# Option data
# ---------------------------------------------------------------------------

OPTIONS = [
    (1, "Thiazolidinedione",  "VGhpYXpvbGlkaW5lZGlvbmU=", "Mg=="),   # 2 %
    (2, "DPP4I",              "RFBQNEI=",                  "Mw=="),   # 3 %
    (3, "SGLT2 inhibitor",    "U0dMVDIgaW5oaWJpdG9y",     "ODY="),   # 86%
    (4, "GLP1 Agonist",       "R0xQMSBBZ29uaXN0",         "OA=="),   # 8 %
]

STEM_TEXT = (
    "خانمی ۵۵ ساله با سابقه یک سال دیابت نوع ۲ تحت درمان با متفورمین "
    "۲ گرم در روز. FBS=175 mg/dL, HbA1c=8.7%, BMI=28، سابقه بیماری "
    "قلبی-عروقی و سرطان تیروئید مدولاری، BP=145/95، EF=35%. "
    "کدام دسته دارویی باید اضافه شود؟"
)

EXPLANATION_TEXT = (
    "مهارکننده‌های SGLT2 (مانند امپاگلیفلوزین، داپاگلیفلوزین) در بیماران "
    "با دیابت نوع ۲ و نارسایی قلبی با EF کاهش‌یافته (HFrEF) علاوه بر "
    "کنترل قند، مرگ‌ومیر قلبی-عروقی را کاهش می‌دهند. "
)


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def make_html(*, submitted: bool) -> str:
    # Decoy class names (random per page load — not stable)
    decoy_class1 = "GF77T0lx SDgTdCy2"
    decoy_class2 = "Z569e3f3 DOun5FSF"

    # ---- Stem ----
    stem_html = (
        f'<p>{inject_invisible(STEM_TEXT)}'
        f'<i>{"" if not submitted else " "}</i>'
        f'<img src="https://s3-strg.medofast.ir/qimages/q999_stem.png" alt="ECG"></p>'
    )
    # After submit: <i> tags get inline colour style
    if submitted:
        stem_highlight = (
            '<i style="font-style: normal; color: rgb(231,76,60); font-weight:700">'
            'قلبی-عروقی</i>'
        )
    else:
        stem_highlight = '<i>قلبی-عروقی</i>'

    # ---- Options ----
    def option_block(idx: int, text: str, enc_text: str, enc_pct: str) -> str:
        if submitted:
            if idx == 3:
                label_style = ' style="color: rgb(48, 127, 226);"'
                bar_style   = f' style="width:86%; background-color: rgb(48,127,226);"'
            else:
                pct_map = {1: 2, 2: 3, 4: 8}
                pct = pct_map.get(idx, 0)
                label_style = ' style="color: rgb(239, 64, 86);"'
                bar_style   = f' style="width:{pct}%; background-color: rgb(239,64,86);"'
            progress_display = 'style="display:block;"'
            radio_disabled   = ' disabled'
        else:
            label_style      = ''
            bar_style        = ''
            progress_display = 'style="display:none;"'
            radio_disabled   = ''

        return f"""
      <label id="label{idx}" for="{idx}"{label_style}>
        <input type="radio" name="answer" value="{idx}" id="{idx}"{radio_disabled}>
        <span class="wrappable encoded-option" inert
              data-encoded-text="{enc_text}"
              data-decoded="true"
              data-xjunk="{b64('junk-css-here')}">{inject_invisible(text)}</span>
      </label>
      <div id="progress{idx}" class="progress" {progress_display}>
        <div id="prograssBar{idx}" class="progress-bar"
             data-encoded-percent="{enc_pct}"{bar_style}></div>
      </div>"""

    options_html = "\n".join(
        option_block(i, txt, enc, pct) for i, txt, enc, pct in OPTIONS
    )

    # ---- Explanation block ----
    expl_display = 'style="display:block;"' if submitted else 'style="display:none;"'
    marker_span  = (
        '<span style="color:hsl(210,75%,60%)">(تایید گزینه 3)</span>'
    )

    expl_block = f"""
    <div id="block-b" {expl_display}>
      <div class="qtext-after" inert>
        <h5>مهارکننده‌های SGLT2 در نارسایی قلبی</h5>
        <p>{inject_invisible(EXPLANATION_TEXT)}{marker_span}</p>
        <ul>
          <li><strong>مکانیسم:</strong> مهار انتقال‌دهنده سدیم-گلوکز نوع ۲ در کلیه</li>
          <li><strong>داروها:</strong> امپاگلیفلوزین، داپاگلیفلوزین، کاناگلیفلوزین</li>
          <li><strong>موارد منع:</strong> GFR &lt; 30، کتواسیدوز دیابتی</li>
        </ul>
        <div id="image-container">
          <img src="https://s3-strg.medofast.ir/mindmaps/mm_sglt2_cardiac.png"
               alt="mindmap SGLT2 cardiac benefits">
        </div>
        <div class="watermark">MedoFast.ir</div>
      </div>
    </div>"""

    # ---- Decoy blocks (same-level siblings, random class names, hidden by CSS) ----
    decoy1 = f"""
    <div class="{decoy_class1}">
      <p>{inject_invisible("درمانکاتاتونیاصحیحکدام")}</p>
      <label id="label1_decoy">گزینه جعلی الف</label>
    </div>"""
    decoy2 = f"""
    <div class="{decoy_class2}">
      <p>{inject_invisible("آنتیبیوتیکمناسببرایعفونت")}</p>
      <label id="label2_decoy">گزینه جعلی ب</label>
    </div>"""

    # Submit / nav buttons
    submit_btn = (
        '<button id="show-b-btn" style="display:none;">ثبت و مشاهده پاسخ صحیح</button>'
        if submitted else
        '<button id="show-b-btn">ثبت و مشاهده پاسخ صحیح</button>'
    )
    prev_btn = '<button data-nav-role="prev" data-page="1" class="arrow-button turned-off">سوال قبلی</button>'
    next_btn = '<button data-nav-role="next" data-page="2" class="arrow-button-reverse">سوال بعدی</button>'

    # Points popup (noise)
    popup = '<div id="pointPopup" style="display:none;">10 امتیاز جدید دریافت کردی!</div>'

    # Obfuscating CSS (class names change per load — our parser must NOT rely on them)
    obf_css = f"""
    <style>
      .{decoy_class1.split()[0]} {{ display: none !important; }}
      .{decoy_class2.split()[0]} {{ display: none !important; }}
    </style>"""

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="UTF-8">
  <title>بانک سوالات پزشکی - مدوفست</title>
  {obf_css}
  <style>
    body {{ font-family: Vazirmatn, sans-serif; direction: rtl; }}
    .progress {{ display: none; }}
  </style>
</head>
<body>
  <div id="yektanet-ad"></div>

  <!-- Real question stem (first visible block) -->
  <div id="q-stem-container">
    {stem_html.replace('<i>قلبی-عروقی</i>', stem_highlight)}
  </div>

  <!-- Decoy stems (siblings, hidden by obfuscated CSS class) -->
  {decoy1}
  {decoy2}

  <!-- Options -->
  <div id="options-container">
    {options_html}
  </div>

  {submit_btn}

  {expl_block}

  {popup}

  <!-- Navigation -->
  <div id="nav-buttons">
    {prev_btn}
    {next_btn}
    <button id="end">لیست سوالات</button>
  </div>

  <div id="mediaad"></div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# MHTML wrapper
# ---------------------------------------------------------------------------

def wrap_mhtml(html: str, url: str) -> str:
    import base64
    boundary = "----=_NextPart_MedoFixture_001"
    html_b64 = base64.b64encode(html.encode("utf-8")).decode()
    # Wrap at 76 chars per MIME spec; use only \n (LF) so Python email parser
    # works cleanly on both Windows and Linux regardless of file open mode.
    wrapped = "\n".join(html_b64[i:i + 76] for i in range(0, len(html_b64), 76))

    lines = [
        f"MIME-Version: 1.0",
        f'Content-Type: multipart/related; boundary="{boundary}"',
        f"Subject: {url}",
        f"Snapshot-Content-Location: {url}",
        f"",
        f"--{boundary}",
        f"Content-Type: text/html; charset=utf-8",
        f"Content-Transfer-Encoding: base64",
        f"Content-Location: {url}",
        f"",
        wrapped,
        f"",
        f"--{boundary}--",
        f"",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    url = "https://medofast.ir/qbank/topic-id-79"

    before_html = make_html(submitted=False)
    after_html  = make_html(submitted=True)

    # Write as binary to avoid Windows adding extra \r before \n
    (FIXTURES / "before.mhtml").write_bytes(wrap_mhtml(before_html, url).encode("ascii"))
    (FIXTURES / "after.mhtml").write_bytes(wrap_mhtml(after_html,  url).encode("ascii"))
    print(f"[OK] Fixtures written to {FIXTURES}")
    print(f"  before.mhtml  {(FIXTURES / 'before.mhtml').stat().st_size:,} bytes")
    print(f"  after.mhtml   {(FIXTURES / 'after.mhtml').stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
