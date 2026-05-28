=====================================
IMPORTS
=====================================

import discord
from discord.ext import commands
from fastapi import FastAPI
import threading
import uvicorn
import os
import json

=====================================
TOKEN
=====================================

TOKEN = os.getenv("DISCORD_TOKEN")

=====================================
FILES
=====================================

LINKS_FILE = "links.json"

=====================================
CREATE LINKS FILE
=====================================

if not os.path.exists(LINKS_FILE):

with open(LINKS_FILE, "w") as f:

    json.dump({}, f, indent=4)
=====================================
LOAD LINKS
=====================================

def load_links():

with open(LINKS_FILE, "r") as f:

    return json.load(f)
=====================================
SAVE LINKS
=====================================

def save_links(data):

with open(LINKS_FILE, "w") as f:

    json.dump(data, f, indent=4)
=====================================
COMMAND STORAGE
=====================================

commands_queue = {}

=====================================
DISCORD SETTINGS
=====================================

intents = discord.Intents.default()

intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(
command_prefix="!",
intents=intents,
help_command=None
)

=====================================
FASTAPI
=====================================

app = FastAPI()

@app.get("/")
def home():

return {
    "status": "WhaleBots Server Online"
}
=====================================
GET COMMAND
=====================================

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
=====================================
BOT READY
=====================================

@bot.event
async def on_ready():

print(f"Logged in as {bot.user}")
=====================================
HELP
=====================================

@bot.command()
async def help(ctx):

embed = discord.Embed(
    title="🐋 WhaleBots Remote Panel",
    description="Remote cloud control system",
    color=0x00b0f4
)

embed.add_field(
    name="⚙️ Setup",
    value="`!setup`",
    inline=False
)

embed.add_field(
    name="🎮 Game Controls",
    value=(
        "`!rok`\n"
        "`!cod`"
    ),
    inline=False
)

embed.add_field(
    name="🖥️ Monitoring",
    value=(
        "`!screen`\n"
        "`!tick 1`\n"
        "`!close all`"
    ),
    inline=False
)

await ctx.send(embed=embed)
=====================================
SETUP
=====================================

@bot.command()
async def setup(ctx):

user_id = str(ctx.author.id)

links = load_links()

links[user_id] = ctx.author.name

save_links(links)

await ctx.send(
    f"✅ Linked to `{ctx.author.name}`"
)
=====================================
GET LINKED CLIENT
=====================================

def get_client_id(user_id):

links = load_links()

return links.get(str(user_id))
=====================================
ROK
=====================================

@bot.command()
async def rok(ctx):

client_id = get_client_id(ctx.author.id)

if not client_id:

    await ctx.send(
        "⚠️ Not linked.\nUse `!setup` first."
    )

    return

commands_queue[client_id] = "rok"

print(f"ROK SENT TO {client_id}")

await ctx.send(
    "✅ ROK launched."
)
=====================================
COD
=====================================

@bot.command()
async def cod(ctx):

client_id = get_client_id(ctx.author.id)

if not client_id:

    await ctx.send(
        "⚠️ Not linked.\nUse `!setup` first."
    )

    return

commands_queue[client_id] = "cod"

print(f"COD SENT TO {client_id}")

await ctx.send(
    "✅ COD launched."
)
=====================================
SCREEN
=====================================

@bot.command()
async def screen(ctx):

client_id = get_client_id(ctx.author.id)

if not client_id:

    await ctx.send(
        "⚠️ Not linked.\nUse `!setup` first."
    )

    return

commands_queue[client_id] = "screen bot"

print(f"SCREEN SENT TO {client_id}")

await ctx.send(
    "📸 Screenshot requested."
)
=====================================
TICK
=====================================

@bot.command()
async def tick(ctx, number: int):

client_id = get_client_id(ctx.author.id)

if not client_id:

    await ctx.send(
        "⚠️ Not linked.\nUse `!setup` first."
    )

    return

commands_queue[client_id] = f"tick {number}"

print(f"TICK SENT TO {client_id}")

await ctx.send(
    f"✅ Tick sent: {number}"
)
=====================================
CLOSE
=====================================

@bot.command()
async def close(ctx, target="all"):

client_id = get_client_id(ctx.author.id)

if not client_id:

    await ctx.send(
        "⚠️ Not linked.\nUse `!setup` first."
    )

    return

commands_queue[client_id] = f"close {target}"

print(f"CLOSE SENT TO {client_id}")

await ctx.send(
    "🛑 Close command sent."
)
=====================================
PING
=====================================

@bot.command()
async def ping(ctx):

await ctx.send("🏓 Pong!")
=====================================
START BOT
=====================================

def start_bot():

try:

    print("STARTING DISCORD BOT...")

    bot.run(TOKEN)

except Exception as e:

    print("DISCORD BOT ERROR:")
    print(e)
=====================================
START EVERYTHING
=====================================

if name == "main":

threading.Thread(
    target=start_bot,
    daemon=True
).start()

uvicorn.run(
    app,
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 10000))
)
