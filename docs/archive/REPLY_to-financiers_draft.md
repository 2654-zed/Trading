Scott, Richard —

Thanks for pushing on this. I want to engage it directly, because I think we're closer on method than it sounds, and there are a couple of points where the record just needs correcting.

**First, a factual correction: I'm not analyzing price alone.** The most recent test already used an eight-factor matrix — momentum, volatility, acceleration, volume, price range, funding rate, funding change, and longer-horizon momentum — combined with Richard's exact Minkowski-signature metric. That *is* the D1–D8 concept. Earlier tests went further and used our L3 behavioral data (transfer-role activity, flow concentration), not price at all. So "add more inputs beyond price" isn't an untried idea here — it's something I've already built and run. I want to be clear about that so we're not proposing to re-run an experiment that's already on the books.

**What that eight-factor test showed:** the Minkowski metric had essentially zero correlation with forward returns *in-sample* (+0.006) — meaning there was no relationship to fit even with hindsight — and a plain distance metric (none of the advanced structure) performed identically. The only factor with any signal was ordinary momentum, a long-known effect. I'm happy to share the script and raw numbers; it's fully reproducible.

**On public vs. private / "intent" data:** I used public historical data because that's the only data that *can* be backtested. Pre-execution / mempool / "intent" data isn't recorded historically — it exists for milliseconds in a live mempool and is gone. There's no archive of it to test against. The only way to evaluate a strategy built on intent data is to run it live, with real capital and a real mempool feed — which means we'd be committing money on faith rather than evidence. That's precisely the step I'm trying not to take blindly. (Worth noting too: the mempool is public — every MEV bot sees the same data — so it's a shared firehose you have to win a latency race to use, not a private edge.)

**On methodology — this is the part I'd most want us aligned on.** "Collect the inputs, analyze 24 hours of trades, and tweak the weights" will *always* produce a good-looking result, because it grades the model on the same data it was tuned on. That's curve-fitting, not edge, and 24 hours is far too short a window to mean anything — a model tuned that way looks excellent and then fails the moment real money hits it. The discipline that actually separates a real signal from a fitted one is out-of-sample testing: fit the weights on one period, then evaluate on a *separate, later* period the model never saw. That's the standard I've held every test to, and it's the only standard a serious allocator applies to their own capital.

**And on speed:** I follow the point that Richard's math evaluates the matrix quickly. But speed and edge are independent. Computing a signal fast only helps if the signal predicts something and if we're faster than the competition. Fast evaluation of a signal with no predictive power just loses money more efficiently — and on raw latency we won't out-run co-located MEV firms regardless. So speed is a real engineering strength, but it isn't itself the edge.

**Here's what I'd propose, and I mean it as a genuine green light:** define the strategy precisely — the exact D1–D8 inputs, and the exact buy/sell rule — and I'll test it the right way: fit on one window, evaluate out-of-sample on another, costs included. If it holds up out-of-sample, I'm wrong, and we move forward with real conviction and real evidence to show any future partner. If we find the eight aren't right, we swap factors and test again. The only thing I'm holding firm on is the testing standard, not the idea.

If the strategy can be specified that precisely, we can have an answer in a day. If it can't be pinned down precisely enough to test that way, I think that tells us something too.

Let's define the inputs and the rule, and I'll run it.

— Jason
