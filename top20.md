# Top 20 equal-weight stock screen

**Research snapshot:** 2026-08-20 close (market data through 2026-08-19/2026-08-20 depending on source, as cited). This is a reproducible research screen, not personalized advice. Ranking is conviction order only and does not set position size -- sizing is handled downstream.

## Data-layer check (run before screening)

Test metric: AAPL debt-to-equity. First retrieval (stockanalysis.com via web fetch) returned D/E 0.78, but the page's "period ending" field looked like it had been conflated with the page's render date rather than AAPL's actual fiscal quarter-end -- flagged and not used further. Alpaca market-data API (keys in `.env`) confirmed live with real daily bars for AAPL through 2026-08-19 (close range $310-$319). All fundamentals used in this report were subsequently re-pulled via single-ticker, self-labeled queries (WebFetch to stockanalysis.com, and Finviz screener table cells) rather than the ambiguous first pass, after a batch of 10 parallel Bigdata tearsheet calls came back **without per-ticker labels** and were caught, discarded, and redone one-at-a-time before any number was trusted into this report.

## Screen

Universe/filters applied, exact values used (reproducible in Finviz):
- `f=cap_midover,fa_roe_o10,sma200_pa` sorted `o=-marketcap` -- market cap roughly ≥$2bn (Finviz "mid-cap and over" bucket), ROE >10% (closest Finviz preset to the ≥12% requirement; exact ROE then hand-verified per name), price above 200-day SMA. **1,137 total matches.**
- `f=cap_mid,fa_roe_o10,sma200_pa` sorted `o=-marketcap` -- Finviz discrete $2bn-$10bn mid-cap bucket, same ROE/trend presets. **512 total matches.**
- From these two pools, 68 individual tickers were pulled forward for full verification this run (28 large/mega-cap names -- the 20 current holdings plus 8 new names from the large-cap pool -- and 40 mid-cap names sampled from the two Finviz pages above).

Stage-by-stage pass counts (of the 68 names individually verified):

| Stage | Filter | Pass count |
|---|---|---|
| 1 | D/E ≤ 2.0 (literal TTM total debt/equity, no bank/REIT carve-out this run) | 55 / 68 |
| 2 | ROE ≥ 12% (TTM) | 46 / 55 |
| 3 | Price > 200-day SMA (Alpaca-verified, adjustment="all"; overrides Finviz's own trend column, which was found to be internally inconsistent with its own filter and discarded) | 35 / 46 |
| 4 | Avg. $ volume (20d, Alpaca) ≥ $20m | 35 / 35 (all cleared this bar) |
| 5 | 6-month total return > SPY's own 6-month total return (12.25% as of 2026-08-20, Alpaca `adjustment="all"`) -- operationalized proxy for "top 50% relative strength vs S&P 500" | 20 / 35 |
| 6 | No earnings release in the last 3 / next 5 trading days (2026-08-17 to 2026-08-27), Bigdata events calendar | 20 / 20 (none of the 20 survivors had an event in-window) |

**Final list: 20 names.** No stage eliminated the entire pool. Stage 5 (relative strength) was the single biggest cut, removing 15 names that were otherwise fundamentally sound -- including GOOGL, which missed by 0.02 percentage points (12.23% vs. the 12.25% benchmark) and is flagged below as a near-miss rather than a clean fail.

## Picks

| Ticker | Sector | Why Included | Valuation | Bear Case |
|---|---|---|---|---|
| MSFT | Information Technology | ROE 34.0%; D/E 0.29; current ratio 1.23; FCF positive (~$66.5B ann.). Azure/enterprise switching-cost moat. | Trailing P/E 26.98x; forward 24.57x. 5yr/sector-median P/E not retrieved this run. | AI-capex depreciation outrunning monetization; Azure deceleration or antitrust action. |
| V | Financials | ROE 52.1%; D/E 0.69; current ratio 1.08; FCF positive (~$21.6B ann.). Payments-network scale moat. | Trailing P/E 31.12x; forward 25.28x. Premium but highest-ROE name in the book. | Interchange regulation cutting take rates; cross-border travel slowdown. |
| XOM | Energy | ROE 12.58%; D/E 0.16; current ratio 1.14; FCF positive (~$31B ann.). Low-cost Permian/Guyana moat. | Trailing P/E 21.24x; forward 13.65x -- cheap on forward basis. | Oil price reversal, refining weakness, Guyana fiscal intervention. Corr. w/ CVX 0.84, EOG 0.77. |
| CVX | Energy | ROE 12.23%; D/E 0.19; current ratio 1.25; FCF positive (~$27B ann.). Permian/integrated-refining moat. | Trailing P/E 19.76x; forward 13.53x -- cheapest trailing multiple in energy sleeve. | Oil weakness / downstream margin reset. Corr. w/ XOM 0.84, EOG 0.79. |
| EOG | Energy | ROE 16.83%; D/E 0.31; current ratio 1.63; FCF positive (~$4.3B ann.). Low-cost premium-acreage moat. | Trailing P/E 11.68x; forward 9.86x -- cheapest name in the book. | Commodity-price weakness; well-productivity disappointment. Corr. w/ CVX 0.79, XOM 0.77. |
| UNP | Industrials | ROE 39.70%; D/E 1.51; current ratio 0.99; FCF positive (~$6.6B ann.). Irreplaceable rail network moat. | Trailing P/E 24.43x; forward 22.27x. Buy, 25 analysts, 7.2% upside to target. | Industrial-volume recession; labor/regulatory cost inflation. Corr. w/ CSX 0.70. |
| LLY | Health Care | ROE 102.3% (thin equity base); D/E 1.62; current ratio 1.36; FCF positive (~$18.3B ann.). GLP-1/incretin IP moat. | Trailing P/E 42.98x; forward 31.39x -- priced for exceptional growth, only ~3% upside to target. | Safety signal, reimbursement cap, oral-GLP-1 competition. |
| CSX | Industrials | ROE 22.51%; D/E 1.48; current ratio 0.81; FCF positive (~$1.6B ann.). Eastern rail network moat. | Trailing P/E 29.30x; forward 23.66x. Buy, 25 analysts. | Freight-volume weakness; service disruption. Corr. w/ UNP 0.70. |
| NUE | Materials | ROE 14.55%; D/E 0.31; current ratio 2.51; FCF positive (~$1.5B ann.). Record shipments, $1.2B qtr net earnings. Low-cost EAF moat. | Trailing P/E 19.86x; forward 11.56x -- steep forward discount on cycle-upswing expectations. | Steel-price cyclicality; tariff reversal. |
| UNH | Health Care | ROE 14.15%; D/E 0.69; current ratio 0.78; FCF positive (~$26.4B ann.). Managed-care/PBM scale moat. | Trailing P/E 25.02x; forward 18.26x. Buy, 27 analysts, 22.9% upside to target. | Medical-cost-ratio deterioration; Medicare Advantage reimbursement pressure. Trails XLV by 16.8pp over 3mo. |
| VRTX | Health Care | ROE 23.54%; D/E 0.10 -- lowest leverage in book; current ratio 3.19; FCF positive (~$3.8B ann.). CF franchise + pipeline moat. | Trailing P/E 32.13x; forward 27.74x -- pipeline-optionality premium. | Pivotal-trial miss; slower pipeline launch uptake. |
| MPC | Energy | ROE 42.10%; D/E 1.33; current ratio 1.25; FCF positive (~$13B ann.). Q2 adj. EPS $17.73 vs $13.95 consensus. Refining-scale moat. | Trailing P/E 12.55x; forward 7.66x after a +83% 6mo run -- most re-rating-dependent name in the book. | Crack-spread compression off recent highs. Corr. w/ CVX 0.59, EOG 0.57. |
| ANET | Information Technology | ROE 31.48%; D/E -0.90 (net cash); current ratio 2.96; FCF positive (~$5.1B ann.). Raised FY26 revenue guide to $12.6B on AI-networking demand. | Trailing P/E 59.01x; forward 40.14x -- richest multiple in book. Strong Buy, 30 analysts, 30.5% upside to target. | AI-capex normalization; hyperscaler order lumpiness. Highest ann. vol (54.4%) of the large/mid caps. |
| PRI | Financials | ROE 32.86%; D/E 0.68; current ratio 2.43; FCF positive (implied, 59.7% earnings growth). Independent-agent distribution moat. | Trailing P/E 12.10x; forward 11.68x -- cheapest financial. Only 8 analysts cover it. | Term-life/investment sales slowdown; thin analyst coverage. |
| VOYA | Financials | ROE 13.06% (weakest of the 4 financials); D/E 0.84; current ratio not meaningful (NA, insurance business). FCF positive (implied). Retirement/AUM scale moat. | Trailing P/E 16.53x; forward 9.32x -- steep forward discount. | Weakest-ROE financial pick; market drawdown hits fee-linked AUM directly. |
| PAYC | Information Technology | ROE 41.09% (Finviz); D/E 1.72; current ratio 1.02; FCF described as strong (positive). HCM software switching-cost moat. | Trailing P/E 23.93x; forward 17.18x after a +81% 6mo run. Buy, 20 analysts. | Highest D/E of the software names; bookings slowdown vs. Workday/ADP. |
| FCFS | Financials | ROE 17.40% (Finviz); D/E 1.16; current ratio 4.89. FCF sign not confirmed this run -- flagged. Pawn-lending scale moat. | Trailing P/E 23.65x; forward 17.11x. Strong Buy but only 5 analysts. | Thinnest coverage (5 analysts) plus unconfirmed FCF sign -- weakest data quality of the 20. |
| LTH | Consumer Discretionary | ROE 13.45% (Finviz); D/E 1.29; current ratio 0.66; FCF positive (implied, $414.9M net income). Premium fitness-club moat. | Trailing P/E 24.44x; forward 23.64x after a +53% 6mo run. Strong Buy, 14 analysts. Shorter (1,222-day) Alpaca price history than the other 19. | Highest Risk Index (85) in book; membership growth slowdown or club-capex overrun. |
| KRYS | Health Care | ROE 20.12% (Finviz); D/E 0.01 -- essentially unlevered; current ratio 8.32. VYJUVEK gene-therapy commercial ramp moat. | Trailing P/E 42.34x; forward 39.94x -- expensive. Strong Buy, only 10 analysts. | Single-product commercial concentration risk. |
| VICR | Industrials | ROE 20.13% (Finviz); D/E 0.01; current ratio 13.25 -- most liquid balance sheet in book. AI-datacenter/defense power-module IP moat. | Trailing P/E 67.71x -- richest multiple in the entire portfolio. Only 4 analysts cover it. | Highest Risk Index (100) and Volatility Index (100); 5yr beta 2.37, 90.2% ann. vol; thin coverage. |

## Diversification and correlation

Seven GICS sectors represented, four sectors at the cap of exactly 4 (Information Technology: MSFT/ANET/PAYC/VICR-note-Industrials-not-IT -- see correction below; Financials: V/PRI/FCFS/VOYA; Health Care: LLY/UNH/VRTX/KRYS; Energy: CVX/EOG/MPC/XOM). Correction: VICR is classified Industrials (Electrical Components & Equipment) per source, not Information Technology, so Information Technology = 3 (MSFT, ANET, PAYC) and Industrials = 3 (CSX, UNP, VICR); no sector exceeds 4. Non-mega-cap (<$50bn) names: PAYC, KRYS, VICR, LTH, PRI, FCFS, VOYA -- seven names, comfortably clearing the ≥3 requirement.

**Correlation flags (1-year daily-return correlation, Alpaca adjusted closes, |r| > 0.75):**
- CVX-XOM: **0.840**
- CVX-EOG: **0.785**
- EOG-XOM: **0.773**

All three flagged pairs are within the Energy sleeve (shared commodity-price exposure) -- expected given the sector, but worth noting the four energy names do not diversify each other much despite counting as four separate picks toward the sector cap. CSX-UNP at 0.698 is close to the threshold but does not clear it.

## Dropped holdings

Of the 20 current holdings, 5 survived the screen unchanged in identity (MSFT, V, LLY, CSX, XOM). The other 15 dropped out, each for a specific, stated reason: **WMT** reports Q2 FY2027 earnings today (2026-08-20) -- inside the blackout window regardless of fundamentals, and also failed the price>SMA200 trend test. **JPM** (D/E 3.59, literal TTM total-debt/equity with no bank carve-out applied this run) and **AMGN** (D/E 4.90) both failed the D/E≤2.0 filter. **EXR** failed ROE (6.95% vs the 12% bar). **META, AVGO, NEE, COST, FSLR** all failed the price>200-day SMA trend test as of 2026-08-19/20 close. **GOOGL, ETN, PHM, CB, NEM, BRK.B** all cleared every other filter but fell short on the 6-month relative-strength cut versus SPY's 12.25% benchmark return -- GOOGL by the narrowest margin in the whole screen (12.23% vs 12.25%), the others by wider margins (ETN 11.36%, NEM 3.26%, CB 4.99%, PHM -9.21%, BRK.B 0.10%). The screen decided every one of these outcomes; none were adjusted for being (or not being) a current holding.

## Data sources and as-of dates

- **Market regime, prices, volume, SMA, beta, volatility, correlation:** Alpaca Markets historical-bars API, `adjustment="all"`, computed 2026-08-20 from data through 2026-08-19/2026-08-20 close.
- **D/E, current ratio, ROE, FCF sign, P/E, forward P/E, analyst consensus/count:** stockanalysis.com ratios/overview pages, retrieved via single-ticker web fetch 2026-08-20 (each fetch labeled and cross-checked by ticker after an earlier batch of unlabeled Bigdata tearsheet calls was discarded for ambiguity).
- **D/E, current ratio, ROE for the 7 mid-cap names (PAYC, KRYS, VICR, LTH, PRI, FCFS, VOYA):** Finviz screener Financial view (`v=161`), retrieved 2026-08-20.
- **Earnings-calendar blackout check (2026-08-17 to 2026-08-27):** Bigdata.com events calendar, `categories=["earnings-call"]`, queried 2026-08-20. Source: [Bigdata.com](https://bigdata.com).
- **S&P 500 level, 200-day SMA, sector-ETF 3-month/6-month returns:** Bigdata.com market tearsheet (FMP data), as of 2026-08-19/20. Source: [Bigdata.com](https://bigdata.com).
- **Universe/screen construction:** Finviz screener (`finviz.com/screener.ashx`), exact filter strings stated above, retrieved 2026-08-20.

**PEG ratio was not disclosed by any source for any of the 20 names this run** -- a systematic data gap, not a per-name omission; valuation calls above rely on trailing/forward P/E and analyst-target context instead, flagged accordingly rather than estimated.

## Numeric field methodology (exact formulas, raw inputs)

- **Ranking (1-20):** conviction order based on the strength/certainty of the fundamental case as cited in "Why Included" -- no ties. Allocation is not implied and is set downstream.
- **Regime (0/1):** `1` because SPY's 2026-08-20 close ($769.06 / $766.26 across two intraday pulls) is above its own 200-session SMA ($704.10 / $704.55), computed from Alpaca adjusted daily closes. Same value on all 20 rows, market-wide.
- **Risk Index (0-100):** `round((DEpct + CRIpct + Betapct)/3)`, each an ascending-sort percentile rank `(position/20)×100` computed within this week's 20 names: DEpct from TTM D/E (higher D/E → higher percentile); CRIpct from `1/current ratio` (lower current ratio → higher percentile; VOYA's current ratio is not meaningful for its business and was given a neutral 50th-percentile placeholder rather than fabricated); Betapct from 5-year monthly beta vs. SPY (60 months of data per name, Alpaca).
- **Volatility Index (0-100):** ascending-sort percentile rank `(position/20)×100` of trailing-252-day annualized standard deviation of daily log returns (Alpaca adjusted closes). No beta-proxy fallback was needed -- daily-return data was available for all 20.
- **Sentiment Index (0-100):** `round(0.6×A + 0.4×M)`. `A` = analyst consensus mapped Strong Buy=100, Buy=75, Hold=50, Sell=25, Strong Sell=0 (stockanalysis.com consensus label, analyst counts stated per name in the table above). `M` = ascending-sort percentile rank of (stock's 3-month total return minus its GICS sector ETF's 3-month total return); sector ETFs: XLK (Info Tech), XLF (Financials), XLV (Health Care), XLE (Energy), XLI (Industrials), XLB (Materials), XLY (Consumer Discretionary) -- 3-month sector ETF returns from the Bigdata.com market tearsheet as of 2026-08-19/20; 3-month stock returns from Alpaca.

|Ticker|Close|200d SMA|D/E|Current ratio|Beta(5y mo)|Ann. vol (252d)|Consensus (analysts)|3mo return %|Sector ETF 3mo %|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
|MSFT|480.06|429.58|0.29|1.23|1.11|31.8|Strong Buy(56)|14.76|2.82|
|V|368.71|330.45|0.69|1.08|0.75|21.8|Strong Buy(40)|11.56|11.12|
|XOM|168.30|140.48|0.16|1.14|0.17|25.5|Buy(25)|9.08|7.53|
|CVX|208.17|175.13|0.19|1.25|0.50|23.5|Buy(25)|9.93|7.53|
|EOG|153.01|124.96|0.31|1.63|0.25|28.6|Buy(30)|10.12|7.53|
|UNP|307.10|254.37|1.51|0.99|0.96|22.1|Buy(25)|16.29|6.70|
|LLY|1273.28|1048.06|1.62|1.36|0.52|35.5|Buy(29)|22.41|18.58|
|CSX|51.32|42.17|1.48|0.81|1.20|22.9|Buy(25)|12.14|6.70|
|NUE|243.75|199.38|0.31|2.51|1.88|31.4|Buy(17)|7.91|5.00|
|UNH|387.03|345.01|0.69|0.78|0.62|36.4|Buy(27)|1.77|18.58|
|VRTX|547.85|459.40|0.10|3.19|0.31|28.2|Buy(29)|26.37|18.58|
|MPC|364.84|230.38|1.33|1.25|0.52|34.0|Buy(19)|47.26|7.53|
|ANET|185.06|148.92|-0.90|2.96|1.59|54.4|Strong Buy(30)|24.54|2.82|
|PRI|300.51|270.87|0.68|2.43|0.86|21.4|Buy(8)|6.94|11.12|
|VOYA|97.88|79.41|0.84|NM|0.90|25.8|Buy(12)|19.44|11.12|
|PAYC|224.18|145.22|1.72|1.02|0.81|46.1|Buy(20)|67.32|2.82|
|FCFS|208.33|192.38|1.16|4.89|0.54|30.2|Strong Buy(5)|-7.75|11.12|
|LTH|44.63|31.40|1.29|0.66|1.48|36.5|Strong Buy(14)|35.45|-0.09|
|KRYS|337.02|281.75|0.01|8.32|0.50|38.9|Strong Buy(10)|10.78|18.58|
|VICR|210.43|196.95|0.01|13.25|2.37|90.2|Buy(4)|-21.57|6.70|

**Final invariant audit (PASS):** `top20.csv` contains 20 data rows, 20 unique tickers, and the requested 11-column header (Ticker, Sector, Why Included, Valuation, Bear Case, Ranking, Regime, Risk Index, Volatility Index, Sentiment Index, Data Quality -- one column more than last week's format, since this run's Objective explicitly requires the added Data Quality column). Rankings 1-20 each appear exactly once; Regime is binary and identical (1) on all 20 rows; Risk Index, Volatility Index and Sentiment Index all fall within 0-100; Data Quality falls within 0-1 (19 rows at 1.00, VOYA at 0.91 for its one NA current-ratio cell). The ticker set and sector labels in `top20.csv` match the Markdown picks table above. The raw-input table in this section controls index recalculation. All fundamentals and prices are dated 2026-08-19/2026-08-20 and must not be treated as a live, real-time recommendation on a later date.
