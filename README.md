# 🤖 Xento Account Creator Bot

Telegram bot that creates xento.org accounts using temporary email + OTP verification, with referral support and quest completion.

## Features

- 🔐 **Auto Account Creation** - Temporary email + OTP verification
- 🔗 **Referral Support** - Use referral code/link during signup
- 🔥 **Mass Creation** - Bulk accounts with parallel processing (up to 7 concurrent)
- 🎯 **Quest Completion** - Auto-complete quests 07 (X), 08 (Instagram), 10 (YouTube)
- 📧 **Multi Email Providers** - 6+ temp email services with round-robin load balancing
- 📊 **Live Progress UI** - Real-time emoji progress bar for mass creation

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message & instructions |
| `/create` | Create a single account (auto-quest) |
| `/create CODE` | Create with referral code |
| `/mass` | Bulk account creation (parallel) |
| `/quest` | Complete quests 07, 08, 10 |
| `/status` | Check bot status |
| `/help` | Help message |
| `/cancel` | Cancel current operation |

## Email Providers (Round-Robin)

The bot uses multiple temp email services with round-robin load balancing:

1. **mail.tm** - Primary, most reliable
2. **1secmail.com** - Fast, simple API
3. **TempMail.plus** - 1secmail-compatible domains
4. **Guerrilla Mail** - Session-based fallback
5. **Tempmail.lol** - Modern API
6. **Mailnesia** - Free inbox

Each account creation request cycles to the next provider, so parallel requests don't overwhelm a single service. On failure, it retries with the next provider.

## Deploy to Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new?template=https://github.com/xento-bot/xento-bot&envs=TELEGRAM_BOT_TOKEN&TELEGRAM_BOT_TOKENDesc=Your+Telegram+Bot+Token+from+@BotFather)

### Manual Railway Deployment

1. Click the button above, or:
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add environment variable: `TELEGRAM_BOT_TOKEN` = your bot token
4. Railway will auto-install dependencies and Playwright browsers
5. Bot starts automatically!

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium --with-deps

# Run the bot
python3 bot.py YOUR_TELEGRAM_BOT_TOKEN
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | ✅ Yes | - | Telegram bot token from @BotFather |
| `MAX_CONCURRENT_ACCOUNTS` | No | 7 | Max parallel browser instances for /mass |
| `MAX_MASS_LIMIT` | No | 500 | Max accounts per /mass command |

## Quest Screenshots

Place proof screenshots in the `proofs/` directory:
- `quest_07_x.jpg` - X (Twitter) follow screenshot
- `quest_08_instagram.jpg` - Instagram follow screenshot
- `quest_10_youtube.jpg` - YouTube subscribe screenshot

## Architecture

```
bot.py           → Telegram bot handlers (commands, callbacks, progress UI)
temp_mail.py     → Multi-provider temp email with round-robin
xento_signup.py  → Playwright browser automation for account creation
xento_quests.py  → Quest completion via browser automation
proofs/          → Quest proof screenshots
data/            → Account data storage (accounts.json)
```

## License

MIT
