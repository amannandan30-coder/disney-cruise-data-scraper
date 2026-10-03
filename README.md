# Disney Cruise Line Data Scraper

A robust, production-grade automated web scraper and dynamic analysis tool for Disney Cruise Line itineraries, pricing, ships, and sailing dates built using Playwright and Python.

## Overview
This project was developed for the Relu Challenge (Objective 1) to comprehensively extract and structure all Disney Cruise Line itineraries without relying on hardcoded records or manual paging limits.

### Key Capabilities
- **Full Catalog Extraction**: Driven dynamically by DCL product catalog metadata and network API interception.
- **Dynamic Date & Pricing Enrichment**: Parses all sailing date ranges, ship allocations, and 4-tier stateroom pricing (`Inside`, `Outside`, `Verandah`, `Suite`).
- **Challenge Answering Logic**: Dynamically calculates Pacific Coast cruises, earliest/latest departures, longest/shortest durations, and price ranges.
- **Zero-Null Schema**: Clean data extraction ensuring valid, populated fields for every itinerary.
- **Dual Execution Modes**: Runs seamlessly in standard headed mode or `--headless` mode.

## Project Structure
```text
disney-cruise-data-scraper/
├── disney_cruise_scraper.py   # Main scraper and challenge solver
├── results.csv                # Complete extracted cruise dataset (CSV)
├── requirements.txt           # Python dependencies
├── .gitignore                 # Excluded environments and temporary artifacts
└── README.md                  # Project documentation
```

## Dataset Fields (`results.csv`)
| Column | Description |
|---|---|
| `cruise_code` | Unique Disney Cruise Line itinerary code (e.g., `DA0086`) |
| `cruise_name` | Official title of the cruise itinerary |
| `nights` | Duration of the cruise in nights |
| `departure_port` | Port city of departure |
| `destination` | Cruise destination region |
| `ships` | Assigned Disney Cruise ship(s) |
| `dates` | Pipe-delimited list of all sailing date ranges (`YYYY-MM-DD to YYYY-MM-DD`) |
| `num_dates` | Total number of sailing dates available for this cruise |
| `first_sailing_prices` | 4-tier room prices for the earliest sailing date |
| `url` | Direct Disney Cruise Line URL for the cruise |

## Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/amannandan30-coder/disney-cruise-data-scraper.git
   cd disney-cruise-data-scraper
   ```

2. **Create and activate a virtual environment (optional but recommended):**
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # Linux / macOS:
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

## Usage

Run the scraper in standard (headed) mode:
```bash
python disney_cruise_scraper.py
```

Run in headless mode:
```bash
python disney_cruise_scraper.py --headless
```

Output will be saved to `results.csv` and a full validation and question summary report will be printed to stdout.
