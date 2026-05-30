# =====================================
# IMPORTS
# =====================================

import discord
from discord.ext import commands, tasks
from fastapi import FastAPI, UploadFile, File, Header, HTTPException, Depends, Request
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
import random
import time
import re
from pymongo import MongoClient

# =====================================
# ENV
# =====================================

TOKEN      = os.getenv("DISCORD_TOKEN")
SERVER_URL = os.getenv("SERVER_URL", "https://whalebots-remote-server-production.up.railway.app")
MONGO_URI  = os.getenv("MONGO_URI")

OWNER_IDS = {316613385485680650, 641191095258185728}

# =====================================
# AUTO UPDATE
# =====================================

LATEST_VERSION = "1.0.9"

EXE_DOWNLOAD_LINK = (
    "https://github.com/JoystickIX/whalebots-remote-server/releases/download/V1.0.9/WhaleBotsRemote.exe"
)

# SHA-256 of the official WhaleBotsRemote.exe for v1.0.9.
# The client verifies the downloaded EXE against this before installing.
# Filled in after the build (leave "" to skip verification).
LATEST_SHA256 = "0b8f7301d0f5c02b6a370d8dc9aca5b2a04fa950b4f23723e859addb7b699482"

# =====================================
# DATABASE
# =====================================

_mongo_client  = MongoClient(
    MONGO_URI,
    maxPoolSize=50,
    serverSelectionTimeoutMS=5000,
    retryWrites=True,
)
_db            = _mongo_client["whalebots"]
_licenses_col  = _db["licenses"]
_links_col     = _db["links"]
_paircodes_col = _db["pair_codes"]
# Durable backing stores so a server restart never loses pending work
_cmd_col       = _db["cmd_queue"]      # pending commands per client
_status_col    = _db["status_pending"] # latest pending status msg per client
_online_col    = _db["online_clients"] # pair_code -> client_id (for !setup)
_tokens_col    = _db["client_tokens"]  # client_id -> per-client auth token

# =====================================
# LOAD / SAVE
# =====================================

_links_cache      = {}
_links_cache_time = 0
LINKS_CACHE_TTL   = 10  # seconds

def load_links():
    global _links_cache, _links_cache_time
    if time.time() - _links_cache_time < LINKS_CACHE_TTL:
        return _links_cache
    result = {}
    for doc in _links_col.find():
        user_id = doc["_id"]
        result[user_id] = {
            "client_id":  doc["client_id"],
            "channel_id": doc["channel_id"]
        }
    _links_cache      = result
    _links_cache_time = time.time()
    return result

def save_links(data):
    global _links_cache_time
    for user_id, entry in data.items():
        _links_col.update_one(
            {"_id": user_id},
            {"$set": {
                "client_id":  entry["client_id"],
                "channel_id": entry["channel_id"]
            }},
            upsert=True
        )
    _links_cache_time = 0  # invalidate cache

_licenses_cache      = {}
_licenses_cache_time = 0
LICENSES_CACHE_TTL   = 10  # seconds

def _invalidate_licenses_cache():
    global _licenses_cache_time
    _licenses_cache_time = 0

def load_licenses():
    global _licenses_cache, _licenses_cache_time
    if time.time() - _licenses_cache_time < LICENSES_CACHE_TTL:
        return _licenses_cache
    result = {}
    for doc in _licenses_col.find():
        key = doc["_id"]
        result[key] = {k: v for k, v in doc.items() if k != "_id"}
    _licenses_cache      = result
    _licenses_cache_time = time.time()
    return result

def save_licenses(data):
    for key, entry in data.items():
        _licenses_col.update_one(
            {"_id": key},
            {"$set": entry},
            upsert=True
        )
    _invalidate_licenses_cache()

# =====================================
# STORAGE
# =====================================

commands_queue   = {}  # client_id -> list of {"command":..., "queued_at":..., "_id": mongo_id}
status_queue     = {}  # client_id -> list of {"message":..., "queued_at":..., "_id": mongo_id}
online_clients   = {}  # pair_code -> client_id (mirror of _online_col)
image_queue      = {}  # kept in memory only — images are large and cheap to regenerate
client_tokens    = {}  # client_id -> per-client auth token (mirror of _tokens_col)
_welcomed_clients = set()  # per-session; intentionally not persisted

# ─────────────────────────────────────────────────────────────
# DURABILITY: reload pending work from MongoDB after a restart
# ─────────────────────────────────────────────────────────────

def _reload_state_from_db():
    """Repopulate in-memory queues from MongoDB on boot so a server
    restart never silently drops commands or status messages."""
    try:
        _cmd_col.create_index("queued_at")
        _status_col.create_index("queued_at")
        # Commands — preserve FIFO order via queued_at
        for doc in _cmd_col.find().sort("queued_at", 1):
            commands_queue.setdefault(doc["client_id"], []).append({
                "command":   doc["command"],
                "queued_at": doc["queued_at"],
                "_id":       doc["_id"],
            })
        # Pending status messages — FIFO list per client
        for doc in _status_col.find().sort("queued_at", 1):
            status_queue.setdefault(doc["client_id"], []).append({
                "message":   doc["message"],
                "queued_at": doc["queued_at"],
                "_id":       doc["_id"],
            })
        # Online clients (pair_code -> client_id)
        for doc in _online_col.find():
            online_clients[doc["_id"]] = doc["client_id"]
        # Per-client auth tokens
        for doc in _tokens_col.find():
            client_tokens[doc["_id"]] = doc["token"]
        print(
            f"STATE RELOADED: {sum(len(v) for v in commands_queue.values())} commands, "
            f"{len(status_queue)} status, {len(online_clients)} online, "
            f"{len(client_tokens)} tokens",
            flush=True,
        )
    except Exception as e:
        print(f"STATE RELOAD ERROR: {e}", flush=True)

# ── Write-through helpers ─────────────────────────────────────

def queue_command(client_id, command):
    """Append a command to a client's queue, persisting to MongoDB."""
    queued_at = time.time()
    try:
        res = _cmd_col.insert_one({
            "client_id": client_id,
            "command":   command,
            "queued_at": queued_at,
        })
        mongo_id = res.inserted_id
    except Exception as e:
        print(f"queue_command DB error: {e}", flush=True)
        mongo_id = None
    commands_queue.setdefault(client_id, []).append({
        "command":   command,
        "queued_at": queued_at,
        "_id":       mongo_id,
    })

def _delete_cmd_doc(entry):
    mid = entry.get("_id")
    if mid is not None:
        try:
            _cmd_col.delete_one({"_id": mid})
        except Exception as e:
            print(f"cmd delete error: {e}", flush=True)

def add_status(client_id, message):
    """Append a status message (FIFO) so rapid messages never overwrite
    each other, persisting to MongoDB."""
    queued_at = time.time()
    try:
        res = _status_col.insert_one({
            "client_id": client_id,
            "message":   message,
            "queued_at": queued_at,
        })
        mongo_id = res.inserted_id
    except Exception as e:
        print(f"add_status DB error: {e}", flush=True)
        mongo_id = None
    status_queue.setdefault(client_id, []).append({
        "message":   message,
        "queued_at": queued_at,
        "_id":       mongo_id,
    })

def drain_status(client_id):
    """Pop all pending status messages for a client (in order),
    deleting their MongoDB docs. Returns a list of message strings."""
    entries = status_queue.get(client_id)
    if not entries:
        return []
    status_queue[client_id] = []
    messages = []
    for entry in entries:
        messages.append(entry["message"])
        mid = entry.get("_id")
        if mid is not None:
            try:
                _status_col.delete_one({"_id": mid})
            except Exception as e:
                print(f"status delete error: {e}", flush=True)
    return messages

def set_online(pair_code, client_id):
    online_clients[pair_code] = client_id
    try:
        _online_col.replace_one(
            {"_id": pair_code},
            {"_id": pair_code, "client_id": client_id},
            upsert=True,
        )
    except Exception as e:
        print(f"set_online DB error: {e}", flush=True)

def issue_token(client_id):
    """Return the client's existing per-client token, or mint a new one.
    Stored server-side and never derivable from the shared key, so one
    customer cannot act on another customer's client_id."""
    existing = client_tokens.get(client_id)
    if existing:
        return existing
    token = secrets.token_urlsafe(32)
    client_tokens[client_id] = token
    try:
        _tokens_col.replace_one(
            {"_id": client_id},
            {"_id": client_id, "token": token},
            upsert=True,
        )
    except Exception as e:
        print(f"issue_token DB error: {e}", flush=True)
    return token

_reload_state_from_db()

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
# SECRET KEY AUTH
# =====================================

_SECRET_KEY = os.getenv("WHALEBOTS_SECRET_KEY", "")

def verify_key(x_whalebots_key: str = Header(...)):
    if not _SECRET_KEY or x_whalebots_key != _SECRET_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")

auth = Depends(verify_key)

# =====================================
# RATE LIMITER
# =====================================

_rate_data: dict = {}  # key -> (count, window_start)
RATE_LIMIT       = 30  # max requests
RATE_WINDOW      = 60  # per N seconds

def check_rate_limit(key: str, limit: int = RATE_LIMIT):
    now   = time.time()

    # Purge stale entries to prevent unbounded memory growth
    stale = [k for k, (_, start) in _rate_data.items() if now - start > RATE_WINDOW]
    for k in stale:
        del _rate_data[k]

    entry = _rate_data.get(key)
    if entry is None or now - entry[1] > RATE_WINDOW:
        _rate_data[key] = (1, now)
        return
    count, start = entry
    if count >= limit:
        raise HTTPException(status_code=429, detail="Too many requests")
    _rate_data[key] = (count + 1, start)

def client_ip(request: Request) -> str:
    """Real client IP. Behind Railway's proxy request.client.host is the
    proxy, so prefer the first hop in X-Forwarded-For when present."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

# =====================================
# PER-CLIENT TOKEN AUTH
# =====================================

def verify_token(client_id: str, request: Request):
    """For client-specific endpoints. If this client has been issued a
    per-client token (i.e. it's running a token-aware build), require a
    matching token. Legacy clients without a token fall back to the
    shared-key check only — so the rollout tightens automatically as
    clients update, with no breakage."""
    expected = client_tokens.get(client_id)
    if expected is None:
        return  # legacy client; shared-key auth already enforced
    provided = request.headers.get("x-whalebots-token", "")
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Invalid client token")

# =====================================
# CLIENT ID VALIDATOR
# =====================================

_CLIENT_ID_RE = re.compile(r'^[\w\-\.]{1,64}$')

def validate_client_id(client_id: str):
    if not _CLIENT_ID_RE.match(client_id):
        raise HTTPException(status_code=400, detail="Invalid client_id")

# =====================================
# MODELS
# =====================================

class RegisterBody(BaseModel):
    client_id: str
    pair_code: str
    token_auth: bool = False  # new clients set this to opt into per-client tokens
    version: str = ""

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

@app.get("/version", dependencies=[auth])
def version():
    return {
        "version":  LATEST_VERSION,
        "download": EXE_DOWNLOAD_LINK,
        "sha256":   LATEST_SHA256
    }

# =====================================
# REGISTER
# =====================================

@app.post("/register", dependencies=[auth])
def register(body: RegisterBody, request: Request):
    if not body.client_id or not body.pair_code:
        return {"success": False}

    validate_client_id(body.client_id)
    check_rate_limit(f"register:{client_ip(request)}")

    set_online(body.pair_code, body.client_id)

    # Send a welcome message the first time this client connects each session
    if body.client_id not in _welcomed_clients:
        _welcomed_clients.add(body.client_id)
        add_status(body.client_id, (
            "👋 **Your Remote Control is Online!**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🖥️ PC: `{body.client_id}`\n"
            "✅ Ready to receive commands\n"
            "Type `!help` to see all available commands 🎮"
        ))

    print(f"REGISTERED: {body.client_id} ({body.pair_code}) v{body.version}")

    resp = {"success": True}
    # New (token-aware) clients receive a unique per-client token to use
    # on all subsequent client-specific requests.
    if body.token_auth:
        resp["token"] = issue_token(body.client_id)

    return resp

# =====================================
# PAIR CODE
# =====================================

@app.get("/pair_code/{client_id}", dependencies=[auth])
def get_pair_code(client_id: str, request: Request):
    validate_client_id(client_id)
    # Tight limit: the pair code is a bootstrap secret, so throttle hard
    # to make enumeration/harvesting across many client_ids impractical.
    check_rate_limit(f"pair_code:{client_ip(request)}", limit=10)
    doc = _paircodes_col.find_one({"_id": client_id})
    if doc:
        return {"pair_code": doc["pair_code"]}
    # 12 hex chars (~48 bits) instead of 6 digits — infeasible to guess,
    # still short enough to read off-screen and type into Discord.
    code = secrets.token_hex(6).upper()
    _paircodes_col.insert_one({"_id": client_id, "pair_code": code})
    return {"pair_code": code}

# =====================================
# COMMAND
# =====================================

COMMAND_TTL = 300  # discard commands older than 5 minutes

@app.get("/command/{client_id}", dependencies=[auth])
def get_command(client_id: str, request: Request):
    validate_client_id(client_id)
    verify_token(client_id, request)
    check_rate_limit(f"command:{client_id}")
    queue = commands_queue.get(client_id)

    if not queue:
        return {"command": None}

    # Discard stale commands from the front of the queue
    now = time.time()
    while queue:
        entry = queue[0]
        if now - entry["queued_at"] > COMMAND_TTL:
            queue.pop(0)
            _delete_cmd_doc(entry)
            print(f"STALE COMMAND DISCARDED [{client_id}]: {entry['command']}")
        else:
            break

    if not queue:
        return {"command": None}

    entry = queue.pop(0)
    _delete_cmd_doc(entry)
    return {"command": entry["command"]}

# =====================================
# STATUS
# =====================================

@app.post("/status/{client_id}", dependencies=[auth])
def receive_status(client_id: str, body: StatusBody, request: Request):
    validate_client_id(client_id)
    verify_token(client_id, request)
    check_rate_limit(f"status:{client_id}")
    add_status(client_id, body.message[:2000])  # cap to Discord's message limit
    return {"success": True}

@app.get("/status/{client_id}", dependencies=[auth])
def get_status(client_id: str, request: Request):
    # Kept for backward compatibility. The Discord bot now reads status
    # directly from memory (see check_status), so this is rarely used.
    validate_client_id(client_id)
    messages = drain_status(client_id)
    if not messages:
        return {"message": None}
    return {"message": "\n".join(messages)}

# =====================================
# IMAGE UPLOAD
# =====================================

MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB

@app.post("/upload/{client_id}", dependencies=[auth])
async def upload_image(
    client_id: str,
    request: Request,
    file: UploadFile = File(...)
):
    validate_client_id(client_id)
    verify_token(client_id, request)
    check_rate_limit(f"upload:{client_id}")
    content = await file.read(MAX_IMAGE_SIZE + 1)
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=413, detail="File too large")
    image_queue[client_id] = content
    return {"success": True}

# =====================================
# LICENSE VALIDATION
# =====================================

@app.post("/license/validate", dependencies=[auth])
def validate_license(body: LicenseValidateBody, request: Request):
    validate_client_id(body.client_id)
    check_rate_limit(f"license:{client_ip(request)}")
    client_id = body.client_id
    licenses  = load_licenses()
    links     = load_links()

    key, entry = None, None

    for k, e in licenses.items():
        if e.get("bound_client") == client_id:
            key, entry = k, e
            break

    if entry is None:
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

    if entry.get("bound_client") is None:
        _licenses_col.update_one({"_id": key}, {"$set": {"bound_client": client_id}})
        _invalidate_licenses_cache()

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
    print(f"Logged in as {bot.user}", flush=True)
    check_status.start()
    check_images.start()

# =====================================
# FASTAPI STARTUP
# =====================================

@app.on_event("startup")
async def startup_event():
    threading.Thread(target=start_bot, daemon=True).start()
    print("Discord bot thread started.", flush=True)

# =====================================
# STATUS LOOP
# =====================================

@tasks.loop(seconds=2)
async def check_status():
    # Reads status directly from in-memory queue (same process) — no HTTP
    # self-call, so a brief network/server hiccup can't drop a message.
    links = load_links()

    for user_id, data in links.items():
        try:
            client_id  = data["client_id"]
            channel_id = data["channel_id"]

            # Peek first — only drain if we have a channel to deliver to,
            # otherwise leave messages queued for the next tick.
            if not status_queue.get(client_id):
                continue

            channel = bot.get_channel(channel_id)
            if not channel:
                continue

            for message in drain_status(client_id):
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
                        image_queue[client_id] = None
                    except Exception as send_err:
                        print(f"check_images send error [{user_id}]: {send_err}")
                    finally:
                        os.remove(tmp_path)

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
        title="WhaleBots Control Panel",
        description="Remote control system for WhaleBots",
        color=0x00b0f4
    )

    embed.add_field(
        name="Setup",
        value="`!setup CODE` - Link your Discord account",
        inline=False
    )

    embed.add_field(
        name="Game Controls",
        value=(
            "`!rok` - Launch Rise of Kingdoms\n"
            "`!cod` - Launch Call of Dragons"
        ),
        inline=False
    )

    embed.add_field(
        name="Monitoring",
        value=(
            "`!screen bot` - Screenshot WhaleBots\n"
            "`!screen <number>` - Screenshot emulator\n"
            "Example: `!screen 1`\n\n"
            "`!tick <number>` - Toggle a specific bot\n"
            "`!tick all` - Tick all bots\n"
            "Example: `!tick 1`, `!tick all`\n\n"
            "`!log <number>` - Read activity log for a bot\n"
            "Example: `!log 1`"
        ),
        inline=False
    )

    embed.add_field(
        name="System",
        value=(
            "`!close all` - Close everything\n"
            "`!close <number>` - Close selected window\n"
            "Example: `!close 1`\n\n"
            "`!update` - Check for updates\n"
            "`!shutdown confirm` - Shutdown the PC"
        ),
        inline=False
    )

    embed.add_field(
        name="License",
        value="`!license` - Check status",
        inline=False
    )

    embed.add_field(
        name="Connected PC",
        value=f"`{connected_pc}`",
        inline=False
    )

    await ctx.send(embed=embed)

# =====================================
# HELP OWNER
# =====================================

@bot.command()
async def helpowner(ctx):
    if ctx.author.id not in OWNER_IDS:
        await ctx.send("❌ Access denied.")
        return

    embed = discord.Embed(
        title="WhaleBots Owner Panel",
        description="Owner-only commands",
        color=0xffa500
    )

    embed.add_field(
        name="License Management",
        value=(
            "`!license @user <days>` - Issue new license or extend existing\n"
            "`!license @user 0` - Issue lifetime license\n"
            "`!rmlicense @user` - Revoke a license\n"
            "`!reducelicense @user <days>` - Reduce license by X days"
        ),
        inline=False
    )

    embed.add_field(
        name="Client Overview",
        value="`!clients` - List all active subscribers",
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

    queue_command(data["client_id"], "rok")

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

    queue_command(data["client_id"], "cod")

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

    queue_command(data["client_id"], f"screen {target}")

    await ctx.send(f"📸 Taking screenshot of {target}...")

# =====================================
# LOG
# =====================================

@bot.command()
async def log(ctx, number: int):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return

    queue_command(data["client_id"], f"log {number}")

    await ctx.send(f"📋 Fetching activity log for bot {number}...")

# =====================================
# TICK
# =====================================

@bot.command()
async def tick(ctx, target: str):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return

    if target.lower() == "all":
        queue_command(data["client_id"], "tick all")
        await ctx.send("⏳ Ticking all bots...")
    else:
        try:
            number = int(target)
        except ValueError:
            await ctx.send("❌ Usage: `!tick <number>` or `!tick all`")
            return
        queue_command(data["client_id"], f"tick {number}")
        await ctx.send(f"⏳ Ticking bot {number}...")

# =====================================
# CLOSE
# =====================================

@bot.command()
async def close(ctx, target="all"):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return

    queue_command(data["client_id"], f"close {target}")

    await ctx.send(f"⏳ Closing {target}...")

# =====================================
# SHUTDOWN
# =====================================

@bot.command()
async def shutdown(ctx, confirm: str = None):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return

    if confirm != "confirm":
        await ctx.send("⚠️ **Are you sure?** Type `!shutdown confirm` to proceed.")
        return

    queue_command(data["client_id"], "shutdown")

    await ctx.send("🔴 Shutting down PC...")

# =====================================
# UPDATE
# =====================================

@bot.command()
async def update(ctx):
    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return

    queue_command(data["client_id"], "update")

    await ctx.send("🔍 Checking for updates...")

# =====================================
# LICENSE
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
            await ctx.send("❌ Only owner can issue licenses.")
            return

        licenses   = load_licenses()
        key, entry = get_license_by_discord(member.id)

        if entry is not None:
            current_expires = entry.get("expires")

            if days <= 0:
                new_expires = None
            elif current_expires:
                base = max(
                    datetime.date.fromisoformat(current_expires),
                    datetime.date.today()
                )
                new_expires = (base + datetime.timedelta(days=days)).isoformat()
            else:
                new_expires = None

            licenses[key]["expires"] = new_expires
            licenses[key]["active"]  = True
            save_licenses(licenses)

            display_expires = new_expires or "Lifetime"

            embed = discord.Embed(
                title="License Extended",
                color=0x00b04f
            )
            embed.add_field(name="User",       value=member.mention,  inline=True)
            embed.add_field(name="New Expiry", value=display_expires, inline=True)

            await ctx.send(embed=embed)
            return

        # No existing license — create a new one
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
            title="✅ License Issued",
            color=0x00b04f
        )

        embed.add_field(name="User",    value=member.mention,  inline=True)
        embed.add_field(name="Expires", value=display_expires, inline=True)

        await ctx.send(embed=embed)

        return

    # USER CHECK LICENSE

    data = get_client(ctx.author.id)

    if not data:
        await ctx.send("⚠️ Use `!setup CODE` first.")
        return

    client_id = data["client_id"]

    key, entry = get_license_info(client_id)

    if not key:
        await ctx.send("❌ No license found.")
        return

    active  = entry.get("active", False)
    expires = entry.get("expires") or "Lifetime"
    status  = "✅ Active" if active else "❌ Disabled"

    embed = discord.Embed(
        title="🔑 License Status",
        color=0x00b04f if active else 0xff0000
    )

    embed.add_field(name="Status",  value=status,           inline=True)
    embed.add_field(name="Expires", value=expires,          inline=True)
    embed.add_field(name="PC",      value=f"`{client_id}`", inline=False)

    await ctx.send(embed=embed)

# =====================================
# REVOKE LICENSE
# =====================================

@bot.command()
async def rmlicense(ctx, member: discord.Member = None):
    if ctx.author.id not in OWNER_IDS:
        await ctx.send("❌ Only owner can remove licenses.")
        return

    if member is None:
        await ctx.send("Usage: `!rmlicense @user`")
        return

    key, entry = get_license_by_discord(member.id)

    if entry is None:
        await ctx.send(f"❌ No license found for {member.mention}.")
        return

    _licenses_col.update_one({"_id": key}, {"$set": {"active": False}})
    _invalidate_licenses_cache()

    embed = discord.Embed(title="🚫 License Revoked", color=0xff0000)
    embed.add_field(name="User", value=member.mention, inline=True)
    await ctx.send(embed=embed)

# =====================================
# REDUCE LICENSE
# =====================================

@bot.command()
async def reducelicense(ctx, member: discord.Member = None, days: int = None):
    if ctx.author.id not in OWNER_IDS:
        await ctx.send("❌ Only owner can reduce licenses.")
        return

    if member is None or days is None:
        await ctx.send("Usage: `!reducelicense @user <days>`")
        return

    if days <= 0:
        await ctx.send("❌ Days must be a positive number.")
        return

    key, entry = get_license_by_discord(member.id)

    if entry is None:
        await ctx.send(f"❌ No license found for {member.mention}.")
        return

    current_expires = entry.get("expires")

    if current_expires is None:
        await ctx.send(f"❌ {member.mention} has a Lifetime license — set an expiry first with `!license @user <days>`.")
        return

    expiry_date = datetime.date.fromisoformat(current_expires)
    new_expires = (expiry_date - datetime.timedelta(days=days)).isoformat()

    _licenses_col.update_one({"_id": key}, {"$set": {"expires": new_expires}})
    _invalidate_licenses_cache()

    embed = discord.Embed(title="✂️ License Reduced", color=0xffa500)
    embed.add_field(name="User",        value=member.mention, inline=True)
    embed.add_field(name="New Expiry",  value=new_expires,    inline=True)
    await ctx.send(embed=embed)

# =====================================
# CLIENTS
# =====================================

@bot.command()
async def clients(ctx):
    if ctx.author.id not in OWNER_IDS:
        await ctx.send("❌ Only owners can view the client list.")
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

        customer = entry.get("customer", "Unknown")
        expires  = entry.get("expires")
        bound    = entry.get("bound_client") or "Not bound"

        if expires:
            expiry_date = datetime.date.fromisoformat(expires)
            days_left   = (expiry_date - today).days
            if days_left < 0:
                expiry_str = f"~~{expires}~~ (expired)"
            else:
                expiry_str = f"{expires} ({days_left}d left)"
        else:
            expiry_str = "Lifetime"

        lines.append(f"**{customer}**\n└ PC: `{bound}` | Expires: {expiry_str}")

    if not lines:
        await ctx.send("No active licenses.")
        return

    page_size = 10
    pages     = [lines[i:i + page_size] for i in range(0, len(lines), page_size)]

    for i, page in enumerate(pages, 1):
        embed = discord.Embed(
            title=f"Subscribed Clients ({len(lines)} total)" if i == 1 else f"Clients (page {i})",
            description="\n\n".join(page),
            color=0x00b0f4
        )
        await ctx.send(embed=embed)

# =====================================
# START BOT
# =====================================

def start_bot():
    import sys
    try:
        print("STARTING DISCORD BOT...", flush=True)
        bot.run(TOKEN)
    except BaseException as e:
        print(f"DISCORD BOT ERROR: {type(e).__name__}: {e}", flush=True)
        sys.stdout.flush()

# =====================================
# START EVERYTHING
# =====================================

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080))
    )
