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
