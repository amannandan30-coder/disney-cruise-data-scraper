# Disney Cruise Line Scraper (Relu Challenge, Objective 1)

Scrapes all cruise products from <https://disneycruise.disney.go.com/en-in/> with Playwright and saves a cleaned `results.csv`.

## How it works

1. Opens the site, accepts the consent banner and clicks **View dates**.
2. Scrolls the results page until all cruise cards are loaded (the catalog size is read from the site's own API response, not hardcoded).
3. Uses the page session to fetch every sailing (dates, ships, stateroom prices) for each cruise.
4. Applies the **Pacific Coast** destination filter programmatically to get the live filtered count.
5. Cleans the data (stripped titles, cleaned departure ports, no empty fields, no duplicates) and writes `results.csv`.
6. Prints the answers to the five challenge questions in the terminal.

## Setup and run

```bash
pip install -r requirements.txt
playwright install chromium

python disney_cruise_scraper.py              # headed browser (default)
python disney_cruise_scraper.py --headless   # headless (e.g. Colab)
```

Google Colab:

```python
!pip install playwright pandas
!playwright install chromium
!python disney_cruise_scraper.py --headless
```

## Output: `results.csv`

One row per cruise product (171 rows).

| Column | Description |
|---|---|
| cruise_code | Sailing code taken from the cruise URL |
| cruise_name | Cruise title |
| nights | Number of nights |
| departure_port | Departure port (text after "from", cut before "ending" / "with") |
| destination | Destination as returned by the site |
| ships | Ships sailing this cruise |
| dates | All sailing dates, joined with ` \| ` |
| num_dates | Number of sailing dates |
| first_sailing_prices | Stateroom prices of the first sailing |
| url | Cruise details link |

## Challenge answers

| Question | Answer |
|---|---|
| (i) Total cruises for Pacific as a destination | 2 |
| (ii) Total cruises | 171 |
| (iii) Holiday cruises (Very Merrytime 44 + Halloween on the High Seas 27) | 71 |
| (iv) Cruises offering more than 2 dates | 67 |
| (v) Cruises with Miami / London as departure ports | 0 / 0 |

## Notes

- "Cruise" means one cruise card (product). Each card can hold many sailing dates.
- Disney does not list Miami or London as departure ports; the closest ports in the data are Fort Lauderdale and Southampton, which are not counted for (v).
- In the latest run the sum of `num_dates` was 947, while the live page header showed 948 sailings. The cruise count (171) and all five answers are unaffected.
- Requests are paced with short delays to avoid loading the site.
