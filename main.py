# =====================================
# IMPORTS
# =====================================

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
# COMMAND STORAGE
# =====================================

commands_queue = {}

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
# BOT READY
# =====================================

@bot.event
async def on_ready():

    print(f"Logged in as {bot.user}")

# =====================================
# HELP
# =====================================

@bot.command()
async def help(ctx):

    embed = discord.Embed(
        title="🐋 WhaleBots Remote Panel",
        description="Remote cloud control system",
        color=0x00b0f4
    )

    embed.add_field(
        name="🎮 Game Controls",
        value=(
            "`!rok <client>`\n"
            "`!cod <client>`"
        ),
        inline=False
    )

    embed.add_field(
        name="🖥️ Monitoring",
        value=(
            "`!screen <client>`\n"
            "`!screen <client> 1`\n"
            "`!tick <client> 1`\n"
            "`!close <client> all`\n"
            "`!close <client> 1`"
        ),
        inline=False
    )

    await ctx.send(embed=embed)

# =====================================
# ROK
# =====================================

@bot.command()
async def rok(ctx, client_id):

    commands_queue[client_id] = "rok"

    print(f"ROK SENT TO {client_id}")

    await ctx.send(
        f"✅ ROK sent to `{client_id}`"
    )

# =====================================
# COD
# =====================================

@bot.command()
async def cod(ctx, client_id):

    commands_queue[client_id] = "cod"

    print(f"COD SENT TO {client_id}")

    await ctx.send(
        f"✅ COD sent to `{client_id}`"
    )

# =====================================
# SCREEN
# =====================================

@bot.command()
async def screen(ctx, client_id, target="bot"):

    commands_queue[client_id] = f"screen {target}"

    print(f"SCREEN SENT TO {client_id}")

    await ctx.send(
        f"📸 Screen command sent to `{client_id}`"
    )

# =====================================
# TICK
# =====================================

@bot.command()
async def tick(ctx, client_id, number: int):

    commands_queue[client_id] = f"tick {number}"

    print(f"TICK SENT TO {client_id}")

    await ctx.send(
        f"✅ Tick command sent to `{client_id}`"
    )

# =====================================
# CLOSE
# =====================================

@bot.command()
async def close(ctx, client_id, target="all"):

    commands_queue[client_id] = f"close {target}"

    print(f"CLOSE SENT TO {client_id}")

    await ctx.send(
        f"🛑 Close command sent to `{client_id}`"
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
