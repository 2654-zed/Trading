"""Realized profit of an actual backrun, straight from its receipt.

We do NOT simulate a hypothetical backrun (that needs archive-node pool
reserves + a price model + optimal-sizing assumptions, all error-prone). We
measure what the REAL backrun that already landed actually netted: its ERC-20
Transfer logs show exactly what the searcher put in and took out, and the
receipt gives gas used × effective gas price.

  gross = net positive token delta to the searcher side ({tx.from, tx.to})
          in a settlement token (WETH / major stable), in USD
  gas   = gasUsed × effectiveGasPrice, in USD
  net_visible = gross − gas

HONEST LIMITS (stated, not hidden):
  * The BUILDER BRIBE is usually a direct ETH transfer to block.coinbase,
    which is an internal transfer invisible in receipt logs (needs a trace).
    So net_visible EXCLUDES the bribe ⇒ it OVERSTATES searcher take-home but
    correctly measures the TOTAL arb value on the table per backrun.
  * If a bot routes profit to a treasury that is neither tx.from nor tx.to,
    we miss it ⇒ gross can UNDERSTATE. The two biases oppose.
  * For WETH-settled arbs, gross and gas are BOTH in ETH, so the SIGN of
    net_visible is independent of the assumed ETH price; only the USD
    magnitude depends on it.
"""

from __future__ import annotations

from typing import Optional

# ERC-20 Transfer(address,address,uint256) event topic0
TRANSFER_TOPIC = ("0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a"
                  "4df523b3ef")

WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
# settlement token -> (decimals, is_eth_denominated)
TOKENS = {
    WETH: (18, True),                                            # WETH ~ ETH price
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": (6, False),    # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7": (6, False),    # USDT
    "0x6b175474e89094c44da98b954eedeac495271d0f": (18, False),   # DAI
}


def decode_transfers(receipt: Optional[dict]) -> list[tuple[str, str, str, int]]:
    """(token, from, to, value) for every ERC-20 Transfer log. ERC-721
    transfers (4 topics) are skipped — they carry no fungible value."""
    out = []
    if not receipt:
        return out
    for log in receipt.get("logs") or []:
        topics = log.get("topics") or []
        if len(topics) != 3 or (topics[0] or "").lower() != TRANSFER_TOPIC:
            continue
        try:
            frm = "0x" + topics[1][-40:]
            to = "0x" + topics[2][-40:]
            val = int(log.get("data") or "0x0", 16)
        except (ValueError, TypeError):
            continue
        addr = (log.get("address") or "").lower()
        if addr:
            out.append((addr, frm.lower(), to.lower(), val))
    return out


def token_delta(transfers, addrs: set[str], token: str) -> int:
    """Net wei of `token` flowing TO the address set (received − sent)."""
    net = 0
    for tok, frm, to, val in transfers:
        if tok != token:
            continue
        if to in addrs:
            net += val
        if frm in addrs:
            net -= val
    return net


def gas_cost_wei(receipt: Optional[dict]) -> int:
    if not receipt:
        return 0
    try:
        return int(receipt["gasUsed"], 16) * int(
            receipt.get("effectiveGasPrice") or "0x0", 16)
    except (ValueError, TypeError, KeyError):
        return 0


def realized_profit(receipt: Optional[dict], eth_price_usd: float) -> dict:
    """Realized PnL in USD for the searcher side ({from,to}), netted ACROSS
    settlement tokens (WETH + major stables). A cyclic arb that receives WETH
    and pays a stable nets to its true spread, not the gross WETH leg — taking
    the max single-token delta would mis-count those as huge fake profits.

    `net_token_usd` can be negative (a loss leg of a bundle, or profit taken in
    a token we don't track). LIMITATION: if the offsetting leg is an UNtracked
    token (a random altcoin), netting is incomplete — flag, don't trust those.
    """
    if not receipt:
        return {"profit_token": None, "net_token_usd": 0.0, "gas_usd": 0.0,
                "pnl_usd": 0.0}
    side = {(receipt.get("from") or "").lower(), (receipt.get("to") or "").lower()}
    side.discard("")
    transfers = decode_transfers(receipt)
    net_usd = 0.0
    best_token, best_usd = None, 0.0
    for tok, (dec, is_eth) in TOKENS.items():
        d = token_delta(transfers, side, tok)
        usd = (d / 10 ** dec) * (eth_price_usd if is_eth else 1.0)
        net_usd += usd                       # NET across tokens, not max
        if usd > best_usd:
            best_token, best_usd = tok, usd
    gas_usd = (gas_cost_wei(receipt) / 1e18) * eth_price_usd
    return {"profit_token": best_token, "net_token_usd": net_usd,
            "gas_usd": gas_usd, "pnl_usd": net_usd - gas_usd}


__all__ = ["decode_transfers", "token_delta", "gas_cost_wei", "realized_profit",
           "TRANSFER_TOPIC", "WETH", "TOKENS"]
