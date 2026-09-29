# Evidence behind the five intraday setups (2026-09-29)

Why the article's playbook lost on our data (DECISIONS #30), what published
studies actually tested, and the rules pre-registered in DECISIONS #31.
Numbers are the sources' own; none were produced on Indian data unless
stated.

## Base rate (India)
- SEBI study of intraday cash traders, FY23: 71% lost money; among those
  with more than 500 trades a year, 80% lost. For loss-makers, trading
  costs were 57% of their losses. (Business Today summary of SEBI's July
  2024 study.)
- Groww charges (`config/costs.yaml`) per round trip: Rs 25k position about
  Rs 55 (0.22%), Rs 1 lakh about Rs 83 (0.08%), Rs 5 lakh about Rs 224
  (0.045%). The flat Rs 20 per order is fixed by the broker, so at Rs 25k a
  setup must average better than ~0.22% per trade just to break even.

## 1. Opening range breakout
- Zarattini, Barbon & Aziz (2024), "A Profitable Day Trading Strategy For
  The U.S. Equity Market" (SSRN 4729284), US stocks 2016-2023:
  - Trade only "stocks in play": top 20 by relative volume of the first
    5 minutes vs its 14-day average, relative volume > 100%, price > $5,
    14-day ATR > $0.50.
  - 5-minute opening range; long only if the first candle is green, short
    only if red; stop order at the range high (low).
  - Stop 10% of the 14-day ATR; no profit target, exit at the close.
  - Risk 1% per trade, leverage up to 4x. Reported 1,637% total return,
    Sharpe 2.81, after commissions. Without the stocks-in-play filter the
    same rules performed far worse.
- Independent replications (QuantConnect forum thread 18444; Quantified
  Strategies write-up) reproduce a positive but smaller edge and stress
  that it depends on the in-play selection and on letting winners run.

## 2. VWAP trend
- Zarattini & Aziz (2023), "Volume Weighted Average Price (VWAP) The
  Holy Grail for Day Trading Systems" (SSRN 4631351), QQQ 1-min, 2018-2023:
  long above VWAP, short below, reverse or exit on the cross, flat at the
  close. 671% total, Sharpe 2.1, max drawdown 9.4%, win rate under 50%:
  the edge comes from a few trend days.
- Exit is the cross back through VWAP, not a fixed R-multiple.

## 3. EMA pullback with candle trigger
- Marshall, Young & Rose (2006), candlestick patterns on DJIA stocks
  1992-2002: hammer, engulfing and the other classic reversal patterns
  showed no statistically significant predictive value.
- No peer-reviewed intraday test of a 5/15 EMA rejection was found; treat
  it as untested.

## 4. Bollinger band reversal
- Lento, Gradojevic & Wright (2007) on several indices: traditional
  Bollinger rules do not beat buy-and-hold after costs; the contrarian
  application did better. A Taiwan 50 study (ScienceDirect) reached a
  similar cost-driven conclusion.

## 5. Previous-day levels
- Osler (2000, Federal Reserve Bank of New York), FX support/resistance
  published by firms: price bounced at the levels 60.8% of the time vs
  56.2% at random levels — real but small.

## Cross-cutting
- Gao, Han, Li & Zhou (SSRN 2440866), intraday momentum: the first
  half-hour return predicts the last half-hour return (ETFs, 1993-2013);
  strongest on high-volume and volatile days.
- Common thread in the positive studies: trend-following exits (hold to
  the close or the VWAP cross), selection by opening volume, and both
  sides of the market. Fixed small targets and candle patterns alone have
  weak support.

## Why our first implementation lost (DECISIONS #30)

| Our rule | What the evidence did | Pre-registered change (#31) |
|---|---|---|
| Fixed 2R target | Hold to close / exit on VWAP cross | NATIVE exits + user's 5-10% targets (T1/T2/T5/T10) |
| 25 handpicked stocks | Scan the whole market, in-play stocks | Every stock in `data/universe_1y`, no cap; opening RVOL tagged |
| 15-min range, stop at range high | 5-min range, green first candle, 10% ATR stop | As the paper |
| Long only | Long and short | Mirrored short setups |
| Candle pattern required | No proven value alone | Kept (user rule) and measured against a no-pattern control |
| Only the setup timeframe | — | 10/30-min and daily trend agreement as an MTF variant (user rule) |
| Rs 25k per trade | Larger positions dilute fixed fees | Kept at Rs 25k (user's broker and limits); cost share reported |

## Sources
- SEBI study summary: businesstoday.in (SEBI intraday traders FY23, July 2024)
- SSRN 4729284 (ORB, stocks in play); QuantConnect forum 18444;
  quantifiedstrategies (ORB replication)
- SSRN 4631351 (VWAP); Concretum Research substack (VWAP follow-ups)
- Marshall, Young & Rose 2006 (researchgate: candlestick technical trading
  strategies)
- Lento, Gradojevic & Wright (acfr.aut.ac.nz Bollinger bands PDF);
  ScienceDirect Taiwan 50 Bollinger study
- Osler 2000, newyorkfed.org staff report (support and resistance)
- SSRN 2440866 (intraday momentum)
