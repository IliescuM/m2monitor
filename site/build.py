"""Build the website in every language from template.html and strings.json.

English goes to docs/index.html, every other language to docs/<code>/.
Run from anywhere: python site/build.py
"""
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "..", "docs")
BASE_URL = "https://m2monitor.app/"
DOWNLOAD_URL = "https://github.com/IliescuM/m2monitor/releases/latest/download/M2Monitor.exe"
DEFAULT = "en"
# Flag image in docs/flags/ for each language.
FLAGS = {"en": "gb", "tr": "tr", "ro": "ro", "de": "de", "it": "it",
         "pl": "pl", "pt": "pt", "es": "es", "hu": "hu"}


def file_version(name):
    """Short content hash, so browsers fetch a changed file instead of a cached one."""
    with open(os.path.join(DOCS, name), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:10]


def page_url(lang):
    return BASE_URL if lang == DEFAULT else f"{BASE_URL}{lang}/"


def main():
    with open(os.path.join(HERE, "template.html"), encoding="utf-8") as f:
        template = f.read()
    with open(os.path.join(HERE, "strings.json"), encoding="utf-8") as f:
        strings = json.load(f)

    langs = list(strings)
    keys = set(strings[DEFAULT])
    used = set(re.findall(r"\{\{(\w+)\}\}", template))
    generated = {"lang", "root", "canonical", "hreflang", "langnav", "css_version", "js_version"}
    errors = [f"{lang}: missing {sorted(keys - set(s))}, extra {sorted(set(s) - keys)}"
              for lang, s in strings.items() if set(s) != keys]
    errors += [f"template uses unknown key {k}" for k in sorted(used - keys - generated)]
    if errors:
        sys.exit("\n".join(errors))

    hreflang = "\n".join(
        [f'<link rel="alternate" hreflang="{lang}" href="{page_url(lang)}">' for lang in langs]
        + [f'<link rel="alternate" hreflang="x-default" href="{page_url(DEFAULT)}">'])

    for lang in langs:
        root = "" if lang == DEFAULT else "../"
        def flag(code):
            return f'<img class="flag" src="{root}flags/{FLAGS[code]}.svg" alt="" width="22" height="16">'

        items = "".join(
            f'<li><a href="{root}{"" if other == DEFAULT else other + "/"}" hreflang="{other}" '
            f'lang="{other}" data-lang="{other}"'
            + (' aria-current="page"' if other == lang else "")
            + f'>{flag(other)}{strings[other]["lang_name"]}</a></li>'
            for other in langs)
        langnav = (f'<details class="lang-menu"><summary aria-label="{strings[lang]["lang_label"]}">'
                   f'{flag(lang)}<span>{lang.upper()}</span></summary><ul>{items}</ul></details>')
        values = dict(strings[lang], lang=lang, root=root, canonical=page_url(lang),
                      hreflang=hreflang, langnav=langnav,
                      css_version=file_version("style.css"), js_version=file_version("site.js"))
        html = re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], template)
        html = html.replace("{dl}", DOWNLOAD_URL)

        out_dir = DOCS if lang == DEFAULT else os.path.join(DOCS, lang)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
        print(f"wrote {os.path.relpath(os.path.join(out_dir, 'index.html'), os.path.join(HERE, '..'))}")


if __name__ == "__main__":
    main()
