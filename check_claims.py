import requests, json, re, os, time
from datetime import datetime, timezone

URL = "https://map.ottersmp.com/tiles/minecraft_overworld/markers.json"
SNAPSHOT_FILE = "data/snapshot.json"
LOG_FILE = "data/changes.md"
WATCHLIST_FILE = "watchlist.txt"
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")
REMOVAL_THRESHOLD = 150  # if more than this many claims are removed, verify before alerting

os.makedirs("data", exist_ok=True)


def load_watchlist():
    if not os.path.exists(WATCHLIST_FILE):
        return set()
    with open(WATCHLIST_FILE) as f:
        names = {line.strip().lower() for line in f if line.strip() and not line.startswith("#")}
    print(f"Watchlist loaded: {names}")
    return names


def fetch_with_retry(url, retries=3, delay=10):
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
    raise RuntimeError(f"All {retries} attempts failed.")


def parse_claims(data):
    layer = next((l for l in data if l["id"] == "griefprevention"), None)
    if not layer:
        return []
    claims = []
    for m in layer["markers"]:
        owner_match = re.search(r'Claim Owner:.*?<span[^>]*>(.*?)</span>', m["popup"])
        trust_match = re.search(r'Trust:.*?<span[^>]*>(.*?)</span>', m["popup"])
        owner = owner_match.group(1) if owner_match else "Unknown"
        trusted = trust_match.group(1) if trust_match else ""
        p1, p2 = m["points"][0], m["points"][1]
        x1, x2 = min(p1["x"], p2["x"]), max(p1["x"], p2["x"])
        z1, z2 = min(p1["z"], p2["z"]), max(p1["z"], p2["z"])
        cx = (x1 + x2) // 2
        cz = (z1 + z2) // 2
        w = x2 - x1
        h = z2 - z1
        claims.append({
            "owner": owner,
            "trusted": trusted,
            "x1": x1, "z1": z1,
            "x2": x2, "z2": z2,
            "cx": cx, "cz": cz,
            "width": w, "height": h,
            "area": w * h,
        })
    return claims


def claim_key(c):
    return f"{c['owner']}|{c['x1']},{c['z1']},{c['x2']},{c['z2']}"


def map_link(cx, cz):
    return f"https://map.ottersmp.com/#minecraft_overworld:{cx}:{cz}:5"


def group_by_player(claims):
    grouped = {}
    for c in claims:
        grouped.setdefault(c["owner"], []).append(c)
    return dict(sorted(grouped.items(), key=lambda x: len(x[1]), reverse=True))


def post_discord(content):
    payload = {"content": content}
    print(f"Sending Discord message ({len(content)} chars)...")
    r = requests.post(DISCORD_WEBHOOK, json=payload)
    if r.status_code not in (200, 204):
        print(f"Discord webhook failed: {r.status_code} {r.text}")
    else:
        print("Discord message sent successfully.")


def verify_removals(snapshot, removed, retries=2, delay=30):
    """Re-fetch the map to confirm large removals are real and not a glitch."""
    snapshot_keys = {claim_key(c) for c in snapshot}
    removed_keys = {claim_key(c) for c in removed}

    for attempt in range(1, retries + 1):
        print(f"Large removal detected ({len(removed)} claims) — verifying (attempt {attempt}/{retries})...")
        print(f"Waiting {delay} seconds before re-fetch...")
        time.sleep(delay)

        try:
            data = fetch_with_retry(URL)
            fresh = parse_claims(data)
            fresh_keys = {claim_key(c) for c in fresh}

            # Check how many of the "removed" claims are still gone in the fresh fetch
            still_gone = removed_keys - fresh_keys
            came_back = removed_keys & fresh_keys

            print(f"  Verification {attempt}: {len(still_gone)} still gone, {len(came_back)} came back")

            if len(came_back) > len(still_gone):
                print(f"  Most claims came back — this looks like a map glitch. Skipping notification.")
                # Return the fresh claims as the new current so snapshot updates correctly
                return False, fresh
            else:
                print(f"  Claims still gone after re-fetch — looks real.")

        except Exception as e:
            print(f"  Verification fetch failed: {e} — assuming glitch, skipping notification.")
            return False, None

    print(f"All verifications confirm removals are real.")
    return True, None


def send_discord(removed, total, watchlist):
    if not DISCORD_WEBHOOK:
        print("No Discord webhook set, skipping notification.")
        return
    if not removed:
        post_discord(
            f"✅ **OtterSMP claim check** — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"No claims removed. Total claims: **{total}**"
        )
        return

    # --- Watchlist alert (separate urgent message) ---
    watched_removed = [c for c in removed if c["owner"].lower() in watchlist]
    if watched_removed:
        lines = [f"🚨 **WATCHLIST ALERT** — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"]
        by_player = group_by_player(watched_removed)
        for owner, claims in by_player.items():
            total_area = sum(c["area"] for c in claims)
            coords = ", ".join(f"`{c['cx']},{c['cz']}`" for c in claims[:3])
            if len(claims) > 3:
                coords += f" +{len(claims) - 3} more"
            lines.append(
                f"• **{owner}** lost {len(claims)} claim(s), {total_area:,} blocks total\n"
                f"  {coords}"
            )
        post_discord("\n".join(lines))

    # --- General removed claims message ---
    lines = [f"**OtterSMP claim changes** — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"]
    lines.append(f"Total claims: **{total}** | Removed: **{len(removed)}**\n")
    lines.append("🔴 **Removed claims:**")

    by_player = group_by_player(removed)
    shown = 0
    for owner, claims in by_player.items():
        if shown >= 15:
            lines.append("  *...and more players*")
            break
        total_area = sum(c["area"] for c in claims)
        coords = ", ".join(f"`{c['cx']},{c['cz']}`" for c in claims[:3])
        if len(claims) > 3:
            coords += f" +{len(claims) - 3} more"
        tag = " 👁️" if owner.lower() in watchlist else ""
        lines.append(
            f"• **{owner}**{tag} — {len(claims)} claim(s), {total_area:,} blocks total\n"
            f"  {coords}"
        )
        shown += 1

    post_discord("\n".join(lines))


def append_log(removed, added, now_str, total):
    lines = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f:
            lines = f.readlines()

    entry = [f"\n## {now_str}\n"]
    entry.append(f"Total claims: {total} | Removed: {len(removed)} | Added: {len(added)}\n\n")

    if removed:
        entry.append("### Removed\n")
        by_player = group_by_player(removed)
        for owner, claims in by_player.items():
            total_area = sum(c["area"] for c in claims)
            entry.append(f"**{owner}** — {len(claims)} claim(s), {total_area:,} blocks total\n")
            for c in claims:
                entry.append(
                    f"  - ({c['x1']},{c['z1']}) → ({c['x2']},{c['z2']}) "
                    f"center ({c['cx']},{c['cz']}) size {c['width']}×{c['height']}\n"
                )
    if added:
        entry.append("\n### Added\n")
        by_player = group_by_player(added)
        for owner, claims in by_player.items():
            total_area = sum(c["area"] for c in claims)
            entry.append(f"**{owner}** — {len(claims)} claim(s), {total_area:,} blocks total\n")
            for c in claims:
                entry.append(
                    f"  - ({c['x1']},{c['z1']}) → ({c['x2']},{c['z2']}) "
                    f"center ({c['cx']},{c['cz']}) size {c['width']}×{c['height']}\n"
                )
    if not removed and not added:
        entry.append("No changes detected.\n")

    with open(LOG_FILE, "w") as f:
        f.writelines(entry + lines)


def main():
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"Fetching claims at {now_str}...")

    watchlist = load_watchlist()
    data = fetch_with_retry(URL)

    current = parse_claims(data)
    current_keys = {claim_key(c) for c in current}
    print(f"Found {len(current)} current claims.")

    removed, added = [], []

    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE) as f:
            saved = json.load(f)

        snapshot = saved.get("claims", [])
        snapshot_time = saved.get("time", "unknown")

        if len(snapshot) < 100:
            print(f"Snapshot only has {len(snapshot)} claims — looks corrupt, resetting baseline.")
            snapshot = []

        if snapshot:
            snapshot_keys = {claim_key(c) for c in snapshot}
            removed = [c for c in snapshot if claim_key(c) not in current_keys]
            added   = [c for c in current  if claim_key(c) not in snapshot_keys]

            print(f"Baseline from {snapshot_time}: {len(snapshot)} claims")
            print(f"Changes — removed: {len(removed)}, added: {len(added)}")

            # If removals exceed threshold, verify before alerting
            if len(removed) > REMOVAL_THRESHOLD:
                confirmed, fresh_claims = verify_removals(snapshot, removed)
                if not confirmed:
                    print("Removals not confirmed — treating as map glitch, resetting baseline.")
                    # Use the fresh fetch as the new current if we got one
                    if fresh_claims:
                        current = fresh_claims
                    removed, added = [], []
                else:
                    print(f"Removals confirmed across multiple fetches — sending alert.")
                    for c in removed:
                        print(f"  REMOVED — {c['owner']} at ({c['cx']},{c['cz']})")
            else:
                for c in removed:
                    print(f"  REMOVED — {c['owner']} at ({c['cx']},{c['cz']})")
                for c in added:
                    print(f"  ADDED   — {c['owner']} at ({c['cx']},{c['cz']})")
    else:
        print("No snapshot found — saving initial baseline.")

    append_log(removed, added, now_str, len(current))
    send_discord(removed, len(current), watchlist)

    with open(SNAPSHOT_FILE, "w") as f:
        json.dump({"claims": current, "time": now_str}, f, indent=2)

    print("Snapshot updated.")


if __name__ == "__main__":
    main()
