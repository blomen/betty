"""Dedicated SSE broadcast channels for the two-lane fire window.

Three channels replace the single odds_broadcaster for mirror events:
- sync_channel:   balance, history, settlements, notifications, provider state
- price_channel:  live odds ticks, price verification, edge updates
- action_channel: navigation, autofill, bet placement/skip confirmations
"""
from ..pipeline.broadcast import Broadcaster

sync_channel = Broadcaster()
price_channel = Broadcaster()
action_channel = Broadcaster()
