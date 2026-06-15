import os
import re
import json
import time
import hashlib
from datetime import datetime

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


# ==============================
# FONCTION : EXTRACTION ARTICLE COMPLET
# ==============================

@st.cache_data(show_spinner=False)
def extraire_texte_article(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Supprimer éléments inutiles
        for tag in soup(["script", "style", "header", "footer", "nav", "aside"]):
            tag.decompose()

        paragraphs = soup.find_all("p")
        texte = "\n".join(p.get_text() for p in paragraphs)

        texte = re.sub(r'\s+', ' ', texte)

        return texte.strip()

    except Exception:
        return ""


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


@st.cache_data(show_spinner=False, ttl=600)
def collecter_articles():
    """Agrège tous les flux en une liste mixée façon magazine."""
    par_flux = []

    for label, url in RSS_FEEDS.items():
        emoji, nom = _split_emoji(label)
        flux = feedparser.parse(url)
        articles_flux = []

        for i, entry in enumerate(flux.entries):
            lien = entry.get("link", "")
            if not lien:
                continue
            aid = hashlib.md5(lien.encode("utf-8")).hexdigest()[:12]
            summary_html = entry.get("summary", "")
            date_txt, ts = _date_lisible(entry)
            image = _extraire_image(entry)

            articles_flux.append({
                "id": aid,
                "source": nom,
                "emoji": emoji,
                "title": (entry.get("title") or "Sans titre").strip(),
                "author": entry.get("author", "") or "",
                "link": lien,
                "date": date_txt,
                "timestamp": ts,
                "excerpt": _excerpt(summary_html),
                "summary_html": summary_html,
                "image": image,
                # Premier article illustré d'un flux = carte "à la une"
                "featured": bool(image) and i == 0,
            })

        if articles_flux:
            par_flux.append(articles_flux)

    # Mixage round-robin pour un rendu magazine multi-sources
    articles = []
    if par_flux:
        for i in range(max(len(f) for f in par_flux)):
            for flux in par_flux:
                if i < len(flux):
                    articles.append(flux[i])

    return articles


@st.cache_data(show_spinner=False)
def extraire_paragraphes(url):
    """Extrait l'article en paragraphes pour le mode lecture (full-text)."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=12)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup(["script", "style", "header", "footer", "nav",
                         "aside", "form", "figure", "figcaption"]):
            tag.decompose()

        paragraphes = []
        for p in soup.find_all("p"):
            txt = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
            if len(txt) > 40:
                paragraphes.append(txt)
        return paragraphes
    except Exception:
        return []


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

articles = collecter_articles()
index = {a["id"]: a for a in articles}

enrichments = {
    "text": st.session_state.enrich_text,
    "summary": st.session_state.enrich_summary,
    "notebooklm": NOTEBOOKLM_URL,
}

valeur = flipboard(articles, enrichments)

# Traitement des actions renvoyées par le composant (lecture / résumé)
if isinstance(valeur, dict) and valeur.get("nonce") != st.session_state.last_nonce:
    st.session_state.last_nonce = valeur.get("nonce")
    aid = valeur.get("id")
    want = valeur.get("want")
    art = index.get(aid)

    if art:
        if want in ("text", "both") and aid not in st.session_state.enrich_text:
            paras = extraire_paragraphes(art["link"])
            if not paras:
                fallback = nettoyer_html(art["summary_html"]).strip()
                paras = [fallback] if fallback else []
            st.session_state.enrich_text[aid] = paras

        if want in ("summary", "both") and aid not in st.session_state.enrich_summary:
            st.session_state.enrich_summary[aid] = resume_pour_url(
                art["link"], art["summary_html"]
            )

    st.rerun()
