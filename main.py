import discord
from discord.ext import commands
from fastapi import FastAPI
import threading
import uvicorn
import os

# =====================================
# TOKEN
# =====================================

TOKEN = os.getenv("DISCORD_TOKEN")

# =====================================
# DISCORD BOT
# =====================================

intents = discord.Intents.default()
intents.message_content = True

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
# DISCORD EVENTS
# =====================================

@bot.event
async def on_ready():

    print(f"Logged in as {bot.user}")

# =====================================
# TEST COMMAND
# =====================================

@bot.command()
async def ping(ctx):

    await ctx.send("🏓 Pong!")

# =====================================
# START DISCORD BOT
# =====================================

def start_bot():

    bot.run(TOKEN)

# =====================================
# START EVERYTHING
# =====================================

threading.Thread(
    target=start_bot
).start()

uvicorn.run(
    app,
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 10000))
)
# =====================================
# COMMAND STORAGE
# =====================================

commands_queue = {}
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
# ROK COMMAND
# =====================================

@bot.command()
async def ROK(ctx, client_id):

    commands_queue[client_id] = "ROK"

    await ctx.send(
        f"ROK sent to {client_id}"
    )
    # =====================================
# COD COMMAND
# =====================================

@bot.command()
async def COD(ctx, client_id):

    commands_queue[client_id] = "COD"

    await ctx.send(
        f"COD sent to {client_id}"
    )
