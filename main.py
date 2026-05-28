# =====================================
# IMPORTS
# =====================================

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

# =====================================
# ENV
# =====================================

TOKEN      = os.getenv("DISCORD_TOKEN")
API_SECRET = os.getenv("ADMIN_API_SECRET", "changeme-please-set-this")

OWNER_ID = 316613385485680650

# =====================================
# AUTO UPDATE
# =====================================

LATEST_VERSION = "1.0.1"

EXE_DOWNLOAD_LINK = (
    "https://YOUR-DIRECT-EXE-DOWNLOAD-LINK"
)

# =====================================
# FILES
# =====================================

LINKS_FILE    = "links.json"
LICENSES_FILE = "licenses.json"

# =====================================
# CREATE FILES
# =====================================

if not os.path.exists(LINKS_FILE):

    with open(LINKS_FILE, "w") as f:

        json.dump({}, f, indent=4)

if not os.path.exists(LICENSES_FILE):

    with open(LICENSES_FILE, "w") as f:

        json.dump({}, f, indent=4)

# =====================================
# LOAD / SAVE
# =====================================

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

    key: str
    client_id: str

class LicenseKeyBody(BaseModel):

    key: str

# =====================================
# HOME
# =====================================

@app.get("/")
def home():

    return {
        "status": "WhaleBots Server Online"
    }

# =====================================
# VERSION API
# =====================================

@app.get("/version")
def version():

    return {

        "version": LATEST_VERSION,

        "download": EXE_DOWNLOAD_LINK
    }

# =====================================
# REGISTER
# =====================================

@app.post("/register")
def register(body: RegisterBody):

    if not body.client_id or not body.pair_code:

        return {
            "success": False
        }

    online_clients[body.pair_code] = body.client_id

    print(
        f"REGISTERED: {body.client_id} ({body.pair_code})"
    )

    return {
        "success": True
    }

# =====================================
# COMMAND
# =====================================

@app.get("/command/{client_id}")
def get_command(client_id: str):

    command = commands_queue.get(client_id)

    if not command:

        return {
            "command": None
        }

    commands_queue[client_id] = None

    return {
        "command": command
    }

# =====================================
# STATUS
# =====================================

@app.post("/status/{client_id}")
def receive_status(
    client_id: str,
    body: StatusBody
):

    status_queue[client_id] = body.message

    return {
        "success": True
    }

@app.get("/status/{client_id}")
def get_status(client_id: str):

    message = status_queue.get(client_id)

    if not message:

        return {
            "message": None
        }

    status_queue[client_id] = None

    return {
        "message": message
    }

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

    return {
        "success": True
    }

# =====================================
# LICENSE VALIDATION
# =====================================

@app.post("/license/validate")
def validate_license(
    body: LicenseValidateBody
):

    key       = body.key.strip().upper()
    client_id = body.client_id

    licenses = load_licenses()

    if key not in licenses:

        print(
            f"LICENSE INVALID: {key}"
        )

        return {

            "valid": False,

            "reason":
            "Invalid license key."
        }

    entry = licenses[key]

    if not entry.get("active", False):

        return {

            "valid": False,

            "reason":
            "License is disabled."
        }

    expires = entry.get("expires")

    if expires:

        expiry_date = datetime.date.fromisoformat(
            expires
        )

        if datetime.date.today() > expiry_date:

            return {

                "valid": False,

                "reason":
                "License expired."
            }

    bound = entry.get("bound_client")

    if bound is None:

        licenses[key]["bound_client"] = client_id

        save_licenses(licenses)

    elif bound != client_id:

        return {

            "valid": False,

            "reason":
            "License already active on another PC."
        }

    return {

        "valid": True,

        "expires":
        expires or "lifetime",

        "customer":
        entry.get("customer", "")
    }

# =====================================
# ADMIN RESET
# =====================================

@app.post("/admin/reset")
def reset_license(
    body: LicenseKeyBody,
    api_key: str = Header(..., alias="X-API-Key")
):

    if api_key != API_SECRET:

        raise HTTPException(
            status_code=403,
            detail="Invalid API key."
        )

    key = body.key.strip().upper()

    licenses = load_licenses()

    if key not in licenses:

        return {
            "success": False
        }

    licenses[key]["bound_client"] = None

    save_licenses(licenses)

    return {
        "success": True
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

    for user_id, data in links.items():

        try:

            client_id  = data["client_id"]
            channel_id = data["channel_id"]

            response = requests.get(
                f"https://whalebots-remote-server.onrender.com/status/{client_id}"
            )

            result = response.json()

            message = result.get("message")

            if message:

                channel = bot.get_channel(
                    channel_id
                )

                if channel:

                    await channel.send(message)

        except Exception as e:

            print(e)

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

                channel = bot.get_channel(
                    channel_id
                )

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
        title="🐋 WhaleBots Control Panel",
        description="Remote control system for WhaleBots",
        color=0x00b0f4
    )

    embed.add_field(
        name="🔗 Setup",
        value=(
            "`!setup CODE` → Link your Discord account"
        ),
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
            "Example: `!close 1`\n\n"

            "`!update` → Update client"
        ),
        inline=False
    )

    embed.add_field(
        name="🔑 Licence",
        value=(
            "`!licence` → Check status"
        ),
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

        await ctx.send(
            "❌ Invalid pair code."
        )

        return

    links = load_links()

    links[str(ctx.author.id)] = {

        "client_id": client_id,

        "channel_id": ctx.channel.id
    }

    save_links(links)

    await ctx.send(
        f"✅ Linked to `{client_id}`"
    )

# =====================================
# ROK
# =====================================

@bot.command()
async def rok(ctx):

    data = get_client(ctx.author.id)

    if not data:

        await ctx.send(
            "⚠️ Use `!setup CODE` first."
        )

        return

    commands_queue[data["client_id"]] = "rok"

    await ctx.send(
        "⏳ Launching ROK..."
    )

# =====================================
# COD
# =====================================

@bot.command()
async def cod(ctx):

    data = get_client(ctx.author.id)

    if not data:

        await ctx.send(
            "⚠️ Use `!setup CODE` first."
        )

        return

    commands_queue[data["client_id"]] = "cod"

    await ctx.send(
        "⏳ Launching COD..."
    )

# =====================================
# SCREEN
# =====================================

@bot.command()
async def screen(ctx, target="bot"):

    data = get_client(ctx.author.id)

    if not data:

        await ctx.send(
            "⚠️ Use `!setup CODE` first."
        )

        return

    commands_queue[data["client_id"]] = f"screen {target}"

    await ctx.send(
        f"📸 Taking screenshot of {target}..."
    )

# =====================================
# TICK
# =====================================

@bot.command()
async def tick(ctx, number: int):

    data = get_client(ctx.author.id)

    if not data:

        await ctx.send(
            "⚠️ Use `!setup CODE` first."
        )

        return

    commands_queue[data["client_id"]] = f"tick {number}"

    await ctx.send(
        f"⏳ Ticking bot {number}..."
    )

# =====================================
# CLOSE
# =====================================

@bot.command()
async def close(ctx, target="all"):

    data = get_client(ctx.author.id)

    if not data:

        await ctx.send(
            "⚠️ Use `!setup CODE` first."
        )

        return

    commands_queue[data["client_id"]] = f"close {target}"

    await ctx.send(
        f"⏳ Closing {target}..."
    )

# =====================================
# UPDATE
# =====================================

@bot.command()
async def update(ctx):

    data = get_client(ctx.author.id)

    if not data:

        await ctx.send(
            "⚠️ Use `!setup CODE` first."
        )

        return

    commands_queue[data["client_id"]] = "update"

    await ctx.send(
        "⬇️ Sending update command..."
    )

# =====================================
# LICENCE
# =====================================

@bot.command()
async def licence(
    ctx,
    member: discord.Member = None,
    days: int = None
):

    # OWNER CREATE LICENSE

    if member is not None and days is not None:

        if ctx.author.id != OWNER_ID:

            await ctx.send(
                "❌ Only owner can issue licences."
            )

            return

        raw = secrets.token_hex(8).upper()

        key = (
            f"{raw[0:4]}-"
            f"{raw[4:8]}-"
            f"{raw[8:12]}-"
            f"{raw[12:16]}"
        )

        if days > 0:

            expires = (
                datetime.date.today() +
                datetime.timedelta(days=days)
            ).isoformat()

        else:

            expires = None

        licenses = load_licenses()

        licenses[key] = {

            "active": True,

            "expires": expires,

            "bound_client": None,

            "customer": str(member),

            "discord_id": str(member.id)
        }

        save_licenses(licenses)

        await ctx.send(
            f"✅ Licence created for {member}\n```{key}```"
        )

        try:

            await member.send(
                f"🔑 Your WhaleBots licence:\n```{key}```"
            )

        except:

            pass

        return

    # USER CHECK LICENSE

    data = get_client(ctx.author.id)

    if not data:

        await ctx.send(
            "⚠️ Use `!setup CODE` first."
        )

        return

    client_id = data["client_id"]

    key, entry = get_license_info(client_id)

    if not key:

        await ctx.send(
            "❌ No licence found."
        )

        return

    active  = entry.get("active", False)

    expires = entry.get("expires") or "Lifetime"

    masked = f"****-****-****-{key[-4:]}"

    status = (
        "✅ Active"
        if active else
        "❌ Disabled"
    )

    embed = discord.Embed(
        title="🔑 Licence Status",
        color=0x00b04f if active else 0xff0000
    )

    embed.add_field(
        name="Status",
        value=status,
        inline=True
    )

    embed.add_field(
        name="Key",
        value=f"`{masked}`",
        inline=True
    )

    embed.add_field(
        name="Expires",
        value=expires,
        inline=True
    )

    embed.add_field(
        name="PC",
        value=f"`{client_id}`",
        inline=False
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
