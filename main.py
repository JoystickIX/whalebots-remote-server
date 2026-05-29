# =====================================
# IMPORTS
# =====================================

import discord
from discord.ext import commands, tasks
from fastapi import FastAPI, UploadFile, File
from pydantic import BaseModel
import threading
import uvicorn
import os
import json
import aiohttp
import datetime
import secrets
import tempfile
import asyncio
from pymongo import MongoClient

# =====================================
# ENV
# =====================================

TOKEN      = os.getenv("DISCORD_TOKEN")
SERVER_URL = os.getenv("SERVER_URL", "https://whalebots-remote-server.onrender.com")
MONGO_URI  = os.getenv("MONGO_URI")

OWNER_IDS = {316613385485680650, 641191095258185728}

# =====================================
# AUTO UPDATE
# =====================================

LATEST_VERSION = "1.0.2"

EXE_DOWNLOAD_LINK = (
    "https://github.com/JoystickIX/whalebots-remote-server/releases/download/V1.0.2/WhaleBotsRemote.exe"
)


# =====================================
# DATABASE
# =====================================

_mongo_client = MongoClient(MONGO_URI)
_db           = _mongo_client["whalebots"]
_licenses_col  = _db["licenses"]
_links_col     = _db["links"]
_paircodes_col = _db["pair_codes"]

# =====================================
# LOAD / SAVE
# =====================================

def load_links():
    result = {}
    for doc in _links_col.find():
        user_id = doc["_id"]
        result[user_id] = {
            "client_id":  doc["client_id"],
            "channel_id": doc["channel_id"]
        }
    return result

def save_links(data):
    for user_id, entry in data.items():
        _links_col.update_one(
            {"_id": user_id},
            {"$set": {
                "client_id":  entry["client_id"],
                "channel_id": entry["channel_id"]
            }},
            upsert=True
        )

def load_licenses():
    result = {}
    for doc in _licenses_col.find():
        key = doc["_id"]
        result[key] = {k: v for k, v in doc.items() if k != "_id"}
    return result

def save_licenses(data):
    for key, entry in data.items():
        _licenses_col.update_one(
            {"_id": key},
            {"$set": entry},
            upsert=True
        )

# =====================================
# STORAGE
# =====================================

commands_queue = {}
status_queue   = {}
online_clients = {}
image_queue    = {}

# =====================================
# DISCORD SETTINGS
# =====================================

intents = discord.Intents.default()

intents.message_content = True
intents.guilds          = True
intents.members         = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)

# =====================================
# FASTAPI
# =====================================

app = FastAPI()

# =====================================
# MODELS
# =====================================

class RegisterBody(BaseModel):
    client_id: str
    pair_code: str

class StatusBody(BaseModel):
    message: str

class LicenseValidateBody(BaseModel):
    client_id: str

class LicenseKeyBody(BaseModel):
    key: str

# =====================================
# HOME
# =====================================

@app.get("/")
def home():
    return {"status": "WhaleBots Server Online"}

# =====================================
# VERSION API
# =====================================

@app.get("/version")
def version():
    return {
        "version":  LATEST_VERSION,
        "download": EXE_DOWNLOAD_LINK
    }

# =====================================
# REGISTER
# =====================================

@app.post("/register")
def register(body: RegisterBody):
    if not body.client_id or not body.pair_code:
        return {"success": False}

    online_clients[body.pair_code] = body.client_id

    print(f"REGISTERED: {body.client_id} ({body.pair_code})")

    return {"success": True}

# =====================================
# PAIR CODE
# =====================================

@app.get("/pair_code/{client_id}")
def get_pair_code(client_id: str):
    import random
    doc = _paircodes_col.find_one({"_id": client_id})
    if doc:
        return {"pair_code": doc["pair_code"]}
    code = str(random.randint(100000, 999999))
    _paircodes_col.insert_one({"_id": client_id, "pair_code": code})
    return {"pair_code": code}

# =====================================
# COMMAND
# =====================================

@app.get("/command/{client_id}")
def get_command(client_id: str):
    command = commands_queue.get(client_id)

    if not command:
        return {"command": None}

    commands_queue[client_id] = None

    return {"command": command}

# =====================================
# STATUS
# =====================================

@app.post("/status/{client_id}")
def receive_status(client_id: str, body: StatusBody):
    status_queue[client_id] = body.message
    return {"success": True}

@app.get("/status/{client_id}")
def get_status(client_id: str):
    message = status_queue.get(client_id)

    if not message:
        return {"message": None}

    status_queue[client_id] = None

    return {"message": message}

# =====================================
# IMAGE UPLOAD
# =====================================

@app.post("/upload/{client_id}")
async def upload_image(
    client_id: str,
    file: UploadFile = File(...)
):
    content = await file.read()
    image_queue[client_id] = content
    return {"success": True}

# =====================================
# LICENSE VALIDATION
# =====================================

@app.post("/license/validate")
def validate_license(body: LicenseValidateBody):
    client_id = body.client_id
    licenses  = load_licenses()
    links     = load_links()

    # Find license â€” first by bound_client, then by discord_id via links
    key, entry = None, None

    for k, e in licenses.items():
        if e.get("bound_client") == client_id:
            key, entry = k, e
            break

    if entry is None:
        # Look up discord_id linked to this client_id
        discord_id = None
        for uid, data in links.items():
            if data.get("client_id") == client_id:
                discord_id = uid
                break

        if discord_id:
            for k, e in licenses.items():
                if e.get("discord_id") == str(discord_id) and e.get("bound_client") is None:
                    key, entry = k, e
                    break

    if entry is None:
        return {"valid": False, "reason": "No license found. Do !setup in Discord first."}

    if not entry.get("active", False):
        return {"valid": False, "reason": "License disabled."}

    expires = entry.get("expires")

    if expires:
        expiry_date = datetime.date.fromisoformat(expires)
        if datetime.date.today() > expiry_date:
            return {"valid": False, "reason": "License expired."}

    # Bind to this client if not already bound
    if entry.get("bound_client") is None:
        _licenses_col.update_one({"_id": key}, {"$set": {"bound_client": client_id}})

    return {
        "valid":    True,
        "expires":  expires or "lifetime",
        "customer": entry.get("customer", "")
    }

# =====================================
# DISCORD READY
# =====================================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    check_status.start()
    check_images.start()

# =====================================
# STATUS LOOP
# =====================================

@tasks.loop(seconds=2)
async def check_status():
    links = load_links()

    async with aiohttp.ClientSession() as session:
        for user_id, data in links.items():
            try:
                client_id  = data["client_id"]
                channel_id = data["channel_id"]

                async with session.get(
                    f"{SERVER_URL}/status/{client_id}",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    result  = await response.json()
                    message = result.get("message")

                if message:
                    channel = bot.get_channel(channel_id)
                    if channel:
                        await channel.send(message)

            except Exception as e:
                print(f"check_status error [{user_id}]: {e}")

# =====================================
# IMAGE LOOP
# =====================================

@tasks.loop(seconds=2)
async def check_images():
    links = load_links()

    for user_id, data in links.items():
        try:
            client_id  = data["client_id"]
            channel_id = data["channel_id"]

            image = image_queue.get(client_id)

            if image:
                channel = bot.get_channel(channel_id)

                if channel:
                    with tempfile.NamedTemporaryFile(
                        suffix=f"_{client_id}.png",
                        delete=False
                    ) as tmp:
                        tmp.write(image)
                        tmp_path = tmp.name

                    try:
                        await channel.send(file=discord.File(tmp_path))
                    finally:
                        os.remove(tmp_path)

                image_queue[client_id] = None

        except Exception as e:
            print(f"check_images error [{user_id}]: {e}")

# =====================================
# HELPERS
# =====================================

def get_client(user_id):
    links = load_links()
    return links.get(str(user_id))

def get_license_info(client_id):
    licenses = load_licenses()
    for key, entry in licenses.items():
        if entry.get("bound_client") == client_id:
            return key, entry
    return None, None

def get_license_by_discord(discord_id):
    licenses = load_licenses()
    for key, entry in licenses.items():
        if entry.get("discord_id") == str(discord_id):
            return key, entry
    return None, None

# =====================================
# HELP
# =====================================

@bot.command()
async def help(ctx):
    data = get_client(ctx.author.id)

    connected_pc = (
        data["client_id"]
        if data else
        "Not Connected"
    )

    embed = discord.Embed(
        title="ðŸ‹ WhaleBots Control Panel",
        description="Remote control system for WhaleBots",
        color=0x00b0f4
    )

    embed.add_field(
        name="ðŸ”— Setup",
        value="`!setup CODE` â†’ Link your Discord account",
        inline=False
    )

    embed.add_field(
        name="ðŸŽ® Game Controls",
        value=(
            "`!rok` â†’ Launch Rise of Kingdoms\n"
            "`!cod` â†’ Launch Call of Dragons"
        ),
        inline=False
    )

    embed.add_field(
        name="ðŸ–¥ï¸ Monitoring",
        value=(
            "`!screen bot` â†’ Screenshot WhaleBots\n"
            "`!screen <number>` â†’ Screenshot emulator\n"
            "Example: `!screen 1`\n\n"
            "`!tick <number>` â†’ Toggle selected window\n"
            "Example: `!tick 1`"
        ),
        inline=False
    )

    embed.add_field(
        name="âš™ï¸ System",
        value=(
            "`!close all` â†’ Close everything\n"
            "`!close <number>` â†’ Close selected window\n"
            "Example: `!close 1`\n\n"
            "`!update` â†’ Check for updates"
        ),
        inline=False
    )

    embed.add_field(
        name="ðŸ”‘ license",
        value="`!license` â†’ Check status",
        inline=False
    )

    embed.add_field(
        name="Connected PC",
        value=f"`{connected_pc}`",
        inline=False
    )

    await ctx.send(embed=embed)

# =====================================
# SETUP
# =====================================

@bot.command()
async def setup(ctx, pair_code: str):
    client_id = online_clients.get(pair_code)

    if not client_id:
        await ctx.send("âŒ Invalid pair code.")
        return

    links = load_links()

    links[str(ctx.author.id)] = {
        "client_id":  client_id,
        "channel_id": ctx.channel.id
    }

    save_links(links)

    await ctx.send(f"âœ… Linked to `{client_id}`")

# =====================================
# ROK
# =====================================

@bot.command()
async def rok(ctx):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("âš ï¸ Use `!setup CODE` first.")
        return

    commands_queue[data["client_id"]] = "rok"

    await ctx.send("â³ Launching ROK...")

# =====================================
# COD
# =====================================

@bot.command()
async def cod(ctx):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("âš ï¸ Use `!setup CODE` first.")
        return

    commands_queue[data["client_id"]] = "cod"

    await ctx.send("â³ Launching COD...")

# =====================================
# SCREEN
# =====================================

@bot.command()
async def screen(ctx, target="bot"):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("âš ï¸ Use `!setup CODE` first.")
        return

    commands_queue[data["client_id"]] = f"screen {target}"

    await ctx.send(f"ðŸ“¸ Taking screenshot of {target}...")

# =====================================
# TICK
# =====================================

@bot.command()
async def tick(ctx, number: int):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("âš ï¸ Use `!setup CODE` first.")
        return

    commands_queue[data["client_id"]] = f"tick {number}"

    await ctx.send(f"â³ Ticking bot {number}...")

# =====================================
# CLOSE
# =====================================

@bot.command()
async def close(ctx, target="all"):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("âš ï¸ Use `!setup CODE` first.")
        return

    commands_queue[data["client_id"]] = f"close {target}"

    await ctx.send(f"â³ Closing {target}...")

# =====================================
# UPDATE
# =====================================

@bot.command()
async def update(ctx):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("âš ï¸ Use `!setup CODE` first.")
        return

    commands_queue[data["client_id"]] = "update"

    await ctx.send("ðŸ” Checking for updates...")

# =====================================
# license
# =====================================

@bot.command()
async def license(
    ctx,
    member: discord.Member = None,
    days: int = None
):
    # OWNER CREATE / EXTEND LICENSE

    if member is not None and days is not None:

        if ctx.author.id not in OWNER_IDS:
            await ctx.send("âŒ Only owner can issue licenses.")
            return

        licenses  = load_licenses()
        key, entry = get_license_by_discord(member.id)

        if entry is not None:
            # Member already has a license â€” extend it
            current_expires = entry.get("expires")

            if days <= 0:
                new_expires = None  # upgrade to lifetime
            elif current_expires:
                base = max(
                    datetime.date.fromisoformat(current_expires),
                    datetime.date.today()
                )
                new_expires = (base + datetime.timedelta(days=days)).isoformat()
            else:
                new_expires = None  # already lifetime, keep it

            licenses[key]["expires"] = new_expires
            licenses[key]["active"]  = True
            save_licenses(licenses)

            display_expires = new_expires or "Lifetime"

            embed = discord.Embed(
                title=”🔄 License Extended”,
                color=0x00b04f
            )
            embed.add_field(name=”User”,       value=member.mention,  inline=True)
            embed.add_field(name=”New Expiry”, value=display_expires, inline=True)

            await ctx.send(embed=embed)
            return

        # No existing license â€” create a new one
        raw = secrets.token_hex(8).upper()
        key = f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"

        if days > 0:
            expires = (
                datetime.date.today() +
                datetime.timedelta(days=days)
            ).isoformat()
        else:
            expires = None

        licenses[key] = {
            "active":       True,
            "expires":      expires,
            "bound_client": None,
            "customer":     str(member),
            "discord_id":   str(member.id)
        }

        save_licenses(licenses)

        display_expires = expires or "Lifetime"

        embed = discord.Embed(
            title=”✅ License Issued”,
            color=0x00b04f
        )

        embed.add_field(name=”User”,    value=member.mention,  inline=True)
        embed.add_field(name=”Expires”, value=display_expires, inline=True)

        await ctx.send(embed=embed)

        return

    # USER CHECK LICENSE

    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("âš ï¸ Use `!setup CODE` first.")
        return

    client_id = data["client_id"]

    key, entry = get_license_info(client_id)

    if not key:
        await ctx.send("âŒ No license found.")
        return

    active  = entry.get("active", False)
    expires = entry.get("expires") or "Lifetime"
    masked  = f"****-****-****-{key[-4:]}"
    status  = "âœ… Active" if active else "âŒ Disabled"

    embed = discord.Embed(
        title="ðŸ”‘ license Status",
        color=0x00b04f if active else 0xff0000
    )

    embed.add_field(name="Status",  value=status,           inline=True)
    embed.add_field(name="Expires", value=expires,          inline=True)
    embed.add_field(name="PC",      value=f"`{client_id}`", inline=False)

    await ctx.send(embed=embed)

# =====================================
# CLIENTS
# =====================================

@bot.command()
async def clients(ctx):
    if ctx.author.id not in OWNER_IDS:
        await ctx.send("âŒ Only owners can view the client list.")
        return

    licenses = load_licenses()

    if not licenses:
        await ctx.send("No licenses found.")
        return

    today = datetime.date.today()
    lines = []

    for key, entry in licenses.items():
        if not entry.get("active", False):
            continue

        customer  = entry.get("customer", "Unknown")
        expires   = entry.get("expires")
        bound     = entry.get("bound_client") or "Not bound"
        masked    = f"****-{key[-4:]}"

        if expires:
            expiry_date = datetime.date.fromisoformat(expires)
            days_left   = (expiry_date - today).days
            if days_left < 0:
                expiry_str = f"~~{expires}~~ (expired)"
            else:
                expiry_str = f"{expires} ({days_left}d left)"
        else:
            expiry_str = "Lifetime"

        lines.append(f"**{customer}** `{masked}`\nâ”” PC: `{bound}` | Expires: {expiry_str}")

    if not lines:
        await ctx.send("No active licenses.")
        return

    # Split into pages of 10 to avoid hitting Discord's 4096 char embed limit
    page_size = 10
    pages     = [lines[i:i + page_size] for i in range(0, len(lines), page_size)]

    for i, page in enumerate(pages, 1):
        embed = discord.Embed(
            title=f"ðŸ“‹ Subscribed Clients ({len(lines)} total)" if i == 1 else f"ðŸ“‹ Clients (page {i})",
            description="\n\n".join(page),
            color=0x00b0f4
        )
        await ctx.send(embed=embed)

# =====================================
# START BOT
# =====================================

def start_bot():
    try:
        print("STARTING DISCORD BOT...")
        bot.run(TOKEN)
    except Exception as e:
        print(f"DISCORD BOT ERROR: {e}")

# =====================================
# START EVERYTHING
# =====================================

if __name__ == "__main__":

    threading.Thread(
        target=start_bot,
        daemon=True
    ).start()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
