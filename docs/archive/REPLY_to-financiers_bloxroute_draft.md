Scott, Richard —

This is helpful, and I think we're actually aligned on the core idea. You're right that mempool / relay data is fundamentally different from node data: it shows you transactions *before* they land, which is genuinely forward-looking in a way public state isn't. No argument from me there — that "intent" layer is real, and seeing a large pending swap before it's mined is a legitimate signal that a move is coming. I'm fully on board with exploring it.

There's one practical wrinkle I want to flag early, just so we set this up the right way and don't lose time:

**bloXroute data can't be run through a backtest — because it's live-only.** The fields that make it valuable (the microsecond arrival timestamp, the live mempool backlog, propagation latency, clock drift) exist in real time and aren't stored anywhere historically. There's no archive of "what the relay saw at 3:47 a.m. last Tuesday" to replay. That's not a limitation of our setup — it's the nature of the data. So "re-run the Minkowski backtest on bloXroute data" isn't something that can be done after the fact; the data only exists going forward, in real time.

The good news is that points us at the *right* way to evaluate it, and I think it's actually a cleaner test than a backtest would be: **a live shadow run.**

Here's what I'd propose:

1. We subscribe to bloXroute (a trial or the entry tier is plenty to start).
2. I wire the feed into the engine and let it run live for a week or two — but in **shadow mode**: every time the model sees pending flow and predicts "X is about to move +N bps next block," it logs that prediction with a timestamp.
3. We then score those predictions against what actually happened on-chain.

This tests the exact claim that matters — *does the relay data let us predict the next block?* — using real live data, the way it's meant to be used, with **zero capital at risk.** If the predictions come in accurate, that's the strongest possible green light, and we move to execution with real evidence behind us (and something concrete to show any future partner). If they don't, we've learned it for the cost of a month's subscription rather than a deployed position.

It also lets us nail down the data-field mapping in practice: once the live feed is flowing, we can see exactly which of the fields you and Richard listed carry signal and tune from there.

One small logistics note: bloXroute's coverage is strongest on Ethereum/BSC/Base and doesn't currently extend to Arbitrum/Optimism, so we'd want to point the shadow run at the chains it actually covers. Easy to do — just worth knowing up front.

If you're good with this, send me whichever bloXroute tier you'd like to start on and I'll get the shadow run wired up. I'm genuinely keen to see what the live feed shows — this is the right thing to test, and a live shadow run is the honest, fastest way to find out before any money is on the line.

— Jason
