"""All on Black: extreme-risk single-position PAPER strategy. No real orders.

Picks the strongest already-eligible USD pair exhibiting a volume-backed
price breakout. Concentrated exposure amplifies losses as well as gains.
Stop and trailing exits are only simulated on periodic quotes.
"""
STOP_LOSS = 0.25
TRAILING_ACTIVATION = 0.20
TRAILING_DISTANCE = 0.12
DAILY_LOSS_PAUSE = 300.0  # Paper-only; unlike other bots' $30 limit

def signal(f):
    price = f['price']
    hour = f['hour']
    four = f['four']
    volume_ratio = f['vol_ratio']
    # Require unusually strong momentum, not just a low nominal token price.
    if not (0.03 <= hour <= 0.25 and four >= 0.05 and volume_ratio >= 2.2):
        return None
    if not (price > f['ma'] and price > f['prev_high'] * 1.002):
        return None
    breakout = price / f['prev_high'] - 1
    return round(min(100, 45 + min(20, hour * 150) +
                         min(20, (volume_ratio - 1) * 7) +
                         min(15, breakout * 350)), 1)
