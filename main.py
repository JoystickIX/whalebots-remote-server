# =====================================
# IMPORTS
# =====================================

import discord
from discord.ext import commands, tasks
from fastapi import FastAPI, UploadFile, File, Header, HTTPException
import threading
import uvicorn
import os
import json
import requests
import datetime
import secrets

# =====================================
# TOKEN
# =====================================

TOKEN = os.getenv("DISCORD_TOKEN")

# =====================================
# ADMIN API SECRET
# Set on Render as env var:
# ADMIN_API_SECRET=yourkey
# =====================================

API_SECRET = os.getenv(
    "ADMIN_API_SECRET",
    "changeme-please-set-this"
)

# =====================================
# OWNER ID
# Only this Discord user can run
# !licence @user days
# =====================================

OWNER_ID = 316613385485680650

# =====================================
# FILES
# =====================================

LINKS_FILE    = "links.json"
LICENSES_FILE = "licenses.json"

# =====================================
# CREATE FILES IF MISSING
# =====================================

if not os.path.exists(LINKS_FILE):
    with open(LINKS_FILE, "w") as f:
        json.dump({}, f, indent=4)

if not os.path.exists(LICENSES_FILE):
    with open(LICENSES_FILE, "w") as f:
        json.dump({}, f, indent=4)

# =====================================
# LOAD / SAVE LINKS
# =====================================

def load_links():
    with open(LINKS_FILE, "r") as f:
        return json.load(f)

def save_links(data):
    with open(LINKS_FILE, "w") as f:
        json.dump(data, f, indent=4)

# =====================================
# LOAD / SAVE LICENSES
# =====================================

def load_licenses():
    with open(LICENSES_FILE, "r") as f:
        return json.load(f)

def save_licenses(data):
    with open(LICENSES_FILE, "w") as f:
        json.dump(data, f, indent=4)

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
intents.guilds           = True
intents.members          = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)

# =====================================
# FASTAPI
# =====================================

app = FastAPI()

@app.get("/")
def home():
    return {"status": "WhaleBots Server Online"}

# =====================================
# REGISTER CLIENT
# =====================================

@app.post("/register")
def register(dict):
    client_id = data.get("client_id")
    pair_code  = data.get("pair_code")

    if not client_id or not pair_code:
        return {"success": False}

    online_clients[pair_code] = client_id
    print(f"REGISTERED: {client_id} ({pair_code})")
    return {"success": True}

# =====================================
# GET COMMAND
# =====================================

@app.get("/command/{client_id}")
def get_command(client_id: str):
    command = commands_queue.get(client_id)

    if not command:
        return {"command": None}

    commands_queue[client_id] = None
    return {"command": command}

# =====================================
# SEND STATUS
# =====================================

@app.post("/status/{client_id}")
def receive_status(client_id: str, dict):
    status_queue[client_id] = data.get("message")
    return {"success": True}

# =====================================
# GET STATUS
# =====================================

@app.get("/status/{client_id}")
def get_status(client_id: str):
    message = status_queue.get(client_id)

    if not message:
        return {"message": None}

    status_queue[client_id] = None
    return {"message": message}

# =====================================
# UPLOAD SCREENSHOT
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
# LICENSE — VALIDATE
# Called by 123.py on every startup.
# Public endpoint — no API key needed.
#
# POST /license/validate
# Body: {
#   "key":       "XXXX-XXXX-XXXX-XXXX",
#   "client_id": "DESKTOP-ABC"
# }
# =====================================

@app.post("/license/validate")
def validate_license(dict):
    key       = data.get("key", "").strip().upper()
    client_id = data.get("client_id", "")

    licenses = load_licenses()

    if key not in licenses:
        print(f"LICENSE INVALID: {key} not found")
        return {"valid": False, "reason": "Invalid license key."}

    entry = licenses[key]

    if not entry.get("active", False):
        print(f"LICENSE DISABLED: {key}")
        return {"valid": False, "reason": "License is disabled."}

    expires = entry.get("expires")
    if expires:
        expiry_date = datetime.date.fromisoformat(expires)
        if datetime.date.today() > expiry_date:
            print(f"LICENSE EXPIRED: {key}")
            return {"valid": False, "reason": "License has expired."}

    bound = entry.get("bound_client")
    if bound is None:
        licenses[key]["bound_client"] = client_id
        save_licenses(licenses)
        print(f"LICENSE BOUND: {key} → {client_id}")
    elif bound != client_id:
        print(
            f"LICENSE CONFLICT: {key} "
            f"bound to {bound}, "
            f"attempted by {client_id}"
        )
        return {
            "valid": False,
            "reason": "License is already activated on another PC."
        }

    print(f"LICENSE OK: {key} for {client_id}")
    return {
        "valid":    True,
        "expires":  expires or "lifetime",
        "customer": entry.get("customer", "")
    }

# =====================================
# LICENSE — REVOKE (ADMIN ONLY)
#
# POST /admin/revoke
# Header: X-API-Key: yourkey
# Body: { "key": "XXXX-XXXX-XXXX-XXXX" }
# =====================================

@app.post("/admin/revoke")
def revoke_license(
    dict,
    api_key: str = Header(..., alias="X-API-Key")
):
    if api_key != API_SECRET:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key."
        )

    key      = data.get("key", "").strip().upper()
    licenses = load_licenses()

    if key not in licenses:
        return {"success": False, "reason": "Key not found."}

    licenses[key]["active"] = False
    save_licenses(licenses)

    print(f"LICENSE REVOKED: {key}")
    return {"success": True}

# =====================================
# LICENSE — RESET BINDING (ADMIN ONLY)
#
# POST /admin/reset
# Header: X-API-Key: yourkey
# Body: { "key": "XXXX-XXXX-XXXX-XXXX" }
# =====================================

@app.post("/admin/reset")
def reset_license(
    dict,
    api_key: str = Header(..., alias="X-API-Key")
):
    if api_key != API_SECRET:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key."
        )

    key      = data.get("key", "").strip().upper()
    licenses = load_licenses()

    if key not in licenses:
        return {"success": False, "reason": "Key not found."}

    licenses[key]["bound_client"] = None
    save_licenses(licenses)

    print(f"LICENSE RESET (unbound): {key}")
    return {"success": True}

# =====================================
# LICENSE — LIST ALL (ADMIN ONLY)
#
# GET /admin/licenses
# Header: X-API-Key: yourkey
# =====================================

@app.get("/admin/licenses")
def list_licenses(
    api_key: str = Header(..., alias="X-API-Key")
):
    if api_key != API_SECRET:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key."
        )
    return load_licenses()

# =====================================
# BOT READY
# =====================================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    check_status.start()
    check_images.start()

# =====================================
# STATUS CHECKER
# =====================================

@tasks.loop(seconds=2)
async def check_status():
    links = load_links()

    for user_id, data in links.items():
        try:
            client_id  = data["client_id"]
            channel_id = data["channel_id"]

            response = requests.get(
                f"https://whalebots-remote-server.onrender.com"
                f"/status/{client_id}"
            )

            result  = response.json()
            message = result.get("message")

            if message:
                channel = bot.get_channel(channel_id)
                if channel:
                    await channel.send(message)

        except Exception as e:
            print(e)

# =====================================
# IMAGE CHECKER
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
                    with open("temp.png", "wb") as f:
                        f.write(image)

                    await channel.send(
                        file=discord.File("temp.png")
                    )

                image_queue[client_id] = None

        except Exception as e:
            print(e)

# =====================================
# GET CLIENT
# =====================================

def get_client(user_id):
    links = load_links()
    return links.get(str(user_id))

# =====================================
# GET LICENSE INFO BY CLIENT ID
# =====================================

def get_license_info(client_id):
    licenses = load_licenses()
    for key, entry in licenses.items():
        if entry.get("bound_client") == client_id:
            return key, entry
    return None, None

# =====================================
# GET LICENSE INFO BY DISCORD USER ID
# =====================================

def get_license_by_discord_id(discord_id):
    licenses = load_licenses()
    for key, entry in licenses.items():
        if str(entry.get("discord_id", "")) == str(discord_id):
            return key, entry
    return None, None

# =====================================
# HELP
# =====================================

@bot.command()
async def help(ctx):
    data = get_client(ctx.author.id)

    connected_pc = "Not Connected"
    if data:
        connected_pc = data["client_id"]

    embed = discord.Embed(
        title="🐋 WhaleBots Control Panel",
        description="Remote control system for WhaleBots",
        color=0x00b0f4
    )

    embed.add_field(
        name="🔗 Setup",
        value="`!setup CODE` → Link your Discord account",
        inline=False
    )

    embed.add_field(
        name="🎮 Game Controls",
        value=(
            "`!rok` → Launch Rise of Kingdoms\n"
            "`!cod` → Launch Call of Dragons"
        ),
        inline=False
    )

    embed.add_field(
        name="🖥️ Monitoring",
        value=(
            "`!screen bot` → Screenshot WhaleBots\n"
            "`!screen <number>` → Screenshot emulator\n"
            "Example: `!screen 1`\n\n"
            "`!tick <number>` → Toggle selected window\n"
            "Example: `!tick 1`"
        ),
        inline=False
    )

    embed.add_field(
        name="⚙️ System",
        value=(
            "`!close all` → Close everything\n"
            "`!close <number>` → Close selected window\n"
            "Example: `!close 1`"
        ),
        inline=False
    )

    embed.add_field(
        name="🔑 Licence",
        value="`!licence` → Check licence status",
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
        await ctx.send("❌ Invalid pair code.")
        return

    links = load_links()
    links[str(ctx.author.id)] = {
        "client_id":  client_id,
        "channel_id": ctx.channel.id
    }
    save_links(links)

    await ctx.send(f"✅ Linked to `{client_id}`")

# =====================================
# ROK
# =====================================

@bot.command()
async def rok(ctx):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return

    commands_queue[data["client_id"]] = "rok"
    await ctx.send("⏳ Launching ROK...")

# =====================================
# COD
# =====================================

@bot.command()
async def cod(ctx):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return

    commands_queue[data["client_id"]] = "cod"
    await ctx.send("⏳ Launching COD...")

# =====================================
# SCREEN
# =====================================

@bot.command()
async def screen(ctx, target="bot"):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return

    commands_queue[data["client_id"]] = f"screen {target}"
    await ctx.send(f"📸 Taking screenshot of {target}...")

# =====================================
# TICK
# =====================================

@bot.command()
async def tick(ctx, number: int):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return

    commands_queue[data["client_id"]] = f"tick {number}"
    await ctx.send(f"⏳ Checking bot {number}...")

# =====================================
# CLOSE
# =====================================

@bot.command()
async def close(ctx, target="all"):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return

    commands_queue[data["client_id"]] = f"close {target}"
    await ctx.send(f"⏳ Closing {target}...")

# =====================================
# LICENCE
# =====================================

@bot.command()
async def licence(
    ctx,
    member: discord.Member = None,
    days: int = None
):

    # =====================================
    # OWNER ONLY — GIVE LICENCE TO USER
    # Usage: !licence @user 30
    #        !licence @user 0  ← lifetime
    # =====================================

    if member is not None and days is not None:

        if ctx.author.id != OWNER_ID:
            await ctx.send(
                "❌ Only the owner can issue licences."
            )
            return

        # Generate key
        raw = secrets.token_hex(8).upper()
        key = (
            f"{raw[0:4]}-{raw[4:8]}-"
            f"{raw[8:12]}-{raw[12:16]}"
        )

        # Calculate expiry
        if days > 0:
            expires = (
                datetime.date.today() +
                datetime.timedelta(days=days)
            ).isoformat()
            expires_display = f"{expires} ({days} days)"
        else:
            expires         = None
            expires_display = "Lifetime"

        # Save license
        licenses = load_licenses()
        licenses[key] = {
            "active":       True,
            "expires":      expires,
            "bound_client": None,
            "customer":     str(member),
            "discord_id":   str(member.id)
        }
        save_licenses(licenses)

        print(
            f"LICENCE ISSUED: {key} "
            f"→ {member} ({member.id}) "
            f"expires={expires or 'lifetime'}"
        )

        # DM the key to the user
        try:
            dm_embed = discord.Embed(
                title="🔑 Your WhaleBots Licence Key",
                description=(
                    "Your licence has been activated.\n"
                    "Paste this key into `license.txt` "
                    "next to `123.py` on your PC."
                ),
                color=0x00b04f
            )

            dm_embed.add_field(
                name="Key",
                value=f"```{key}```",
                inline=False
            )

            dm_embed.add_field(
                name="Expires",
                value=expires_display,
                inline=True
            )

            dm_embed.add_field(
                name="How to activate",
                value=(
                    "1. Create `license.txt` next to `123.py`\n"
                    "2. Paste your key inside\n"
                    "3. Run `123.py`"
                ),
                inline=False
            )

            await member.send(embed=dm_embed)
            dm_status = "✅ Key sent via DM"

        except discord.Forbidden:
            dm_status = "⚠️ Could not DM user (DMs disabled)"

        # Confirm to owner in channel
        masked_key = f"****-****-****-{key[-4:]}"

        confirm_embed = discord.Embed(
            title="✅ Licence Issued",
            color=0x00b04f
        )

        confirm_embed.add_field(
            name="User",
            value=member.mention,
            inline=True
        )

        confirm_embed.add_field(
            name="Key (masked)",
            value=f"`{masked_key}`",
            inline=True
        )

        confirm_embed.add_field(
            name="Expires",
            value=expires_display,
            inline=True
        )

        confirm_embed.add_field(
            name="DM Status",
            value=dm_status,
            inline=False
        )

        await ctx.send(embed=confirm_embed)
        return

    # =====================================
    # MISSING DAYS ARGUMENT
    # e.g. !licence @user  (no days given)
    # =====================================

    if member is not None and days is None:
        await ctx.send(
            "⚠️ Please specify days.\n"
            "Usage: `!licence @user 30` "
            "or `!licence @user 0` for lifetime."
        )
        return

    # =====================================
    # NO ARGS — CHECK OWN LICENCE STATUS
    # Usage: !licence
    # =====================================

    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return

    client_id  = data["client_id"]
    key, entry = get_license_info(client_id)

    if not key:
        key, entry = get_license_by_discord_id(ctx.author.id)

    if not key:
        embed = discord.Embed(
            title="🔑 Licence Status",
            color=0xff4444
        )
        embed.add_field(
            name="Status",
            value="❌ No licence found for your account.",
            inline=False
        )
        await ctx.send(embed=embed)
        return

    masked_key  = f"****-****-****-{key[-4:]}"
    active       = entry.get("active", False)
    expires      = entry.get("expires") or "Lifetime"
    customer     = entry.get("customer", "—")

    is_expired = False
    if entry.get("expires"):
        expiry_date = datetime.date.fromisoformat(
            entry["expires"]
        )
        if datetime.date.today() > expiry_date:
            is_expired = True

    if not active or is_expired:
        status_text = "❌ Inactive / Expired"
        embed_color = 0xff4444
    else:
        status_text = "✅ Active"
        embed_color = 0x00b04f

    embed = discord.Embed(
        title="🔑 Licence Status",
        color=embed_color
    )

    embed.add_field(
        name="Status",
        value=status_text,
        inline=True
    )

    embed.add_field(
        name="Key",
        value=f"`{masked_key}`",
        inline=True
    )

    embed.add_field(
        name="Expires",
        value=expires,
        inline=True
    )

    embed.add_field(
        name="Customer",
        value=customer,
        inline=True
    )

    embed.add_field(
        name="Bound PC",
        value=f"`{client_id}`",
        inline=True
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
        print("DISCORD BOT ERROR:")
        print(e)

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
