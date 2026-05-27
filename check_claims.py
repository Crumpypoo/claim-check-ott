import requests, json, re, os, time
from datetime import datetime, timezone

URL = "https://map.ottersmp.com/tiles/minecraft_overworld/markers.json"
SNAPSHOT_FILE = "data/snapshot.json"
LOG_FILE = "data/changes.md"
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

os.makedirs("data", exist_ok=True)


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


def send_discord(removed, added, total):
    if not DISCORD_WEBHOOK:
        return
    if not removed and not added:
        return

    lines = [f"**OtterSMP claim changes** — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"]
    lines.append(f"Total claims: **{total}** | Removed: **{len(removed)}** | Added: **{len(added)}**\n")

    if removed:
        lines.append("🔴 **Removed claims:**")
        by_player = group_by_player(removed)
        shown = 0
        for owner, claims in by_player.items():
            if shown >= 15:
                lines.append(f"  *...and more players*")
                break
            total_area = sum(c["area"] for c in claims)
            coords = ", ".join(f"`{c['cx']},{c['cz']}`" for c in claims[:3])
            if len(claims) > 3:
                coords += f" +{len(claims) - 3} more"
            lines.append(
                f"• **{owner}** — {len(claims)} claim(s), {total_area:,} blocks total\n"
                f"  {coords}"
            )
            shown += 1

    
    payload = {"content": "\n".join(lines)}
    r = requests.post(DISCORD_WEBHOOK, json=payload)
    if r.status_code not in (200, 204):
        print(f"Discord webhook failed: {r.status_code} {r.text}")
    else:
        print("Discord notification sent.")


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

    if not removed:
        entry.append("No changes detected.\n")

    with open(LOG_FILE, "w") as f:
        f.writelines(entry + lines)


def main():
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"Fetching claims at {now_str}...")

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

            if len(removed) > len(snapshot) * 0.5:
                print(f"WARNING: {len(removed)}/{len(snapshot)} claims appear removed — "
                      f"this seems wrong, skipping notification and resetting baseline.")
                removed, added = [], []
            else:
                print(f"Baseline from {snapshot_time}: {len(snapshot)} claims")
                print(f"Changes — removed: {len(removed)}, added: {len(added)}")
                for c in removed:
                    print(f"  REMOVED — {c['owner']} at ({c['cx']},{c['cz']})")
                for c in added:
                    print(f"  ADDED   — {c['owner']} at ({c['cx']},{c['cz']})")
    else:
        print("No snapshot found — saving initial baseline.")

    append_log(removed, added, now_str, len(current))
    send_discord(removed, added, len(current))

    with open(SNAPSHOT_FILE, "w") as f:
        json.dump({"claims": current, "time": now_str}, f, indent=2)

    print("Snapshot updated.")


if __name__ == "__main__":
    main()
