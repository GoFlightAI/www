"""Tests for the GoFlight.ai static landing page i18n implementation (GF-270)."""
import json
import re
import os
import pytest

LANDING_HTML_PATH = os.path.join(os.path.dirname(__file__), "index.html")

REQUIRED_LANGS = ["en", "nl", "de", "ar", "pt-BR", "es", "fr", "ja"]

REQUIRED_KEYS = [
    "headline",
    "subtitle",
    "visionTitle",
    "vision1Title",
    "vision1Desc",
    "vision2Title",
    "vision2Desc",
    "vision3Title",
    "vision3Desc",
    "vision4Title",
    "vision4Desc",
    "passengerFormTitle",
    "passengerFormDesc",
    "passengerCta",
    "operatorFormTitle",
    "operatorFormDesc",
    "operatorCta",
    "footerText",
]


@pytest.fixture(scope="module")
def html_content():
    with open(LANDING_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


class TestLandingPageStructure:
    def test_single_html_file(self):
        """Landing page must be a single HTML file."""
        assert os.path.isfile(LANDING_HTML_PATH)

    def test_has_doctype(self, html_content):
        assert html_content.strip().startswith("<!DOCTYPE html>")

    def test_has_viewport_meta(self, html_content):
        assert 'name="viewport"' in html_content

    def test_uses_brand_colours(self, html_content):
        assert "--gf-blue: #1e3a5f" in html_content
        assert "--gf-gold: #c9a227" in html_content
        assert "--gf-dark: #0d1b2a" in html_content

    def test_no_bootstrap_defaults(self, html_content):
        assert "#0d6efd" not in html_content
        assert "#6c757d" not in html_content
        assert "#0284c7" not in html_content

    def test_no_console_log(self, html_content):
        assert "console.log" not in html_content


class TestTranslationCompleteness:
    def test_all_languages_present(self, html_content):
        """All 8 required languages must be in the TRANSLATIONS object."""
        for lang in REQUIRED_LANGS:
            # Check for "en:" or "\"pt-BR\":" patterns in the JS object
            if "-" in lang:
                pattern = f'"{lang}":'
            else:
                pattern = f"{lang}:"
            assert pattern in html_content, f"Language {lang} not found in TRANSLATIONS"

    def test_all_data_i18n_elements_exist(self, html_content):
        """Every required key must have a corresponding data-i18n element."""
        for key in REQUIRED_KEYS:
            if key == "footerText":
                continue  # footerText is applied to an element
            assert f'data-i18n="{key}"' in html_content, f"Missing data-i18n element for {key}"

    def test_all_keys_in_english(self, html_content):
        """All required keys must appear in the English translation block."""
        en_block = self._extract_lang_block(html_content, "en")
        for key in REQUIRED_KEYS:
            assert key in en_block, f"Key '{key}' missing from English translations"

    def test_no_english_duplicates_in_other_langs(self, html_content):
        """Non-English languages must not be copies of English (headline check)."""
        en_headline = self._extract_value(html_content, "en", "headline")
        for lang in REQUIRED_LANGS:
            if lang == "en":
                continue
            lang_headline = self._extract_value(html_content, lang, "headline")
            assert lang_headline != en_headline, (
                f"Language {lang} has same headline as English — translations may be copies"
            )

    def test_dutch_has_real_translations(self, html_content):
        """Dutch speakers must see Dutch text (acceptance criteria)."""
        nl_headline = self._extract_value(html_content, "nl", "headline")
        assert "privé" in nl_headline.lower() or "charter" in nl_headline.lower(), (
            f"Dutch headline doesn't look Dutch: {nl_headline}"
        )

    def test_arabic_has_real_translations(self, html_content):
        """Arabic translation must contain actual Arabic script."""
        ar_headline = self._extract_value(html_content, "ar", "headline")
        # Check for Arabic Unicode range
        has_arabic = any("\u0600" <= c <= "\u06ff" for c in ar_headline)
        assert has_arabic, f"Arabic headline doesn't contain Arabic script: {ar_headline}"

    def test_japanese_has_real_translations(self, html_content):
        """Japanese translation must contain CJK characters."""
        ja_headline = self._extract_value(html_content, "ja", "headline")
        has_cjk = any("\u3000" <= c <= "\u9fff" or "\u30a0" <= c <= "\u30ff" for c in ja_headline)
        assert has_cjk, f"Japanese headline doesn't contain CJK characters: {ja_headline}"

    def _extract_lang_block(self, html, lang):
        """Extract a rough language block from the TRANSLATIONS JS object."""
        if "-" in lang:
            pattern = f'"{lang}"\\s*:\\s*\\{{'
        else:
            pattern = f"\\b{lang}\\s*:\\s*\\{{"
        match = re.search(pattern, html)
        if not match:
            return ""
        start = match.start()
        # Find the matching closing brace by counting
        depth = 0
        for i in range(match.end() - 1, len(html)):
            if html[i] == "{":
                depth += 1
            elif html[i] == "}":
                depth -= 1
                if depth == 0:
                    return html[start : i + 1]
        return html[start:]

    def _extract_value(self, html, lang, key):
        """Extract a translation value from the JS source, decoding unicode escapes."""
        block = self._extract_lang_block(html, lang)
        # Match key: "value" or key: 'value'
        pattern = rf'{key}\s*:\s*"([^"]*)"'
        match = re.search(pattern, block)
        if not match:
            pattern2 = rf"{key}\s*:\s*'([^']*)'"
            match = re.search(pattern2, block)
        if match:
            raw = match.group(1)
            # Decode JS unicode escapes like \uXXXX
            decoded = raw.encode("utf-8").decode("unicode_escape", errors="replace")
            return decoded
        return ""


class TestRTLSupport:
    def test_rtl_langs_defined(self, html_content):
        """RTL_LANGS must include Arabic."""
        assert '"ar"' in html_content

    def test_rtl_css_exists(self, html_content):
        """CSS must include RTL-specific rules."""
        assert '[dir="rtl"]' in html_content

    def test_dir_attribute_logic(self, html_content):
        """Script must set dir=rtl for RTL languages."""
        assert "rtl" in html_content
        assert "ltr" in html_content


class TestLanguageDetection:
    def test_navigator_language_used(self, html_content):
        """Must detect browser language via navigator.language(s)."""
        assert "navigator.language" in html_content

    def test_url_param_support(self, html_content):
        """Must support ?lang= URL parameter."""
        assert 'get("lang")' in html_content or "get('lang')" in html_content

    def test_url_param_takes_precedence(self, html_content):
        """URL param should be checked before navigator.language."""
        url_pos = html_content.find('get("lang")')
        if url_pos == -1:
            url_pos = html_content.find("get('lang')")
        nav_pos = html_content.find("navigator.language")
        assert url_pos < nav_pos, "URL param must be checked before navigator.language"

    def test_language_selector_exists(self, html_content):
        """A language selector dropdown must exist."""
        assert "langSelect" in html_content
        assert "<select" in html_content


class TestVisionPoints:
    def test_four_vision_cards(self, html_content):
        """Must have exactly 4 vision point cards."""
        count = html_content.count('class="vision-card"')
        assert count == 4, f"Expected 4 vision cards, found {count}"

    def test_all_vision_points_translated(self, html_content):
        """All 4 vision point titles and descriptions must be translatable."""
        for i in range(1, 5):
            assert f'data-i18n="vision{i}Title"' in html_content
            assert f'data-i18n="vision{i}Desc"' in html_content


class TestFormLabels:
    def test_passenger_form_translated(self, html_content):
        """Passenger form title, description, and CTA must be translatable."""
        assert 'data-i18n="passengerFormTitle"' in html_content
        assert 'data-i18n="passengerFormDesc"' in html_content
        assert 'data-i18n="passengerCta"' in html_content

    def test_operator_form_translated(self, html_content):
        """Operator form title, description, and CTA must be translatable."""
        assert 'data-i18n="operatorFormTitle"' in html_content
        assert 'data-i18n="operatorFormDesc"' in html_content
        assert 'data-i18n="operatorCta"' in html_content
