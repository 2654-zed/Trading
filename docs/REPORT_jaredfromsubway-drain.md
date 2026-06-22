# Report — jaredfromsubway.eth MEV bot drained (~$7.5M)

> Dated **2026-06-20** (the drain happened **today** — so no formal security-firm
> post-mortem exists yet; treat the exact bytecode-level bug as unconfirmed).
> Researched + adversarially verified across on-chain data + named sources.
> The hook (per Jason): the attacker used the **adversarial / game-theory perspective
> Layer 3 was built around — targeting the *predator bots themselves*, not users.**

## TL;DR
The most notorious Ethereum sandwich/front-running MEV bot, **jaredfromsubway.eth**,
was **drained of ~4,400+ ETH (~$7.5–7.6M** in ETH/USDC/USDT) on **2026-06-20** in a
**"counter-MEV honeypot attack"** (Blockaid CTO Raz Niv, on record). The attacker
deployed **66 fake token contracts** mimicking WETH/USDC/USDT with fake
profitable-looking pools; the bot's **own automated, generalized profit-seeking logic**
took the bait and approved attacker-controlled helper contracts to spend its real
funds; the attacker then called all 66 "backdoors" and swept everything. **The
predator became the prey** — and it's the latest in a clean lineage of "hunt the
hunters" attacks (Salmonella 2021, 0xbadc0de 2022).

## Confirmed facts
| | |
|---|---|
| **Date** | 2026-06-20 |
| **Victim** | jaredfromsubway.eth — a top Ethereum sandwich/MEV bot (historically ~$34M revenue, >11k trades on peak days) |
| **Amount** | ~4,400+ ETH ≈ **$7.5–7.6M** (ETH + USDC + USDT). On-chain: four ETH outflows totaling **exactly 4,423 ETH** (1,423 + 1,000×3), preceded by a 4,424-ETH WETH withdraw. Largest single transfer **1,423 ETH (~$2.46M)**. |
| **Drained account (on-chain flows)** | `0x3e37f4A10d771Ba9dE44b6d301410b1BEdeA65d0` — an **EIP-7702-delegated smart account** (active since 2026-06-07) |
| **Canonical bot contracts** | `0x6b75d8AF000000e20B7a7DDf000Ba900b4009A80` (original, OLI-labeled "jaredfromsubway: MEV Bot", **unverified** source) and `0x1f2f10d1c40777ae1da742455c65828ff36df387` ("Jared 2.0") |
| **Cash-out** | funds routed toward **Tornado Cash** |
| **Framing** | Blockaid CTO Raz Niv: *"a counter-MEV honeypot attack… it specifically targeted the automated, trust-minimized decision-making logic that MEV bots utilize."* |

## The mechanism (confirmed shape; exact primitive not yet)
1. Attacker deploys **66 counterfeit tokens** impersonating WETH/USDC/USDT, paired with
   **fraudulent pools engineered to look like profitable trades**.
2. jaredfromsubway's bot — which **indiscriminately** front-runs/sandwiches anything that
   scores as profitable — **takes the bait**, and in doing so its automated logic
   **grants spend approvals to attacker-controlled "helper" contracts**.
3. Attacker **calls all 66 backdoors** and sweeps the real ETH/USDC/USDT.

The exploit isn't a conventional code bug so much as **the weaponization of the bot's
defining trait**: fully automated, generalized, trust-minimized opportunism. The
attacker manufactured "opportunities" the bot was built to seize.

## Confirmed vs. speculative (intellectual honesty)
- **CONFIRMED:** the drain, the ~$7.5–7.6M / 4,423-ETH figure, the 66-fake-token honeypot
  structure, the counter-MEV framing, the on-chain flows, the bot identity/labels.
- **SPECULATIVE / REFUTED:** the precise **"dangling-approval"** primitive (leftover
  uncleared allowance drained in a follow-on tx) was an early analyst theory
  (SpecterAnalyst, "suspected") and was **rejected under verification** — *how* the bot
  was led to approve the helpers, at the bytecode level, is **not yet established**.
- **ATTRIBUTION caveat:** the drained EIP-7702 account is **not itself name-tagged**
  "jaredfromsubway" on Etherscan; the link rests on **press/analyst attribution of the
  flows**, not an explorer label. Attacker identity unknown; the 66 fake-token / helper
  addresses weren't enumerated in available sources.
- **No formal post-mortem yet** (it happened today): only Blockaid's CTO quote + journalism
  (Cointelegraph, CryptoAdventure) + on-chain data. A rekt.news/BlockSec/Dedaub teardown
  would move the exact bug from "suspected" to confirmed.

## Lineage — "hunting the hunters" is a documented class
| Year | Attack | What it did |
|---|---|---|
| **2021** | **Salmonella** (Nathan Worsley / Defi-Cartel) | Poison token: transfers credit non-owners only **10%** of the amount while **emitting a full-amount `Transfer` event** → sandwich bots misjudge what they received → forced losing trade. ~**$250K** (130 ETH), caught Ethermine. Worsley: *"turns the tables on the exploiters… this game is highly adversarial and we play for keeps."* |
| **2022** | **0xbadc0de** MEV-bot drain | ~**1,101 WETH (~$1.46M)** via **missing access-control** in the bot's *own* dYdX flash-loan callback + auto-approve-when-allowance-zero logic. (A *contract-bug* class — distinct from the honeypot bait.) |
| **2023** | **Zellic** "Your Sandwich Is My Lunch" | Demonstrated draining a gas-optimized assembly bot via a **calldata-controlled jump gadget** bypassing auth → arbitrary-call primitive. **PoC only**, different bot. |
| **2023** | **Flashbots relay** incident | Malicious proposer drained **~$20–25M** from ~5 sandwich bots via a **mev-boost relay flaw** (leaked block content on an invalid header → backran private sandwiches). Infra exploit, different bots. |

*(0xbadc0de / Zellic / Flashbots target* other *bots — lineage, not the same event.)*

## The Layer 3 connection — why this is *our* thesis, validated by someone else
You called it: the drainer is using **the adversarial / behavioral-intelligence
perspective Layer 3 was reaching for** — and it's the perspective that actually *works*
in this arena. The throughline:

- **You can't beat the predator bots at their own game** (speed/latency in the builder
  auction) — *we measured that, and it's structurally unwinnable from our position.*
- **But the predators are a predictable, exploitable population.** Their edge — fully
  automated, generalized opportunism — is also their **attack surface**: a decision
  function you can *model and bait*. The drainer didn't out-race jaredfromsubway; it
  **out-thought** it, by understanding the bot's behavior and manufacturing inputs it
  was built to act on.
- That is precisely the **"shape ≠ intent → model the actor's intent/behavior"** idea at
  the core of Layer 3: the durable edge isn't in the price tape or the latency race, it's
  in **modeling other agents' behavior and assumptions** — here, turning the apex
  predators into prey.

**Honest framing for us:** this *validates the adversarial-perspective thesis* without
being a strategy we should run. Deploying counter-MEV honeypots is its own specialized,
capital-/skill-intensive, **legally grey** game (Zellic refused to execute their own PoC
on mainnet for exactly that reason; draining a bot, even a malicious one, is "attacking
smart contracts without permission"). The takeaway is **directional, not operational**:
the recurring lesson across our work — *the edge is in understanding the other actors,
not in out-running them* — just got a $7.5M real-world exhibit.

---

### Sources
Incident: [Cointelegraph](https://cointelegraph.com/news/notorious-sandwich-attack-bot-jaredfromsubwayeth-exploited-for-75m) · [CryptoAdventure](https://cryptoadventure.com/jaredfromsubway-mev-bot-contract-hit-in-suspected-7-6m-ethereum-drain/) · on-chain `0x3e37f4A1…` / `0x6b75d8AF…`.
Lineage: [Salmonella repo](https://github.com/Defi-Cartel/salmonella) · [CoinDesk 2021](https://www.coindesk.com/tech/2021/03/22/bad-sandwich-defi-trader-poisons-front-running-miners-for-250k-profit) · [Immunefi 0xbadc0de](https://medium.com/immunefi/0xbadc0de-mev-bot-hack-analysis-30b9031ff0ba) · [Zellic](https://www.zellic.io/blog/your-sandwich-is-my-lunch-how-to-drain-mev-contracts-v2/) · [BlockSec / Flashbots relay](https://blocksec.com/blog/harvesting-mev-bots-by-exploiting-vulnerabilities-in-flashbots-relay).
*Confidence: incident facts high (on-chain + named-source corroboration) but pre-post-mortem; exact bug "suspected"; lineage high (primary sources).*
