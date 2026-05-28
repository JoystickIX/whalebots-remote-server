
# =====================================
# IMPORTS
# =====================================

import discord
from discord.ext import commands, tasks
from fastapi import FastAPI
import threading
import uvicorn
import os
import json
import requests

# =====================================
# TOKEN
# =====================================

TOKEN = os.getenv("DISCORD_TOKEN")

# =====================================
# FILES
# =====================================

LINKS_FILE = "links.json"

# =====================================
# CREATE LINKS FILE
# =====================================

if not os.path.exists(LINKS_FILE):

    with open(LINKS_FILE, "w") as f:

        json.dump({}, f, indent=4)

# =====================================
# LOAD LINKS
# =====================================

def load_links():

    with open(LINKS_FILE, "r") as f:

        return json.load(f)

# =====================================
# SAVE LINKS
# =====================================

def save_links(data):

    with open(LINKS_FILE, "w") as f:

        json.dump(data, f, indent=4)

# =====================================
# STORAGE
# =====================================

commands_queue = {}
status_queue = {}
online_clients = {}

# =====================================
# DISCORD SETTINGS
# =====================================

intents = discord.Intents.default()

intents.message_content = True
intents.guilds = True
intents.members = True

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

    return {
        "status": "WhaleBots Server Online"
    }

# =====================================
# REGISTER CLIENT
# =====================================

@app.post("/register")
def register(data: dict):

    client_id = data.get("client_id")
    pair_code = data.get("pair_code")

    if not client_id or not pair_code:

        return {
            "success": False
        }

    online_clients[pair_code] = client_id

    print(f"REGISTERED: {client_id} ({pair_code})")

    return {
        "success": True
    }

# =====================================
# GET COMMAND
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
# SEND STATUS
# =====================================

@app.post("/status/{client_id}")
def send_status(client_id: str, data: dict):

    status_queue[client_id] = data.get("message")

    return {
        "success": True
    }

# =====================================
# GET STATUS
# =====================================

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
# BOT READY
# =====================================

@bot.event
async def on_ready():

    print(f"Logged in as {bot.user}")

    check_status.start()

# =====================================
# STATUS CHECKER
# =====================================

@tasks.loop(seconds=2)
async def check_status():

    links = load_links()

    for user_id, data in links.items():

        try:

            client_id = data["client_id"]
            channel_id = data["channel_id"]

            response = requests.get(
                f"https://whalebots-remote-server.onrender.com/status/{client_id}"
            )

            result = response.json()

            message = result.get("message")

            if message:

                channel = bot.get_channel(channel_id)

                if channel:

                    await channel.send(message)

        except Exception as e:

            print(e)

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
        name="Connected PC",
        value=f"`{connected_pc}`",
        inline=False
    )

    embed.set_footer(
        text="WhaleBots Remote System"
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
# GET CLIENT
# =====================================

def get_client(user_id):

    links = load_links()

    return links.get(str(user_id))

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
        f"⏳ Checking bot {number}..."
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
# PING
# =====================================

@bot.command()
async def ping(ctx):

    await ctx.send("🏓 Pong!")

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
