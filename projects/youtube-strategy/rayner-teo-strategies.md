# Trading Strategy Report: Rayner Teo
*Generated 2026-04-05 10:54*

## Overview
- **Channel:** Rayner Teo
- **Total strategies extracted:** 43
- **Unique strategies:** 41
- **Strategy types:** breakout, breakout|price_action, breakout|price_action|momentum, breakout|reversal|price_action|multiple_timeframe, counter_trend, mean_reversion, price_action, price_action|trend_following, reversal, reversal|price_action, risk_management|trend_following|mean_reversion, swing, trend_following, trend_following|mean_reversion|price_action

## Top Strategies (Ranked by Confidence)

### 1. Bullish Price Rejection at Area of Value [++++]
**Type:** price_action | **Timeframe:** multiple | **Markets:** multiple

**Summary:** This strategy identifies bullish price rejection at a pre-defined 'area of value' after multiple failed attempts to break lower. Entry is on the next candle's open, with a stop loss one ATR below the recent low. The strategy uses multiple profit targets, with the first target placed just before the most recent swing high, and implies partial profit-taking.

**Indicators:** Area of Value (Support/Resistance/Demand Zone), ATR

**Entry Rules:**
Enter long on the open of the next candle after observing a bullish price rejection candlestick pattern (market tried to break lower multiple times and failed, closing near highs) at a pre-defined 'area of value'.

**Exit Rules:**
Target 1: Set just before the most recent extreme swing high. Target 2: Set further away. Implies partial profit-taking.

**Stop Loss:**
Place stop loss one ATR below the most recent extreme low.

**Risk Management:**
Adhere strictly to stop loss. Consider trading on demo or small accounts. Risk-reward for Target 1 can be less than 1 (e.g., 1:0.8).

**Backtested Results:**
One example trade shown resulted in an overall loss, as the first target profit was insufficient to cover the loss from the second half of the position being stopped out.

> ["at this point when I see this right I want to go along because the market is tell me that he tried to push the price down lower two times and failed", "I will set my stop loss like one ATR from this most recent extreme low which is this one here"]

---

### 2. Price Action Trading with MAY Formula [++++]
**Type:** price_action | **Timeframe:** multiple | **Markets:** multiple

**Summary:** This strategy outlines a comprehensive 5-step price action framework, dubbed the 'MAY Formula', for analyzing and executing trades. It emphasizes identifying market structure and areas of value, waiting for specific entry triggers, and then defining logical stop-loss placements and profit-taking methods based on whether the goal is to capture a swing or ride a trend. The approach is applicable across various markets and timeframes.

**Indicators:** Moving Average (20, 50, 200)

**Entry Rules:**
1. Identify Market Structure: Determine if the market is in an uptrend (series of higher highs and higher lows), downtrend (series of lower highs and lower lows), or range. Use a higher timeframe if the current timeframe is unclear. 2. Identify Area of Value: Locate potential zones where buying or selling pressure may emerge, such as support/resistance levels, trendlines, moving averages (e.g., 50-period MA), or channels. 3. Wait for an Entry Trigger at the Area of Value: Look for confirmation signals like price rejection (e.g., bullish/bearish engulfing patterns, hammer, shooting star, false breaks of swing lows/highs), a break of structure (e.g., a higher high/low forming in a downtrend), or a trendline break (of a counter-trend line, ideally at an area of value).

**Exit Rules:**
If aiming to capture a swing: Set a fixed target profit at logical levels such as previous swing highs/lows, support/resistance, or Fibonacci extensions. If aiming to ride a trend: Use a trailing stop loss. This can be done using a Moving Average (e.g., 20MA for short-term, 50MA for medium-term, 200MA for long-term; exit when price closes below the MA) or by trailing using price structure (exit when the previous swing low in an uptrend or swing high in a downtrend is broken).

**Stop Loss:**
Place the stop loss at a level that invalidates the trading setup. For support/resistance, place it below support or above resistance. For breakouts, place it below the breakout level (aggressive) or below a previous swing low/high (conservative). For chart patterns, place it at a point where the pattern would be distorted or proven wrong (e.g., above the 'head' of a Head & Shoulders pattern).

**Risk Management:**
The primary risk management advice is to place a logical stop loss that invalidates the trade setup. Additionally, if in doubt about the market direction or setup clarity, stay out of the market.

**Backtested Results:**
None mentioned.

> ["If you can focus on these five things you can pretty much ignore everything else.", "You want to place your stop loss at a level where it invalidates your trading setup."]

---

### 3. ATR-based Stop Loss Placement [++++]
**Type:** price_action | **Timeframe:** multiple | **Markets:** multiple

**Summary:** This strategy outlines a quantitative method for setting stop losses using the Average True Range (ATR) indicator. For long positions, the stop loss is placed 1 ATR below a defined area of value (like a swing low). For short positions, it's placed 1 ATR above a swing high. This approach aims to provide the trade with sufficient 'breathing room' to avoid premature stop-outs due to market volatility.

**Indicators:** ATR 20 (SMA)

**Entry Rules:**
Not specified (this strategy focuses on stop-loss placement after an entry).

**Exit Rules:**
Not specified (this strategy focuses on stop-loss placement).

**Stop Loss:**
For long trades, set stop loss 1 ATR below the area of value (e.g., a swing low near a moving average). For short trades, set stop loss 1 ATR above the area of value (e.g., a swing high near resistance). The ATR value is calculated using a 20-period SMA ATR. The multiple of ATR (e.g., 1, 2, or 3 ATR) can be adjusted based on personal preference.

**Risk Management:**
Wider stop losses (e.g., 2 or 3 ATR) require smaller position sizes to maintain consistent risk per trade, while narrower stop losses (e.g., 1 ATR) allow for larger position sizes but increase the risk of premature stop-outs.

**Backtested Results:**
None mentioned.

> You want to set your stop loss one atr beyond the area of value.

---

### 4. Stuck in a Box (Range Trading) [++++]
**Type:** price_action | **Timeframe:** multiple | **Markets:** multiple

**Summary:** This strategy targets ranging markets by buying at support levels. Entry occurs on the next candle's open after observing price rejection at support. A stop-loss is placed with a buffer below the rejection low, and profit is taken before the price reaches the opposing resistance level.

**Entry Rules:**
Identify a market in a range. Wait for price to come into an area of support (area of value). Look for price rejection of lower prices (e.g., candle closing near highs after trading lower). Enter long on the open of the next candle.

**Exit Rules:**
Take profit before the opposing pressure (resistance/swing high) is reached, looking left for previous resistance levels.

**Stop Loss:**
Place stop-loss a distance away (buffer) below the low of the price rejection candle, not precisely at the low.

**Risk Management:**
Not explicitly detailed beyond stop-loss placement.

**Backtested Results:**
No specific results mentioned.

> The core idea here is that the market is in a range, you wanna buy low and sell high. Let the price come into an area of value, an area of support, and let the price reject the lower prices.

---

### 5. Catch the Wave (Trend Following Pullback) [++++]
**Type:** trend_following | **Timeframe:** multiple | **Markets:** multiple

**Summary:** This strategy focuses on trading with the trend in an uptrend. It involves waiting for price to pull back to the 50-period moving average, then entering long upon observing a price rejection pattern. Stop-loss is placed below the rejection low, and profit is taken before the previous swing high.

**Indicators:** EMA 50

**Entry Rules:**
Identify a healthy uptrend. Wait for price to pull back to the 50-period Moving Average (area of value). Look for a form of price rejection (e.g., hammer, bullish engulfing pattern) at the 50MA. Enter long on the open of the next candle.

**Exit Rules:**
Exit the trade just before the previous swing high, as this is where potential sellers could come in (opposing pressure).

**Stop Loss:**
Place stop-loss with some buffer below the low of the price rejection candle.

**Risk Management:**
This strategy allows for more room to breathe compared to counter-trend trades. Traders can use market structure (hold as long as low/high remains intact) or a trailing stop-loss approach.

**Backtested Results:**
No specific results mentioned.

> In an up trend if it's in a healthy up trend the market tends to bounce off the 50MA. Wait for the price to come to an area of value which is this 50 period moving average. And again, look for a form of price rejection.

---

### 6. Fade the Move (Counter-Trend Reversal) [++++]
**Type:** counter_trend | **Timeframe:** multiple | **Markets:** multiple

**Summary:** This aggressive counter-trend strategy involves shorting a market that has made a strong rally into a key resistance level and shows a clear rejection pattern (e.g., bearish engulfing). The entry is on the next candle's open, with a tight stop-loss above the rejection high. The goal is to capture a quick swing down, exiting if the price shows signs of reversing back into the trend.

**Entry Rules:**
Identify a market in an uptrend that has rallied a long distance into a key resistance or swing high. Look for a strong price rejection pattern, such as a bearish engulfing pattern. Enter short on the open of the next candle, anticipating a move fueled by trapped breakout traders' stop-losses.

**Exit Rules:**
Capture one swing down towards a previous swing low. Exit quickly if the price breaks and closes above the entry candle's high, as this indicates the pullback might be ending and the trend resuming. Do not hold trades for too long.

**Stop Loss:**
Place stop-loss a distance away (buffer) above the high of the price rejection candle. Use a tighter stop-loss due to the counter-trend nature of the trade.

**Risk Management:**
This is a more aggressive strategy due to trading against the trend. It requires tighter stop-losses and quicker exits when signs of reversal against the trade appear. Do not overstay your welcome.

**Backtested Results:**
No specific results mentioned.

> This is actually a counter trend trade. When the market makes a strong rally into a key resistance or key level and then got rejected. You can take a short position and capture a swing down lower.

---

### 7. MAY Formula (Market Structure, Area of Value, Entry Trigger, Exits) [++++]
**Type:** price_action | **Timeframe:** multiple | **Markets:** stocks|forex|crypto

**Summary:** The 'MAY Formula' is a price action strategy that combines Market Structure, Area of Value, Entry Triggers, and Exits. Traders first identify the market's trend or range, then locate key support or resistance levels. Entries are timed using bullish (Hammer, False Break below support) or bearish (Shooting Star, False Break above resistance) candlestick/price patterns. Stop losses are placed to invalidate the trade idea, and take profits are set conservatively before the next opposing price structure.

**Entry Rules:**
1. **Market Structure (M):** Identify the current market condition (Uptrend, Downtrend, or Range). In an uptrend, look for buying opportunities. In a downtrend, look for selling opportunities. In a range, consider both buying at support and selling at resistance. 
2. **Area of Value (A):** Identify significant support (for buys) or resistance (for sells) levels. Focus on the two most recent and significant areas. Support can become resistance after a break, and vice versa. 
3. **Entry Trigger (E):** At the identified Area of Value, wait for a specific price action signal:
    *   **Bullish (for long entries):** A Hammer candlestick pattern (buyers regain control, closing near highs) or a False Break below support (price breaks below support then quickly reverses back above it).
    *   **Bearish (for short entries):** A Shooting Star candlestick pattern (sellers regain control, closing near lows) or a False Break above resistance (price breaks above resistance then quickly reverses back below it).
4. Enter the trade on the open of the next candle after the entry trigger is confirmed.

**Exit Rules:**
**Take Profit:** For long trades, set the target profit just before the next significant resistance level. For short trades, set the target profit just before the next significant support level. The goal is to be conservative and respect market structure to increase the odds of a profitable exit.

**Stop Loss:**
Place the stop loss at a location where if the price reaches it, the entire trading setup or idea is invalidated. For example, when buying at support, place the stop loss below the support level, away from the immediate price structure, to give the trade room to breathe and avoid premature stops from false breaks. For a bull flag pattern, place the stop loss below the structure that would invalidate the bull flag.

**Risk Management:**
Not explicitly detailed beyond stop loss placement. No specific mention of position sizing or risk per trade percentage.

**Backtested Results:**
No specific backtested results, win rates, or performance metrics are mentioned.

> ["The market can only be in one of three market conditions either it's in an uptrend a downtrend or range.", "Your stop-loss must be at a location right where if the price reaches it it will invalidate your entire trading setup your entire trading idea."]

---

### 8. Price Action Reversal from Area of Value (MAEE Framework) [++++]
**Type:** price_action | **Timeframe:** daily | **Markets:** forex|futures

**Summary:** This strategy focuses on identifying reversals within an established trend using price action at key 'Areas of Value'. It involves waiting for a specific candlestick pattern (like a hammer/pin bar) after a false break of a low within the value area. Stop loss is placed using ATR, and targets are set at previous swing highs, often with a multi-target approach.

**Entry Rules:**
1. Identify market structure (e.g., uptrend for buys). 2. Identify an 'Area of Value' (e.g., previous support/resistance zone). 3. Wait for price to enter the Area of Value. 4. Look for a specific price action pattern: price swings down, takes out previous lows within the area, then shows strong price rejection (e.g., a hammer/pin bar) closing near the highs of the day, indicating buyers stepping in. 5. Enter long on the open of the next candle.

**Exit Rules:**
1. First Target: Set just before the most recent extreme swing high. 2. Second Target: Set further away at a more distant swing high. (The example trade used a split position, taking partial profit at the first target).

**Stop Loss:**
Place stop loss one ATR (Average True Range) below the most recent extreme low that formed the entry trigger.

**Risk Management:**
Implied risk-to-reward calculation (e.g., 1:0.7 for first target, aiming for higher with second target). No explicit position sizing or risk per trade mentioned.

**Backtested Results:**
The specific example trade discussed resulted in an overall loss, as the first target profit was insufficient to cover the loss from the second half of the position which was stopped out. The speaker emphasizes that losers are part of trading.

> ["Market structure is in an uptrend so we are looking for buying opportunities.", "The market tried to push the price down lower two times and failed so that to me is a signal that okay buyers are possibly coming in from this area of value and could push the price up higher."]

---

### 9. Stacked Areas of Value Reversal (MAEE Framework) [++++]
**Type:** price_action|trend_following | **Timeframe:** multiple | **Markets:** forex|stocks|futures|multiple

**Summary:** This strategy enhances the 'Area of Value' concept by looking for 'Stacked Areas' where multiple support/resistance levels (e.g., horizontal support and a dynamic moving average) converge. This convergence is believed to increase the probability of a reversal. Entry is triggered by a specific price action pattern, such as a double bottom with a false break, within this stacked area.

**Indicators:** 50-period Simple Moving Average (SMA)

**Entry Rules:**
1. Identify market structure (e.g., uptrend). 2. Identify 'Stacked Areas of Value' where multiple support/resistance levels converge (e.g., horizontal support/resistance and a significant moving average like the 50 SMA). 3. Wait for price to hit this stacked area. 4. Look for a specific price action pattern: price forms a double bottom with a false break below the support level, then reverses and closes back above support. 5. Enter long on the open of the next candle.

**Exit Rules:**
1. First Target: Set before the most recent swing high. 2. Second Target: Set at a further swing high.

**Stop Loss:**
Place stop loss a distance below the low of the double bottom/false break pattern.

**Risk Management:**
Not explicitly mentioned.

**Backtested Results:**
This strategy was presented as a 'game plan' for an ongoing trade, so no backtested results or live trade outcomes were provided.

> ["This is what I call a stack area of value this is significant because again we have multiple areas of value coming together and that increase the odds of a a reversal right at this area.", "I still want to wait for a valid entry trigger to go long so now what I would look for at least in this case like what I look for is for the price to hit down lower... give me a bounce up higher... come back a second time right try to break below this low but couldn't and then break it and then reverse up and close backup of support."]

---

### 10. Bollinger Band Trailing Stop Loss [++++]
**Type:** trend_following | **Timeframe:** multiple | **Markets:** multiple

**Summary:** This strategy is designed for managing and riding existing trends. It uses either the 20-period moving average (middle Bollinger Band) or the outer Bollinger Band as a dynamic trailing stop loss. Traders remain in the trade as long as the price respects the chosen band, exiting only when price breaks and closes beyond it, allowing for capture of significant trend moves.

**Indicators:** Bollinger Bands (20-period Moving Average / middle band), Bollinger Bands (outer bands)

**Entry Rules:**
Not discussed; this strategy is for managing existing trend-following trades.

**Exit Rules:**
For a long trade, exit when the market breaks and closes below the 20-period moving average (middle band) or the lower Bollinger Band. For a short trade, exit when the market breaks and closes above the 20-period moving average (middle band) or the upper Bollinger Band.

**Stop Loss:**
Use either the 20-period moving average (middle band) or the outer Bollinger Band as a trailing stop loss. The outer band provides more room for the trade to breathe.

**Risk Management:**
Not mentioned.

**Backtested Results:**
Not mentioned.

> you can drill your stop-loss right that means you will remain in the trade right until the market break and close above the 20-period moving average and then you exit your trade.

---

### 11. False Break Technique [++++]
**Type:** reversal|price_action | **Timeframe:** multiple | **Markets:** multiple

**Summary:** This strategy profits from 'trap traders' by identifying when price breaks a significant level with strong momentum, only to reverse sharply and close back within the previous range. The reversal traps breakout traders, whose subsequent stop-loss triggers can fuel the false break's continuation. It emphasizes looking for strong momentum into the level and avoiding 'stair-stepping' price action.

**Entry Rules:**
Identify a significant high or low (resistance/support). Price breaks above/below this level with strong momentum, then makes a huge, sudden reversal and closes back within the previous range. Avoid 'stair-stepping' price action into the level.

**Exit Rules:**
Not explicitly detailed, but implies targeting the opposite end of the range or where trapped breakout traders' stop losses would be triggered.

**Stop Loss:**
Not explicitly detailed for the false break entry, but would typically be placed just beyond the high/low of the false break candle or structure.

**Risk Management:**
Profiting from 'trap traders' whose stop losses fuel the reversal.

**Backtested Results:**
None mentioned.

> this occurs right when the price breaks above a significant high only to reverse back in the opposite direction. traders who buy the breaker of these highs... they are now trapped.

---

### 12. Build-up Breakout Technique [++++]
**Type:** breakout|price_action|momentum | **Timeframe:** multiple | **Markets:** multiple

**Summary:** This strategy focuses on trading breakouts after a period of 'tight consolidation' (build-up) near a resistance or support level. The build-up indicates volatility contraction, which often precedes volatility expansion upon breakout. A 20-period Moving Average can be used as a guideline to confirm the build-up, and stop losses are placed just beyond the build-up's range for a tighter risk profile.

**Indicators:** EMA 20

**Entry Rules:**
Identify a resistance or support level. Wait for a 'tight consolidation' (build-up) to form near this level. The 20 EMA should start to 'hug' or 'touch the lows/highs' of this build-up. Enter on the breakout of this build-up.

**Exit Rules:**
Not explicitly detailed.

**Stop Loss:**
Place stop loss just below the lows of the build-up (for a long breakout) or above the highs (for a short breakout) for a tighter risk profile.

**Risk Management:**
Tighter stop loss due to the build-up improves risk-to-reward. Volatility expansion after the build-up can lead to fast moves in your favor.

**Backtested Results:**
None mentioned.

> a build up is when a tight consolidation forms right prior to the breakup. you have a logical place to set your stop loss... just below the lows of this build up. the 20 ma will start to touch the lows of the build up.

---

### 13. Pre-Breakout Entry (Multi-Timeframe Build-up & False Break) [++++]
**Type:** breakout|reversal|price_action|multiple_timeframe | **Timeframe:** multiple | **Markets:** multiple

**Summary:** This advanced strategy aims to enter a breakout trade early by combining higher timeframe analysis with lower timeframe reversals. First, a build-up is identified on a higher timeframe. Then, on a lower timeframe, a false break within that build-up is used as an entry point, anticipating the larger breakout. Partial profit-taking is recommended to manage risk if the initial move is not immediately followed by the full breakout.

**Entry Rules:**
1. On a higher timeframe (e.g., Daily), identify a 'build-up' forming near a resistance/support level. 2. On a lower timeframe (e.g., 4-hour), within that build-up area, look for a 'false break' setup (price breaks a minor high/low within the build-up, then reverses). Enter the trade based on this lower timeframe false break, anticipating the higher timeframe breakout.

**Exit Rules:**
Take a portion of your position off if the market moves in your favor but doesn't immediately break out. This leaves a remaining position to ride the larger breakout wave.

**Stop Loss:**
Not explicitly detailed for the initial entry, but implies placing it beyond the false break structure on the lower timeframe.

**Risk Management:**
Taking partial profits allows for managing risk if the market doesn't immediately break out, potentially reducing losses or allowing for a breakeven exit if the market reverses.

**Backtested Results:**
None mentioned.

> identify a build up on the TV time frame... look for a Falls break only for what time frame. you might suffer one or two losing trades before you catch the move... take a portion of your position off... to write the mix wave down lower.

---

### 14. Rubber Band Snap (Avoidance Strategy) [++++]
**Type:** risk_management|trend_following|mean_reversion | **Timeframe:** multiple | **Markets:** multiple

**Summary:** This is an avoidance strategy that advises against trading in the direction of the trend when price is significantly extended and far from its 'area of value' (e.g., a moving average or trendline). The rationale is that trading far from value results in wide stop losses and poor risk-to-reward ratios, as price is likely to revert to the mean, potentially stopping out the trade. Instead, traders should wait for price to return to the area of value before considering an entry.

**Indicators:** EMA 50, EMA 200, Trendlines, Trend Channels

**Entry Rules:**
N/A (This is an avoidance strategy, not an entry strategy).

**Exit Rules:**
N/A (This is an avoidance strategy).

**Stop Loss:**
N/A (This is an avoidance strategy), but highlights that trading far from value leads to wide stop losses and poor risk-to-reward.

**Risk Management:**
Avoid trading in the direction of the trend when the price is far away from its 'area of value' (e.g., moving average, trendline). Instead, wait for price to return to the area of value to achieve tighter stop losses and improved risk-to-reward ratios.

**Backtested Results:**
None mentioned.

> avoid trading in the direction of the trend when the price is far away from the area of value. if you let the market come to you you trade near an area of value now can you see your stop-loss now it's much tighter and it really improves your risk to reward.

---

### 15. Trendline Channel Bounce Trading [++++]
**Type:** trend_following | **Timeframe:** multiple | **Markets:** multiple

**Summary:** This strategy involves identifying a long-term trend using carefully drawn trendlines and trend channels. Traders aim to enter trades when the price retests the trendline or the boundary of the trend channel, buying in uptrends and selling in downtrends, to achieve a favorable risk-to-reward ratio with tight stop-losses.

**Indicators:** Trendlines, Trend Channels

**Entry Rules:**
Identify a long-term trend by drawing a main trendline connecting at least two major swing points, adjusting for maximum touches. Copy-paste the main trendline to create a parallel trend channel, defining an 'area of value'. In an uptrend, enter long when price retests the lower boundary of the trend channel. In a downtrend, enter short when price retests the upper boundary of the trend channel.

**Exit Rules:**
Not explicitly defined for profit-taking, but implies holding for the trend or potentially exiting at the opposite side of the trend channel.

**Stop Loss:**
Place stop-loss 'much tighter' just beyond the trendline or channel boundary to achieve a favorable risk-to-reward ratio.

**Risk Management:**
Focus on achieving a favorable risk-to-reward by placing tight stop-losses near the trendline retest. No specific position sizing mentioned.

**Backtested Results:**
None mentioned.

> trend line helps you to better time your entries and exits... buying near the trend line offers write a much more favorable risk to reward on your traits. you know that this area over here right is where you look for potential selling opportunities... if the if you know the market is near the lows over here of this downward trend line you don't want to be shorting because there's a good chance the market can spike up higher.

---

### 16. Buy at Support in Uptrend with Candlestick Confirmation [++++]
**Type:** trend_following | **Timeframe:** daily | **Markets:** forex

**Summary:** This strategy involves identifying an uptrend and drawing key support levels. Traders wait for a pullback to these support areas and confirm buying pressure with a bullish candlestick pattern. Entry is on the next candle open, with a stop loss placed 1 ATR below the trigger candle's low and a take profit set just before the next resistance level.

**Indicators:** ATR (20 period SMA)

**Entry Rules:**
Identify an uptrend. Draw the two most recent support levels (often previous resistance turned support). Wait for price to pull back to one of these support areas. Look for a bullish reversal candlestick pattern (e.g., Hammer, Bullish Engulfing) as an entry trigger. Enter on the open of the next candle after the trigger.

**Exit Rules:**
Set target profit just before the recent swing high (resistance).

**Stop Loss:**
Place stop loss below the low of the entry trigger candle, minus 1 ATR (20 period SMA) value. The stop loss should be at a point where the support area is clearly invalidated.

**Risk Management:**
Calculate risk to reward ratio. Example showed 1.13 R:R.

**Backtested Results:**
Cherry-picked chart example showed profit.

> ["The market overall is in an uptrend, the market make a pullback towards this area of value... you got Clues right that buyers are stepping in because the market opened over here try to break down lower couldn't but reverse it eventually and close near the highs of the day so this tells you that buyers are stepping in.", "Your stop loss must be at a location right where if the price reaches it it will invalidate your entire trading setup your entire trading idea."]

---

### 17. Sell at Resistance in Downtrend with Candlestick Confirmation [++++]
**Type:** trend_following | **Timeframe:** 4h | **Markets:** forex

**Summary:** This strategy focuses on identifying a downtrend and drawing key resistance levels. Traders wait for a rally to these resistance areas and confirm selling pressure with a bearish candlestick pattern. Entry is on the next candle open, with a stop loss placed 1 ATR above the trigger candle's high and a take profit set just before the next support level.

**Indicators:** ATR (20 period SMA)

**Entry Rules:**
Identify a downtrend. Draw the two most recent resistance levels. Wait for price to rally to one of these resistance areas. Look for a bearish reversal candlestick pattern (e.g., Bearish Engulfing, Shooting Star, or a false break) as an entry trigger. Enter on the open of the next candle after the trigger.

**Exit Rules:**
Set target profit just before the recent swing low (support).

**Stop Loss:**
Place stop loss above the high of the entry trigger candle, plus 1 ATR (20 period SMA) value. The stop loss should be at a point where the resistance area is clearly invalidated.

**Risk Management:**
No specific risk management mentioned beyond stop loss placement and implied R:R.

**Backtested Results:**
Cherry-picked chart example showed profit.

> ["The market is in a downtrend... area of value is at resistance fantastic... we have a valid entry trigger this is what we actually call a bearish engulfing pattern.", "What we are trying to do over here is to set our stop loss a distance away from the resistance because we don't want to get stopped up prematurely."]

---

### 18. Trend Following with Dynamic Support/Resistance & Candlestick Confirmation [++++]
**Type:** trend_following | **Timeframe:** daily | **Markets:** stocks|forex|crypto|multiple

**Summary:** This strategy focuses on trading with the prevailing trend by identifying strong support (in uptrends) or resistance (in downtrends) using recent swing points. Entries are confirmed by specific candlestick patterns like Hammer, Shooting Star, or False Break setups. Stop losses are placed using the ATR indicator, and profits are taken in stages, first at recent swing highs/lows and then using Fibonacci extensions for trend continuation.

**Indicators:** ATR 20 (SMA), Hammer Candlestick Pattern, Shooting Star Candlestick Pattern, False Break Price Action Setup, Trend-based Fibonacci Extension

**Entry Rules:**
1. Identify the market trend (Uptrend or Downtrend).
2. Draw relevant Support (in an uptrend) or Resistance (in a downtrend) using the two most recent swing points. Ensure the S&R level is consistent with the current trend (i.e., if price reaches it, the trend structure is still intact).
3. Wait for price to pull back into the identified S&R area.
4. Wait for a specific entry trigger:
   - For Buys (Uptrend at Support): A Hammer candlestick pattern or a False Break setup (price breaks below support then reverses strongly to close back above).
   - For Sells (Downtrend at Resistance): A Shooting Star candlestick pattern or a False Break setup (price breaks above resistance then reverses strongly to close back below).
5. Enter on the open of the next candle after the trigger.

**Exit Rules:**
For Buys (Uptrend):
1. Target 1 (Partial Profit): Set at the recent swing high (resistance).
2. Target 2 (Remaining Position): Use the Trend-based Fibonacci Extension tool (drawn from the swing low preceding the pullback, to the swing high, then back to the pullback low). Target the 127% or 162% extension level.

For Sells (Downtrend - implied inverse):
1. Target 1 (Partial Profit): Set at the recent swing low (support).
2. Target 2 (Remaining Position): Use the Trend-based Fibonacci Extension tool (drawn from the swing high preceding the pullback, to the swing low, then back to the pullback high). Target the 127% or 162% extension level.

**Stop Loss:**
1. For Buys (Uptrend): Place stop loss 1 ATR (20-period SMA) below the low of the trigger candle (Hammer or False Break low).
2. For Sells (Downtrend - implied inverse): Place stop loss 1 ATR (20-period SMA) above the high of the trigger candle (Shooting Star or False Break high).

**Risk Management:**
Risk-to-reward ratio is assessed for each trade (e.g., 1:1.14). No explicit position sizing rules are provided beyond taking partial profits.

**Backtested Results:**
No specific backtested results or win rates are mentioned; only trade examples are shown.

> ["In an uptrend support is more powerful than resistance because it tends not to get broken.", "What I'd like to do instead is to actually wait for some sort of a confirmation otherwise known as an entry trigger."]

---

### 19. The MAEE Formula (Market Structure, Area of Value, Entry, Exits) [++++]
**Type:** trend_following|mean_reversion|price_action | **Timeframe:** multiple | **Markets:** multiple

**Summary:** The MAEE Formula is a robust trading strategy that focuses on trading with the trend from key areas of value. It involves identifying market structure, waiting for price to retrace to an area of value (support/resistance or moving average), and then entering based on a specific entry trigger (candlestick pattern or moving average break). Exits are managed with an initial ATR-based stop loss and either fixed swing targets or trailing stops using moving averages for trend following.

**Indicators:** Moving Average (5-period), Moving Average (50-period), Moving Average (100-period), Average True Range (ATR), Candlestick Patterns (Hammer, Bullish Engulfing, Bearish Engulfing, Dragonfly Doji/Bearish Price Rejection)

**Entry Rules:**
1. Market Structure (M): Identify a clear uptrend (higher highs, higher lows) or downtrend (lower highs, lower lows). Only trade in the direction of the trend.
2. Area of Value (A): Price must retrace to a significant area of value. For uptrends, this is typically support (previous resistance turned support, swing lows) or a respected Moving Average (e.g., 50-period MA). For downtrends, this is resistance (previous support turned resistance, swing highs) or a respected Moving Average. Confluence of S/R and MA makes the area more powerful.
3. Entry Trigger (E): A specific signal confirming buyers/sellers are in control. This can be a bullish reversal candlestick pattern (e.g., Hammer, Bullish Engulfing) for long entries, or a bearish reversal candlestick pattern (e.g., Bearish Engulfing, Dragonfly Doji/Bearish Price Rejection) for short entries. Alternatively, a Moving Average Break can be used: price breaks and closes above a 5-period Moving Average for long entries, or breaks and closes below for short entries. Enter on the next candle open after the trigger.

**Exit Rules:**
For swing trading, set a target just before the next significant opposing swing high (for long trades) or swing low (for short trades).
For trend following, trail the stop loss using a longer-period Moving Average (e.g., 50-period MA or 100-period MA). Exit the trade when the price breaks and closes below (for long trades) or above (for short trades) the trailing Moving Average.

**Stop Loss:**
Place the initial stop loss 1 ATR (Average True Range) below the extreme low of the area of value/entry candle for long trades.
Place the initial stop loss 1 ATR above the extreme high of the area of value/entry candle for short trades.

**Risk Management:**
Practice sound risk management. Do not risk a large percentage (e.g., 50-100%) of the account on a single trade. Be prepared for losing trades even when all conditions align.

**Backtested Results:**
No specific backtested results or win rates are mentioned, only illustrative examples.

> my own trading strategy right is based on all these concepts that i've just shared with you; this entire training on technical analysis is sharing with you this trading strategy called it's like a formula i call it m a e e the may formula; this trading strategy it's robust in the sense that you know you can tweak it right to your needs

---

### 20. S&P 500 Pullback Trading Strategy [++++]
**Type:** mean_reversion | **Timeframe:** daily | **Markets:** S&P 500 (ETFs, Futures). Potentially adaptable to other stock indices (Russell 1000, Nasdaq, local stock markets) and individual stocks, though specific rules for individual stocks are in a separate resource.

**Summary:** This strategy focuses on buying pullbacks in an uptrending S&P 500 market. It uses the 200-day SMA to define the trend and the 10-period RSI below 30 for entry. Trades are exited when the RSI crosses above 40 or after 10 days, demonstrating a high win rate but low trade frequency based on backtesting.

**Indicators:** 200-day Moving Average (SMA), 10-period Relative Strength Index (RSI)

**Entry Rules:**
1. The S&P 500 must be above its 200-day Moving Average (SMA) to confirm an uptrend. 2. The 10-period Relative Strength Index (RSI) must be below 30. 3. Enter long using a market order on the next day's open after both conditions are met.

**Exit Rules:**
1. Exit when the 10-period RSI crosses above 40. Sell on the next day's open. OR 2. If the 10-period RSI has not crossed above 40, exit manually after 10 trading days (time stop).

**Stop Loss:**
Not explicitly defined. The exit rules serve as profit-taking and time-based exits, but a specific stop-loss for limiting losses is not provided.

**Risk Management:**
Not explicitly defined (no mention of position sizing or risk per trade).

**Backtested Results:**
Backtested from 1996 to 2019 (23 years) on the S&P 500. Total trades: 36. Winning rate: 88.89%. Average gain per trade: 1.43%. Average loss per trade: -0.87%. The main downside is the low frequency of trading opportunities.

> ["the s p 500 must be above the 200-day moving average", "we want the 10 period rsi to be below 30.", "we are looking to exit right when the 10 period rsi has crossed above 40 or after 10 trading days", "Our winning rate over here is 88.89%... average gain per trade is about 1.43% and your average loss right on each trade is about negative 0.87 percent"]

---

## Strategy Type Breakdown

| Type | Count | Avg Confidence |
|------|-------|----------------|
| breakout | 4 | 3.2/5 |
| breakout|price_action | 1 | 4.0/5 |
| breakout|price_action|momentum | 1 | 4.0/5 |
| breakout|reversal|price_action|multiple_timeframe | 1 | 4.0/5 |
| counter_trend | 1 | 4.0/5 |
| mean_reversion | 2 | 3.5/5 |
| price_action | 11 | 3.7/5 |
| price_action|trend_following | 1 | 4.0/5 |
| reversal | 1 | 4.0/5 |
| reversal|price_action | 1 | 4.0/5 |
| risk_management|trend_following|mean_reversion | 1 | 4.0/5 |
| swing | 1 | 3.0/5 |
| trend_following | 16 | 3.6/5 |
| trend_following|mean_reversion|price_action | 1 | 4.0/5 |

## Most Referenced Indicators

| Indicator | Mentions |
|-----------|----------|
| ATR 20 (SMA) | 3 |
| ATR | 2 |
| EMA 50 | 2 |
| Bollinger Bands (20-period Moving Average / middle band) | 2 |
| EMA 20 | 2 |
| Trendlines | 2 |
| Trend Channels | 2 |
| ATR (20 period SMA) | 2 |
| Candlestick range analysis | 2 |
| Market structure (highs/lows) | 2 |
| Support and Resistance Levels | 2 |
| Area of Value (Support/Resistance/Demand Zone) | 1 |
| Moving Average (20, 50, 200) | 1 |
| 50-period Simple Moving Average (SMA) | 1 |
| Bollinger Bands (outer bands) | 1 |
