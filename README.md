# 🤖 Discord MM/Ticket Bot — Setup Guide

## 📦 Installation

```bash
pip install -r requirements.txt
```

## 🔑 Running the Bot

Set your bot token as an environment variable, then run:

```bash
# Windows
set DISCORD_TOKEN=your_token_here
python bot.py

# Mac/Linux
DISCORD_TOKEN=your_token_here python bot.py
```

---

## ⚙️ First-Time Setup (Admin Commands)

Run these commands in your Discord server after inviting the bot.

### 1. Set Roles

| Command | Description |
|---|---|
| `!setsendrole <role_id>` | Allow this role to use `!sendmsg` |
| `!setsupportrole <role_id>` | MM/support role — can claim tickets, use MM commands |
| `!setfreerole <role_id>` | Can message in tickets & use commands WITHOUT claiming |

> You can run these multiple times to add multiple roles.

### 2. Set Ticket Category (optional)

```
!setticketcategory <category_id>
```
Tickets will be created inside this category.

### 3. Add Ticket Panel Buttons

```
!addticketoption Trade 🔁
!addticketoption Support 🛠️
!addticketoption Other ❓
```

### 4. Send the Ticket Panel

```
!ticketpanel https://your-image-url.com/banner.png
```
Omit the URL if you don't want an image.

---

## 🎫 How Tickets Work

1. User clicks a button on the ticket panel
2. A **modal form** appears asking:
   - What is the trade?
   - @user or user ID of the other person
   - Can you join PS?
3. After submit → a private channel `ticket-username` is created
4. Support roles are **pinged** automatically
5. Support role can `!claim` the ticket to gain messaging access
6. Free roles can message immediately without claiming

---

## 📋 Full Command List

### Admin Only
| Command | Description |
|---|---|
| `!setsendrole <role_id>` | Grant sendmsg permission to role |
| `!setsupportrole <role_id>` | Add a support/MM role |
| `!setfreerole <role_id>` | Add a free-access role (no claim needed) |
| `!setticketcategory <id>` | Set ticket channel category |
| `!addticketoption <label> [emoji]` | Add ticket panel button |
| `!clearticketoptions` | Remove all panel buttons |
| `!ticketpanel [image_url]` | Post the ticket panel |
| `!rolemsg <role_id> <btn_label> <message>` | Post a message with role-grant button |

### Allowed Roles
| Command | Description |
|---|---|
| `!sendmsg <channel_id> <message>` | Send embed to a channel |
| `!claim` | Claim a ticket (support role only) |
| `!close` | Close & delete ticket channel |
| `!adduser <user_id>` | Add user to ticket |
| `!confirmtrade` | Show confirm trade prompt |
| `!mminfoeng` | MM info in English |
| `!mminfofrc` | MM info in French |

### Everyone
| Command | Description |
|---|---|
| `!help` | Show all commands |

---

## 🔐 Bot Permissions Required

When inviting the bot, make sure it has:
- **Manage Channels** (to create/delete ticket channels)
- **Manage Roles** (to assign roles via role button)
- **Read/Send Messages**
- **Embed Links**
- **Manage Permissions** (to set channel overwrites)

---

## 🛠️ Getting IDs in Discord

Enable **Developer Mode**: Settings → Advanced → Developer Mode → ON

Then right-click any role/channel/category → **Copy ID**
