import os
import json
import asyncio
import subprocess
import threading
import time
from datetime import datetime
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
import discord
from discord.ext import commands

# ================== قراءة المتغيرات البيئية ==================
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
CHANNEL_ID = int(os.environ.get('CHANNEL_ID', 0))

# 🔑 توكن المصادقة بين السيرفر والعميل
SERVER_SECRET = os.environ.get('SERVER_SECRET', 'my_super_secret_key_12345')

# التحقق من وجود المتغيرات
if not DISCORD_TOKEN:
    print("❌ خطأ: DISCORD_TOKEN غير موجود في المتغيرات البيئية!")
    print("📌 أضفه في Render: Environment Variables → DISCORD_TOKEN")
    exit(1)

if not CHANNEL_ID:
    print("❌ خطأ: CHANNEL_ID غير موجود في المتغيرات البيئية!")
    print("📌 أضفه في Render: Environment Variables → CHANNEL_ID")
    exit(1)

print(f"✅ تم قراءة المتغيرات البيئية بنجاح")
print(f"   - CHANNEL_ID: {CHANNEL_ID}")
print(f"   - SERVER_SECRET: {'*' * len(SERVER_SECRET)} (مخفي)")

# ================== إعدادات Flask و WebSocket ==================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key_here')

# استخدام eventlet كل async_mode
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# ================== متغيرات عامة ==================
connected_clients = {}  # {client_name: {'sid': sid, 'name': name, 'ip': ip, 'authenticated': bool}}
client_responses = {}   # {command_id: response_data}
discord_queue = []
failed_attempts = {}    # تتبع محاولات المصادقة الفاشلة

# ================== WebSocket للعملاء ==================
@socketio.on('connect')
def handle_connect():
    """عند اتصال عميل جديد"""
    print(f"[+] عميل متصل: {request.sid} من {request.remote_addr}")
    # نطلب المصادقة فوراً
    emit('request_auth', {'message': 'يرجى إرسال توكن المصادقة'})

@socketio.on('authenticate')
def handle_authenticate(data):
    """مصادقة العميل"""
    client_token = data.get('token', '')
    client_name = data.get('name', 'Unknown')
    client_ip = request.remote_addr
    
    # التحقق من التوكن
    if client_token == SERVER_SECRET:
        # مصادقة ناجحة
        connected_clients[client_name] = {
            'sid': request.sid,
            'name': client_name,
            'ip': client_ip,
            'authenticated': True,
            'connected_at': datetime.now().isoformat()
        }
        print(f"[✅] مصادقة ناجحة للجهاز: {client_name} من {client_ip}")
        send_to_discord(f"✅ الجهاز **{client_name}** متصل من {client_ip}")
        emit('auth_success', {
            'status': 'success',
            'message': f'مرحباً {client_name}، تم المصادقة بنجاح'
        })
        
        if client_ip in failed_attempts:
            del failed_attempts[client_ip]
    else:
        print(f"[❌] محاولة مصادقة فاشلة: {client_name} من {client_ip}")
        
        if client_ip not in failed_attempts:
            failed_attempts[client_ip] = 0
        failed_attempts[client_ip] += 1
        
        if failed_attempts[client_ip] >= 5:
            print(f"[🚫] تم حظر {client_ip} بسبب محاولات فاشلة متكررة")
            emit('auth_failed', {
                'status': 'error',
                'message': 'تم حظرك بسبب محاولات فاشلة متكررة'
            })
            request.disconnect()
        else:
            emit('auth_failed', {
                'status': 'error',
                'message': f'توكن غير صحيح! المحاولة {failed_attempts[client_ip]} من 5'
            })

@socketio.on('command_result')
def handle_command_result(data):
    """استقبال نتيجة الأمر من العميل"""
    client_name = data.get('client_name')
    if client_name not in connected_clients or not connected_clients[client_name]['authenticated']:
        print(f"[❌] محاولة غير مصرح بها من {client_name}")
        return
    
    result = data.get('result')
    command_id = data.get('command_id')
    
    if client_name in connected_clients:
        client_responses[command_id] = result
        print(f"[+] نتيجة من {client_name}: {result[:50]}...")

@socketio.on('disconnect')
def handle_disconnect():
    """عند انقطاع العميل"""
    disconnected_name = None
    for name, info in connected_clients.items():
        if info['sid'] == request.sid:
            disconnected_name = name
            break
    if disconnected_name:
        del connected_clients[disconnected_name]
        send_to_discord(f"🔴 الجهاز **{disconnected_name}** غير متصل")
        print(f"[-] عميل غير متصل: {disconnected_name}")

def send_to_discord(message):
    """إضافة رسالة إلى قائمة الانتظار للبوت"""
    discord_queue.append(message)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'[+] بوت ديسكورد متصل كـ {bot.user}')
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("🖥️ **السيرفر على Render جاهز للعمل!**")
        await channel.send(f"🔑 **توكن المصادقة:** `{SERVER_SECRET}` (احتفظ به سرياً)")
    else:
        print(f"❌ تحذير: لم أجد الشانل {CHANNEL_ID}")
        print(f"📌 تأكد من أن البوت مضاف إلى السيرفر وأن CHANNEL_ID صحيح")

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
            await message.channel.send("⚠️ استخدم: `!exec <اسم_الجهاز> <أمر>`")
            return
        
        _, client_name, command = parts
        
        if client_name not in connected_clients:
            await message.channel.send(f"❌ الجهاز **{client_name}** غير متصل")
            return
        
        if not connected_clients[client_name]['authenticated']:
            await message.channel.send(f"❌ الجهاز **{client_name}** غير مصرح له")
            return
        
        command_id = f"{client_name}_{datetime.now().timestamp()}"
        client_sid = connected_clients[client_name]['sid']
        
        try:
            socketio.emit('execute_command', {
                'command': command,
                'command_id': command_id
            }, room=client_sid)
            
            await asyncio.sleep(30)
            
            result = client_responses.pop(command_id, "⏰ انتهت المهلة")
            
            if len(result) > 1900:
                result = result[:1900] + "\n... (مقطوع)"
            
            await message.channel.send(f"💻 **{client_name}** => `{command}`\n```\n{result}\n```")
            
        except Exception as e:
            await message.channel.send(f"❌ خطأ: {e}")
    
    elif content == '!list':
        if not connected_clients:
            await message.channel.send("❌ لا توجد أجهزة متصلة")
            return
        devices = "\n".join([f"- {name} {'🔒' if info['authenticated'] else '❌'}" 
                            for name, info in connected_clients.items()])
        await message.channel.send(f"📱 **الأجهزة المتصلة:**\n{devices}")
    
    elif content == '!help':
        help_text = """
**🤖 أوامر البوت:**
`!help` - عرض هذه المساعدة
`!list` - عرض الأجهزة المتصلة
`!exec <اسم_الجهاز> <أمر>` - تنفيذ أمر على جهاز معين

**📝 أمثلة:**
`!exec laptop dir`
`!exec desktop ipconfig`

**🔑 توكن المصادقة:** تم إرساله عند تشغيل البوت
        """
        await message.channel.send(help_text)
    
    elif content == '!ping':
        await message.channel.send("🏓 Pong!")

@app.route('/')
def index():
    return jsonify({
        'status': 'online',
        'clients': len(connected_clients),
        'authenticated_clients': sum(1 for c in connected_clients.values() if c['authenticated']),
        'message': '🚀 سيرفر التحكم عن بعد يعمل!'
    })

@app.route('/status')
def status():
    return jsonify({
        'connected_clients': list(connected_clients.keys()),
        'authenticated': [name for name, info in connected_clients.items() if info['authenticated']],
        'total': len(connected_clients)
    })

def run_socketio():
    """تشغيل سيرفر WebSocket"""
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)

if __name__ == '__main__':
    def run_bot():
        try:
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            print(f"❌ خطأ في تشغيل البوت: {e}")
    
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    time.sleep(2)
    
    # تشغيل خادم WebSocket
    run_socketio()
