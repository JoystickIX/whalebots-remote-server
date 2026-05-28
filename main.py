import discord
from discord.ext import commands, tasks
from fastapi import FastAPI, UploadFile, File, Header, HTTPException
from pydantic import BaseModel
import threading
import uvicorn
import os
import json
import requests
import datetime
import secrets

TOKEN      = os.getenv("DISCORD_TOKEN")
API_SECRET = os.getenv("ADMIN_API_SECRET", "changeme-please-set-this")
OWNER_ID   = 316613385485680650

LINKS_FILE    = "links.json"
LICENSES_FILE = "licenses.json"

if not os.path.exists(LINKS_FILE):
    with open(LINKS_FILE, "w") as f:
        json.dump({}, f, indent=4)

if not os.path.exists(LICENSES_FILE):
    with open(LICENSES_FILE, "w") as f:
        json.dump({}, f, indent=4)

def load_links():
    with open(LINKS_FILE, "r") as f:
        return json.load(f)

def save_links(data):
    with open(LINKS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def load_licenses():
    with open(LICENSES_FILE, "r") as f:
        return json.load(f)

def save_licenses(data):
    with open(LICENSES_FILE, "w") as f:
        json.dump(data, f, indent=4)

commands_queue = {}
status_queue   = {}
online_clients = {}
image_queue    = {}

intents = discord.Intents.default()
intents.message_content = True
intents.guilds           = True
intents.members          = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)

app = FastAPI()

# =====================================
# PYDANTIC MODELS
# =====================================

class RegisterBody(BaseModel):
    client_id: str
    pair_code: str

class StatusBody(BaseModel):
    message: str

class LicenseValidateBody(BaseModel):
    key: str
    client_id: str

class LicenseKeyBody(BaseModel):
    key: str

# =====================================
# ENDPOINTS
# =====================================

@app.get("/")
def home():
    return {"status": "WhaleBots Server Online"}

@app.post("/register")
def register(body: RegisterBody):
    if not body.client_id or not body.pair_code:
        return {"success": False}
    online_clients[body.pair_code] = body.client_id
    print(f"REGISTERED: {body.client_id} ({body.pair_code})")
    return {"success": True}

@app.get("/command/{client_id}")
def get_command(client_id: str):
    command = commands_queue.get(client_id)
    if not command:
        return {"command": None}
    commands_queue[client_id] = None
    return {"command": command}

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

@app.post("/upload/{client_id}")
async def upload_image(
    client_id: str,
    file: UploadFile = File(...)
):
    content = await file.read()
    image_queue[client_id] = content
    return {"success": True}

@app.post("/license/validate")
def validate_license(body: LicenseValidateBody):
    key       = body.key.strip().upper()
    client_id = body.client_id

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
        print(f"LICENSE CONFLICT: {key} bound to {bound}, attempted by {client_id}")
        return {"valid": False, "reason": "License is already activated on another PC."}

    print(f"LICENSE OK: {key} for {client_id}")
    return {
        "valid":    True,
        "expires":  expires or "lifetime",
        "customer": entry.get("customer", "")
    }

@app.post("/admin/revoke")
def revoke_license(
    body: LicenseKeyBody,
    api_key: str = Header(..., alias="X-API-Key")
):
    if api_key != API_SECRET:
        raise HTTPException(status_code=403, detail="Invalid API key.")
    key      = body.key.strip().upper()
    licenses = load_licenses()
    if key not in licenses:
        return {"success": False, "reason": "Key not found."}
    licenses[key]["active"] = False
    save_licenses(licenses)
    print(f"LICENSE REVOKED: {key}")
    return {"success": True}

@app.post("/admin/reset")
def reset_license(
    body: LicenseKeyBody,
    api_key: str = Header(..., alias="X-API-Key")
):
    if api_key != API_SECRET:
        raise HTTPException(status_code=403, detail="Invalid API key.")
    key      = body.key.strip().upper()
    licenses = load_licenses()
    if key not in licenses:
        return {"success": False, "reason": "Key not found."}
    licenses[key]["bound_client"] = None
    save_licenses(licenses)
    print(f"LICENSE RESET (unbound): {key}")
    return {"success": True}

@app.get("/admin/licenses")
def list_licenses(
    api_key: str = Header(..., alias="X-API-Key")
):
    if api_key != API_SECRET:
        raise HTTPException(status_code=403, detail="Invalid API key.")
    return load_licenses()

# =====================================
# DISCORD EVENTS
# =====================================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    check_status.start()
    check_images.start()

@tasks.loop(seconds=2)
async def check_status():
    links = load_links()
    for user_id, data in links.items():
        try:
            client_id  = data["client_id"]
            channel_id = data["channel_id"]
            response   = requests.get(
                f"https://whalebots-remote-server.onrender.com/status/{client_id}"
            )
            result  = response.json()
            message = result.get("message")
            if message:
                channel = bot.get_channel(channel_id)
                if channel:
                    await channel.send(message)
        except Exception as e:
            print(e)

@tasks.loop(seconds=2)
async def check_images():
    links = load_links()
    for user_id, data in links.items():
        try:
            client_id  = data["client_id"]
            channel_id = data["channel_id"]
            image      = image_queue.get(client_id)
            if image:
                channel = bot.get_channel(channel_id)
                if channel:
                    with open("temp.png", "wb") as f:
                        f.write(image)
                    await channel.send(file=discord.File("temp.png"))
                image_queue[client_id] = None
        except Exception as e:
            print(e)

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

def get_license_by_discord_id(discord_id):
    licenses = load_licenses()
    for key, entry in licenses.items():
        if str(entry.get("discord_id", "")) == str(discord_id):
            return key, entry
    return None, None

# =====================================
# COMMANDS
# =====================================

@bot.command()
async def help(ctx):
    data         = get_client(ctx.author.id)
    connected_pc = data["client_id"] if data else "Not Connected"

    embed = discord.Embed(
        title="🐋 WhaleBots Control Panel",
        description="Remote control system for WhaleBots",
        color=0x00b0f4
    )
    embed.add_field(name="🔗 Setup",        value="`!setup CODE`",                                                                  inline=False)
    embed.add_field(name="🎮 Game Controls", value="`!rok` → Launch ROK\n`!cod` → Launch COD",                                      inline=False)
    embed.add_field(name="🖥️ Monitoring",   value="`!screen bot` / `!screen <number>`\n`!tick <number>`",                          inline=False)
    embed.add_field(name="⚙️ System",       value="`!close all` / `!close <number>`",                                              inline=False)
    embed.add_field(name="🔑 Licence",      value="`!licence` → Check status",                                                     inline=False)
    embed.add_field(name="Connected PC",    value=f"`{connected_pc}`",                                                             inline=False)
    await ctx.send(embed=embed)

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

@bot.command()
async def rok(ctx):
    data = get_client(ctx.author.id)
    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return
    commands_queue[data["client_id"]] = "rok"
    await ctx.send("⏳ Launching ROK...")

@bot.command()
async def cod(ctx):
    data = get_client(ctx.author.id)
    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return
    commands_queue[data["client_id"]] = "cod"
    await ctx.send("⏳ Launching COD...")

@bot.command()
async def screen(ctx, target="bot"):
    data = get_client(ctx.author.id)
    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return
    commands_queue[data["client_id"]] = f"screen {target}"
    await ctx.send(f"📸 Taking screenshot of {target}...")

@bot.command()
async def tick(ctx, number: int):
    data = get_client(ctx.author.id)
    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return
    commands_queue[data["client_id"]] = f"tick {number}"
    await ctx.send(f"⏳ Ticking bot {number}...")

@bot.command()
async def close(ctx, target="all"):
    data = get_client(ctx.author.id)
    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return
    commands_queue[data["client_id"]] = f"close {target}"
    await ctx.send(f"⏳ Closing {target}...")

@bot.command()
async def licence(ctx, member: discord.Member = None, days: int = None):

    if member is not None and days is not None:
        if ctx.author.id != OWNER_ID:
            await ctx.send("❌ Only the owner can issue licences.")
            return

        raw = secrets.token_hex(8).upper()
        key = f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"

        if days > 0:
            expires         = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
            expires_display = f"{expires} ({days} days)"
        else:
            expires         = None
            expires_display = "Lifetime"

        licenses = load_licenses()
        licenses[key] = {
            "active":       True,
            "expires":      expires,
            "bound_client": None,
            "customer":     str(member),
            "discord_id":   str(member.id)
        }
        save_licenses(licenses)
        print(f"LICENCE ISSUED: {key} → {member} ({member.id}) expires={expires or 'lifetime'}")

        try:
            dm_embed = discord.Embed(
                title="🔑 Your WhaleBots Licence Key",
                description="Paste this key into `license.txt` next to `123.py` on your PC.",
                color=0x00b04f
            )
            dm_embed.add_field(name="Key",            value=f"```{key}```",   inline=False)
            dm_embed.add_field(name="Expires",        value=expires_display,  inline=True)
            dm_embed.add_field(name="How to activate",
                value="1. Create `license.txt` next to `123.py`\n2. Paste your key inside\n3. Run `123.py`",
                inline=False
            )
            await member.send(embed=dm_embed)
            dm_status = "✅ Key sent via DM"
        except discord.Forbidden:
            dm_status = "⚠️ Could not DM user (DMs disabled)"

        masked_key    = f"****-****-****-{key[-4:]}"
        confirm_embed = discord.Embed(title="✅ Licence Issued", color=0x00b04f)
        confirm_embed.add_field(name="User",       value=member.mention,   inline=True)
        confirm_embed.add_field(name="Key",        value=f"`{masked_key}`",inline=True)
        confirm_embed.add_field(name="Expires",    value=expires_display,  inline=True)
        confirm_embed.add_field(name="DM Status",  value=dm_status,        inline=False)
        await ctx.send(embed=confirm_embed)
        return

    if member is not None and days is None:
        await ctx.send("⚠️ Usage: `!licence @user 30` or `!licence @user 0` for lifetime.")
        return

    data = get_client(ctx.author.id)
    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return

    client_id  = data["client_id"]
    key, entry = get_license_info(client_id)

    if not key:
        key, entry = get_license_by_discord_id(ctx.author.id)

    if not key:
        embed = discord.Embed(title="🔑 Licence Status", color=0xff4444)
        embed.add_field(name="Status", value="❌ No licence found for your account.", inline=False)
        await ctx.send(embed=embed)
        return

    masked_key = f"****-****-****-{key[-4:]}"
    active      = entry.get("active", False)
    expires     = entry.get("expires") or "Lifetime"
    customer    = entry.get("customer", "—")

    is_expired = False
    if entry.get("expires"):
        if datetime.date.today() > datetime.date.fromisoformat(entry["expires"]):
            is_expired = True

    status_text = "✅ Active" if (active and not is_expired) else "❌ Inactive / Expired"
    embed_color = 0x00b04f  if (active and not is_expired) else 0xff4444

    embed = discord.Embed(title="🔑 Licence Status", color=embed_color)
    embed.add_field(name="Status",   value=status_text,      inline=True)
    embed.add_field(name="Key",      value=f"`{masked_key}`",inline=True)
    embed.add_field(name="Expires",  value=expires,          inline=True)
    embed.add_field(name="Customer", value=customer,         inline=True)
    embed.add_field(name="Bound PC", value=f"`{client_id}`", inline=True)
    await ctx.send(embed=embed)

# =====================================
# START
# =====================================

def start_bot():
    try:
        print("STARTING DISCORD BOT...")
        bot.run(TOKEN)
    except Exception as e:
        print("DISCORD BOT ERROR:", e)

if __name__ == "__main__":
    threading.Thread(target=start_bot, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
