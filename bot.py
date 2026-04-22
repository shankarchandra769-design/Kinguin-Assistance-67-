import discord
from discord.ext import commands, tasks
import logging
import time
import json
import os
from keep_alive import keep_alive
import asyncio
from collections import defaultdict

# ── Config ──────────────────────────────────────────────────────────────────
intents = discord.Intents.all()

# Use larger cache sizes and smart http settings to reduce API calls
bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    max_messages=1000,        # Cache more messages locally = fewer API calls
)

# ── Cooldown tracker — prevents spam which causes rate limits ────────────────
user_cooldowns = defaultdict(lambda: 0)
COOLDOWN_SECONDS = 2  # minimum seconds between commands per user

CONFIG_FILE = "config.json"
TICKETS_FILE = "tickets.json"

# ── Helpers ──────────────────────────────────────────────────────────────────
def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def get_config():
    return load_json(CONFIG_FILE, {
        "sendmsg_roles": [],
        "ticket_support_roles": [],
        "ticket_free_roles": [],
        "ticket_options": [],
        "ticket_panel_channel": None,
        "ticket_category": None,
        "role_button_role": None,
        "cooked_rich_role": None,
        "ticket_panel_title": "REQUEST A MIDDLE MAN",
        "ticket_panel_description": (
            "Click below to create a middle man ticket 🍕\n\n"
            "**Middleman Request - Roblox Trade**\n"
            "To request a middleman from this server, click the blue \"Request Middleman\" button on this message.\n\n"
            "**How does middleman work?:**\n"
            "Example: Trade is 500m/s Dragon Cannelloni for Robux.\n\n"
            "Seller gives 500m/s Dragon Cannelloni to middleman\n\n"
            "Buyer pays seller robux (After middleman confirms receiving Dragon Cannelloni)\n\n"
            "Middleman gives buyer 500m/s Dragon Cannelloni (After seller confirmed receiving robux)\n\n"
            "**NOTES:**\n"
            "You must both agree on the deal before using a middleman. Troll tickets will have consequences.\n\n"
            "Specify what you're trading (e.g. FR Frost Dragon in Adopt me > $20 USD LTC).\n\n"
            "Don't just put \"adopt me\" in the embed."
        ),
    })

def save_config(cfg):
    save_json(CONFIG_FILE, cfg)

def get_tickets():
    return load_json(TICKETS_FILE, {})

def save_tickets(t):
    save_json(TICKETS_FILE, t)

def has_any_role(member, role_ids):
    """Returns True if member:
    - Has one of the exact roles
    - Has a role higher than any of the given roles
    - Is an administrator
    """
    # Admins always pass
    if member.guild_permissions.administrator:
        return True

    if not role_ids:
        return False

    # Get the highest position among the target roles
    guild_roles = {r.id: r for r in member.guild.roles}
    target_positions = [
        guild_roles[rid].position
        for rid in role_ids
        if rid in guild_roles
    ]

    if not target_positions:
        return False

    highest_target = max(target_positions)

    # Check if member has any of the exact roles OR any role higher than the highest target role
    for r in member.roles:
        if r.id in role_ids:
            return True
        if r.position > highest_target:
            return True

    return False

def embed(title, description, color=0x5865F2, footer=None):
    e = discord.Embed(title=title, description=description, color=color)
    if footer:
        e.set_footer(text=footer)
    return e

# ══════════════════════════════════════════════════════════════════════════════
#  VIEWS / BUTTONS
# ══════════════════════════════════════════════════════════════════════════════

class TicketPanelView(discord.ui.View):
    def __init__(self, options):
        super().__init__(timeout=None)
        for opt in options:
            self.add_item(TicketOptionButton(opt["label"], opt["emoji"]))

class TicketOptionButton(discord.ui.Button):
    def __init__(self, label, emoji=None):
        super().__init__(
            label=label,
            emoji=emoji if emoji else None,
            style=discord.ButtonStyle.primary,
            custom_id=f"ticket_opt_{label}"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TicketFormModal(self.label))


class TicketFormModal(discord.ui.Modal, title="🎫 Create a Ticket"):
    trade = discord.ui.TextInput(
        label="What is the trade?",
        style=discord.TextStyle.paragraph,
        placeholder="Describe your trade here...",
        required=True
    )
    user_id = discord.ui.TextInput(
        label="@user or User ID of the other person",
        placeholder="@username or 123456789012345678",
        required=True
    )
    can_join_ps = discord.ui.TextInput(
        label="Can you join PS?",
        placeholder="Yes / No",
        required=True
    )

    def __init__(self, option_label):
        super().__init__()
        self.option_label = option_label

    async def on_submit(self, interaction: discord.Interaction):
        cfg = get_config()
        guild = interaction.guild
        member = interaction.user

        # Resolve the other user
        raw = self.user_id.value.strip()
        other_member = None
        user_not_in_server = False

        # Try to extract a user ID from mention or raw ID
        cleaned = raw.lstrip("<@!>").rstrip(">").replace("<@", "").replace("!", "").replace(">", "").strip()
        try:
            uid = int(cleaned)
            # It's a valid ID or mention — try to find them
            other_member = guild.get_member(uid) or await guild.fetch_member(uid)
            if other_member is None:
                user_not_in_server = True
        except ValueError:
            # Not a number/mention — user just typed a name, don't show error
            pass
        except discord.NotFound:
            # Valid ID format but user not in server
            user_not_in_server = True
        except Exception:
            pass

        # Create ticket channel
        category = None
        if cfg.get("ticket_category"):
            category = guild.get_channel(int(cfg["ticket_category"]))

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        if other_member:
            overwrites[other_member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        # Free roles (can see & msg without claiming)
        for rid in cfg.get("ticket_free_roles", []):
            role = guild.get_role(int(rid))
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True, use_application_commands=True
                )

        # Support roles — view only until claimed
        for rid in cfg.get("ticket_support_roles", []):
            role = guild.get_role(int(rid))
            if role and role not in overwrites:
                overwrites[role] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=False, use_application_commands=False
                )

        channel = await guild.create_text_channel(
            name=f"ticket-{member.name}",
            category=category,
            overwrites=overwrites
        )

        # Save ticket data
        tickets = get_tickets()
        tickets[str(channel.id)] = {
            "creator_id": str(member.id),
            "other_user_id": str(other_member.id) if other_member else None,
            "option": self.option_label,
            "trade": self.trade.value,
            "user_field": self.user_id.value,
            "can_join_ps": self.can_join_ps.value,
            "claimed_by": None,
            "confirm_users": []
        }
        save_tickets(tickets)

        # Build ticket embed
        if other_member:
            other_display = other_member.mention
        elif user_not_in_server:
            other_display = f"⚠️ User not found in server (`{self.user_id.value}`)"
        else:
            other_display = self.user_id.value  # just show what they typed
        ticket_embed = discord.Embed(
            title=f"🎫 Ticket — {self.option_label}",
            color=0x5865F2
        )
        ticket_embed.add_field(name="📦 Trade", value=self.trade.value, inline=False)
        ticket_embed.add_field(name="👤 Other User", value=other_display, inline=True)
        ticket_embed.add_field(name="🎮 Can Join PS?", value=self.can_join_ps.value, inline=True)
        ticket_embed.add_field(name="🙋 Opened by", value=member.mention, inline=False)
        ticket_embed.set_footer(text="Use !claim to claim this ticket | !close to close it")

        # Ping support roles
        pings = " ".join(
            guild.get_role(int(rid)).mention
            for rid in cfg.get("ticket_support_roles", [])
            if guild.get_role(int(rid))
        )

        await channel.send(
            content=pings if pings else None,
            embed=ticket_embed,
            view=TicketActionsView()
        )

        await interaction.response.send_message(
            embed=embed("✅ Ticket Created", f"Your ticket has been opened: {channel.mention}", color=0x57F287),
            ephemeral=True
        )


class TicketActionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.success, emoji="✋", custom_id="claim_ticket")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_config()
        if not has_any_role(interaction.user, [int(r) for r in cfg.get("ticket_support_roles", [])]):
            await interaction.response.send_message(
                embed=embed("❌ No Permission", "Only support roles can claim tickets.", color=0xED4245),
                ephemeral=True
            )
            return

        tickets = get_tickets()
        tid = str(interaction.channel.id)
        if tid not in tickets:
            await interaction.response.send_message(embed=embed("❌ Error", "Ticket data not found.", color=0xED4245), ephemeral=True)
            return

        if tickets[tid]["claimed_by"]:
            claimer = interaction.guild.get_member(int(tickets[tid]["claimed_by"]))
            await interaction.response.send_message(
                embed=embed("⚠️ Already Claimed", f"This ticket is already claimed by {claimer.mention if claimer else 'someone'}.", color=0xFEE75C),
                ephemeral=True
            )
            return

        tickets[tid]["claimed_by"] = str(interaction.user.id)
        save_tickets(tickets)

        # Grant send perms to claimer
        await interaction.channel.set_permissions(
            interaction.user,
            read_messages=True, send_messages=True, use_application_commands=True
        )

        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"
        await interaction.message.edit(view=self)

        await interaction.response.send_message(
            embed=embed("✅ Ticket Claimed", f"{interaction.user.mention} has claimed this ticket!", color=0x57F287)
        )

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="close_ticket_btn")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_config()
        allowed = [int(r) for r in cfg.get("ticket_support_roles", [])]
        if not has_any_role(interaction.user, allowed):
            await interaction.response.send_message(
                embed=embed("❌ No Permission", "Only support roles can close tickets.", color=0xED4245),
                ephemeral=True
            )
            return
        await interaction.response.defer()
        await close_ticket_channel(interaction.channel, interaction.user)


class ConfirmTradeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success, custom_id="confirm_trade")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        tickets = get_tickets()
        tid = str(interaction.channel.id)
        if tid not in tickets:
            await interaction.response.send_message(embed=embed("❌ Error", "Ticket not found.", color=0xED4245), ephemeral=True)
            return

        uid = str(interaction.user.id)
        if uid in tickets[tid]["confirm_users"]:
            await interaction.response.send_message(
                embed=embed("⚠️ Already Confirmed", "You already confirmed the trade.", color=0xFEE75C),
                ephemeral=True
            )
            return

        tickets[tid]["confirm_users"].append(uid)
        save_tickets(tickets)

        await interaction.response.send_message(
            f"✅ {interaction.user.mention} **confirmed the trade!**"
        )


class MMInfoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ I Understood", style=discord.ButtonStyle.success, custom_id="mm_understood")
    async def understood(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"✅ {interaction.user.mention} **understood!**"
        )


class RoleButtonView(discord.ui.View):
    def __init__(self, button_label, role_id):
        super().__init__(timeout=None)
        self.add_item(RoleClaimButton(button_label, role_id))


class RoleClaimButton(discord.ui.Button):
    def __init__(self, label, role_id):
        super().__init__(label=label, style=discord.ButtonStyle.primary, custom_id=f"rolebtn_{role_id}")
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(int(self.role_id))
        if not role:
            await interaction.response.send_message(embed=embed("❌ Error", "Role not found.", color=0xED4245), ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.response.send_message(embed=embed("⚠️", f"You already have {role.mention}.", color=0xFEE75C), ephemeral=True)
            return
        await interaction.user.add_roles(role)
        await interaction.response.send_message(
            f"✅ {interaction.user.mention} has been given the role {role.mention}!"
        )


class CookedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="💰 I Wanna Be Rich", style=discord.ButtonStyle.success, custom_id="cooked_rich")
    async def rich_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_config()
        rich_role_id = cfg.get("cooked_rich_role")
        if rich_role_id:
            role = interaction.guild.get_role(int(rich_role_id))
            if role:
                try:
                    await interaction.user.add_roles(role)
                except Exception:
                    pass
        await interaction.response.send_message(
            f"{interaction.user.mention} choose to be rich 💰",
            allowed_mentions=discord.AllowedMentions(users=True)
        )

    @discord.ui.button(label="😴 I Wanna Stay Pooron", style=discord.ButtonStyle.danger, custom_id="cooked_poor")
    async def poor_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"{interaction.user.mention} choose to stay pooron 😴",
            allowed_mentions=discord.AllowedMentions(users=True)
        )




async def close_ticket_channel(channel, closer):
    tickets = get_tickets()
    tid = str(channel.id)
    if tid in tickets:
        del tickets[tid]
        save_tickets(tickets)
    close_embed = discord.Embed(
        title="🔒 Ticket Closing",
        description=f"Closed by {closer.mention}. Channel will be deleted in 5 seconds.",
        color=0xED4245
    )
    await channel.send(embed=close_embed)
    await asyncio.sleep(5)
    await channel.delete()


# ══════════════════════════════════════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")
    # Re-register persistent views
    bot.add_view(TicketActionsView())
    bot.add_view(ConfirmTradeView())
    bot.add_view(MMInfoView())
    bot.add_view(CookedView())
    # Reload ticket option buttons
    cfg = get_config()
    if cfg.get("ticket_options"):
        bot.add_view(TicketPanelView(cfg["ticket_options"]))


@bot.event
async def on_message(message):
    # Ignore bots
    if message.author.bot:
        return

    # Global per-user cooldown to prevent spam → rate limits
    uid = message.author.id
    now = time.time()
    if now - user_cooldowns[uid] < COOLDOWN_SECONDS:
        return  # silently ignore — too fast
    user_cooldowns[uid] = now

    await bot.process_commands(message)


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

# ── !sendmsg <channel_id> <message> [attach image] ──────────────────────────
@bot.command(name="sendmsg")
async def sendmsg(ctx, channel_id: str = None, *, message: str = None):
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("sendmsg_roles", [])]
    if not has_any_role(ctx.author, allowed):
        await ctx.send(embed=embed("❌ No Permission", "You don't have permission to use this command.", color=0xED4245))
        return
    if not channel_id or not message:
        await ctx.send(embed=embed("❌ Usage", "`!sendmsg <channel_id> <message>` (optionally attach an image)", color=0xED4245))
        return
    try:
        ch = ctx.guild.get_channel(int(channel_id))
        if not ch:
            await ctx.send(embed=embed("❌ Error", "Channel not found.", color=0xED4245))
            return

        msg_embed = embed("📢 Message", message)

        # If an image is attached, use the first one
        file_to_send = None
        if ctx.message.attachments:
            attachment = ctx.message.attachments[0]
            if any(attachment.filename.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]):
                file_bytes = await attachment.read()
                import io
                file_to_send = discord.File(io.BytesIO(file_bytes), filename=attachment.filename)
                msg_embed.set_image(url=f"attachment://{attachment.filename}")

        if file_to_send:
            await ch.send(embed=msg_embed, file=file_to_send)
        else:
            await ch.send(embed=msg_embed)

        await ctx.send(embed=embed("✅ Sent", f"Message sent to {ch.mention}.", color=0x57F287))
    except Exception as e:
        await ctx.send(embed=embed("❌ Error", str(e), color=0xED4245))


# ── !setsendrole <role_id> ───────────────────────────────────────────────────
@bot.command(name="setsendrole")
@commands.has_permissions(administrator=True)
async def setsendrole(ctx, role_id: str):
    cfg = get_config()
    if role_id not in cfg["sendmsg_roles"]:
        cfg["sendmsg_roles"].append(role_id)
        save_config(cfg)
    await ctx.send(embed=embed("✅ Updated", f"Role `{role_id}` can now use `!sendmsg`.", color=0x57F287))


# ── !setsupportrole <role_id> ────────────────────────────────────────────────
@bot.command(name="setsupportrole")
@commands.has_permissions(administrator=True)
async def setsupportrole(ctx, role_id: str):
    cfg = get_config()
    if role_id not in cfg["ticket_support_roles"]:
        cfg["ticket_support_roles"].append(role_id)
        save_config(cfg)
    await ctx.send(embed=embed("✅ Updated", f"Role `{role_id}` is now a ticket support role.", color=0x57F287))


# ── !setfreerole <role_id> ───────────────────────────────────────────────────
@bot.command(name="setfreerole")
@commands.has_permissions(administrator=True)
async def setfreerole(ctx, role_id: str):
    """Role that can message in tickets WITHOUT claiming"""
    cfg = get_config()
    if role_id not in cfg["ticket_free_roles"]:
        cfg["ticket_free_roles"].append(role_id)
        save_config(cfg)
    await ctx.send(embed=embed("✅ Updated", f"Role `{role_id}` can now message in tickets without claiming.", color=0x57F287))


# ── !setpanelchannel <channel_id> ────────────────────────────────────────────
@bot.command(name="setpanelchannel")
@commands.has_permissions(administrator=True)
async def setpanelchannel(ctx, channel_id: str):
    """Set the channel where !ticketpanel will always send the panel."""
    ch = ctx.guild.get_channel(int(channel_id))
    if not ch:
        await ctx.send(embed=embed("❌ Error", "Channel not found.", color=0xED4245))
        return
    cfg = get_config()
    cfg["ticket_panel_channel"] = channel_id
    save_config(cfg)
    await ctx.send(embed=embed("✅ Panel Channel Set", f"Ticket panel will be sent to {ch.mention} when you use `!ticketpanel`.", color=0x57F287))


# ── !ticketpanel [image_url] ─────────────────────────────────────────────────
# Attach an image directly OR pass a URL — both work.
# Sends to the channel set with !setpanelchannel, or the current channel if not set.
@bot.command(name="ticketpanel")
@commands.has_permissions(administrator=True)
async def ticketpanel(ctx, image_url: str = None):
    cfg = get_config()
    options = cfg.get("ticket_options", [])
    if not options:
        await ctx.send(embed=embed("❌ No Options", "Add ticket options first with `!addticketoption <label> [emoji]`.", color=0xED4245))
        return

    # Determine target channel
    target_channel = ctx.channel
    panel_ch_id = cfg.get("ticket_panel_channel")
    if panel_ch_id:
        found = ctx.guild.get_channel(int(panel_ch_id))
        if found:
            target_channel = found

    panel_embed = discord.Embed(
        title=cfg.get("ticket_panel_title", "REQUEST A MIDDLE MAN"),
        description=cfg.get("ticket_panel_description", "Click below to create a middle man ticket."),
        color=0x5865F2
    )
    panel_embed.set_footer(text="One ticket per issue please.")

    import io
    file_to_send = None

    # Priority: attached image > URL argument > saved image URL
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
        if any(attachment.filename.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]):
            file_bytes = await attachment.read()
            file_to_send = discord.File(io.BytesIO(file_bytes), filename=attachment.filename)
            panel_embed.set_image(url=f"attachment://{attachment.filename}")
            # Save the CDN URL after sending (done below)
    elif image_url:
        panel_embed.set_image(url=image_url)
        cfg["ticket_panel_image_url"] = image_url
        save_config(cfg)
    elif cfg.get("ticket_panel_image_url"):
        panel_embed.set_image(url=cfg["ticket_panel_image_url"])

    view = TicketPanelView(options)
    if file_to_send:
        sent = await target_channel.send(embed=panel_embed, view=view, file=file_to_send)
        # Save the CDN attachment URL from the sent message for future use
        if sent.embeds and sent.embeds[0].image:
            cfg["ticket_panel_image_url"] = sent.embeds[0].image.url
            save_config(cfg)
    else:
        await target_channel.send(embed=panel_embed, view=view)

    # Confirm to the person who ran the command (if different channel)
    if target_channel != ctx.channel:
        await ctx.send(embed=embed("✅ Panel Sent", f"Ticket panel sent to {target_channel.mention}.", color=0x57F287))


# ── !setpanelmsg <title> | <description> ────────────────────────────────────
@bot.command(name="setpanelmsg")
@commands.has_permissions(administrator=True)
async def setpanelmsg(ctx, *, content: str = None):
    """Set the ticket panel title and description.
    Format: !setpanelmsg Your Title | Your description here
    Use a pipe | to separate title from description.
    """
    if not content or "|" not in content:
        await ctx.send(embed=embed(
            "❌ Usage",
            "`!setpanelmsg <title> | <description>`\nSeparate title and description with a `|` character.",
            color=0xED4245
        ))
        return
    parts = content.split("|", 1)
    title = parts[0].strip()
    description = parts[1].strip()
    cfg = get_config()
    cfg["ticket_panel_title"] = title
    cfg["ticket_panel_description"] = description
    save_config(cfg)
    await ctx.send(embed=embed(
        "✅ Panel Message Updated",
        f"**Title:** {title}\n**Description:** {description[:200]}{'...' if len(description) > 200 else ''}",
        color=0x57F287
    ))


# ── !setimagelog <channel_id> ────────────────────────────────────────────────
@bot.command(name="setimagelog")
@commands.has_permissions(administrator=True)
async def setimagelog(ctx, channel_id: str):
    """Set a private channel where the bot stores panel images permanently."""
    ch = ctx.guild.get_channel(int(channel_id))
    if not ch:
        await ctx.send(embed=embed("❌ Error", "Channel not found.", color=0xED4245))
        return
    cfg = get_config()
    cfg["image_log_channel"] = channel_id
    save_config(cfg)
    await ctx.send(embed=embed("✅ Image Log Channel Set", f"Panel images will be stored in {ch.mention}.", color=0x57F287))


# ── !setpanelimage ───────────────────────────────────────────────────────────
@bot.command(name="setpanelimage")
@commands.has_permissions(administrator=True)
async def setpanelimage(ctx):
    """Attach an image to this command to set it as the ticket panel image."""
    if not ctx.message.attachments:
        await ctx.send(embed=embed(
            "❌ No Image",
            "Please attach an image to the command.\nExample: type `!setpanelimage` and attach your image before sending.",
            color=0xED4245
        ))
        return

    attachment = ctx.message.attachments[0]
    if not any(attachment.filename.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]):
        await ctx.send(embed=embed("❌ Invalid File", "Please attach a valid image (PNG, JPG, GIF, WEBP).", color=0xED4245))
        return

    import io
    file_bytes = await attachment.read()

    cfg = get_config()

    # Try to store in image log channel (permanent storage)
    log_ch_id = cfg.get("image_log_channel")
    store_channel = None
    if log_ch_id:
        store_channel = ctx.guild.get_channel(int(log_ch_id))

    # Fall back to current channel if no log channel set
    if not store_channel:
        store_channel = ctx.channel

    # Send image to storage channel — do NOT delete it, so URL stays alive
    stored_msg = await store_channel.send(
        content="🖼️ Panel image (do not delete this message)",
        file=discord.File(io.BytesIO(file_bytes), filename=attachment.filename)
    )
    stable_url = stored_msg.attachments[0].url

    cfg["ticket_panel_image_url"] = stable_url
    save_config(cfg)

    confirm = discord.Embed(
        title="✅ Panel Image Set",
        description="Your image has been saved and will appear on the ticket panel.\n\n⚠️ **Do not delete the image message** that was just sent — it keeps the image alive.",
        color=0x57F287
    )
    confirm.set_image(url=stable_url)
    await ctx.send(embed=confirm)


# ── !addticketoption <label> [emoji] ────────────────────────────────────────
@bot.command(name="addticketoption")
@commands.has_permissions(administrator=True)
async def addticketoption(ctx, label: str, emoji: str = None):
    cfg = get_config()
    cfg["ticket_options"].append({"label": label, "emoji": emoji})
    save_config(cfg)
    await ctx.send(embed=embed("✅ Option Added", f"Button `{emoji or ''} {label}` added to ticket panel.", color=0x57F287))


# ── !clearticketoptions ──────────────────────────────────────────────────────
@bot.command(name="clearticketoptions")
@commands.has_permissions(administrator=True)
async def clearticketoptions(ctx):
    cfg = get_config()
    cfg["ticket_options"] = []
    save_config(cfg)
    await ctx.send(embed=embed("✅ Cleared", "All ticket options removed.", color=0x57F287))


# ── !setticketcategory <category_id> ────────────────────────────────────────
@bot.command(name="setticketcategory")
@commands.has_permissions(administrator=True)
async def setticketcategory(ctx, category_id: str):
    cfg = get_config()
    cfg["ticket_category"] = category_id
    save_config(cfg)
    await ctx.send(embed=embed("✅ Updated", f"Tickets will be created in category `{category_id}`.", color=0x57F287))


# ── !adduser <user_id or username> ──────────────────────────────────────────
@bot.command(name="adduser")
async def adduser(ctx, *, user_input: str):
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("ticket_support_roles", [])] + [int(r) for r in cfg.get("ticket_free_roles", [])]
    if not has_any_role(ctx.author, allowed):
        await ctx.send(embed=embed("❌ No Permission", "You don't have permission to use this command.", color=0xED4245))
        return

    tickets = get_tickets()
    if str(ctx.channel.id) not in tickets:
        await ctx.send(embed=embed("❌ Error", "This command can only be used inside a ticket channel.", color=0xED4245))
        return

    member = None

    # Try by ID or mention first
    cleaned = user_input.strip().replace("<@", "").replace("!", "").replace(">", "").strip()
    try:
        uid = int(cleaned)
        member = ctx.guild.get_member(uid) or await ctx.guild.fetch_member(uid)
    except ValueError:
        # Not an ID — search by username or display name
        search = user_input.strip().lower().lstrip("@")
        member = discord.utils.find(
            lambda m: m.name.lower() == search or m.display_name.lower() == search,
            ctx.guild.members
        )
        # If exact match not found, try partial match
        if not member:
            member = discord.utils.find(
                lambda m: search in m.name.lower() or search in m.display_name.lower(),
                ctx.guild.members
            )
    except discord.NotFound:
        pass

    if not member:
        await ctx.send(embed=embed("❌ User Not Found", f"Could not find `{user_input}` in this server. Try using their User ID instead.", color=0xED4245))
        return

    await ctx.channel.set_permissions(member, read_messages=True, send_messages=True)
    await ctx.send(embed=embed("✅ User Added", f"{member.mention} has been added to this ticket.", color=0x57F287))


# ── !claim ───────────────────────────────────────────────────────────────────
@bot.command(name="claim")
async def claim(ctx):
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("ticket_support_roles", [])]
    if not has_any_role(ctx.author, allowed):
        await ctx.send(embed=embed("❌ No Permission", "Only support roles can claim tickets.", color=0xED4245))
        return

    tickets = get_tickets()
    tid = str(ctx.channel.id)
    if tid not in tickets:
        await ctx.send(embed=embed("❌ Error", "This is not a ticket channel.", color=0xED4245))
        return

    if tickets[tid]["claimed_by"]:
        claimer = ctx.guild.get_member(int(tickets[tid]["claimed_by"]))
        await ctx.send(embed=embed("⚠️ Already Claimed", f"Claimed by {claimer.mention if claimer else 'someone'}.", color=0xFEE75C))
        return

    tickets[tid]["claimed_by"] = str(ctx.author.id)
    save_tickets(tickets)

    await ctx.channel.set_permissions(ctx.author, read_messages=True, send_messages=True, use_application_commands=True)
    await ctx.send(embed=embed("✅ Claimed", f"{ctx.author.mention} has claimed this ticket!", color=0x57F287))


# ── !unclaim ─────────────────────────────────────────────────────────────────
@bot.command(name="unclaim")
async def unclaim(ctx):
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("ticket_support_roles", [])]
    if not has_any_role(ctx.author, allowed):
        await ctx.send(embed=embed("❌ No Permission", "Only support roles can unclaim tickets.", color=0xED4245))
        return

    tickets = get_tickets()
    tid = str(ctx.channel.id)
    if tid not in tickets:
        await ctx.send(embed=embed("❌ Error", "This is not a ticket channel.", color=0xED4245))
        return

    if not tickets[tid]["claimed_by"]:
        await ctx.send(embed=embed("⚠️ Not Claimed", "This ticket has not been claimed yet.", color=0xFEE75C))
        return

    # Remove claim
    tickets[tid]["claimed_by"] = None
    save_tickets(tickets)

    # Remove send permissions from the person who unclaimed
    await ctx.channel.set_permissions(ctx.author, read_messages=True, send_messages=False, use_application_commands=False)

    # Ping all support roles
    pings = " ".join(
        ctx.guild.get_role(int(rid)).mention
        for rid in cfg.get("ticket_support_roles", [])
        if ctx.guild.get_role(int(rid))
    )

    unclaim_embed = discord.Embed(
        title="🔓 Ticket Unclaimed",
        description=f"{ctx.author.mention} has unclaimed this ticket.\nThis ticket needs a new support member!",
        color=0xFEE75C
    )
    await ctx.send(content=pings if pings else None, embed=unclaim_embed)


# ── !close ───────────────────────────────────────────────────────────────────
@bot.command(name="close")
async def close(ctx):
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("ticket_support_roles", [])]
    if not has_any_role(ctx.author, allowed):
        await ctx.send(embed=embed("❌ No Permission", "Only support roles can close tickets.", color=0xED4245))
        return
    tickets = get_tickets()
    if str(ctx.channel.id) not in tickets:
        await ctx.send(embed=embed("❌ Error", "This is not a ticket channel.", color=0xED4245))
        return
    await close_ticket_channel(ctx.channel, ctx.author)


# ── !confirmtrade ─────────────────────────────────────────────────────────────
@bot.command(name="confirmtrade")
async def confirmtrade(ctx):
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("ticket_support_roles", [])] + [int(r) for r in cfg.get("ticket_free_roles", [])]
    if not has_any_role(ctx.author, allowed):
        await ctx.send(embed=embed("❌ No Permission", "You don't have permission to use this command.", color=0xED4245))
        return

    tickets = get_tickets()
    if str(ctx.channel.id) not in tickets:
        await ctx.send(embed=embed("❌ Error", "This command can only be used in a ticket channel.", color=0xED4245))
        return

    confirm_embed = discord.Embed(
        title="🤝 Confirm Trade?",
        description="Both parties must confirm to complete the trade.\nClick **Confirm** below to agree.",
        color=0xFEE75C
    )
    await ctx.send(embed=confirm_embed, view=ConfirmTradeView())


# ── !mminfoeng ───────────────────────────────────────────────────────────────
@bot.command(name="mminfoeng")
async def mminfoeng(ctx):
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("ticket_support_roles", [])] + [int(r) for r in cfg.get("ticket_free_roles", [])]
    if not has_any_role(ctx.author, allowed):
        await ctx.send(embed=embed("❌ No Permission", "You don't have permission to use this command.", color=0xED4245))
        return

    info_embed = discord.Embed(
        title="🛡️ How This MM Deal Works",
        color=0x5865F2
    )
    info_embed.add_field(name="1️⃣ Item Secured", value="The Seller gives the in-game item to the MM. The MM confirms they have it in their inventory.", inline=False)
    info_embed.add_field(name="2️⃣ Direct Payment", value='Once the MM confirms they have the item, the Buyer sends the PayPal payment directly to the Seller (usually via "Friends & Family").', inline=False)
    info_embed.add_field(name="3️⃣ Proof of Payment", value="The Buyer sends a screenshot of the completed payment to the group chat. The Seller confirms they've received the funds in their PayPal balance.", inline=False)
    info_embed.add_field(name="4️⃣ Item Release", value='Once the Seller says "received," the MM trades the in-game item to the Buyer.', inline=False)
    info_embed.add_field(name="5️⃣ Deal Done", value="The MM leaves the chat, and the trade is complete.", inline=False)

    await ctx.send(embed=info_embed, view=MMInfoView())


# ── !mminfofrc ───────────────────────────────────────────────────────────────
@bot.command(name="mminfofrc")
async def mminfofrc(ctx):
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("ticket_support_roles", [])] + [int(r) for r in cfg.get("ticket_free_roles", [])]
    if not has_any_role(ctx.author, allowed):
        await ctx.send(embed=embed("❌ No Permission", "You don't have permission to use this command.", color=0xED4245))
        return

    info_embed = discord.Embed(
        title="🛡️ Fonctionnement de la Transaction MM",
        color=0x5865F2
    )
    info_embed.add_field(name="1️⃣ Sécurisation de l'objet", value="Le Vendeur donne l'objet en jeu au MM. Le MM confirme qu'il l'a bien dans son inventaire.", inline=False)
    info_embed.add_field(name="2️⃣ Paiement Direct", value='Une fois que le MM confirme avoir l\'objet, l\'Acheteur envoie le paiement PayPal directement au Vendeur (généralement via "Entre proches").', inline=False)
    info_embed.add_field(name="3️⃣ Preuve de Paiement", value="L'Acheteur envoie une capture d'écran du paiement effectué dans le groupe. Le Vendeur confirme qu'il a bien reçu les fonds sur son solde PayPal.", inline=False)
    info_embed.add_field(name="4️⃣ Remise de l'objet", value='Dès que le Vendeur confirme la réception ("reçu"), le MM donne l\'objet en jeu à l\'Acheteur.', inline=False)
    info_embed.add_field(name="5️⃣ Transaction Terminée", value="Le MM quitte la discussion et l'échange est validé.", inline=False)

    await ctx.send(embed=info_embed, view=MMInfoView())


# ── !setcookedrole <role_id> ─────────────────────────────────────────────────
@bot.command(name="setcookedrole")
@commands.has_permissions(administrator=True)
async def setcookedrole(ctx, role_id: str):
    """Set the role given to users who click 'I Wanna Be Rich' in !cooked"""
    cfg = get_config()
    role = ctx.guild.get_role(int(role_id))
    if not role:
        await ctx.send(embed=embed("❌ Error", "Role not found.", color=0xED4245))
        return
    cfg["cooked_rich_role"] = role_id
    save_config(cfg)
    await ctx.send(embed=embed("✅ Updated", f"Role {role.mention} will be given to users who click **I Wanna Be Rich**.", color=0x57F287))


# ── !cooked ───────────────────────────────────────────────────────────────────
@bot.command(name="cooked")
async def cooked(ctx):
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("ticket_support_roles", [])]
    if not has_any_role(ctx.author, allowed):
        await ctx.send(embed=embed("❌ No Permission", "Only support roles can use this command.", color=0xED4245))
        return

    cooked_embed = discord.Embed(
        title="⚠️ YOU ARE SCAMMED ⚠️",
        color=0xED4245
    )
    cooked_embed.add_field(
        name="1️⃣ You can join us to get back what you lost :",
        value="Find guys with good stuff and convince them to use this MM server.",
        inline=False
    )
    cooked_embed.add_field(
        name="2️⃣ You or the guy make a ticket",
        value="Open a ticket in [ 💼 ] request-a-middle-man",
        inline=False
    )
    cooked_embed.add_field(
        name="3️⃣ MM handles everything",
        value="MM will claim and handle everything and will give you your split **50 - 50**.",
        inline=False
    )
    cooked_embed.add_field(
        name="🚀 START YOUR HITTING JOURNEY",
        value='**JOIN US FOR THAT — TAP " I WANNA BE RICH " BELOW**',
        inline=False
    )

    await ctx.send(embed=cooked_embed, view=CookedView())


# ── !help ─────────────────────────────────────────────────────────────────────
@bot.command(name="help")
async def help_cmd(ctx):
    h = discord.Embed(title="📖 Bot Commands", color=0x5865F2)

    h.add_field(name="🔧 Admin Setup", value="""
`!setsendrole <role_id>` — Allow role to use `!sendmsg`
`!setsupportrole <role_id>` — Set ticket support/MM role (claim tickets)
`!setfreerole <role_id>` — Role that can msg in tickets without claiming
`!setticketcategory <category_id>` — Set category for ticket channels
`!addticketoption <label> [emoji]` — Add a button to ticket panel
`!clearticketoptions` — Clear all ticket panel buttons
`!ticketpanel [image_url]` — Send ticket panel (or attach image directly)
`!setpanelchannel <channel_id>` — Set where `!ticketpanel` always sends the panel
`!setpanelmsg <title> | <description>` — Set ticket panel title & description
`!setpanelimage` — Set panel image by attaching it directly (no link needed)
`!setimagelog <channel_id>` — Set private channel to store panel image permanently
`!setcookedrole <role_id>` — Set role given when user clicks "I Wanna Be Rich"
""", inline=False)

    h.add_field(name="📨 Messaging", value="""
`!sendmsg <channel_id> <message>` — Send a message to a channel *(allowed roles)* — attach image to include it
""", inline=False)

    h.add_field(name="🎫 Ticket Commands", value="""
`!claim` — Claim a ticket *(support role)*
`!unclaim` — Unclaim a ticket & ping all support roles *(support role)*
`!close` — Close & delete the ticket channel *(support role only)*
`!adduser <@user, username, or ID>` — Add a user to the ticket *(support/free role)*
`!confirmtrade` — Show confirm trade buttons *(support/free role)*
`!cooked` — Send the "YOU ARE SCAMMED" embed with rich/poor buttons *(support role)*
""", inline=False)

    h.add_field(name="🛡️ MM Info", value="""
`!mminfoeng` — MM deal explanation in English *(support/free role)*
`!mminfofrc` — MM deal explanation in French *(support/free role)*
""", inline=False)

    h.set_footer(text="Prefix: !  |  Admin commands require Administrator permission")
    await ctx.send(embed=h)


# ── Rate limit / error handler ───────────────────────────────────────────────
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=embed("❌ No Permission", "You don't have permission to use this command.", color=0xED4245))
        return
    if isinstance(error, discord.errors.HTTPException) and error.status == 429:
        retry_after = error.retry_after if hasattr(error, 'retry_after') else 30
        print(f"⚠️ Rate limited! Waiting {retry_after:.2f} seconds...")
        await asyncio.sleep(retry_after)
        await ctx.reinvoke()
        return
    print(f"❌ Command error: {error}")


# ══════════════════════════════════════════════════════════════════════════════
#  RUN
# ══════════════════════════════════════════════════════════════════════════════
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ ERROR: Set DISCORD_TOKEN environment variable.")
else:
    keep_alive()
    # Auto reconnect loop — if bot crashes it will restart after 10 seconds
    while True:
        try:
            bot.run(TOKEN, reconnect=True)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                wait = 60
                print(f"⚠️ Rate limited on login! Waiting {wait} seconds before retrying...")
                time.sleep(wait)
            else:
                print(f"❌ HTTP error: {e}")
                time.sleep(10)
        except discord.errors.LoginFailure:
            print("❌ Invalid token! Please check your DISCORD_TOKEN in Render environment variables.")
            break
        except Exception as e:
            print(f"❌ Unexpected error: {e} — Restarting in 10 seconds...")
            time.sleep(10)
