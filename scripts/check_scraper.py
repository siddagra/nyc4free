"""Daily smoke test: fail visibly if the source stops exposing event links."""
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URL = "https://www.nycforfree.co/events"
response = requests.get(URL, timeout=30, headers={"User-Agent": "NYCFreePersonalDashboard/1.0 (+personal use)"})
response.raise_for_status()
soup = BeautifulSoup(response.text, "html.parser")
links = {urljoin(URL, a["href"]) for a in soup.select('a[href*="/events/"]')}
print(f"Found {len(links)} event links on {URL}")
if not links:
    raise SystemExit("No event links found; the source markup may have changed.")

