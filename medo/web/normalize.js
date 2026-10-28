/*
 * medo/web/normalize.js
 * One search canonicalisation for the browser, the Python server and Android.
 *
 * Persian users type the same word in several ways: Arabic ي/ك instead of
 * Persian ی/ک, with or without the half-space (ZWNJ), Latin or Persian digits,
 * and the site itself sprinkles invisible variation selectors inside text.
 * A plain substring search matched almost none of those, so every query is
 * folded through this one function — and the stored text is folded once at
 * load.  ``tests/search_vectors.json`` is the shared contract; the Python
 * (medo/text_utils.py) and Java (Text.java) copies must agree with it.
 *
 * Loadable both as a <script> (window.medoNormalizeSearch) and from Node
 * (`require('./normalize.js')`) so the test can compare all three.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.medoNormalizeSearch = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // Same set as medo/text_utils.py: ZWNJ is removed here on purpose.
  var INVISIBLE = /[\u00AD\u034F\u061C\u200B\u200C\u200D-\u200F\u202A-\u202E\u2060-\u2069\uFE00-\uFE0F\uFEFF]/g;
  var ARABIC = /[\u0643\u064A\u0649]/g;
  var ARABIC_MAP = { '\u0643': '\u06A9', '\u064A': '\u06CC', '\u0649': '\u06CC' };
  var DIGITS = /[\u06F0-\u06F9\u0660-\u0669]/g;

  function digit(ch) {
    var code = ch.charCodeAt(0);
    return String.fromCharCode(code >= 0x06F0 ? code - 0x06F0 + 48 : code - 0x0660 + 48);
  }

  function normalizeSearch(value) {
    var text = String(value === null || value === undefined ? '' : value);
    if (typeof text.normalize === 'function') {
      text = text.normalize('NFKC');
    }
    text = text.replace(INVISIBLE, '')
      .replace(ARABIC, function (ch) { return ARABIC_MAP[ch]; })
      .replace(DIGITS, digit);
    return text.replace(/\s+/g, ' ').trim().toLowerCase();
  }

  return normalizeSearch;
});
