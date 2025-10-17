#!/usr/bin/env python3
"""
slot_with_spinner.py

Sends POST /api/slot every `interval` seconds.
Before each slot it GETs /api/challenge and uses the returned token & id in headers.
If a slot returns spinnerToken (or jackpot contains it), automatically triggers POST /api/spinner
when threeMatchReward.amount >= 100 (or if --spinner is used).

Usage:
  python3 slot_with_spinner.py --wallet <WALLET> [--interval 2.0] [--spinner --symbol "🐯" --spinner-token TOKEN]
"""

import requests
import time
import argparse
import signal
import sys
import uuid
from datetime import datetime, timezone

STOP = False

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def handler(sig, frame):
    global STOP
    STOP = True
    print("\n" + now_iso(), "Graceful shutdown requested (SIGINT). Stopping after current request...")

signal.signal(signal.SIGINT, handler)

SLOT_URL = "https://slots.mewccrypto.com/api/slot"
SPINNER_URL = "https://slots.mewccrypto.com/api/spinner"
CHALLENGE_URL = "https://slots.mewccrypto.com/api/challenge"
JACKPOT_URL = "https://slots.mewccrypto.com/api/jackpot"

def base_headers():
    return {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://slots.mewccrypto.com",
        "Referer": "https://slots.mewccrypto.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        # other Chromium client hints can be added if needed
    }

def print_slot_win(data):
    """
    Returns:
      - True for major win (prize > 0)
      - integer amount for minor reward amount (or 0 if none)
    """
    prize = data.get("prize", 0)
    txid = data.get("txid")
    board = data.get("board")
    claimed = data.get("claimed")

    if prize and prize > 0:
        print(
            f"\n🎉 MAJOR WIN! {now_iso()}\n"
            f"Prize={prize}\nTXID={txid}\nClaimed={claimed}\nBoard:\n"
        )
        if board:
            for row in board:
                print(" ".join(row))
        print()
        return True

    # minor reward (e.g., tokens)
    minor = data.get("threeMatchReward") or data.get("minorReward")
    if minor and isinstance(minor, dict):
        amt = minor.get("amount", 0)
        symbol = minor.get("symbol")
        if amt and amt > 0:
            print(
                f"\n✨ MINOR REWARD! {now_iso()}\n"
                f"{amt} {symbol} (minorReward)\nClaimed={claimed}\nBoard:\n"
            )
            if board:
                for row in board:
                    print(" ".join(row))
            print()
            return int(amt)
    return 0

def print_spinner_result(spin_res):
    if not spin_res:
        print("Nothing")
    prize_type = spin_res.get("prizeType")
    amount = spin_res.get("amount", 0)
    is_jackpot = bool(spin_res.get("isJackpot"))
    txid = spin_res.get("txid")
    symbol = spin_res.get("symbol")

    if (prize_type and prize_type != "none") or (amount and amount > 0) or is_jackpot:
        lines = [
            f"\n🎡 SPINNER RESULT! {now_iso()}",
            f"symbol={symbol}",
            f"prizeType={prize_type}",
            f"amount={amount}",
            f"isJackpot={is_jackpot}",
            f"txid={txid}",
        ]
        print("\n".join(lines))
        print()

def get_challenge(session, headers, timeout=10):
    try:
        r = session.get(CHALLENGE_URL, headers=headers, timeout=timeout)
        if r.status_code == 200:
            j = r.json()
            cid = j.get("challengeId")
            token = j.get("token")
            if cid and token:
                return cid, token
            else:
                print(now_iso(), "Challenge response missing fields:", j)
                return None, None
        else:
            print(now_iso(), f"Challenge request returned {r.status_code}: {r.text}")
            return None, None
    except Exception as e:
        print(now_iso(), "Challenge request failed:", e)
        return None, None

def get_jackpot(session, headers, timeout=8):
    """Try GET /api/jackpot to obtain a spinnerToken if present."""
    try:
        r = session.get(JACKPOT_URL, headers=headers, timeout=timeout)
        if r.status_code == 200:
            try:
                j = r.json()
            except Exception:
                print(now_iso(), "Failed to parse jackpot JSON")
                return None
            # some backends put spinnerToken directly — check common keys
            token = j.get("spinnerToken") or j.get("token")
            if token:
                return token
            # else nothing useful
            return None
        else:
            return None
    except Exception:
        return None

def handle_spinner(session, wallet, symbol, spinner_token, slot_headers):
    """
    Call POST /api/spinner using a spinner_token that's just been returned
    by the slot response (preferred), or provided by caller (fallback).
    """
    print("Running Spinner")
    if not spinner_token:
        print(now_iso(), "No spinner token available; skipping spinner.")
        return

    payload = {"wallet": wallet, "symbol": symbol, "token": spinner_token}
    try:
        sres = session.post(SPINNER_URL, json=payload, headers=slot_headers, timeout=15)
        print(sres)
        if sres.status_code == 200:
            try:
                sdata = sres.json()
            except Exception:
                print(now_iso(), "Failed to parse spinner JSON response")
                sdata = None
            if sdata:
                print_spinner_result(sdata)
        else:
            print(now_iso(), "Spinner error:", sres.status_code, sres.text)
    except Exception as e:
        print(now_iso(), "Spinner request failed:", e)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", required=True, help="Wallet address to use")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between slot requests")
    parser.add_argument("--spinner", action="store_true", help="Force POST /api/spinner after each slot (tries to obtain token)")
    parser.add_argument("--symbol", help="Symbol for spinner (e.g., 🐯)")
    parser.add_argument("--spinner-token", help="Optional: spinner token if you already have one (CLI fallback)")
    args = parser.parse_args()

    wallet = args.wallet
    interval = float(args.interval)
    do_spinner_flag = bool(args.spinner)
    spinner_symbol = args.symbol or "🐯"
    spinner_token_cli = args.spinner_token  # optional fallback token

    session = requests.Session()
    headers = base_headers()

    print(now_iso(), f"Starting slot auto-player | wallet={wallet} | interval={interval}s | spinner_flag={do_spinner_flag}")

    global STOP
    while not STOP:
        try:
            # 1) obtain challenge
            cid, token = get_challenge(session, headers)
            if not cid or not token:
                fallback = str(uuid.uuid4())
                print(now_iso(), "Warning: failed to obtain server challenge — using fallback UUID action token:", fallback)
                token = fallback
                cid = None

            # 2) build per-request headers for slot and spinner
            slot_headers = dict(headers)
            slot_headers["X-Client-Version"] = "v1.0.6"
            slot_headers["X-Action-Token"] = token
            if cid:
                slot_headers["X-Challenge-Id"] = cid

            # 3) POST /api/slot
            start = time.time()
            res = session.post(SLOT_URL, json={"wallet": wallet}, headers=slot_headers, timeout=15)
            elapsed_ms = int((time.time() - start) * 1000)

            if res.status_code != 200:
                print(now_iso(), f"Slot error ({res.status_code}): {res.text}")
                # Sleep and continue (respect backoff)
            else:
                try:
                    data = res.json()
                except Exception:
                    data = None
                    print(now_iso(), "Failed to parse slot JSON response")
                if data:
                    # print and get minor reward amount if any
                    reward_amount = print_slot_win(data)
                    # get dynamic symbol from slot
                    three_match = data.get("threeMatchReward") or data.get("minorReward")
                    symbol_to_use = three_match.get("symbol") if three_match else spinner_symbol

                    # Preferred spinner token: the slot response's spinnerToken
                    spinner_token_from_slot = data.get("spinnerToken") or data.get("spinner_token") or None

                    unlock = bool(data.get("unlockSpinner") or data.get("unlock_spinner"))
                    threshold = 100

                    # If rewarded enough, spin
                    if (reward_amount and reward_amount >= threshold) or do_spinner_flag:
                        token_to_use = spinner_token_from_slot or spinner_token_cli
                        if not token_to_use:
                            token_to_use = get_jackpot(session, headers)
                            if token_to_use:
                                print(now_iso(), "Obtained spinner token from jackpot endpoint.")
                        if token_to_use:
                            print(now_iso(), f"Triggering spinner with symbol '{symbol_to_use}' (reward={reward_amount})")
                            handle_spinner(session, wallet, symbol_to_use, token_to_use, slot_headers)

                            # --- end slot flow ---

        except Exception as e:
            print(now_iso(), "Request failed:", e)

        # wait between iterations, break early if STOP set
        slept = 0.0
        while slept < interval and not STOP:
            time.sleep(min(0.5, interval - slept))
            slept += min(0.5, interval - slept)

    print(now_iso(), "Stopped.")

if __name__ == "__main__":
    main()
