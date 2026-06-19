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

RARITIES = ["divine","prismatic","mythic","legendary","epic","rare","uncommon","common"]

def clean_text(s):
    """Remove Discord custom emojis like <:name:123456> and <a:name:123456>"""
    s = re.sub(r'<a?:[^:]+:\d+>', '', s)   # custom emojis
    s = re.sub(r'<t:\d+:[^>]*>', '', s)     # timestamps
    s = re.sub(r'\*+', '', s)               # bold/italic markdown
    s = re.sub(r'`+', '', s)               # code blocks
    return s.strip()

def detect_rarity(text):
    t = text.lower()
    for r in RARITIES:
        if r in t:
            return r.capitalize()
    return "Common"

def parse_item_line(raw):
    """
    Parses lines like:
    '4x 🔦 - Flashbang'
    '<:Flashbang:151513> - Flashbang'  
    '3x 🥕 - Carrot'
    'Carrot x3'
    """
    s = clean_text(raw).strip()
    if not s or len(s) < 2:
        return None
    
    # Skip header/footer lines
    low = s.lower()
    if any(skip in low for skip in [
        'next stock', 'stock in', 'add me', 'grow a garden',
        'gear stock', 'seed stock', 'egg stock', '---', '==='
    ]):
        return None

    # Remove leading bullet/emoji chars
    s = re.sub(r'^[-•*➤→✦·]\s*', '', s).strip()

    qty = 1
    name = s

    # Pattern: "4x 🔦 - Flashbang" or "4x - Flashbang"
    m = re.match(r'^(\d+)[xX]\s*(?:\S+\s*)?[-–]\s*(.+)$', s)
    if m:
        qty = int(m.group(1))
        name = m.group(2).strip()
    else:
        # Pattern: "Flashbang x4" at end
        m2 = re.match(r'^(.+?)\s+[xX](\d+)$', s)
        if m2:
            name = m2.group(1).strip()
            qty = int(m2.group(2))
        else:
            # Pattern: "4x Flashbang" (no dash)
            m3 = re.match(r'^(\d+)[xX]\s+(.+)$', s)
            if m3:
                qty = int(m3.group(1))
                name = m3.group(2).strip()

    # Clean any remaining emoji unicode from name
    name = re.sub(r'[\U00010000-\U0010ffff]', '', name)  # unicode emojis
    name = re.sub(r'\s+', ' ', name).strip(' -–')

    if not name or len(name) < 2:
        return None

    rarity = detect_rarity(name)
    # Remove rarity word from name
    for r in RARITIES:
        name = re.sub(r'(?i)\b' + r + r'\b', '', name).strip()
    name = re.sub(r'\s+', ' ', name).strip(' -–')

    if not name:
        return None

    return {"name": name, "quantity": qty, "rarity": rarity}

def detect_bucket(text):
    """Detect if text is seeds/gear/eggs/event/weather section"""
    t = text.lower()
    if any(w in t for w in ["seed stock", "grow a garden stock", "🌱 stock", "seed shop"]):
        return "seeds"
    if any(w in t for w in ["gear stock", "tool stock", "⚙", "🔧"]):
        return "gear"
    if any(w in t for w in ["egg stock", "🥚"]):
        return "eggs"
    if any(w in t for w in ["event", "merchant", "special", "limited"]):
        return "event"
    if any(w in t for w in ["weather", "🌤", "⛈", "🌧", "☀", "🌙"]):
        return "weather"
    return None

def parse_message(content, embeds):
    result = {
        "seeds": [], "gear": [], "eggs": [],
        "event": [], "weather": None,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    found = False

    # ── Parse embeds ──────────────────────────────
    for emb in embeds:
        title = clean_text(emb.title or "")
        desc  = clean_text(emb.description or "")
        
        # Detect bucket from title
        bucket = detect_bucket(title)
        
        if bucket == "weather":
            result["weather"] = title
            found = True
            continue

        # Parse embed fields
        for field in (emb.fields or []):
            fname = clean_text(field.name or "")
            fval  = clean_text(field.value or "")
            fb = detect_bucket(fname) or bucket
            
            if fb == "weather":
                result["weather"] = fval
                found = True
                continue
            if fb and fb in result:
                for line in fval.split("\n"):
                    item = parse_item_line(line)
                    if item:
                        result[fb].append(item)
                        found = True

        # Parse description lines
        if bucket and bucket in result and desc:
            for line in desc.split("\n"):
                item = parse_item_line(line)
                if item:
                    result[bucket].append(item)
                    found = True

        # No bucket from title - try each line as bucket detection
        if not bucket and desc:
            current = "seeds"
            for line in desc.split("\n"):
                line = line.strip()
                if not line: continue
                b = detect_bucket(line)
                if b:
                    current = b
                    continue
                item = parse_item_line(line)
                if item and current in result:
                    result[current].append(item)
                    found = True

    # ── Parse plain text ──────────────────────────
    if content:
        content_clean = clean_text(content)
        current = None
        for line in content_clean.split("\n"):
            line = line.strip()
            if not line: continue
            
            b = detect_bucket(line)
            if b:
                current = b if b != "weather" else None
                if b == "weather":
                    result["weather"] = line
                    found = True
                continue
            
            if current and current in result:
                item = parse_item_line(line)
                if item:
                    result[current].append(item)
                    found = True

    return result if found else None

async def push(session, stock):
    try:
        async with session.post(
            WEBHOOK_URL,
            json={"secret": WEBHOOK_SECRET, "stock": stock},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            body = await r.text()
            if r.status == 200:
                total = sum(len(stock[k]) for k in ['seeds','gear','eggs','event'])
                print(f"[✓] Pushed {total} items at {stock['updated_at']}")
            else:
                print(f"[!] WordPress {r.status}: {body[:100]}")
    except Exception as e:
        print(f"[✗] Push error: {e}")

@client.event
async def on_ready():
    print(f"[✓] Bot online as {client.user}")
    print(f"[✓] Channel: {CHANNEL_ID}")
    ch = client.get_channel(CHANNEL_ID)
    if ch:
        print("[*] Loading recent messages...")
        async with aiohttp.ClientSession() as s:
            seeds_found = False
            gear_found  = False
            async for msg in ch.history(limit=20):
                stock = parse_message(msg.content, msg.embeds)
                if stock:
                    # Merge — collect seeds from one msg, gear from another
                    if stock['seeds'] and not seeds_found:
                        await push(s, stock)
                        seeds_found = True
                    elif stock['gear'] and not gear_found:
                        await push(s, stock)
                        gear_found = True
                if seeds_found and gear_found:
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
