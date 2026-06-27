from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote, urljoin, urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

BASE_URL = "https://www.nycforfree.co"
EVENTS_URL = f"{BASE_URL}/events"
HEADERS = {"User-Agent": "NYCFreePersonalDashboard/1.0 (+personal use)"}
TIMEOUT = 25


def clean(value: str | None) -> str:
    if not value:
        return ""
    value = BeautifulSoup(str(value), "html.parser").get_text("\n", strip=True)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def as_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date_parser.parse(value)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except (ValueError, TypeError, OverflowError):
        return None


def fetch(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.text


def event_links_from_html(raw_html: str) -> list[str]:
    soup = BeautifulSoup(raw_html, "html.parser")
    links: set[str] = set()
    for anchor in soup.select('a[href*="/events/"]'):
        href = anchor.get("href", "").strip()
        if href:
            url = urljoin(BASE_URL, href).split("#", 1)[0].split("?", 1)[0]
            if urlparse(url).netloc.endswith("nycforfree.co"):
                links.add(url.rstrip("/"))
    return sorted(links)


def event_links_for_date(raw_html: str, chosen: date) -> list[str]:
    """Use calendar-card dates to avoid downloading irrelevant detail pages."""
    soup = BeautifulSoup(raw_html, "html.parser")
    dated_links: set[str] = set()
    dated_cards = soup.select("article.eventlist-event")
    for card in dated_cards:
        dates = [as_datetime(node.get("datetime")) for node in card.select("time[datetime]")]
        dates = [value for value in dates if value]
        if not dates:
            continue
        start, end = dates[0], dates[-1]
        if start.date() <= chosen <= end.date():
            anchor = card.select_one('a[href*="/events/"]')
            if anchor:
                dated_links.add(urljoin(BASE_URL, anchor.get("href")).split("?", 1)[0].rstrip("/"))
    return sorted(dated_links) if dated_cards else event_links_from_html(raw_html)


def jsonld_events(soup: BeautifulSoup) -> list[dict]:
    found: list[dict] = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            kind = node.get("@type", "")
            if kind == "Event" or (isinstance(kind, list) and "Event" in kind):
                found.append(node)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)

    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            walk(json.loads(tag.string or tag.get_text()))
        except (json.JSONDecodeError, TypeError):
            continue
    return found


def location_text(location) -> str:
    if isinstance(location, str):
        return clean(location)
    if not isinstance(location, dict):
        return ""
    address = location.get("address", {})
    parts = [location.get("name", "")]
    if isinstance(address, str):
        parts.append(address)
    elif isinstance(address, dict):
        parts.extend(address.get(k, "") for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode"))
    return ", ".join(str(x).strip() for x in parts if x and str(x).strip())


def image_url(data: dict, content) -> str:
    image = data.get("image")
    if isinstance(image, list):
        image = image[0] if image else ""
    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl") or ""
    if image:
        return urljoin("https:", str(image))
    node = content.select_one("img") if content else None
    if not node:
        return ""
    return urljoin(BASE_URL, node.get("data-src") or node.get("data-image") or node.get("src") or "")


def useful_external_link(content) -> tuple[str, str]:
    """Prefer the event's action button, excluding site furniture and image links."""
    if not content:
        return "", ""
    selectors = ("a.sqs-button-element[href]", 'a[href*="eventbrite"]', 'a[href*="signup"]', "a[target=_blank][href]")
    for selector in selectors:
        for node in content.select(selector):
            href = urljoin(BASE_URL, node.get("href", ""))
            if href and "nycforfree.co" not in urlparse(href).netloc:
                return href, clean(node.get_text(" ")) or "Event website"
    return "", ""


def extract_event(url: str) -> dict:
    soup = BeautifulSoup(fetch(url), "html.parser")
    structured = jsonld_events(soup)
    data = structured[0] if structured else {}

    title_node = soup.select_one("h1.eventitem-title, h1")
    content = soup.select_one(".eventitem-column-content") or soup.select_one(".eventitem-content, article")
    meta = soup.select_one(".eventitem-meta")
    description = clean(data.get("description")) or clean(content.decode_contents() if content else "")
    title = clean(data.get("name")) or clean(title_node.get_text(" ") if title_node else "")

    start = as_datetime(data.get("startDate"))
    end = as_datetime(data.get("endDate"))
    if not start:
        start_node = soup.select_one("time.event-date, time[datetime], [data-start-date]")
        start = as_datetime((start_node.get("datetime") or start_node.get("data-start-date")) if start_node else None)
    if not end:
        end_node = soup.select_one("[data-end-date]")
        end = as_datetime(end_node.get("data-end-date") if end_node else None)

    location = location_text(data.get("location"))
    if not location and meta:
        location_node = meta.select_one(".eventitem-meta-address, .eventitem-meta-location")
        location = clean(location_node.get_text(" ") if location_node else "")

    signup_url, signup_label = useful_external_link(content)
    photo = image_url(data, content)
    map_node = soup.select_one("a.eventitem-meta-address-maplink[href]")
    google_node = soup.select_one("a.eventitem-meta-export-google[href]")
    ics_node = soup.select_one("a.eventitem-meta-export-ical[href]")

    combined = f"{title} {description}".lower()
    tags = []
    tag_words = {
        "Food & drink": ("food", "drink", "coffee", "ice cream", "sample", "grocery"),
        "Wellness": ("yoga", "fitness", "workout", "wellness", "pilates", "run club"),
        "Arts": ("concert", "music", "film", "theater", "theatre", "dance", "museum", "art"),
        "Beauty": ("beauty", "skincare", "makeup", "sephora"),
        "Family": ("kids", "children", "family"),
        "Sports": ("soccer", "basketball", "golf", "skating", "sports"),
    }
    for label, words in tag_words.items():
        if any(word in combined for word in words):
            tags.append(label)

    return {
        "title": title or url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title(),
        "start": start,
        "end": end or start,
        "location": location,
        "description": description,
        "image_url": photo,
        "signup_url": signup_url,
        "signup_label": signup_label,
        "map_url": urljoin(BASE_URL, map_node.get("href")) if map_node else "",
        "google_calendar_url": urljoin(BASE_URL, google_node.get("href")) if google_node else "",
        "ics_url": urljoin(BASE_URL, ics_node.get("href")) if ics_node else "",
        "categories": ", ".join(tags) or "Other",
        "rsvp": bool(re.search(r"\b(rsvp|register|reservation|sign up|ticket)\b", combined)),
        "url": url,
        "error": "",
    }


def occurs_on(row: dict, chosen: date) -> bool:
    if not row.get("start"):
        return True
    end = row.get("end") or row["start"]
    return row["start"].date() <= chosen <= end.date()


@st.cache_data(ttl=900, show_spinner=False)
def scrape_events(chosen_iso: str, supplied_html: str = "") -> pd.DataFrame:
    chosen = date.fromisoformat(chosen_iso)
    source = supplied_html or fetch(EVENTS_URL)
    links = event_links_from_html(source) if supplied_html else event_links_for_date(source, chosen)
    rows = []
    progress = st.progress(0, text="Reading event details…") if links else None
    for index, url in enumerate(links):
        try:
            row = extract_event(url)
            if occurs_on(row, chosen):
                rows.append(row)
        except requests.RequestException as exc:
            rows.append({"title": url.rsplit("/", 1)[-1].replace("-", " ").title(), "start": None,
                         "end": None, "location": "", "description": "", "categories": "Other",
                         "image_url": "", "signup_url": "", "signup_label": "", "map_url": "",
                         "google_calendar_url": "", "ics_url": "", "rsvp": False,
                         "url": url, "error": str(exc)})
        if progress:
            progress.progress((index + 1) / len(links), text=f"Reading event {index + 1} of {len(links)}…")
        time.sleep(0.08)
    if progress:
        progress.empty()
    return pd.DataFrame(rows)


st.set_page_config(page_title="NYC Free Today", page_icon="🗽", layout="wide")
if "event_card_mode" not in st.session_state:
    st.session_state.event_card_mode = "responsive"

st.markdown("""
<style>
  .block-container {max-width: 1280px; padding-top: 1.5rem}
  [data-testid="stMain"] [data-testid="stExpander"]{margin-bottom:.8rem}
  [data-testid="stMain"] [data-testid="stExpander"] details{border-radius:14px;border-color:rgba(128,128,128,.35)}
  [data-testid="stMain"] [data-testid="stExpander"] summary{justify-content:space-between}
  [data-testid="stMain"] [data-testid="stExpander"] summary [data-testid="stExpanderToggleIcon"]{
    order:2;margin-left:auto;margin-right:0
  }
  [data-testid="stMain"] [data-testid="stExpander"] summary p{font-weight:700;line-height:1.35}
  [data-testid="stMain"] [data-testid="stExpander"] img{
    max-height:260px;object-fit:cover;border-radius:12px
  }
  .st-key-filter_bar [data-testid="stWidgetLabel"] p{white-space:nowrap}
  .st-key-filter_bar button{white-space:nowrap}
  .mobile-date-label{display:none}
  .st-key-day_nav [data-testid="stMarkdownContainer"] p{text-align:center;font-weight:700;font-size:1.05rem;margin:.45rem 0}
  @media(min-width:769px){
    [data-testid="stMain"] [data-testid="stExpander"] summary p{font-size:1.2rem}
  }
  @media(max-width:768px){
    .block-container{padding:1rem}
    .stButton button{width:100%}
    [data-testid="stMain"] [data-testid="stExpander"] summary p{font-size:1rem;font-weight:700;line-height:1.35}
    [data-testid="stMain"] [data-testid="stExpander"] [data-testid="stHorizontalBlock"]{flex-direction:column;gap:.5rem}
    [data-testid="stMain"] [data-testid="stExpander"] [data-testid="stColumn"]{width:100%!important;flex:1 1 100%!important}
    [data-testid="stMain"] [data-testid="stExpander"] img{max-height:220px}
    .st-key-day_nav [data-testid="stHorizontalBlock"]{flex-direction:row!important;flex-wrap:nowrap;gap:.4rem}
    .st-key-day_nav [data-testid="stColumn"]{min-width:0!important}
    .st-key-day_nav [data-testid="stColumn"]:nth-child(1),
    .st-key-day_nav [data-testid="stColumn"]:nth-child(3){width:46px!important;min-width:46px!important;max-width:46px!important;flex:0 0 46px!important}
    .st-key-day_nav [data-testid="stColumn"]:nth-child(2){width:auto!important;min-width:0!important;max-width:none!important;flex:1 1 auto!important}
    .st-key-filter_bar [data-testid="stHorizontalBlock"]{flex-direction:row!important;flex-wrap:wrap;gap:.4rem}
    .st-key-filter_bar [data-testid="stColumn"]{width:auto!important;min-width:0!important}
    .st-key-filter_bar [data-testid="stColumn"]:nth-child(1),
    .st-key-filter_bar [data-testid="stColumn"]:nth-child(2),
    .st-key-filter_bar [data-testid="stColumn"]:nth-child(4){flex:0 0 48px!important;width:48px!important;max-width:48px!important}
    .st-key-filter_bar [data-testid="stColumn"]:nth-child(3){order:2;flex:1 1 100%!important;width:100%!important;max-width:100%!important}
    .st-key-filter_bar [data-testid="stColumn"]:nth-child(1) [data-testid="stWidgetLabel"],
    .st-key-filter_bar [data-testid="stColumn"]:nth-child(4) [data-testid="stWidgetLabel"]{display:none}
    .st-key-filter_bar [data-testid="stColumn"]:nth-child(1){position:relative}
    .st-key-filter_bar [data-testid="stColumn"]:nth-child(1)::after{
      content:'📅';position:absolute;left:0;right:0;bottom:.72rem;text-align:center;
      font-size:1rem;line-height:1;pointer-events:none
    }
    .st-key-filter_bar [data-testid="stColumn"]:nth-child(1) input{font-size:0!important;color:transparent!important;padding-left:.35rem;padding-right:.35rem}
    .st-key-filter_bar [data-testid="stColumn"]:nth-child(2) button p{display:none}
    .st-key-filter_bar [data-testid="stColumn"]:nth-child(4){position:relative}
    .st-key-filter_bar [data-testid="stColumn"]:nth-child(4)::after{
      content:'⋯';position:absolute;left:0;right:0;bottom:.62rem;text-align:center;
      font-size:1.45rem;line-height:1;pointer-events:none
    }
    .st-key-filter_bar [data-testid="stColumn"]:nth-child(4) [data-baseweb="select"] *{color:transparent!important}
    .st-key-actions_bar [data-testid="stHorizontalBlock"]{flex-direction:row;flex-wrap:wrap;gap:.35rem}
    .st-key-actions_bar [data-testid="stColumn"]{width:auto!important;min-width:0!important}
    .st-key-actions_bar [data-testid="stColumn"]:nth-child(1){flex:0 0 100%!important;max-width:100%!important}
    .st-key-actions_bar [data-testid="stColumn"]:nth-child(2),
    .st-key-actions_bar [data-testid="stColumn"]:nth-child(3){flex:0 0 calc(50% - .175rem)!important;max-width:calc(50% - .175rem)!important}
    .st-key-actions_bar [data-testid="stColumn"]:nth-child(4){display:none}
    .st-key-actions_bar [data-testid="stDownloadButton"] button{width:100%;padding-left:.75rem;padding-right:.75rem}
    .st-key-actions_bar [data-testid="stColumn"]:nth-child(2) button p,
    .st-key-actions_bar [data-testid="stColumn"]:nth-child(3) button p{display:none}
    .desktop-date-label{display:none}
    .mobile-date-label{display:inline}
    .st-key-day_nav [data-testid="stMarkdownContainer"] p{font-size:1rem;margin:.45rem 0}
  }
</style>
""", unsafe_allow_html=True)
if st.session_state.event_card_mode == "responsive":
    st.markdown("""
    <style>@media(min-width:769px){
      [data-testid="stMain"] [data-testid="stExpander"] details:not([open]) > :not(summary){display:block!important}
      [data-testid="stMain"] [data-testid="stExpander"] details:not([open]) [data-testid="stExpanderDetails"]{display:block!important}
      [data-testid="stMain"] [data-testid="stExpander"] summary svg{display:none}
      [data-testid="stMain"] [data-testid="stExpander"] summary{cursor:default}
    }</style>
    """, unsafe_allow_html=True)

st.title("🗽 NYC Free Events")
st.caption("Full event details from NYC for FREE, filtered to the day you choose.")

if "chosen_date" not in st.session_state:
    st.session_state.chosen_date = date.today()

with st.container(key="day_nav"):
    previous_col, current_col, next_col = st.columns([1, 5, 1], vertical_alignment="center")
    with previous_col:
        if st.button("←", help="Previous day", use_container_width=True):
            st.session_state.chosen_date -= timedelta(days=1)
            st.rerun()
    with current_col:
        current_date = st.session_state.chosen_date
        desktop_date = current_date.strftime("%A, %B %d, %Y").replace(" 0", " ")
        mobile_date = current_date.strftime("%a, %b %d").replace(" 0", " ")
        st.markdown(f'<span class="desktop-date-label">{desktop_date}</span>'
                    f'<span class="mobile-date-label">{mobile_date}</span>', unsafe_allow_html=True)
    with next_col:
        if st.button("→", help="Next day", use_container_width=True):
            st.session_state.chosen_date += timedelta(days=1)
            st.rerun()

with st.container(key="filter_bar"):
    date_col, refresh_col, search_col, category_col = st.columns([.85, .8, 4.6, 1.5], vertical_alignment="bottom")
    with date_col:
        chosen_day = st.date_input("📅 Date", key="chosen_date")
    with refresh_col:
        if st.button("Refresh", use_container_width=True, icon="🔄"):
            st.cache_data.clear()
            st.rerun()

try:
    with st.spinner("Finding free things to do…"):
        df = scrape_events(chosen_day.isoformat())
except requests.RequestException as exc:
    st.error(f"Could not reach NYC for FREE: {exc}")
    st.stop()

if df.empty:
    st.info("No matching events were found. Try another date or paste/upload the original event list.")
    st.stop()

available = sorted({x.strip() for values in df["categories"] for x in values.split(",")})
with search_col:
    query = st.text_input("Search", placeholder="Title, description, or location…")
with category_col:
    selected = st.multiselect("Categories", available)
filtered = df.copy()
if query:
    mask = filtered[["title", "description", "location"]].fillna("").agg(" ".join, axis=1).str.contains(query, case=False, regex=False)
    filtered = filtered[mask]
if selected:
    filtered = filtered[filtered["categories"].apply(lambda value: any(x in value for x in selected))]

st.caption(f"{len(filtered)} events · {int(filtered['rsvp'].sum())} may require RSVP")

download = filtered.copy()
for column in ("start", "end"):
    download[column] = download[column].apply(lambda x: x.isoformat() if pd.notna(x) and x else "")
with st.container(key="actions_bar"):
    download_col, expand_col, collapse_col, _ = st.columns([1.7, 1, 1, 4.3], vertical_alignment="bottom")
    with download_col:
        st.download_button("Download this list as CSV", download.to_csv(index=False).encode("utf-8"),
                           file_name=f"nyc-free-{chosen_day.isoformat()}.csv", mime="text/csv",
                           use_container_width=False, icon="📄")
    with expand_col:
        if st.button("Expand all", use_container_width=True, icon=":material/unfold_more:", help="Expand all events"):
            st.session_state.event_card_mode = "expanded"
            st.rerun()
    with collapse_col:
        if st.button("Collapse all", use_container_width=True, icon=":material/unfold_less:", help="Collapse all events"):
            st.session_state.event_card_mode = "collapsed"
            st.rerun()

try:
    user_agent = st.context.headers.get("User-Agent", "")
except (AttributeError, RuntimeError):
    user_agent = ""
is_phone_or_tablet = bool(re.search(r"Mobile|Android|iPhone|iPad|iPod", user_agent, re.IGNORECASE))
expand_cards = (
    st.session_state.event_card_mode == "expanded"
    or (st.session_state.event_card_mode == "responsive" and not is_phone_or_tablet)
)

for _, event in filtered.sort_values("start", na_position="last").iterrows():
    when = "Time not listed"
    if event["start"]:
        when = event["start"].strftime("%I:%M %p").lstrip("0")
        if event["end"] and event["end"].date() > event["start"].date():
            when += f" · through {event['end'].strftime('%b %-d') if __import__('os').name != 'nt' else event['end'].strftime('%b %d').replace(' 0', ' ')}"
    with st.expander(f"{event['title']}  ·  {when}", expanded=expand_cards):
        text_col, image_col = st.columns([5, 2], vertical_alignment="top")
        with text_col:
            st.caption(" · ".join(x for x in (event["categories"], "RSVP/check details" if event["rsvp"] else "") if x))
            if event["description"]:
                st.write(event["description"])
            if event["error"]:
                st.warning(f"Details could not be fetched: {event['error']}")
            action_cols = st.columns(2) if event.get("signup_url") else [st.container()]
            if event.get("signup_url"):
                action_cols[0].link_button(event.get("signup_label") or "Sign up / schedule", event["signup_url"], use_container_width=True)
                action_cols[1].link_button("NYC for FREE page", event["url"], use_container_width=True)
            else:
                action_cols[0].link_button("Open original event page", event["url"])
        with image_col:
            if event.get("image_url"):
                st.image(event["image_url"], use_container_width=True)
            if event["start"]:
                if event["end"] and event["end"].date() != event["start"].date():
                    st.markdown(f"**{event['start'].strftime('%b %d, %Y · %I:%M %p').replace(' 0', ' ')}**  \
Through {event['end'].strftime('%b %d, %Y · %I:%M %p').replace(' 0', ' ')}")
                    st.caption("Multi-day listing; check the signup schedule for exact daily times.")
                else:
                    end_text = f"–{event['end'].strftime('%I:%M %p').lstrip('0')}" if event["end"] and event["end"] != event["start"] else ""
                    st.markdown(f"**{event['start'].strftime('%I:%M %p').lstrip('0')}{end_text}**")
            if event["location"]:
                st.caption(event["location"])
            metadata_links = []
            if event.get("map_url"):
                metadata_links.append(f"[Map]({quote(event['map_url'], safe=':/?&=%+#')})")
            if event.get("google_calendar_url"):
                metadata_links.append(f"[Google Calendar]({quote(event['google_calendar_url'], safe=':/?&=%+#')})")
            if event.get("ics_url"):
                metadata_links.append(f"[ICS]({quote(event['ics_url'], safe=':/?&=%+#')})")
            if metadata_links:
                st.markdown(" · ".join(metadata_links))
