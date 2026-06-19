import discord
import aiohttp
import re
import os
from datetime import datetime, timezone

DISCORD_TOKEN  = os.environ.get("DISCORD_TOKEN", "")
CHANNEL_ID     = int(os.environ.get("CHANNEL_ID", "0"))
WEBHOOK_URL    = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "changeme987654-")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ── Rarity detection ──────────────────────────────────────
RARITIES = ["divine","prismatic","mythic","legendary","epic","rare","uncommon","common"]

def detect_rarity(text):
    t = text.lower()
    for r in RARITIES:
        if r in t:
            return r.capitalize()
    return "Common"

# ── Parse "5x - Item Name" format ─────────────────────────
def parse_item_string(s):
    """
    Input:  '5x - Rare Sprinkler'  OR  'Rare Sprinkler x5'  OR  'Sprinkler'
    Output: {"name": "Sprinkler", "quantity": 5, "rarity": "Rare"}
    """
    s = s.strip()
    
    # Skip Discord timestamps and empty
    if not s or "<t:" in s or "next stock" in s.lower():
        return None

    qty = 1
    name = s

    # Match "5x - Item" or "5x Item"
    m = re.match(r'^(\d+)[xX]\s*[-–]?\s*(.+)$', s)
    if m:
        qty = int(m.group(1))
        name = m.group(2).strip()
    else:
        # Match "Item x5" at end
        m2 = re.match(r'^(.+?)\s+[xX](\d+)$', s)
        if m2:
            name = m2.group(1).strip()
            qty = int(m2.group(2))

    rarity = detect_rarity(name)
    # Clean rarity word from name if present
    for r in RARITIES:
        name = re.sub(r'(?i)\b' + r + r'\b', '', name).strip()
    name = re.sub(r'\s+', ' ', name).strip(' -–')

    if not name:
        return None

    return {"name": name, "quantity": qty, "rarity": rarity}

# ── Parse full Discord message ─────────────────────────────
def parse_message(content, embeds):
    result = {
        "seeds":      [],
        "gear":       [],
        "eggs":       [],
        "event":      [],
        "weather":    None,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    found = False

    # ── Embed parsing ──
    for emb in embeds:
        title = (emb.title or "").lower()
        desc  = emb.description or ""
        all_text = desc

        # Check fields too
        for field in (emb.fields or []):
            fname = (field.name or "").lower()
            fval  = field.value or ""
            all_text += "\n" + fval

            bucket = None
            if any(w in fname for w in ["seed","shop","plant"]):   bucket = "seeds"
            elif any(w in fname for w in ["gear","tool","equip"]): bucket = "gear"
            elif any(w in fname for w in ["egg","pet","hatch"]):   bucket = "eggs"
            elif any(w in fname for w in ["event","special","merchant","limited"]): bucket = "event"
            elif any(w in fname for w in ["weather","storm","rain","sun","moon"]): 
                result["weather"] = field.value
                found = True
                continue

            if bucket:
                for line in fval.split("\n"):
                    item = parse_item_string(line)
                    if item:
                        result[bucket].append(item)
                        found = True

        # Bucket from embed title
        bucket = None
        if any(w in title for w in ["seed","shop","plant"]):   bucket = "seeds"
        elif any(w in title for w in ["gear","tool","equip"]): bucket = "gear"
        elif any(w in title for w in ["egg","pet","hatch"]):   bucket = "eggs"
        elif any(w in title for w in ["event","special","merchant"]): bucket = "event"
        elif any(w in title for w in ["weather","storm","rain","sun"]): 
            result["weather"] = emb.title
            found = True

        if bucket and desc:
            for line in desc.split("\n"):
                item = parse_item_string(line)
                if item:
                    result[bucket].append(item)
                    found = True

    # ── Plain text fallback ──
    if not found and content:
        current_bucket = "seeds"
        for line in content.split("\n"):
            line = line.strip()
            if not line: continue
            lower = line.lower()

            # Section headers
            if any(w in lower for w in ["seed","🌱"]):   current_bucket = "seeds";  continue
            if any(w in lower for w in ["gear","🔧"]):   current_bucket = "gear";   continue
            if any(w in lower for w in ["egg","🥚"]):    current_bucket = "eggs";   continue
            if any(w in lower for w in ["event","⭐"]): current_bucket = "event";  continue
            if any(w in lower for w in ["weather","🌤","⛈","☀","🌧"]):
                result["weather"] = line
                found = True
                continue

            item = parse_item_string(line)
            if item:
                result[current_bucket].append(item)
                found = True

    return result if found else None

# ── Send to WordPress ──────────────────────────────────────
async def push(session, stock):
    try:
        async with session.post(
            WEBHOOK_URL,
            json={"secret": WEBHOOK_SECRET, "stock": stock},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            if r.status == 200:
                print(f"[✓] Stock updated at {stock['updated_at']}")
            else:
                print(f"[!] WordPress returned {r.status}: {await r.text()}")
    except Exception as e:
        print(f"[✗] Push failed: {e}")

# ── Discord events ─────────────────────────────────────────
@client.event
async def on_ready():
    print(f"[✓] Bot online as {client.user}")
    print(f"[✓] Watching channel: {CHANNEL_ID}")
    ch = client.get_channel(CHANNEL_ID)
    if ch:
        async with aiohttp.ClientSession() as s:
            async for msg in ch.history(limit=10):
                stock = parse_message(msg.content, msg.embeds)
                if stock:
                    await push(s, stock)
                    break

@client.event
async def on_message(msg):
    if msg.channel.id != CHANNEL_ID or msg.author == client.user: return
    stock = parse_message(msg.content, msg.embeds)
    if stock:
        async with aiohttp.ClientSession() as s:
            await push(s, stock)

@client.event
async def on_message_edit(_, after):
    if after.channel.id != CHANNEL_ID: return
    stock = parse_message(after.content, after.embeds)
    if stock:
        async with aiohttp.ClientSession() as s:
            await push(s, stock)

client.run(DISCORD_TOKEN)
