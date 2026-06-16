import os
import re
import json
import time
import hashlib
from datetime import datetime
from urllib.parse import urljoin, urlparse

import streamlit as st
import streamlit.components.v1 as components
import feedparser
import requests
from bs4 import BeautifulSoup
from mistralai import Mistral

# ==============================
# CONFIGURATION MISTRAL
# ==============================

client = Mistral(api_key="yLoE1iD8DZpbusRDpVQ44wmyc2uIqaTx")

MAX_CHARS = 12000  # Limite envoyée à Mistral

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

SCRAPE_PRESETS = {
    "wordpress": {
        "link_selectors": [
            "article h2 a",
            "article h3 a",
            ".post-title a",
            ".entry-title a",
            "h2.entry-title a",
        ],
        "max_items": 20,
    },
    "generic": {
        "link_selectors": [
            "article a[href]",
            "main h2 a",
            "main h3 a",
            ".views-row a[href]",
        ],
        "max_items": 15,
    },
}


# ==============================
# FONCTION : EXTRACTION ARTICLE COMPLET
# ==============================

@st.cache_data(show_spinner=False)
def extraire_texte_article(url):
    paras = _extraire_paragraphes_web(url)
    return "\n\n".join(paras)


# ==============================
# FONCTION : NETTOYAGE HTML SIMPLE (fallback)
# ==============================

def nettoyer_html(raw_html):
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', raw_html)


# ==============================
# FONCTION : GENERATION RESUME
# ==============================

def generer_resume(texte):
    try:
        texte_utilise = texte[:MAX_CHARS]

        response = client.chat.complete(
            model="mistral-small-latest",
            messages=[
                {
                    "role": "user",
                    "content": f"""
Fais un résumé de l'article suivant en 5 points majeurs.

Le résumé commencera par une problématique générale formulée sous forme de question paradoxale.

Ensuite :
- 5 points différenciés par des emojis adaptés
- Chaque point contient, au format bullet point :
    • un titre court
    • une explication synthétique
    • si présent dans l'article : un chiffre ou exemple précis

Article :
{texte_utilise}
"""
                }
            ]
        )

        return response.choices[0].message.content

    except Exception as e:
        return f"Erreur Mistral : {str(e)}"


# ==============================
# FLUX RSS (inchangés)
# ==============================

RSS_FEEDS = {
    "🧠 La Vie des Idées": "https://laviedesidees.fr/spip.php?page=backend",
    "🌍 Diploweb": "https://www.diploweb.com/spip.php?page=backend",
    "📚 Telos": "https://www.telos-eu.com/fr/rss.xml",
    "📰 Institut Montaigne": "https://www.institutmontaigne.org/rss.xml",
    "👁 Les Yeux du Monde": "https://les-yeux-du-monde.fr/feed"
}

NOTEBOOKLM_URL = "https://notebooklm.google.com"

MOIS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre"
]


# ==============================
# HELPERS : METADONNEES ARTICLE
# ==============================

def _split_emoji(label):
    """Sépare l'emoji du nom de la source ('🧠 La Vie des Idées')."""
    parts = label.split(" ", 1)
    if len(parts) == 2 and not parts[0][0].isalnum():
        return parts[0], parts[1]
    return "📰", label


def _extraire_image(entry):
    """Trouve la meilleure image disponible pour un article de flux."""
    candidats = []

    for enc in entry.get("enclosures", []) or []:
        href = enc.get("href") or enc.get("url")
        if href and ("image" in (enc.get("type", "") or "")
                     or re.search(r"\.(jpe?g|png|webp|gif)", href, re.I)):
            candidats.append(href)

    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure":
            href = link.get("href")
            if href and ("image" in (link.get("type", "") or "")
                         or re.search(r"\.(jpe?g|png|webp|gif)", href, re.I)):
                candidats.append(href)

    for media in (entry.get("media_content") or []):
        if media.get("url"):
            candidats.append(media["url"])
    for thumb in (entry.get("media_thumbnail") or []):
        if thumb.get("url"):
            candidats.append(thumb["url"])

    html_blob = ""
    if entry.get("content"):
        html_blob += entry["content"][0].get("value", "")
    html_blob += entry.get("summary", "")
    if html_blob:
        soup = BeautifulSoup(html_blob, "html.parser")
        img = soup.find("img")
        if img:
            src = img.get("src") or img.get("data-src")
            if src:
                candidats.append(src)

    for src in candidats:
        if src and src.startswith("http"):
            return src
    return None


def _date_lisible(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return "", 0
    try:
        dt = datetime(*parsed[:6])
        return f"{dt.day} {MOIS_FR[dt.month - 1]} {dt.year}", time.mktime(parsed)
    except Exception:
        return "", 0


def _excerpt(summary_html, limite=240):
    texte = nettoyer_html(summary_html or "")
    texte = re.sub(r"\s+", " ", texte).strip()
    if len(texte) <= limite:
        return texte
    coupe = texte[:limite].rsplit(" ", 1)[0]
    return coupe + "…"


def _liens_scrape(source_url, preset="generic"):
    """Extrait les liens d'articles depuis une page de publications."""
    cfg = SCRAPE_PRESETS.get(preset, SCRAPE_PRESETS["generic"])
    try:
        response = requests.get(source_url, headers=REQUEST_HEADERS, timeout=15)
        response.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    base_netloc = urlparse(source_url).netloc
    seen = set()
    items = []

    for sel in cfg["link_selectors"]:
        for anchor in soup.select(sel):
            href = anchor.get("href", "")
            title = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
            if not href or not title or len(title) < 15:
                continue
            full = urljoin(source_url, href)
            if urlparse(full).netloc != base_netloc:
                continue
            if full in seen:
                continue
            seen.add(full)
            items.append({"title": title, "link": full})
            if len(items) >= cfg["max_items"]:
                return items
    return items


def _collecter_scrape(source_url, nom, emoji, preset="generic"):
    """Construit une liste d'articles à partir d'une page (sans flux RSS)."""
    articles = []
    for i, item in enumerate(_liens_scrape(source_url, preset)):
        lien = item["link"]
        aid = hashlib.md5(lien.encode("utf-8")).hexdigest()[:12]
        articles.append({
            "id": aid,
            "source": nom,
            "emoji": emoji,
            "title": item["title"],
            "author": "",
            "link": lien,
            "date": "",
            "timestamp": 0,
            "excerpt": _excerpt(""),
            "summary_html": "",
            "body_html": "",
            "image": None,
            "featured": i == 0,
        })
    return articles


def _article_depuis_entry(entry, nom, emoji, i):
    lien = entry.get("link", "")
    if not lien:
        return None
    summary_html = entry.get("summary", "")
    body_html = _best_feed_html(entry)
    date_txt, ts = _date_lisible(entry)
    return {
        "id": hashlib.md5(lien.encode("utf-8")).hexdigest()[:12],
        "source": nom,
        "emoji": emoji,
        "title": (entry.get("title") or "Sans titre").strip(),
        "author": entry.get("author", "") or "",
        "link": lien,
        "date": date_txt,
        "timestamp": ts,
        "excerpt": _excerpt(summary_html or body_html),
        "summary_html": summary_html,
        "body_html": body_html,
        "image": _extraire_image(entry),
        "featured": i == 0 and bool(_extraire_image(entry)),
    }


def _feeds_cache_key(custom_feeds):
    """Clé de cache stable pour les flux personnalisés."""
    normalized = []
    for f in custom_feeds or []:
        normalized.append({
            "url": f.get("url", ""),
            "name": f.get("name", ""),
            "emoji": f.get("emoji", ""),
            "type": f.get("type", "rss"),
            "preset": f.get("preset", "generic"),
        })
    return json.dumps(
        sorted(normalized, key=lambda f: (f.get("type", ""), f.get("url", ""))),
        sort_keys=True,
        ensure_ascii=False,
    )


@st.cache_data(show_spinner=False, ttl=600)
def collecter_articles(feeds_key=""):
    """Agrège tous les flux en une liste mixée façon magazine."""
    par_flux = []

    for label, url in RSS_FEEDS.items():
        emoji, nom = _split_emoji(label)
        flux = feedparser.parse(url)
        articles_flux = []
        for i, entry in enumerate(flux.entries):
            art = _article_depuis_entry(entry, nom, emoji, i)
            if art:
                articles_flux.append(art)
        if articles_flux:
            par_flux.append(articles_flux)

    try:
        for f in json.loads(feeds_key or "[]"):
            url = (f.get("url") or "").strip()
            name = (f.get("name") or "Source").strip()
            emoji = (f.get("emoji") or "📰").strip() or "📰"
            ftype = (f.get("type") or "rss").strip()
            preset = (f.get("preset") or "generic").strip()
            if not url:
                continue
            if ftype == "scrape":
                articles_flux = _collecter_scrape(url, name, emoji, preset)
            else:
                flux = feedparser.parse(url)
                articles_flux = []
                for i, entry in enumerate(flux.entries):
                    art = _article_depuis_entry(entry, name, emoji, i)
                    if art:
                        articles_flux.append(art)
            if articles_flux:
                par_flux.append(articles_flux)
    except (json.JSONDecodeError, TypeError):
        pass

    # Mixage round-robin pour un rendu magazine multi-sources
    articles = []
    if par_flux:
        for i in range(max(len(f) for f in par_flux)):
            for flux in par_flux:
                if i < len(flux):
                    articles.append(flux[i])

    return articles


def _best_feed_html(entry):
    """Meilleur HTML disponible dans le flux (content:encoded ou summary)."""
    summary = entry.get("summary", "") or ""
    content = ""
    if entry.get("content"):
        content = entry["content"][0].get("value", "") or ""
    if len(nettoyer_html(content)) >= len(nettoyer_html(summary)):
        return content or summary
    return summary or content


def html_en_paragraphes(html):
    """Convertit un bloc HTML (flux RSS ou page) en paragraphes lisibles."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    paras = []
    for el in soup.find_all(["p", "li", "h2", "h3", "blockquote"]):
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if len(txt) > 40:
            paras.append(txt)
    if not paras:
        txt = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
        if len(txt) > 80:
            paras = [txt]
    plain = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
    if len(plain) > sum(len(p) for p in paras) + 150 and len(plain) > 400:
        morceaux = re.split(r'(?<=[.!?…])\s+(?=[A-ZÀ-ÖØ-Þ«""(])', plain)
        morceaux = [m.strip() for m in morceaux if len(m.strip()) > 40]
        if morceaux:
            return morceaux
    return paras


def _extraire_paragraphes_web(url):
    """Extraction full-text depuis la page web de l'article."""
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "iframe"]):
            tag.decompose()

        selectors = [
            "article .field--name-body",
            "article .node__content",
            ".article-body",
            ".article-content",
            ".post-content",
            "article .entry-content",
            ".entry-content",
            "article",
            "main",
            "[role=main]",
        ]
        root = None
        for sel in selectors:
            root = soup.select_one(sel)
            if root:
                break
        if not root:
            root = soup.body or soup

        for tag in root.find_all(["header", "footer", "nav", "aside", "form"]):
            tag.decompose()

        paras = []
        for el in root.find_all(["p", "li", "h2", "h3", "blockquote"]):
            txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
            if len(txt) > 40:
                paras.append(txt)
        return paras
    except Exception:
        return []


def obtenir_paragraphes(article):
    """Texte intégral : page web et flux RSS — on garde la source la plus riche."""
    url = article.get("link", "")
    fallback_html = article.get("body_html") or article.get("summary_html") or ""
    web_paras = _extraire_paragraphes_web(url) if url else []
    html_paras = html_en_paragraphes(fallback_html)
    web_len = sum(len(p) for p in web_paras)
    html_len = sum(len(p) for p in html_paras)
    if html_len > web_len:
        return html_paras
    if web_len >= 400:
        return web_paras
    return html_paras or web_paras


@st.cache_data(show_spinner=False)
def resume_pour_url(url, fallback_html):
    texte = extraire_texte_article(url)
    if len(texte) < 800:
        texte = nettoyer_html(fallback_html)
    return generer_resume(texte)


# ==============================
# COMPOSANT FLIPBOARD
# ==============================

_DIR = os.path.dirname(os.path.abspath(__file__))
_flipboard = components.declare_component(
    "flipboard_magazine",
    path=os.path.join(_DIR, "flipboard_component"),
)


def flipboard(articles, enrichments):
    return _flipboard(
        articles=articles,
        enrichments=enrichments,
        default=None,
        key="flipboard_magazine",
    )


# ==============================
# INTERFACE
# ==============================

st.set_page_config(page_title="Flipboard · News AI", layout="wide")

st.markdown(
    """
    <style>
    #MainMenu, header, footer {visibility: hidden;}
    [data-testid="stToolbar"] {display: none;}
    [data-testid="stDecoration"] {display: none;}
    .stApp {background: #0e0e10;}
    .block-container {padding: 0.3rem 0.5rem 0 0.5rem; max-width: 100%;}
    [data-testid="stAppViewBlockContainer"] {padding: 0.3rem 0.5rem 0 0.5rem;}
    iframe {border: none !important;}
    </style>
    """,
    unsafe_allow_html=True,
)

# Etat des enrichissements (texte intégral + résumé IA), conservé en session
if "enrich_text" not in st.session_state:
    st.session_state.enrich_text = {}
if "enrich_summary" not in st.session_state:
    st.session_state.enrich_summary = {}
if "last_nonce" not in st.session_state:
    st.session_state.last_nonce = None
if "custom_feeds" not in st.session_state:
    st.session_state.custom_feeds = []

feeds_key = _feeds_cache_key(st.session_state.custom_feeds)
articles = collecter_articles(feeds_key)
index = {a["id"]: a for a in articles}

enrichments = {
    "text": st.session_state.enrich_text,
    "summary": st.session_state.enrich_summary,
    "notebooklm": NOTEBOOKLM_URL,
    "custom_feeds": st.session_state.custom_feeds,
}

valeur = flipboard(articles, enrichments)

# Traitement des actions renvoyées par le composant
if isinstance(valeur, dict) and valeur.get("nonce") != st.session_state.last_nonce:
    st.session_state.last_nonce = valeur.get("nonce")

    if valeur.get("action") == "feeds":
        new_feeds = valeur.get("feeds") or []
        if new_feeds != st.session_state.custom_feeds:
            st.session_state.custom_feeds = new_feeds
            collecter_articles.clear()
            st.rerun()
    elif valeur.get("id"):
        aid = valeur.get("id")
        want = valeur.get("want")
        art = index.get(aid)

        if art:
            if want in ("text", "both") and aid not in st.session_state.enrich_text:
                st.session_state.enrich_text[aid] = obtenir_paragraphes(art)

            if want in ("summary", "both") and aid not in st.session_state.enrich_summary:
                st.session_state.enrich_summary[aid] = resume_pour_url(
                    art["link"], art["summary_html"]
                )

        st.rerun()
