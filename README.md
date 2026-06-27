# NYC Free Events dashboard

A phone-friendly Streamlit dashboard that reads NYC for FREE event pages, extracts full descriptions, and filters events to a selected day.

## Run locally

```powershell
python -m pip install -r requirements.txt
streamlit run app.py
```

Streamlit prints a local URL. To view it on a phone on the same Wi-Fi, run:

```powershell
streamlit run app.py --server.address 0.0.0.0
```

Then open `http://YOUR-COMPUTER-IP:8501` on the phone. Windows Firewall may ask for permission.

## Put it online

1. Create a GitHub repository and push these files.
2. Sign in to [Streamlit Community Cloud](https://share.streamlit.io/).
3. Choose the repository and set the entry point to `app.py`.
4. Deploy, then open the generated HTTPS link on any phone.

The app reads the live events page every day by default—no upload or manual update is needed. Pick today or any other date in the sidebar. Live results are cached for 15 minutes; use **Refresh live data** to force an immediate update. The saved-list uploader is only a fallback/testing tool.

The included GitHub Actions workflow checks the source each morning and will flag a failed run if NYC for FREE changes its markup. Streamlit Community Cloud wakes the app when you visit its URL, so it always fetches current data without a server you maintain.

## Notes

- This is intended for light personal use. The scraper identifies itself and pauses briefly between requests.
- Site markup can change. The extractor prefers JSON-LD structured data and falls back to Squarespace event markup.
- Always confirm RSVP rules and last-minute changes on the original event page.
