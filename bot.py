import discord
import aiohttp
import asyncio
import json
import re
import os
from datetime import datetime, timezone

# ─── CONFIG ───────────────────────────────────────────────
DISCORD_TOKEN   = os.environ.get("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID      = int(os.environ.get("CHANNEL_ID", "0"))          # Stock channel ID
WEBHOOK_URL     = os.environ.get("WEBHOOK_URL", "")               # Your WordPress PHP endpoint
WEBHOOK_SECRET  = os.environ.get("WEBHOOK_SECRET", "changeme987654-") # Secret key for security
# ──────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ─── PARSE STOCK FROM MESSAGE ─────────────────────────────
def parse_stock_message(content: str, embeds: list) -> dict | None:
    """
    Parse GAG2 stock data from Discord message.
    Works with both plain text and embed messages.
    """
    stock = {
        "seeds": [],
        "gear": [],
        "eggs": [],
        "event": [],
        "weather": None,
        "raw_message": content[:500] if content else "",
        "updated_at": datetime.now(timezone.utc).isoformat()
    }

    found_data = False

    # ── Try parsing embeds first (most bots use embeds) ──
    for embed in embeds:
        title = (embed.title or "").lower()
        desc  = embed.description or ""
        
        # Weather info
        if any(w in title for w in ["weather", "mausam", "storm", "rain", "sun"]):
            stock["weather"] = embed.title
            found_data = True
            continue

        # Seed stock
        if any(w in title for w in ["seed", "shop", "stock", "seeds"]):
            items = _extract_items(desc)
            if items:
                stock["seeds"] = items
                found_data = True

        # Gear stock
        if any(w in title for w in ["gear", "tool", "equipment"]):
            items = _extract_items(desc)
            if items:
                stock["gear"] = items
                found_data = True

        # Egg stock
        if any(w in title for w in ["egg", "pet", "hatch"]):
            items = _extract_items(desc)
            if items:
                stock["eggs"] = items
                found_data = True

        # Event items
        if any(w in title for w in ["event", "special", "limited", "merchant"]):
            items = _extract_items(desc)
            if items:
                stock["event"] = items
                found_data = True

        # Fallback: if any embed has item-looking content
        if not found_data and desc:
            items = _extract_items(desc)
            if items:
                stock["seeds"] = items  # Default bucket
                found_data = True

        # Also check embed fields
        for field in (embed.fields or []):
            fname = (field.name or "").lower()
            fval  = field.value or ""
            if any(w in fname for w in ["seed", "shop"]):
                stock["seeds"].extend(_extract_items(fval))
                found_data = True
            elif any(w in fname for w in ["gear", "tool"]):
                stock["gear"].extend(_extract_items(fval))
                found_data = True
            elif any(w in fname for w in ["egg", "pet"]):
                stock["eggs"].extend(_extract_items(fval))
                found_data = True
            elif any(w in fname for w in ["event", "special"]):
                stock["event"].extend(_extract_items(fval))
                found_data = True
            elif any(w in fname for w in ["weather", "storm"]):
                stock["weather"] = field.value
                found_data = True

    # ── Fallback: parse plain text ──
    if not found_data and content:
        lines = content.split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Basic keyword detection in plain text
            lower = line.lower()
            if any(w in lower for w in ["seed:", "seeds:", "🌱"]):
                items = _extract_items(line)
                stock["seeds"].extend(items)
                found_data = True
            elif any(w in lower for w in ["gear:", "🔧", "tool:"]):
                items = _extract_items(line)
                stock["gear"].extend(items)
                found_data = True
            elif any(w in lower for w in ["egg:", "🥚", "pet:"]):
                items = _extract_items(line)
                stock["eggs"].extend(items)
                found_data = True
            elif any(w in lower for w in ["weather:", "🌤", "🌧", "⛈", "☀"]):
                stock["weather"] = line
                found_data = True

    if not found_data:
        return None  # Not a stock message, ignore

    return stock


def _extract_items(text: str) -> list:
    """Extract item names from a text block."""
    items = []
    if not text:
        return items

    # Remove Discord markdown
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'`+', '', text)
    text = re.sub(r'<:[^>]+>', '', text)  # Remove custom emojis

    lines = text.split("\n")
    for line in lines:
        line = line.strip()
        if not line or len(line) < 2:
            continue

        # Skip lines that look like headers/labels
        if line.endswith(":") and len(line) < 20:
            continue

        # Remove bullet points and common prefixes
        line = re.sub(r'^[-•*>➤➔→✦✧·]\s*', '', line)
        line = re.sub(r'^\d+[.)]\s*', '', line)  # numbered lists
        line = line.strip()

        if line and len(line) > 1:
            items.append(line)

    return items[:20]  # Max 20 items per category


# ─── SEND TO WORDPRESS ────────────────────────────────────
async def send_to_wordpress(session: aiohttp.ClientSession, stock: dict):
    payload = {
        "secret": WEBHOOK_SECRET,
        "stock": stock
    }
    try:
        async with session.post(
            WEBHOOK_URL,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status == 200:
                print(f"[✓] Stock updated at {stock['updated_at']}")
            else:
                text = await resp.text()
                print(f"[!] WordPress returned {resp.status}: {text[:100]}")
    except Exception as e:
        print(f"[✗] Failed to reach WordPress: {e}")


# ─── DISCORD EVENTS ───────────────────────────────────────
@client.event
async def on_ready():
    print(f"[✓] Bot online as {client.user}")
    print(f"[✓] Watching channel ID: {CHANNEL_ID}")

    # Fetch last 5 messages on startup to show current stock immediately
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        print("[*] Fetching recent messages on startup...")
        async with aiohttp.ClientSession() as session:
            async for msg in channel.history(limit=5):
                stock = parse_stock_message(
                    msg.content,
                    msg.embeds
                )
                if stock:
                    await send_to_wordpress(session, stock)
                    break  # Only need the latest valid one


@client.event
async def on_message(message: discord.Message):
    # Only watch the configured channel
    if message.channel.id != CHANNEL_ID:
        return

    # Ignore our own messages
    if message.author == client.user:
        return

    stock = parse_stock_message(message.content, message.embeds)
    if stock:
        async with aiohttp.ClientSession() as session:
            await send_to_wordpress(session, stock)


@client.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    """Some bots edit their stock messages — handle that too."""
    if after.channel.id != CHANNEL_ID:
        return
    stock = parse_stock_message(after.content, after.embeds)
    if stock:
        async with aiohttp.ClientSession() as session:
            await send_to_wordpress(session, stock)


# ─── RUN ──────────────────────────────────────────────────
if __name__ == "__main__":
    if not DISCORD_TOKEN or DISCORD_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("[✗] DISCORD_TOKEN not set!")
        exit(1)
    if CHANNEL_ID == 0:
        print("[✗] CHANNEL_ID not set!")
        exit(1)
    if not WEBHOOK_URL:
        print("[✗] WEBHOOK_URL not set!")
        exit(1)

    client.run(DISCORD_TOKEN)
