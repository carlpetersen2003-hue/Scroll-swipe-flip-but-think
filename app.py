import os
import re
import json
import time
import hashlib
from datetime import datetime
from urllib.parse import urljoin

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

def generer_resume(texte, n_points=5):
    try:
        n_points = max(1, min(5, int(n_points)))
        texte_utilise = texte[:MAX_CHARS]
        point_label = "point majeur" if n_points == 1 else "points majeurs"

        response = client.chat.complete(
            model="mistral-small-latest",
            messages=[
                {
                    "role": "user",
                    "content": f"""
Fais un résumé de l'article suivant en {n_points} {point_label}.

Le résumé commencera par une problématique générale formulée sous forme de question paradoxale.

Ensuite :
- {n_points} points différenciés par des emojis adaptés
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


def _titre_incomplet(titre):
    t = re.sub(r"\s+", " ", (titre or "").strip())
    if not t:
        return True
    return t.lower().rstrip(".") in ("sans titre", "untitled", "(no title)", "no title")


@st.cache_data(show_spinner=False, ttl=3600)
def _metadonnees_page(url):
    """Complète titre / image depuis la page web (articles RSS incomplets)."""
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=12)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        titre = None
        for sel, attr in (
            ('meta[property="og:title"]', "content"),
            ('meta[name="twitter:title"]', "content"),
            ("h1", None),
            ("title", None),
        ):
            el = soup.select_one(sel)
            if not el:
                continue
            val = (el.get(attr) if attr else el.get_text(" ", strip=True)) or ""
            val = re.sub(r"\s+", " ", val).strip()
            if val and not _titre_incomplet(val):
                titre = val
                break

        image = None
        for sel in (
            'meta[property="og:image"]',
            'meta[property="og:image:url"]',
            'meta[name="twitter:image"]',
        ):
            el = soup.select_one(sel)
            if el and el.get("content"):
                image = urljoin(url, el["content"].strip())
                if image.startswith("http"):
                    break

        if not image:
            for img in soup.select("article img, main img, .post-content img, .entry-content img"):
                src = img.get("src") or img.get("data-src")
                if src:
                    image = urljoin(url, src.strip())
                    if image.startswith("http"):
                        break

        return {"title": titre or "", "image": image}
    except Exception:
        return {"title": "", "image": None}


def _enrichir_metadonnees(article):
    """Scraping léger si le flux RSS n'a pas de titre ou d'image."""
    if not article.get("link"):
        return article
    if not _titre_incomplet(article.get("title")) and article.get("image"):
        return article

    meta = _metadonnees_page(article["link"])
    if _titre_incomplet(article.get("title")) and meta.get("title"):
        article["title"] = meta["title"]
    if not article.get("image") and meta.get("image"):
        article["image"] = meta["image"]
    return article


def _article_depuis_entry(entry, nom, emoji, i):
    lien = entry.get("link", "")
    if not lien:
        return None
    summary_html = entry.get("summary", "")
    body_html = _best_feed_html(entry)
    date_txt, ts = _date_lisible(entry)
    art = {
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
    art = _enrichir_metadonnees(art)
    if i == 0 and art.get("image"):
        art["featured"] = True
    return art


def _default_feeds_payload():
    """Flux RSS intégrés à l'application (modifiables par l'utilisateur)."""
    feeds = []
    for label, url in RSS_FEEDS.items():
        emoji, nom = _split_emoji(label)
        feeds.append({"url": url, "name": nom, "emoji": emoji})
    return feeds


def _lister_sources(custom_feeds, disabled_defaults=None):
    """Liste des médias disponibles pour le filtre (flux par défaut + personnalisés)."""
    disabled = set(disabled_defaults or [])
    sources = []
    seen = set()
    for label, url in RSS_FEEDS.items():
        if url in disabled:
            continue
        emoji, nom = _split_emoji(label)
        key = f"{emoji}|{nom}"
        if key not in seen:
            seen.add(key)
            sources.append({"key": key, "name": nom, "emoji": emoji})
    for f in custom_feeds or []:
        emoji = (f.get("emoji") or "📰").strip() or "📰"
        nom = (f.get("name") or "Source").strip()
        key = f"{emoji}|{nom}"
        if key not in seen:
            seen.add(key)
            sources.append({"key": key, "name": nom, "emoji": emoji})
    return sources


def _feeds_cache_key(custom_feeds, disabled_defaults=None):
    """Clé de cache stable pour la configuration des flux."""
    normalized = []
    for f in custom_feeds or []:
        normalized.append({
            "url": f.get("url", ""),
            "name": f.get("name", ""),
            "emoji": f.get("emoji", ""),
        })
    payload = {
        "custom": sorted(normalized, key=lambda f: f.get("url", "")),
        "disabled": sorted(set(disabled_defaults or [])),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


@st.cache_data(show_spinner=False, ttl=600)
def collecter_articles(feeds_key=""):
    """Agrège tous les flux en une liste mixée façon magazine."""
    par_flux = []
    disabled = set()
    custom_feeds = []

    try:
        parsed = json.loads(feeds_key or "{}")
        if isinstance(parsed, list):
            custom_feeds = parsed
        else:
            custom_feeds = parsed.get("custom", []) or []
            disabled = set(parsed.get("disabled", []) or [])
    except (json.JSONDecodeError, TypeError):
        custom_feeds = []

    for label, url in RSS_FEEDS.items():
        if url in disabled:
            continue
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
        for f in custom_feeds:
            url = (f.get("url") or "").strip()
            name = (f.get("name") or "Source").strip()
            emoji = (f.get("emoji") or "📰").strip() or "📰"
            if not url:
                continue
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


def _paragraphes_depuis_root(root):
    """Extrait les paragraphes d'un bloc HTML racine."""
    paras = []
    for el in root.find_all(["p", "li", "h2", "h3", "blockquote"]):
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if len(txt) > 40:
            paras.append(txt)
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
            ".entry-content.texte",   # SPIP (La Vie des Idées, Diploweb…)
            ".texte",
            "article .field--name-body",
            "article .node__content",
            ".article-body",
            ".article-content",
            ".post-content",
            "article .entry-content",
            "[itemprop=articleBody]",
            "article",
            "main",
            "[role=main]",
            ".entry-content",
        ]
        best = []
        seen_roots = set()
        for sel in selectors:
            for root in soup.select(sel):
                if id(root) in seen_roots:
                    continue
                if "chapo" in (root.get("class") or []):
                    continue
                seen_roots.add(id(root))
                paras = _paragraphes_depuis_root(root)
                if sum(len(p) for p in paras) > sum(len(p) for p in best):
                    best = paras

        if best:
            return best

        root = soup.body or soup
        for tag in root.find_all(["header", "footer", "nav", "aside", "form"]):
            tag.decompose()
        return _paragraphes_depuis_root(root)
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
    # Ne pas laisser un court résumé RSS l'emporter sur un texte web substantiel
    if web_len >= 600 and html_len < web_len * 1.25:
        return web_paras
    if html_len > web_len:
        return html_paras
    if web_len >= 400:
        return web_paras
    return html_paras or web_paras


@st.cache_data(show_spinner=False)
def resume_pour_url(url, fallback_html, n_points=5):
    texte = extraire_texte_article(url)
    if len(texte) < 800:
        texte = nettoyer_html(fallback_html)
    return generer_resume(texte, n_points)


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
if "disabled_default_feeds" not in st.session_state:
    st.session_state.disabled_default_feeds = []

feeds_key = _feeds_cache_key(
    st.session_state.custom_feeds,
    st.session_state.disabled_default_feeds,
)
articles = collecter_articles(feeds_key)
index = {a["id"]: a for a in articles}

enrichments = {
    "text": st.session_state.enrich_text,
    "summary": st.session_state.enrich_summary,
    "notebooklm": NOTEBOOKLM_URL,
    "custom_feeds": st.session_state.custom_feeds,
    "default_feeds": _default_feeds_payload(),
    "disabled_default_feeds": st.session_state.disabled_default_feeds,
    "sources": _lister_sources(
        st.session_state.custom_feeds,
        st.session_state.disabled_default_feeds,
    ),
}

valeur = flipboard(articles, enrichments)

# Traitement des actions renvoyées par le composant
if isinstance(valeur, dict) and valeur.get("nonce") != st.session_state.last_nonce:
    st.session_state.last_nonce = valeur.get("nonce")

    if valeur.get("action") == "feeds":
        new_feeds = valeur.get("feeds") or []
        new_disabled = valeur.get("disabled_defaults") or []
        changed = False
        if new_feeds != st.session_state.custom_feeds:
            st.session_state.custom_feeds = new_feeds
            changed = True
        if new_disabled != st.session_state.disabled_default_feeds:
            st.session_state.disabled_default_feeds = new_disabled
            changed = True
        if changed:
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
                n_points = valeur.get("summary_points", 5)
                try:
                    n_points = max(1, min(5, int(n_points)))
                except (TypeError, ValueError):
                    n_points = 5
                st.session_state.enrich_summary[aid] = {
                    "text": resume_pour_url(
                        art["link"], art["summary_html"], n_points
                    ),
                    "points": n_points,
                }

        st.rerun()
