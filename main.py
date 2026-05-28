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
import hashlib
import datetime
import secrets

# =====================================
# TOKEN
# =====================================

TOKEN = os.getenv("DISCORD_TOKEN")

# =====================================
# ADMIN API SECRET
# Set this as an environment variable
# on Render: ADMIN_API_SECRET=yourkey
# Used to protect all /admin/ endpoints
# so only you can generate/revoke keys.
# =====================================

API_SECRET = os.getenv("ADMIN_API_SECRET", "changeme-please-set-this")

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
# VERIFY ADMIN API KEY
# Protects all /admin/ endpoints.
# Pass header: X-API-Key: yourkey
# =====================================

def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_SECRET:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key."
        )
    return x_api_key

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
# Checks key validity, expiry, and PC
# binding. Binds key to first PC that
# activates it.
#
# POST /license/validate
# Body: {
#   "key": "XXXX-XXXX-XXXX-XXXX",
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
        "valid": True,
        "expires": expires or "lifetime",
        "customer": entry.get("customer", "")
    }

# =====================================
# LICENSE — GENERATE (ADMIN ONLY)
# Creates a new license key.
#
# POST /admin/generate
# Header: X-API-Key: yourkey
# Body: {
#   "customer": "John",
#   "days": 30        ← 0 = lifetime
# }
# =====================================

@app.post("/admin/generate")
def generate_license(
    dict,
    api_key: str = Header(..., alias="X-API-Key")
):
    verify_api_key(api_key)

    customer = data.get("customer", "unknown")
    days     = data.get("days", 0)

    raw = secrets.token_hex(8).upper()
    key = f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"

    if days > 0:
        expires = (
            datetime.date.today() +
            datetime.timedelta(days=days)
        ).isoformat()
    else:
        expires = None

    licenses = load_licenses()
    licenses[key] = {
        "active":       True,
        "expires":      expires,
        "bound_client": None,
        "customer":     customer
    }
    save_licenses(licenses)

    print(
        f"LICENSE GENERATED: {key} "
        f"for '{customer}' "
        f"expires={expires or 'lifetime'}"
    )

    return {
        "key":      key,
        "customer": customer,
        "expires":  expires or "lifetime"
    }

# =====================================
# LICENSE — REVOKE (ADMIN ONLY)
# Instantly disables a key.
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
    verify_api_key(api_key)

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
# Unbinds a key from its PC so the
# customer can activate on a new PC.
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
    verify_api_key(api_key)

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
# Returns all keys with full details.
#
# GET /admin/licenses
# Header: X-API-Key: yourkey
# =====================================

@app.get("/admin/licenses")
def list_licenses(
    api_key: str = Header(..., alias="X-API-Key")
):
    verify_api_key(api_key)
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
# CHECK LICENSE STATUS FOR DISCORD USER
# Looks up the client_id linked to the
# Discord user and checks their license
# entry in licenses.json directly.
# =====================================

def get_license_info(client_id):
    """
    Returns the license entry for a
    given client_id, or None if no
    license is bound to that PC.
    """
    licenses = load_licenses()

    for key, entry in licenses.items():
        if entry.get("bound_client") == client_id:
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
# LICENCE — DISCORD COMMAND
# FIXED: Was just a static "active"
# message. Now actually looks up the
# license bound to the user's PC and
# returns real key (masked),
# expiry date, and customer name.
# =====================================

@bot.command()
async def licence(ctx):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return

    client_id    = data["client_id"]
    key, entry   = get_license_info(client_id)

    if not key:
        embed = discord.Embed(
            title="🔑 Licence Status",
            color=0xff4444
        )
        embed.add_field(
            name="Status",
            value="❌ No licence found for this PC.",
            inline=False
        )
        await ctx.send(embed=embed)
        return

    # Mask the key — show only last 4 chars
    masked_key = f"****-****-****-{key[-4:]}"

    active  = entry.get("active", False)
    expires = entry.get("expires") or "Lifetime"
    customer = entry.get("customer", "—")

    # Check if expired
    is_expired = False
    if entry.get("expires"):
        expiry_date = datetime.date.fromisoformat(
            entry["expires"]
        )
        if datetime.date.today() > expiry_date:
            is_expired = True

    if not active or is_expired:
        status_text  = "❌ Inactive / Expired"
        embed_color  = 0xff4444
    else:
        status_text  = "✅ Active"
        embed_color  = 0x00b04f

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
