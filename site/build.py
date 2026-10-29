"""Build the website in every language from template.html and strings.json.

English goes to docs/index.html, every other language to docs/<code>/.
Also writes docs/sitemap.xml with every language version of the page.
Run from anywhere: python site/build.py
"""
import datetime
import hashlib
import html
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "..", "docs")
BASE_URL = "https://m2monitor.app/"
REPO_URL = "https://github.com/IliescuM/m2monitor"
DOWNLOAD_URL = f"{REPO_URL}/releases/latest/download/M2Monitor.exe"
DEFAULT = "en"
OG_IMAGE = f"{BASE_URL}og.png"
# Flag image in docs/flags/ for each language.
FLAGS = {"en": "gb", "tr": "tr", "ro": "ro", "de": "de", "it": "it",
         "pl": "pl", "pt": "pt", "es": "es", "hu": "hu"}


def file_version(name):
    """Short content hash, so browsers fetch a changed file instead of a cached one."""
    with open(os.path.join(DOCS, name), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:10]


def page_url(lang):
    return BASE_URL if lang == DEFAULT else f"{BASE_URL}{lang}/"


def app_version():
    """The latest release tag without its "v", or None outside a git checkout."""
    try:
        tag = subprocess.run(["git", "describe", "--tags", "--abbrev=0"], cwd=HERE,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return tag.lstrip("v") or None


def seo_head(lang, s, langs, strings, version):
    """Title, description, social preview tags and structured data for one page."""
    e = lambda text: html.escape(text, quote=True)  # noqa: E731
    url = page_url(lang)
    app = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "M2 Monitor",
        "description": s["meta_description"],
        "url": url,
        "inLanguage": lang,
        "operatingSystem": "Windows 10, Windows 11",
        "applicationCategory": "UtilitiesApplication",
        "applicationSubCategory": "Game utility",
        "downloadUrl": DOWNLOAD_URL,
        "image": OG_IMAGE,
        "screenshot": OG_IMAGE,
        "isAccessibleForFree": True,
        "license": "https://opensource.org/licenses/MIT",
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "EUR"},
        "author": {"@type": "Person", "name": "IliescuM", "url": "https://github.com/IliescuM"},
    }
    if version:
        app["softwareVersion"] = version
    json_ld = json.dumps(app, ensure_ascii=False).replace("</", "<\\/")
    lines = [
        f"<title>{e(s['page_title'])}</title>",
        f'<meta name="description" content="{e(s["meta_description"])}">',
        f'<link rel="canonical" href="{url}">',
        '<meta name="theme-color" content="#0c0a1c">',
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="M2 Monitor">',
        f'<meta property="og:title" content="{e(s["page_title"])}">',
        f'<meta property="og:description" content="{e(s["meta_description"])}">',
        f'<meta property="og:url" content="{url}">',
        f'<meta property="og:image" content="{OG_IMAGE}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta property="og:image:alt" content="M2 Monitor">',
        f'<meta property="og:locale" content="{s["og_locale"]}">',
        *[f'<meta property="og:locale:alternate" content="{strings[o]["og_locale"]}">'
          for o in langs if o != lang],
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{e(s["page_title"])}">',
        f'<meta name="twitter:description" content="{e(s["meta_description"])}">',
        f'<meta name="twitter:image" content="{OG_IMAGE}">',
        f'<script type="application/ld+json">{json_ld}</script>',
    ]
    return "\n".join(lines)


def write_sitemap(langs):
    """One <url> per language, each listing all language versions."""
    today = datetime.date.today().isoformat()
    alternates = "".join(
        f'\n    <xhtml:link rel="alternate" hreflang="{other}" href="{page_url(other)}"/>'
        for other in langs)
    alternates += f'\n    <xhtml:link rel="alternate" hreflang="x-default" href="{page_url(DEFAULT)}"/>'
    urls = "".join(
        f"\n  <url>\n    <loc>{page_url(lang)}</loc>\n    <lastmod>{today}</lastmod>{alternates}\n  </url>"
        for lang in langs)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
           f'xmlns:xhtml="http://www.w3.org/1999/xhtml">{urls}\n</urlset>\n')
    with open(os.path.join(DOCS, "sitemap.xml"), "w", encoding="utf-8", newline="\n") as f:
        f.write(xml)
    print("wrote docs/sitemap.xml")


def main():
    with open(os.path.join(HERE, "template.html"), encoding="utf-8") as f:
        template = f.read()
    with open(os.path.join(HERE, "strings.json"), encoding="utf-8") as f:
        strings = json.load(f)

    langs = list(strings)
    keys = set(strings[DEFAULT])
    used = set(re.findall(r"\{\{(\w+)\}\}", template))
    generated = {"lang", "root", "seo_head", "hreflang", "langnav", "css_version", "js_version"}
    errors = [f"{lang}: missing {sorted(keys - set(s))}, extra {sorted(set(s) - keys)}"
              for lang, s in strings.items() if set(s) != keys]
    errors += [f"template uses unknown key {k}" for k in sorted(used - keys - generated)]
    if errors:
        sys.exit("\n".join(errors))

    hreflang = "\n".join(
        [f'<link rel="alternate" hreflang="{lang}" href="{page_url(lang)}">' for lang in langs]
        + [f'<link rel="alternate" hreflang="x-default" href="{page_url(DEFAULT)}">'])

    version = app_version()
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
        values = dict(strings[lang], lang=lang, root=root,
                      seo_head=seo_head(lang, strings[lang], langs, strings, version),
                      hreflang=hreflang, langnav=langnav,
                      css_version=file_version("style.css"), js_version=file_version("site.js"))
        html = re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], template)
        html = html.replace("{dl}", DOWNLOAD_URL).replace("{repo}", REPO_URL)
        # "{v}" in the strings is the latest release version.
        html = html.replace("{v}", version) if version else html.replace(" v{v}", "").replace("{v}", "")

        out_dir = DOCS if lang == DEFAULT else os.path.join(DOCS, lang)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
        print(f"wrote {os.path.relpath(os.path.join(out_dir, 'index.html'), os.path.join(HERE, '..'))}")
    write_sitemap(langs)


if __name__ == "__main__":
    main()
