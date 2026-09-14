# The export intelligence system

### Where "seeing around corners" actually comes from

Trade developments pass through a predictable lifecycle. Most export content appears at the last two stages. The Desk works at the first three and adds consequences at the fourth.

| Stage | What exists | Typical source | Who covers it today | Desk role |
| --- | --- | --- | --- | --- |
| 1. Draft | Proposed rule, consultation, investigation initiation | WTO ePing, Federal Register notices, EU "have your say", DGTR initiations, foreign trade-remedy initiations | Almost no one in Indian export content | **Draft Watch** — early warning |
| 2. Notified | Official instrument published | DGFT notifications and trade notices, CBIC notifications, RBI, Gazette, EU OJ | Tax portals (verbatim) | **Fine Print** — meaning and action |
| 3. Effective | Rule starts to apply | The same documents, dated | Few | **Countdown** |
| 4. Enforced | Rejections, duties collected, cases decided | RASFF, FDA refusals, CVD/AD final determinations, court orders | Specialists only | **Rejection Watch**, **Second Order** |
| 5. Reported | News coverage | ET, BS, Mint, Reuters | Everyone | Confirmation only |
| 6. Commented | LinkedIn takes, YouTube explainers | Everyone | Saturated | **Headline vs Reality** when the takes are wrong |


### The source universe

Tiered by value. **P** = primary, **S** = secondary. **Early** = gives a lead time; **Confirm** = verifies.

| Source | P/S | Signal | Cadence | Method | Why it's worth it |
| --- | --- | --- | --- | --- | --- |
| **A · Indian regulatory (core, daily)** |
| DGFT: notifications, public notices, trade notices, circulars | P | Early/Confirm | Daily, twice (DGFT often uploads late in the day) | Page-change watcher | Scheme rates and extensions, FTP changes, procedure |
| CBIC: customs tariff and non-tariff notifications, ADD/CVD/safeguard, circulars | P | Confirm | Daily | Page watcher / RSS | Duty changes (scrip demand), input-cost duties |
| ICEGATE advisories | P | Early | Weekly | Page watcher | Operational changes (e-scrip, filing) |
| RBI: FEMA notifications, master directions | P | Confirm | Daily | RSS | Realisation, EDPMS, trade finance |
| DGTR: anti-dumping initiations and findings | P | Early | Weekly | Page watcher | Input costs for Indian manufacturers |
| PIB (Commerce, Finance, Textiles, MSME, Agriculture) | P | Early | Daily | RSS | Cabinet decisions, scheme launches |
| Commodity boards and EPCs (APEDA, MPEDA, Spices Board, Tea and Coffee Boards, AEPC, EEPC, Texprocil, CLE, Pharmexcil…) | P/S | Early | Weekly | Email subscriptions → inbox parser | Sector circulars, market advisories |
| **B · Foreign regulatory (the edge, daily or weekly)** |
| WTO ePing (SPS/TBT notifications) | P | **Early** | Daily email alerts by HS and market | Email alerts → parser | Draft rules with comment windows. Best early-warning source available, and free. |
| US Federal Register (USTR, Commerce AD/CVD, CBP, FDA) | P | Early/Confirm | Daily | API search for "India" | Tariffs, CVD cases (including RoDTEP findings), import rules |
| EU Official Journal, DG TRADE trade defence, Access2Markets | P | Early/Confirm | Daily/weekly | RSS / page watch | CBAM, EUDR, AD cases, FTA implementation |
| EU RASFF, US FDA import refusals and alerts | P | Enforced | Weekly/monthly | Portal queries | What gets Indian food and agri shipments rejected |
| UK trade tariff, Trade Remedies Authority, UK CBAM guidance | P | Confirm | Weekly | Page watch | CETA implementation |
| GCC, UAE and Saudi regulators; China GACC; trade-remedy agencies in Canada, Australia, Turkey, Brazil | P | Early | Weekly | Page watch + manual | Frequent sources of action against Indian goods |
| **C · Data (weekly, monthly)** |
| Commerce ministry monthly quick estimates; TradeStat / DGCI&S | P | Confirm | Monthly (mid-month) | Download | India's exposure by HS and country |
| UN Comtrade, ITC Trade Map / Market Access Map / Export Potential Map | P/S | Context | On demand | API / manual | Market sizing, competitor shares, tariffs |
| Global Trade Alert; WTO trade monitoring | S | Early | Weekly | Manual | Global interventions database |
| IMF PortWatch | P | Early | Weekly | Manual | Chokepoint transits (Hormuz, Red Sea) |
| **D · Logistics** |
| Drewry WCI (Thu), Freightos FBX, SCFI (Fri) | S | Early | Weekly | Manual / feeds | Rate direction on Indian lanes |
| Carrier advisories (surcharges, war-risk, routing); Indian port authorities | P | Early | Daily in disruption, else weekly | Email / page watch | Real cost changes before they hit invoices |
| **E · News and analysis (confirmation, context)** |
| Business Standard, ET, Mint, FE, BusinessLine, Reuters, Fibre2Fashion | S | Confirm | Daily | Google News RSS queries | What's "reported"; saturation check |
| GTRI, ICRIER, RIS, CSEP, ORF; law-firm alerts | S | Context | Weekly | RSS / email | Quote, extend, don't duplicate |
| **F · Proprietary (the moat)** |
| Advisory conversations, Incentive Desk activity, LEAP questions, CHA and forwarder network, association WhatsApp groups | P | **Early** | Continuous | A 60-second "field note" form | Signals nobody else has: what buyers are asking, where shipments stall. Use only anonymised, with consent; never publish client data or deal terms. |


### Noise (monitor only to measure saturation)

Unverified WhatsApp forwards · "BIG UPDATE" LinkedIn reposts · SEO blogs (leads only, never sources) · YouTube news recaps · "export leads" pages · Telegram tip channels · AI-generated trade blogs · stock-market commentary on exporter shares.


### The exposure line: required on every Signal post

Every Signal post carries one sourced line on how exposed India is. For example: *"India exported [value] of HS [code] to [market] in [period] (source: TradeStat / UN Comtrade)."* This one habit makes the Desk's posts concrete, and it is the hardest thing for casual commentators to copy.


### Cluster reference map (starter)

| Cluster | Main export products | HS chapter / heading (indicative) | Typical sensitivity |
| --- | --- | --- | --- |
| Tirupur | Knitted apparel | Ch. 61 | US tariff tier, UK CETA, RoSCTL, Bangladesh preferences |
| Karur | Home textiles, made-ups | Ch. 63 | RoSCTL, EU FTA, US tariffs |
| Coimbatore | Pumps, motors, castings, engineering | 8413, 8501, Ch. 73 | CBAM (steel content), US Section 232/301, EU FTA |
| Erode | Turmeric, textiles | 0910, Ch. 52/63 | SPS limits, RASFF, FDA |
| Namakkal | Eggs, poultry | 0407 | Gulf demand, SPS rules, Hormuz shipping |
| Ambur / Vellore / Ranipet | Leather, footwear | Ch. 41, 42, 64 | EUDR (leather proposed for exclusion), UK CETA |
| Tuticorin | Marine products, port-linked trade | Ch. 03 | US CVD/AD on shrimp, SPS, freight |
| Chennai / Hosur | Auto components, electronics | 8708, Ch. 85 | US tariffs, EU FTA rules of origin |

HS mappings are indicative starter tags for the database, not tariff classifications. Confirm product-level codes before publishing any exposure figure.
