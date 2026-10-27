# Taiwan exchange closing quotes (public data only)

GitHub Action stores official TWSE/TPEx closing price snapshots under `prices/YYYY-MM-DD.json` and `prices/latest.json`.
It never stores user holdings, share quantities, personal portfolio value or private account information. This folder is safe for a public repository.

Official sources:
* `https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`
* `https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes`

Prices are accepted **only** if their official `Date` parses to today's Asia/Taipei date. Invalid prices are omitted. A stale response is rejected. If both official APIs are unavailable, historical Yahoo Finance 1-day bars for symbols present in the previous saved snapshot are an optional fallback. No intraday quote is used. No report file is written when no verified current-day quote exists. These GitHub Actions schedules can be delayed by GitHub and are not guaranteed to run at exactly 13:40.

**No user portfolio data is stored here.** The user's private calculation is a separate step that intersects the public quote dictionary with their private holding quantities and prior closes.

Schema: `{"date":"2026-10-27","quotes":{"2330":{"close":"2400","market":"twse","source":"TWSE_OpenAPI"}},"counts":{...},"sources":{...}}`.
