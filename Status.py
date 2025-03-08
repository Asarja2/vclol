import discord
from discord.ui import View, Button
from discord.ext import commands, tasks
from pymongo import MongoClient
from dotenv import load_dotenv
import random
import asyncio
import logging
import time
import sys
import os
import io
from flask import Flask, jsonify
from threading import Thread
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

load_dotenv()

# Set up logging to capture errors
logging.basicConfig(level=logging.ERROR, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
OWNER_ID = int(os.getenv("OWNER_ID"))  # Ensure OWNER_ID is an integer

app = Flask(__name__)

@app.route("/status")
def status():
    return "Bot is running!"

def run_flask():
    try:
        app.run(host="0.0.0.0", port=10000)  # Running Flask app on port 10000
    except Exception as e:
        logger.error(f"Error in Flask app: {e}")

def run_flask_thread():
    thread = Thread(target=run_flask)
    thread.daemon = True
    thread.start()

intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True  # Enable message content intent

# Bot initialization
bot = commands.Bot(command_prefix="!", intents=intents)

# ----- MongoDB setup -----
client = MongoClient(MONGO_URI)  # This should now work
db = client["discordBot"]
config_collection = db["serverConfigs"]

# ----- HELPER FUNCTIONS -----
def get_server_config(guild_id):
    try:
        return config_collection.find_one({"guild_id": guild_id})
    except Exception as e:
        logger.error(f"Error fetching server config for guild {guild_id}: {e}")
        return None

async def update_server_config(guild_id, role=None, status=None):
    try:
        update = {}
        if role:
            update["role_id"] = role.id  # Save the role ID
        if status:
            update["status"] = status
        config_collection.update_one(
            {"guild_id": guild_id},
            {"$set": update},
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error updating server config for guild {guild_id}: {e}")

async def delete_server_config(guild_id):
    try:
        config_collection.delete_one({"guild_id": guild_id})
    except Exception as e:
        logger.error(f"Error deleting server config for guild {guild_id}: {e}")

# ----- EMBED FUNCTION -----
def create_embed(title, description, color=discord.Color.blurple(), fields=None):
    embed = discord.Embed(title=title, description=description, color=color)
    if fields:
        for name, value in fields.items():
            embed.add_field(name=name, value=value, inline=False)
    return embed

# ----- CUSTOM SELECT -----
class ServerSelect(discord.ui.Select):
    def __init__(self, guilds):
        options = [
            discord.SelectOption(
                label=guild.name, 
                value=str(guild.id),
                description=f"Status: {get_server_config(guild.id).get('status', 'No status set')}"
            )
            for guild in guilds
            if get_server_config(guild.id)
        ]
        super().__init__(placeholder="Choose a server to manage", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        try:
            selected_guild_id = int(self.values[0])
            selected_guild = discord.utils.get(bot.guilds, id=selected_guild_id)
            config = get_server_config(selected_guild.id)
            embed = create_embed(
                title=f"Selected Server: {selected_guild.name}",
                description=f"Configuration for {selected_guild.name}:",
                fields={"Status": config.get('status') if config else "Not Configured"}
            )
            await interaction.response.send_message(embed=embed)

            # Add delete button
            delete_button = Button(label="Delete Config", style=discord.ButtonStyle.danger)

            async def delete_button_callback(interaction: discord.Interaction):
                await delete_server_config(selected_guild.id)
                await interaction.response.send_message(f"Configuration for **{selected_guild.name}** has been deleted.", ephemeral=True)

            delete_button.callback = delete_button_callback

            # Add invite button
            invite_button = Button(label="Generate Server Invite", style=discord.ButtonStyle.primary)

            async def invite_button_callback(interaction: discord.Interaction):
                # Get the default channel (first available text channel)
                default_channel = selected_guild.text_channels[0] if selected_guild.text_channels else None
                if default_channel:
                    invite = await default_channel.create_invite(max_age=86400, max_uses=1, unique=True)
                    await interaction.response.send_message(f"Here is your invite link for the server {selected_guild.name}: {invite.url}", ephemeral=True)
                else:
                    await interaction.response.send_message("No text channels available to create an invite link.", ephemeral=True)

            invite_button.callback = invite_button_callback

            view = View()
            view.add_item(delete_button)
            view.add_item(invite_button)
            await interaction.followup.send(view=view)
        except Exception as e:
            logger.error(f"Error handling server selection: {e}")
            await interaction.response.send_message("An error occurred while processing your request.", ephemeral=True)

# ----- SLASH COMMANDS -----
@bot.tree.command(name="listservers", description="List all servers with configurations and allow deletion.")
async def listservers(interaction: discord.Interaction):
    """Owner-only command to list all servers with configurations and allow deletion."""
    try:
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("You are not the bot owner and cannot use this command.", ephemeral=True)

        guilds = bot.guilds
        if not guilds:
            return await interaction.response.send_message("The bot is not in any servers.", ephemeral=True)

        view = View()
        select = ServerSelect(guilds)
        view.add_item(select)

        await interaction.response.send_message("Choose a server to manage:", view=view)
    except Exception as e:
        logger.error(f"Error listing servers: {e}")
        await interaction.response.send_message("An error occurred while processing your request.", ephemeral=True)

# Set server configuration
@bot.tree.command(name="setconfig", description="Set the role and status configuration for the server.")
async def setconfig(interaction: discord.Interaction, role: discord.Role, status: str):
    """Slash command to set configuration (role and status) for the server.""" 
    try:
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("You need admin permissions to use this command.", ephemeral=True)

        await update_server_config(interaction.guild.id, role=role, status=status)

        role_mention = role.mention  # Use role mention directly
        embed = create_embed(
            title="Configuration Updated",
            description=f"Server configuration has been updated successfully.",
            color=discord.Color.blurple(),  # Color is set to blackish blurple
            fields={
                "Role": role_mention,  # Mention the role
                "Status": status
            }
        )
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        logger.error(f"Error setting config for guild {interaction.guild.id}: {e}")
        await interaction.response.send_message("An error occurred while updating the configuration.", ephemeral=True)

@bot.tree.command(name="viewconfig", description="View the current configuration for the server.")
async def viewconfig(interaction: discord.Interaction):
    """Slash command to view current configuration for the server.""" 
    try:
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("You need admin permissions to view the configuration.", ephemeral=True)

        config = get_server_config(interaction.guild.id)
        if config:
            role = discord.utils.get(interaction.guild.roles, id=config.get("role_id"))
            role_mention = role.mention if role else "Role not found"
            embed = create_embed(
                title="Current Server Configuration",
                description="Here are the current settings for your server.",
                color=discord.Color.blurple(),  # Color is set to blackish blurple
                fields={
                    "Role": role_mention,  # Mention the role
                    "Status": config.get('status')
                }
            )
            await interaction.response.send_message(embed=embed)
        else:
            embed = create_embed(
                title="No Configuration Set",
                description="There is no configuration set for this server.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)
    except Exception as e:
        logger.error(f"Error viewing config for guild {interaction.guild.id}: {e}")
        await interaction.response.send_message("An error occurred while retrieving the configuration.", ephemeral=True)

# Syncing the tree (for slash commands)
@bot.event
async def on_ready():
    try:
        await bot.tree.sync()  # Sync slash commands across guilds
        print(f'Logged in as {bot.user}')
        # Start the status checking loop
        bot.loop.create_task(check_statuses())
        bot.loop.create_task(changepresence())  # Add the presence change task here
    except Exception as e:
        logger.error(f"Error during bot setup: {e}")

# ----- PRESENCE UPDATE HANDLING ----- 
@tasks.loop(seconds=5)  # How often the bot should change status
async def changepresence():
    while not bot.is_closed():
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="your status"))
        await asyncio.sleep(5)  # Wait for 5 seconds

        total_member_count = sum(len(guild.members) for guild in bot.guilds)  # Calculate the total member count
        await bot.change_presence(activity=discord.Game(name=f'with {total_member_count} members'))  # Change status to Member count
        await asyncio.sleep(5)  # Wait for 5 seconds before switching again


async def check_statuses():
    """Check user statuses every second and assign roles if the status is in the user's activity."""
    await bot.wait_until_ready()  # Ensure the bot is fully ready before starting the loop
    while not bot.is_closed():
        try:
            for guild in bot.guilds:
                config = get_server_config(guild.id)
                if not config:
                    continue  # Skip if no config

                status = config.get("status")
                if not status:
                    continue  # Skip if no status set

                for member in guild.members:
                    if member.bot:
                        continue  # Skip bots

                    custom_status = None
                    for activity in member.activities:
                        if isinstance(activity, discord.CustomActivity) and activity.name:
                            custom_status = activity.name
                            break

                    # Check if status is in the user's activity (case-insensitive)
                    if custom_status and status.lower() in custom_status.lower():
                        role = discord.utils.get(guild.roles, id=config.get("role_id"))
                        if role and role not in member.roles:
                            try:
                                await member.add_roles(role)
                                print(f"Assigned role {role.name} to {member.name} for matching status: {custom_status}.")
                            except discord.errors.Forbidden:
                                logger.error(f"Could not assign role {role.name} to {member.name} due to insufficient permissions.")
                    else:
                        # If status doesn't contain the keyword, remove the role if assigned
                        role = discord.utils.get(guild.roles, id=config.get("role_id"))
                        if role and role in member.roles:
                            try:
                                await member.remove_roles(role)
                                print(f"Removed role {role.name} from {member.name} as status not found.")
                            except discord.errors.Forbidden:
                                logger.error(f"Could not remove role {role.name} from {member.name} due to insufficient permissions.")
            await asyncio.sleep(1)  # Wait for 1 second before checking again
        except Exception as e:
            logger.error(f"Error in status checking loop: {e}")
            await asyncio.sleep(5)  # Wait for a bit before retrying

# Main function to handle crash recovery
def run_bot():
    try:
        bot.run(BOT_TOKEN)
    except Exception as e:
        logger.error(f"Bot encountered a critical error: {e}")
        time.sleep(10)  # Wait a few seconds before restarting
        run_bot()  # Restart bot on crash

# Running Flask app in a separate thread
run_flask_thread()

run_bot()
