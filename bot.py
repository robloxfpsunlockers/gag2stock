import discord
import aiohttp
import re
import os
from datetime import datetime, timezone

DISCORD_TOKEN         = os.environ.get("DISCORD_TOKEN", "")
CHANNEL_ID            = int(os.environ.get("CHANNEL_ID", "0"))
PREDICTION_CHANNEL_ID = int(os.environ.get("PREDICTION_CHANNEL_ID", "1520670972485828728"))
WEBHOOK_URL           = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_SECRET        = os.environ.get("WEBHOOK_SECRET", "changeme987654-")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

RARITIES = ["divine","prismatic","mythic","legendary","epic","rare","uncommon","common"]

def clean_text(s):
    s = re.sub(r'<a?:[^:]+:\d+>', '', s)
    s = re.sub(r'<t:\d+:[^>]*>', '', s)
    s = re.sub(r'\*+', '', s)
    s = re.sub(r'`+', '', s)
    return s.strip()

def detect_rarity(text):
    t = text.lower()
    for r in RARITIES:
        if r in t:
            return r.capitalize()
    return "Common"

def parse_item_line(raw):
    s = clean_text(raw).strip()
    if not s or len(s) < 2:
        return None
    low = s.lower()
    if any(skip in low for skip in [
        'next stock','stock in','add me','grow a garden',
        'gear stock','seed stock','egg stock','---','==='
    ]):
        return None
    s = re.sub(r'^[-•*➤→✦·]\s*', '', s).strip()
    qty = 1
    name = s
    m = re.match(r'^(\d+)[xX]\s*(?:\S+\s*)?[-–]\s*(.+)$', s)
    if m:
        qty = int(m.group(1))
        name = m.group(2).strip()
    else:
        m2 = re.match(r'^(.+?)\s+[xX](\d+)$', s)
        if m2:
            name = m2.group(1).strip()
            qty = int(m2.group(2))
        else:
            m3 = re.match(r'^(\d+)[xX]\s+(.+)$', s)
            if m3:
                qty = int(m3.group(1))
                name = m3.group(2).strip()
    name = re.sub(r'[\U00010000-\U0010ffff]', '', name)
    name = re.sub(r'\s+', ' ', name).strip(' -–')
    if not name or len(name) < 2:
        return None
    rarity = detect_rarity(name)
    for r in RARITIES:
        name = re.sub(r'(?i)\b' + r + r'\b', '', name).strip()
    name = re.sub(r'\s+', ' ', name).strip(' -–')
    if not name:
        return None
    return {"name": name, "quantity": qty, "rarity": rarity}

def detect_bucket(text):
    t = text.lower()
    if any(w in t for w in ["seed stock","grow a garden stock","🌱 stock","seed shop"]): return "seeds"
    if any(w in t for w in ["gear stock","tool stock","⚙","🔧"]): return "gear"
    if any(w in t for w in ["egg stock","🥚"]): return "eggs"
    if any(w in t for w in ["event","merchant","special","limited"]): return "event"
    if any(w in t for w in ["weather","🌤","⛈","🌧","☀","🌙"]): return "weather"
    return None

def parse_stock_message(content, embeds):
    result = {
        "seeds":[], "gear":[], "eggs":[],
        "event":[], "weather":None,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    found = False
    for emb in embeds:
        title  = clean_text(emb.title or "")
        desc   = clean_text(emb.description or "")
        bucket = detect_bucket(title)
        if bucket == "weather":
            result["weather"] = title
            found = True
            continue
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
        if bucket and bucket in result and desc:
            for line in desc.split("\n"):
                item = parse_item_line(line)
                if item:
                    result[bucket].append(item)
                    found = True
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

# ── Parse prediction message ───────────────────────────────
def parse_prediction_message(content, embeds):
    predictions = []
    timestamp = datetime.now(timezone.utc).isoformat()

    # Parse embeds
    for emb in embeds:
        title = clean_text(emb.title or "")
        desc  = clean_text(emb.description or "")

        pred = {}
        if title:
            pred["title"] = title
        if desc:
            pred["description"] = desc

        # Parse fields
        fields_data = []
        for field in (emb.fields or []):
            fname = clean_text(field.name or "")
            fval  = clean_text(field.value or "")
            if fname and fval:
                fields_data.append({"name": fname, "value": fval})

        if fields_data:
            pred["fields"] = fields_data

        # Embed color as hex
        if emb.color:
            pred["color"] = f"#{emb.color.value:06x}"

        if pred:
            predictions.append(pred)

    # Plain text fallback
    if not predictions and content:
        text = clean_text(content)
        if text:
            # Split into paragraphs
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            if lines:
                predictions.append({
                    "title": lines[0] if len(lines) > 0 else "Prediction",
                    "description": "\n".join(lines[1:]) if len(lines) > 1 else lines[0]
                })

    if not predictions:
        return None

    return {
        "predictions": predictions,
        "updated_at": timestamp
    }

# ── Push stock to WordPress ────────────────────────────────
async def push_stock(session, stock):
    try:
        async with session.post(
            WEBHOOK_URL,
            json={"secret": WEBHOOK_SECRET, "stock": stock},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            body = await r.text()
            if r.status == 200:
                total = sum(len(stock[k]) for k in ['seeds','gear','eggs','event'])
                print(f"[✓] Stock pushed: {total} items")
            else:
                print(f"[!] Stock push failed {r.status}: {body[:100]}")
    except Exception as e:
        print(f"[✗] Stock push error: {e}")

# ── Push prediction to WordPress ───────────────────────────
async def push_prediction(session, data):
    try:
        async with session.post(
            WEBHOOK_URL,
            json={"secret": WEBHOOK_SECRET, "prediction": data},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            body = await r.text()
            if r.status == 200:
                print(f"[✓] Prediction pushed: {len(data['predictions'])} items")
            else:
                print(f"[!] Prediction push failed {r.status}: {body[:100]}")
    except Exception as e:
        print(f"[✗] Prediction push error: {e}")

# ── Discord events ─────────────────────────────────────────
@client.event
async def on_ready():
    print(f"[✓] Bot online as {client.user}")
    print(f"[✓] Stock channel:      {CHANNEL_ID}")
    print(f"[✓] Prediction channel: {PREDICTION_CHANNEL_ID}")

    async with aiohttp.ClientSession() as s:
        # Load recent stock
        ch = client.get_channel(CHANNEL_ID)
        if ch:
            print("[*] Loading recent stock messages...")
            seeds_done = gear_done = False
            async for msg in ch.history(limit=20):
                stock = parse_stock_message(msg.content, msg.embeds)
                if stock:
                    if stock['seeds'] and not seeds_done:
                        await push_stock(s, stock)
                        seeds_done = True
                    elif stock['gear'] and not gear_done:
                        await push_stock(s, stock)
                        gear_done = True
                if seeds_done and gear_done:
                    break

        # Load recent prediction
        pch = client.get_channel(PREDICTION_CHANNEL_ID)
        if pch:
            print("[*] Loading recent prediction messages...")
            async for msg in pch.history(limit=5):
                pred = parse_prediction_message(msg.content, msg.embeds)
                if pred:
                    await push_prediction(s, pred)
                    break

@client.event
async def on_message(msg):
    if msg.author == client.user:
        return

    async with aiohttp.ClientSession() as s:
        if msg.channel.id == CHANNEL_ID:
            stock = parse_stock_message(msg.content, msg.embeds)
            if stock:
                await push_stock(s, stock)

        elif msg.channel.id == PREDICTION_CHANNEL_ID:
            pred = parse_prediction_message(msg.content, msg.embeds)
            if pred:
                await push_prediction(s, pred)

@client.event
async def on_message_edit(_, after):
    if after.author == client.user:
        return

    async with aiohttp.ClientSession() as s:
        if after.channel.id == CHANNEL_ID:
            stock = parse_stock_message(after.content, after.embeds)
            if stock:
                await push_stock(s, stock)

        elif after.channel.id == PREDICTION_CHANNEL_ID:
            pred = parse_prediction_message(after.content, after.embeds)
            if pred:
                await push_prediction(s, pred)

client.run(DISCORD_TOKEN)
