import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from gsheets import update_all_sheets, get_all_worksheets, update_sheet
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

class ProfitBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.active_updates = {}  # Track active updates by channel ID

    async def setup_hook(self):
        await self.tree.sync()

    async def on_ready(self):
        print(f"Logged in as {self.user}")

bot = ProfitBot()

@bot.tree.command(name="update", description="Update profit calculations for sheets")
@app_commands.describe(sheet="Specify 'all' or a specific tab name to update")
async def update(interaction: discord.Interaction, sheet: str = "all"):
    # Check if user has admin rights
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need administrator rights to use this command.", ephemeral=True)
        return

    # Check if there's already an active update in this channel
    if interaction.channel_id in bot.active_updates:
        await interaction.response.send_message("❌ An update is already in progress in this channel. Use `/stop` to cancel it first.", ephemeral=True)
        return

    # Check if bot has permission to mention @here
    if not interaction.channel.permissions_for(interaction.guild.me).mention_everyone:
        await interaction.response.send_message("⚠️ I don't have permission to mention @here. Some notifications might not be visible.", ephemeral=True)

    await interaction.response.send_message("🔄 Starting update process...")

    try:
        # Mark this channel as having an active update
        bot.active_updates[interaction.channel_id] = True

        if sheet.lower() == "all":
            # Process all sheets
            all_profit_items = await asyncio.to_thread(update_all_sheets)
        else:
            # Process specific sheet
            worksheets = await asyncio.to_thread(get_all_worksheets)
            target_worksheet = None
            
            # Find the specified worksheet
            for ws in worksheets:
                if ws.title.lower() == sheet.lower():
                    target_worksheet = ws
                    break
            
            if not target_worksheet:
                await interaction.channel.send(f"❌ Sheet '{sheet}' not found.")
                return
                
            # Update single sheet
            all_profit_items = await asyncio.to_thread(update_sheet, target_worksheet)
            # Convert to same format as update_all_sheets
            all_profit_items = {
                'high_profit': all_profit_items['high_profit'],
                'medium_profit': all_profit_items['medium_profit'],
                'low_profit': all_profit_items['low_profit']
            }

        # Create ping messages for each threshold
        ping_messages = []
        if all_profit_items['high_profit']:
            ping_messages.append("🔴 @here HIGH PROFIT ITEMS FOUND!")
        if all_profit_items['medium_profit']:
            ping_messages.append("🟡 @here MEDIUM PROFIT ITEMS FOUND!")
        if all_profit_items['low_profit']:
            ping_messages.append("🟢 @here LOW PROFIT ITEMS FOUND!")

        # Send ping messages
        for ping in ping_messages:
            try:
                await interaction.channel.send(ping)
            except discord.Forbidden:
                await interaction.channel.send("⚠️ I don't have permission to send mentions. Some notifications might not be visible.")

        # Create and send the embed with results
        embed = discord.Embed(
            title="📊 Profit Update Results",
            color=discord.Color.blue()
        )

        if all_profit_items['high_profit']:
            embed.add_field(
                name="🔴 High Profit Items (>15%)",
                value="\n".join(all_profit_items['high_profit']),
                inline=False
            )
        if all_profit_items['medium_profit']:
            embed.add_field(
                name="🟡 Medium Profit Items (>10%)",
                value="\n".join(all_profit_items['medium_profit']),
                inline=False
            )
        if all_profit_items['low_profit']:
            embed.add_field(
                name="🟢 Low Profit Items (>£30)",
                value="\n".join(all_profit_items['low_profit']),
                inline=False
            )

        if not any(all_profit_items.values()):
            embed.add_field(
                name="No Items Found",
                value="No items met the profit thresholds.",
                inline=False
            )

        await interaction.channel.send(embed=embed)

    except Exception as e:
        await interaction.channel.send(f"❌ An error occurred: {str(e)}")
    finally:
        # Clear the active update flag
        bot.active_updates.pop(interaction.channel_id, None)

@bot.tree.command(name="stop", description="Stop the current update process")
async def stop(interaction: discord.Interaction):
    if interaction.channel_id in bot.active_updates:
        bot.active_updates.pop(interaction.channel_id)
        await interaction.response.send_message("🛑 Update process will stop after current operation completes.")
    else:
        await interaction.response.send_message("❌ No active update process to stop.", ephemeral=True)

@bot.tree.command(name="updateall", description="Update profit calculations for ALL sheets (only pings for margin > 15%)")
async def updateall(interaction: discord.Interaction):
    # Check if user has admin rights
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need administrator rights to use this command.", ephemeral=True)
        return

    # Check if there's already an active update in this channel
    if interaction.channel_id in bot.active_updates:
        await interaction.response.send_message("❌ An update is already in progress in this channel. Use `/stop` to cancel it first.", ephemeral=True)
        return

    await interaction.response.send_message("🔄 Starting update process for ALL sheets...")

    try:
        bot.active_updates[interaction.channel_id] = True
        all_profit_items = await asyncio.to_thread(update_all_sheets)
        # Only process high_profit items (profit margin > 15%)
        if all_profit_items['high_profit']:
            for item in all_profit_items['high_profit']:
                # item is a dict from gsheets.py
                embed = discord.Embed(
                    title=f"🔥 A2A Arbitrage: {item.get('brand', '')} {item.get('asin', '')}",
                    url=item.get('asin_url', ''),
                    color=discord.Color.red()
                )
                embed.add_field(name="Brand", value=f"`{item.get('brand', 'N/A')}`", inline=True)
                embed.add_field(name="ASIN", value=f"`{item.get('asin', 'N/A')}`", inline=True)
                embed.add_field(name="Profit Margin", value=f"**{item.get('profit_margin', 0)}%**", inline=True)
                embed.add_field(name="Buy Price", value=f"£{item.get('buy_price', 0)}", inline=True)
                embed.add_field(name="Sell Price", value=f"£{item.get('sell_price', 0)}", inline=True)
                embed.add_field(name="ROI", value=f"{item.get('roi', 0)}%", inline=True)
                embed.add_field(name="SPM", value=f"{item.get('spm', 'N/A')}", inline=True)
                if item.get('image_url'):
                    embed.set_image(url=item['image_url'])
                embed.set_footer(text="A2A Arbitrage Bot • FBA Optimised")
                await interaction.channel.send(content="@everyone :rotating_light: :red_circle: **BIG PROFIT MARGIN ALERT!** :red_circle: :rotating_light:", embed=embed)
        else:
            await interaction.channel.send("No high profit margin items (>15%) found.")
    except Exception as e:
        await interaction.channel.send(f"❌ An error occurred: {str(e)}")
    finally:
        bot.active_updates.pop(interaction.channel_id, None)

# Run the bot
try:
    print("Starting bot...")
    bot.run(DISCORD_TOKEN)
except Exception as e:
    print(f"Error starting bot: {e}") 